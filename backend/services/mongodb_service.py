import os
import json
import time
import uuid
import logging
from datetime import datetime, timezone
import pymongo
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from backend.config import settings

logger = logging.getLogger("mongodb_service")
# Ensure standard handler exists
if not logger.handlers:
    ch = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.setLevel(logging.INFO)

class MongoDBService:
    """
    Service responsible for interacting with MongoDB, logging transaction records,
    biometric check attempts, audit logs, and per-user adaptive calibration profiles.
    Includes a self-healing fallback to local JSONL file storage when MongoDB is offline.
    """
    
    def __init__(self):
        self.use_fallback = True
        self.client = None
        self.db = None
        self.logs_collection = None
        self.calibration_collection = None
        self._last_connect_attempt = 0.0
        self._reconnect_cooldown_sec = 5.0
        # Create temp folder path for fallback logs and calibration profiles
        self.fallback_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp")
        os.makedirs(self.fallback_dir, exist_ok=True)
        self.fallback_filepath = os.path.join(self.fallback_dir, "db_fallback_logs.jsonl")
        self.fallback_calibration_filepath = os.path.join(self.fallback_dir, "db_fallback_calibrations.jsonl")
        self._try_connect(force=True)

    def _try_connect(self, force: bool = False) -> bool:
        """Attempts to connect to MongoDB if currently in fallback mode."""
        if not self.use_fallback and self.logs_collection is not None and self.calibration_collection is not None:
            return True
        now = time.time()
        if not force and (now - self._last_connect_attempt) < self._reconnect_cooldown_sec:
            return False
        self._last_connect_attempt = now
        try:
            logger.info(f"Connecting to MongoDB at {settings.MONGODB_URI}...")
            # Configure connection pooling and low timeout (2000ms) for fast fallback
            self.client = pymongo.MongoClient(
                settings.MONGODB_URI,
                serverSelectionTimeoutMS=2000,
                connectTimeoutMS=2000
            )
            # Trigger server connection check to verify if server is actually alive
            self.client.admin.command('ping')
            
            self.db = self.client[settings.DATABASE_NAME]
            self.logs_collection = self.db["verification_logs"]
            self.calibration_collection = self.db["calibration_profiles"]
            
            # Create indexing for audit retrieval performance
            self.logs_collection.create_index([("userId", pymongo.ASCENDING)])
            self.logs_collection.create_index([("sessionId", pymongo.ASCENDING)])
            self.logs_collection.create_index([("timestamp", pymongo.DESCENDING)])

            # Create indexing for per-user calibration profiles
            self.calibration_collection.create_index([("userId", pymongo.ASCENDING)], unique=True)
            self.calibration_collection.create_index([("updatedAt", pymongo.DESCENDING)])
            
            self.use_fallback = False
            logger.info("Successfully connected to MongoDB database and initialized collection indexes.")
            return True
        except (ConnectionFailure, ServerSelectionTimeoutError, Exception) as err:
            logger.warning(
                f"Failed to connect to MongoDB server: {err}. "
                f"Falling back to local file storage at: {self.fallback_filepath}"
            )
            self.use_fallback = True
            return False
        
    def log_verification_attempt(self, attempt_data: dict) -> str:
        """
        Saves details of a liveness verification check attempt to the database (or fallback file).
        
        Inputs:
            attempt_data (dict): Record containing scores, status, and metadata.
            
        Outputs:
            str: Generated database log ID (MongoDB Object ID or fallback UUID).
        """
        # Ensure timestamp is set in attempt_data
        if "timestamp" not in attempt_data:
            attempt_data["timestamp"] = datetime.now(timezone.utc).isoformat()

        if self.use_fallback:
            self._try_connect()
            
        if self.use_fallback:
            attempt_id = f"fallback-{uuid.uuid4()}"
            record = {"_id": attempt_id, **attempt_data}
            try:
                with open(self.fallback_filepath, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                logger.info(f"Logged verification attempt to fallback file: {attempt_id}")
                return attempt_id
            except Exception as file_err:
                logger.error(f"Failed to write to fallback verification logs: {file_err}")
                return "failed-fallback-write"
        else:
            try:
                # pymongo insertion (copy to avoid mutating caller dict with ObjectId)
                doc_to_insert = dict(attempt_data)
                result = self.logs_collection.insert_one(doc_to_insert)
                attempt_id = str(result.inserted_id)
                logger.info(f"Logged verification attempt to MongoDB: {attempt_id}")
                return attempt_id
            except Exception as mongo_err:
                logger.error(f"Failed to insert verification log into MongoDB: {mongo_err}")
                self.use_fallback = True
                # Fallback on runtime db errors
                attempt_id = f"fallback-{uuid.uuid4()}"
                record = {"_id": attempt_id, **attempt_data, "mongo_error": str(mongo_err)}
                try:
                    with open(self.fallback_filepath, "a", encoding="utf-8") as f:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    logger.info(f"Fallen back to local file logging after MongoDB error: {attempt_id}")
                    return attempt_id
                except Exception as file_err:
                    logger.error(f"Fallback file logging also failed: {file_err}")
                    return "failed-logging"

    def fetch_user_history(self, user_id: str) -> list:
        """
        Retrieves historic biometric authentication records for audit inspection.
        
        Inputs:
            user_id (str): Unique identifier of the user (or session ID) to search.
            
        Outputs:
            list: List of historical verification logs.
        """
        if not user_id:
            return []

        if self.use_fallback:
            self._try_connect()
            
        if self.use_fallback:
            logs = []
            if not os.path.exists(self.fallback_filepath):
                return []
            try:
                with open(self.fallback_filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        if record.get("userId") == user_id or record.get("sessionId") == user_id:
                            # Convert _id to string for JSON compatibility
                            if "_id" in record:
                                record["_id"] = str(record["_id"])
                            logs.append(record)
                # Sort chronologically (newest first)
                logs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                return logs
            except Exception as file_err:
                logger.error(f"Failed to read from fallback verification logs: {file_err}")
                return []
        else:
            try:
                # Query MongoDB collection: find matches by userId or sessionId
                cursor = self.logs_collection.find(
                    {"$or": [{"userId": user_id}, {"sessionId": user_id}]}
                ).sort("timestamp", -1)
                
                results = []
                for doc in cursor:
                    doc["_id"] = str(doc["_id"])
                    results.append(doc)
                return results
            except Exception as mongo_err:
                logger.error(f"Failed to fetch user history from MongoDB: {mongo_err}")
                return []

    def save_calibration_profile(self, profile_data: dict) -> str:
        """
        Upserts a per-user calibration profile into MongoDB (`calibration_profiles`)
        or appends/updates the fallback JSONL store if MongoDB is unavailable.
        """
        user_id = profile_data.get("userId")
        if not user_id:
            raise ValueError("profile_data must include 'userId'")

        now_iso = datetime.now(timezone.utc).isoformat()
        profile_to_save = {k: v for k, v in profile_data.items() if k != "_id"}
        profile_to_save["updatedAt"] = profile_to_save.get("updatedAt") or now_iso

        if self.use_fallback:
            self._try_connect()

        if not self.use_fallback and self.calibration_collection is not None:
            try:
                self.calibration_collection.update_one(
                    {"userId": user_id},
                    {"$set": profile_to_save},
                    upsert=True
                )
                saved_doc = self.calibration_collection.find_one({"userId": user_id})
                record_id = str(saved_doc["_id"]) if saved_doc and "_id" in saved_doc else f"mongo-{user_id}"
                logger.info(f"Saved calibration profile for userId='{user_id}' to MongoDB: {record_id}")
                return record_id
            except Exception as mongo_err:
                logger.error(f"Failed to save calibration profile to MongoDB: {mongo_err}")
                self.use_fallback = True

        # Fallback storage in local JSONL file
        fallback_id = f"fallback-cal-{uuid.uuid4()}"
        record = {"_id": fallback_id, **profile_to_save}
        try:
            with open(self.fallback_calibration_filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info(f"Saved calibration profile for userId='{user_id}' to fallback file: {fallback_id}")
            return fallback_id
        except Exception as file_err:
            logger.error(f"Failed to write calibration profile to fallback file: {file_err}")
            return "failed-calibration-write"

    def get_calibration_profile(self, user_id: str) -> dict:
        """
        Retrieves the latest calibration profile for a given userId (or sessionId)
        from MongoDB (`calibration_profiles`) or the local fallback file.
        Returns None if no profile exists.
        """
        if not user_id:
            return None

        if self.use_fallback:
            self._try_connect()

        if not self.use_fallback and self.calibration_collection is not None:
            try:
                doc = self.calibration_collection.find_one({"userId": user_id})
                if doc:
                    doc["_id"] = str(doc["_id"])
                    return doc
            except Exception as mongo_err:
                logger.error(f"Failed to fetch calibration profile from MongoDB: {mongo_err}")
                self.use_fallback = True

        # Check fallback file (latest entry for user_id wins; handle deleted tombstones)
        if not os.path.exists(self.fallback_calibration_filepath):
            return None
        try:
            latest = None
            with open(self.fallback_calibration_filepath, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record.get("userId") == user_id:
                        if record.get("_deleted") is True:
                            latest = None
                        else:
                            if "_id" in record:
                                record["_id"] = str(record["_id"])
                            latest = record
            return latest
        except Exception as file_err:
            logger.error(f"Failed to read calibration profile from fallback file: {file_err}")
            return None

    def delete_calibration_profile(self, user_id: str) -> bool:
        """
        Deletes/resets the calibration profile for a given userId in both MongoDB and fallback file.
        """
        if not user_id:
            return False

        if self.use_fallback:
            self._try_connect()

        deleted = False
        if not self.use_fallback and self.calibration_collection is not None:
            try:
                res = self.calibration_collection.delete_many({"userId": user_id})
                deleted = res.deleted_count > 0
            except Exception as mongo_err:
                logger.error(f"Failed to delete calibration profile from MongoDB: {mongo_err}")
                self.use_fallback = True

        # Also write a tombstone to fallback file if it exists so stale fallback entries are cleared
        if os.path.exists(self.fallback_calibration_filepath):
            try:
                tombstone = {
                    "_id": f"tombstone-{uuid.uuid4()}",
                    "userId": user_id,
                    "_deleted": True,
                    "updatedAt": datetime.now(timezone.utc).isoformat()
                }
                with open(self.fallback_calibration_filepath, "a", encoding="utf-8") as f:
                    f.write(json.dumps(tombstone) + "\n")
                deleted = True
            except Exception as file_err:
                logger.error(f"Failed to write calibration tombstone to fallback file: {file_err}")
        return deleted

# Singleton instance
mongodb_service = MongoDBService()

