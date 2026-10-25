import os
import json
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
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
        self.monitoring_sessions_collection = None
        self.monitoring_events_collection = None
        self._last_connect_attempt = 0.0
        self._reconnect_cooldown_sec = 5.0
        # Create temp folder path for fallback logs and calibration profiles
        self.fallback_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp")
        os.makedirs(self.fallback_dir, exist_ok=True)
        self.fallback_filepath = os.path.join(self.fallback_dir, "db_fallback_logs.jsonl")
        self.fallback_calibration_filepath = os.path.join(self.fallback_dir, "db_fallback_calibrations.jsonl")
        self.fallback_sessions_filepath = os.path.join(self.fallback_dir, "db_fallback_monitoring_sessions.jsonl")
        self.fallback_events_filepath = os.path.join(self.fallback_dir, "db_fallback_monitoring_events.jsonl")
        self._try_connect(force=True)

    def _try_connect(self, force: bool = False) -> bool:
        """Attempts to connect to MongoDB if currently in fallback mode."""
        if not self.use_fallback and self.logs_collection is not None and self.calibration_collection is not None and self.monitoring_sessions_collection is not None:
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
            self.monitoring_sessions_collection = self.db["monitoring_sessions"]
            self.monitoring_events_collection = self.db["monitoring_events"]
            
            # Create indexing for audit retrieval performance
            self.logs_collection.create_index([("userId", pymongo.ASCENDING)])
            self.logs_collection.create_index([("sessionId", pymongo.ASCENDING)])
            self.logs_collection.create_index([("timestamp", pymongo.DESCENDING)])

            # Create indexing for per-user calibration profiles
            self.calibration_collection.create_index([("userId", pymongo.ASCENDING)], unique=True)
            self.calibration_collection.create_index([("updatedAt", pymongo.DESCENDING)])

            # Create indexing for LIVESCAN 2.0 monitoring sessions and events
            self.monitoring_sessions_collection.create_index([("sessionId", pymongo.ASCENDING)], unique=True)
            self.monitoring_sessions_collection.create_index([("startTime", pymongo.DESCENDING)])
            self.monitoring_events_collection.create_index([("sessionId", pymongo.ASCENDING)])
            self.monitoring_events_collection.create_index([("timestamp", pymongo.DESCENDING)])
            
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

    # =========================================================================
    # LIVESCAN 2.0 Session Monitoring & Audit Logging Implementation
    # =========================================================================

    def start_monitoring_session(self, session_data: dict) -> str:
        """
        Registers a new examination/interview monitoring session.
        Stores session ID, candidate metadata, and initial start timestamp.
        """
        session_id = session_data.get("sessionId") or f"sess-{uuid.uuid4().hex[:10]}"
        session_data["sessionId"] = session_id
        if "startTime" not in session_data:
            session_data["startTime"] = datetime.now(timezone.utc).isoformat()
        session_data["status"] = "IN_PROGRESS"
        session_data["totalEvents"] = 0
        session_data["warningCount"] = 0
        session_data["additionalPersonEvents"] = 0
        session_data["backgroundSpeechEvents"] = 0
        session_data["mediaInterruptionEvents"] = 0

        if self.use_fallback:
            self._try_connect()

        if not self.use_fallback and self.monitoring_sessions_collection is not None:
            try:
                doc = dict(session_data)
                self.monitoring_sessions_collection.update_one(
                    {"sessionId": session_id},
                    {"$set": doc},
                    upsert=True
                )
                logger.info(f"Started monitoring session in MongoDB: {session_id}")
                return session_id
            except Exception as mongo_err:
                logger.error(f"Failed to save monitoring session in MongoDB: {mongo_err}")
                self.use_fallback = True

        # Fallback to local JSONL
        try:
            record = {"_id": f"fallback-sess-{uuid.uuid4()}", **session_data}
            with open(self.fallback_sessions_filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info(f"Started monitoring session in fallback file: {session_id}")
            return session_id
        except Exception as file_err:
            logger.error(f"Failed to write monitoring session to fallback file: {file_err}")
            return session_id

    def log_monitoring_event(self, event_data: dict) -> str:
        """
        Records a discrete monitoring event (e.g. ADDITIONAL_PERSON_DETECTED,
        POSSIBLE_BACKGROUND_SPEECH, CAMERA_INTERRUPTED, etc.) with timestamp and metadata.
        """
        session_id = event_data.get("sessionId")
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        event_data["eventId"] = event_id
        if "timestamp" not in event_data:
            event_data["timestamp"] = datetime.now(timezone.utc).isoformat()

        event_type = event_data.get("eventType", "UNKNOWN")
        severity = event_data.get("severity", "INFO")

        if self.use_fallback:
            self._try_connect()

        if not self.use_fallback and self.monitoring_events_collection is not None:
            try:
                doc = dict(event_data)
                self.monitoring_events_collection.insert_one(doc)
                
                # Increment session event counters
                inc_fields = {"totalEvents": 1}
                if severity == "WARNING":
                    inc_fields["warningCount"] = 1
                if event_type == "ADDITIONAL_PERSON_DETECTED":
                    inc_fields["additionalPersonEvents"] = 1
                elif event_type == "POSSIBLE_BACKGROUND_SPEECH":
                    inc_fields["backgroundSpeechEvents"] = 1
                elif "INTERRUPTED" in event_type:
                    inc_fields["mediaInterruptionEvents"] = 1

                self.monitoring_sessions_collection.update_one(
                    {"sessionId": session_id},
                    {"$inc": inc_fields}
                )
                return event_id
            except Exception as mongo_err:
                logger.error(f"Failed to log monitoring event to MongoDB: {mongo_err}")
                self.use_fallback = True

        # Fallback to local JSONL
        try:
            record = {"_id": f"fallback-{event_id}", **event_data}
            with open(self.fallback_events_filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info(f"Logged monitoring event to fallback file: {event_type} (Session: {session_id})")
            return event_id
        except Exception as file_err:
            logger.error(f"Failed to log monitoring event to fallback: {file_err}")
            return event_id

    def stop_monitoring_session(self, session_id: str, summary_data: Optional[dict] = None) -> bool:
        """
        Marks monitoring session as COMPLETED and computes comprehensive summary statistics.
        """
        end_time = datetime.now(timezone.utc).isoformat()
        update_fields = {
            "status": "COMPLETED",
            "endTime": end_time,
            **(summary_data or {})
        }

        if self.use_fallback:
            self._try_connect()

        if not self.use_fallback and self.monitoring_sessions_collection is not None:
            try:
                res = self.monitoring_sessions_collection.update_one(
                    {"sessionId": session_id},
                    {"$set": update_fields}
                )
                return res.modified_count > 0 or res.matched_count > 0
            except Exception as mongo_err:
                logger.error(f"Failed to complete session in MongoDB: {mongo_err}")
                self.use_fallback = True

        # Fallback JSONL update (append completion record)
        try:
            record = {
                "_id": f"complete-{session_id}-{uuid.uuid4().hex[:6]}",
                "sessionId": session_id,
                "_isUpdate": True,
                **update_fields
            }
            with open(self.fallback_sessions_filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except Exception as file_err:
            logger.error(f"Failed to complete session in fallback file: {file_err}")
            return False

    def get_session_events(self, session_id: str) -> list:
        """
        Retrieves all chronological monitoring events for a specific session.
        """
        if self.use_fallback:
            self._try_connect()

        if not self.use_fallback and self.monitoring_events_collection is not None:
            try:
                cursor = self.monitoring_events_collection.find(
                    {"sessionId": session_id}
                ).sort("timestamp", pymongo.ASCENDING)
                events = []
                for doc in cursor:
                    doc["_id"] = str(doc["_id"])
                    events.append(doc)
                return events
            except Exception as mongo_err:
                logger.error(f"Failed to fetch session events from MongoDB: {mongo_err}")
                self.use_fallback = True

        # Read from fallback file
        events = []
        if os.path.exists(self.fallback_events_filepath):
            try:
                with open(self.fallback_events_filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        if record.get("sessionId") == session_id:
                            events.append(record)
            except Exception as file_err:
                logger.error(f"Failed to read events from fallback file: {file_err}")
        return events

    def get_session_warning_count(self, session_id: str) -> int:
        """Counts confirmed WARNING severity events for a given session."""
        if not session_id:
            return 0
        events = self.get_session_events(session_id)
        return sum(1 for e in events if e.get("severity") == "WARNING")

    def is_monitoring_session_expired(self, session_id: str, max_warnings: int = 3) -> bool:
        """Determines if a monitoring session has exceeded the maximum integrity warning threshold."""
        if not session_id:
            return False
        return self.get_session_warning_count(session_id) >= max_warnings

    def get_session_report(self, session_id: str) -> Optional[dict]:
        """
        Generates a comprehensive audit report for an online interview/exam session.
        Integrates:
        - Session start, end, and duration
        - Total monitoring alerts & breakdown (additional person, background speech, interruptions)
        - Liveness verification decision & synchronization score
        - Chronological event timeline
        """
        if self.use_fallback:
            self._try_connect()

        session_doc = None
        if not self.use_fallback and self.monitoring_sessions_collection is not None:
            try:
                session_doc = self.monitoring_sessions_collection.find_one({"sessionId": session_id})
                if session_doc:
                    session_doc["_id"] = str(session_doc["_id"])
            except Exception as mongo_err:
                logger.error(f"Failed to fetch session from MongoDB: {mongo_err}")
                self.use_fallback = True

        if session_doc is None and os.path.exists(self.fallback_sessions_filepath):
            try:
                merged = {}
                with open(self.fallback_sessions_filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        rec = json.loads(line)
                        if rec.get("sessionId") == session_id:
                            merged.update(rec)
                if merged:
                    session_doc = merged
            except Exception as file_err:
                logger.error(f"Failed to read session from fallback: {file_err}")

        # If still None, construct minimum stub
        if not session_doc:
            session_doc = {
                "sessionId": session_id,
                "status": "UNKNOWN",
                "startTime": datetime.now(timezone.utc).isoformat()
            }

        events = self.get_session_events(session_id)

        # Count event types
        total_events = len(events)
        person_events = sum(1 for e in events if e.get("eventType") == "ADDITIONAL_PERSON_DETECTED")
        speech_events = sum(1 for e in events if e.get("eventType") == "POSSIBLE_BACKGROUND_SPEECH")
        interruption_events = sum(1 for e in events if "INTERRUPTED" in e.get("eventType", ""))

        # Calculate duration if timestamps exist
        duration_sec = 0.0
        start_iso = session_doc.get("startTime")
        end_iso = session_doc.get("endTime") or datetime.now(timezone.utc).isoformat()
        try:
            t0 = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
            duration_sec = max(0.0, (t1 - t0).total_seconds())
        except Exception:
            pass

        # Also retrieve liveness check result for this session if available
        liveness_record = None
        if not self.use_fallback and self.logs_collection is not None:
            try:
                liveness_doc = self.logs_collection.find_one({"sessionId": session_id})
                if liveness_doc:
                    liveness_doc["_id"] = str(liveness_doc["_id"])
                    liveness_record = liveness_doc
            except Exception:
                pass

        if liveness_record is None and os.path.exists(self.fallback_filepath):
            try:
                with open(self.fallback_filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        rec = json.loads(line)
                        if rec.get("sessionId") == session_id:
                            liveness_record = rec
                            break
            except Exception:
                pass

        liveness_decision = "NOT_CONDUCTED"
        sync_score = None
        challenge_phrase = None
        detected_offset_ms = None
        whisper_data = None
        if liveness_record:
            liveness_decision = liveness_record.get("livenessResult", "NOT_CONDUCTED")
            sync_score = liveness_record.get("synchronizationScore")
            challenge_phrase = liveness_record.get("challengePhrase")
            detected_offset_ms = liveness_record.get("detectedTimeOffsetMs")
            whisper_data = liveness_record.get("whisperVerification")

        warning_count = self.get_session_warning_count(session_id)
        is_expired = (warning_count >= settings.MAX_MONITORING_WARNINGS or "TERMINATED" in session_doc.get("status", ""))
        stop_reason = session_doc.get("stopReason") or ("Exceeded maximum integrity warnings" if is_expired else "Normal completion")

        report = {
            "sessionId": session_id,
            "status": session_doc.get("status", "TERMINATED_EXCESSIVE_WARNINGS" if is_expired else "COMPLETED"),
            "stopReason": stop_reason,
            "candidateId": session_doc.get("candidateId", "candidate"),
            "interviewRole": session_doc.get("examId", "Candidate"),
            "language": session_doc.get("language", "en"),
            "startTime": start_iso,
            "endTime": end_iso,
            "durationSeconds": round(duration_sec, 1),
            "warningCount": warning_count,
            "maxWarnings": settings.MAX_MONITORING_WARNINGS,
            "isSessionExpired": is_expired,
            "summary": {
                "totalEvents": total_events,
                "totalWarnings": warning_count,
                "maxWarnings": settings.MAX_MONITORING_WARNINGS,
                "isSessionExpired": is_expired,
                "additionalPersonEvents": person_events,
                "possibleBackgroundSpeechEvents": speech_events,
                "mediaInterruptionEvents": interruption_events,
                "livenessResult": liveness_decision,
                "synchronizationScore": sync_score,
                "detectedTimeOffsetMs": detected_offset_ms,
                "challengePhrase": challenge_phrase,
                "whisperVerification": whisper_data,
                "assessmentOutcome": "FLAGGED_FOR_REVIEW" if (person_events > 0 or speech_events > 0 or liveness_decision == "SPOOF" or is_expired) else "CLEAR"
            },
            "timeline": events,
            "disclaimer": (
                "LIVESCAN 2.0 generates advisory monitoring telemetry for remote interviews and examinations. "
                "Alerts represent events flagged for human proctor review and do not constitute automated candidate disqualification."
            )
        }
        return report

    def get_monitoring_sessions(self, limit: int = 25) -> list:
        """Returns recent monitoring sessions for history view."""
        if self.use_fallback:
            self._try_connect()

        if not self.use_fallback and self.monitoring_sessions_collection is not None:
            try:
                cursor = self.monitoring_sessions_collection.find().sort("startTime", pymongo.DESCENDING).limit(limit)
                sessions = []
                for doc in cursor:
                    doc["_id"] = str(doc["_id"])
                    sessions.append(doc)
                return sessions
            except Exception as mongo_err:
                logger.error(f"Failed to fetch sessions from MongoDB: {mongo_err}")
                self.use_fallback = True

        # Fallback reading
        sessions = {}
        if os.path.exists(self.fallback_sessions_filepath):
            try:
                with open(self.fallback_sessions_filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        rec = json.loads(line)
                        sid = rec.get("sessionId")
                        if sid:
                            if sid not in sessions:
                                sessions[sid] = {}
                            sessions[sid].update(rec)
            except Exception:
                pass

        res = list(sessions.values())
        res.sort(key=lambda s: s.get("startTime", ""), reverse=True)
        return res[:limit]

# Singleton instance
mongodb_service = MongoDBService()


