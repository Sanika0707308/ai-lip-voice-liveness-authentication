import os
import json
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
    biometric check attempts, and audit logs.
    Includes a self-healing fallback to local JSONL file storage when MongoDB is offline.
    """
    
    def __init__(self):
        self.use_fallback = False
        # Create temp folder path for fallback logs
        self.fallback_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp")
        os.makedirs(self.fallback_dir, exist_ok=True)
        self.fallback_filepath = os.path.join(self.fallback_dir, "db_fallback_logs.jsonl")
        
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
            
            # Create indexing for audit retrieval performance
            self.logs_collection.create_index([("userId", pymongo.ASCENDING)])
            self.logs_collection.create_index([("sessionId", pymongo.ASCENDING)])
            self.logs_collection.create_index([("timestamp", pymongo.DESCENDING)])
            
            logger.info("Successfully connected to MongoDB database and initialized collection index.")
        except (ConnectionFailure, ServerSelectionTimeoutError, Exception) as err:
            logger.warning(
                f"Failed to connect to MongoDB server: {err}. "
                f"Falling back to local file storage at: {self.fallback_filepath}"
            )
            self.use_fallback = True
        
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
            attempt_id = f"fallback-{uuid.uuid4()}"
            record = {"_id": attempt_id, **attempt_data}
            try:
                with open(self.fallback_filepath, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
                logger.info(f"Logged verification attempt to fallback file: {attempt_id}")
                return attempt_id
            except Exception as file_err:
                logger.error(f"Failed to write to fallback verification logs: {file_err}")
                return "failed-fallback-write"
        else:
            try:
                # pymongo insertion
                result = self.logs_collection.insert_one(attempt_data)
                attempt_id = str(result.inserted_id)
                logger.info(f"Logged verification attempt to MongoDB: {attempt_id}")
                return attempt_id
            except Exception as mongo_err:
                logger.error(f"Failed to insert verification log into MongoDB: {mongo_err}")
                # Fallback on runtime db errors
                attempt_id = f"fallback-{uuid.uuid4()}"
                record = {"_id": attempt_id, **attempt_data, "mongo_error": str(mongo_err)}
                try:
                    with open(self.fallback_filepath, "a", encoding="utf-8") as f:
                        f.write(json.dumps(record) + "\n")
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

# Singleton instance
mongodb_service = MongoDBService()
