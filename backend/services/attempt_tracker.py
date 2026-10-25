import time
import uuid
import threading
from typing import Optional, Dict, Any, Tuple

class BaseAttemptTracker:
    """
    Abstract interface defining how attempt tracking must be structured.
    This enables seamless database (e.g. MongoDB) migration without changing api routing.
    """
    def check_and_increment(self, session_id: str, limit: int) -> bool:
        raise NotImplementedError()

    def reset_session(self, session_id: str) -> None:
        raise NotImplementedError()

    def set_challenge(
        self,
        session_id: str,
        challenge: str,
        language: str = "en",
        ttl_seconds: int = 60,
        attempt_id: Optional[str] = None
    ) -> str:
        raise NotImplementedError()

    def get_challenge(self, session_id: str) -> Optional[str]:
        raise NotImplementedError()

    def set_language(self, session_id: str, language: str) -> None:
        raise NotImplementedError()

    def get_language(self, session_id: str) -> str:
        raise NotImplementedError()

    def validate_attempt(self, session_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        raise NotImplementedError()

    def consume_attempt(self, session_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        raise NotImplementedError()


class InMemoryAttemptTracker(BaseAttemptTracker):
    """
    Thread-safe in-memory implementation of attempt tracking with:
    - Unique attempt IDs per challenge generation
    - Short expiration times (TTL)
    - One-time verification consumption to prevent replay attacks
    - Authoritative server-stored expected phrase enforcement
    """
    def __init__(self):
        # Maps session_id -> {
        #   "attempts": count,
        #   "attempt_id": str,
        #   "challenge": phrase,
        #   "language": lang,
        #   "created_at": float,
        #   "expires_at": float,
        #   "status": "ACTIVE" | "USED" | "EXPIRED"
        # }
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def check_and_increment(self, session_id: str, limit: int) -> bool:
        if not session_id:
            return False
            
        with self._lock:
            session = self._sessions.setdefault(session_id, {
                "attempts": 0,
                "attempt_id": f"att-{uuid.uuid4().hex[:10]}",
                "challenge": None,
                "language": "en",
                "created_at": time.time(),
                "expires_at": time.time() + 60,
                "status": "ACTIVE"
            })
            current_attempts = session["attempts"]
            if current_attempts >= limit:
                return False
            session["attempts"] = current_attempts + 1
            return True

    def get_attempts(self, session_id: str) -> int:
        if not session_id:
            return 0
        with self._lock:
            return self._sessions.get(session_id, {}).get("attempts", 0)

    def get_remaining_attempts(self, session_id: str, limit: int = 3) -> int:
        if not session_id:
            return limit
        with self._lock:
            attempts = self._sessions.get(session_id, {}).get("attempts", 0)
            return max(0, limit - attempts)

    def is_session_expired(self, session_id: str, limit: int = 3) -> bool:
        if not session_id:
            return False
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False
            if session.get("verification_status") == "LIVE":
                return False
            return session.get("attempts", 0) >= limit

    def record_result(self, session_id: str, result_status: str) -> None:
        if not session_id:
            return
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["verification_status"] = result_status

    def get_verification_status(self, session_id: str) -> Optional[str]:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id, {}).get("verification_status")

    def reset_session(self, session_id: str) -> None:
        if not session_id:
            return
            
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]

    def set_challenge(
        self,
        session_id: str,
        challenge: str,
        language: str = "en",
        ttl_seconds: int = 60,
        attempt_id: Optional[str] = None
    ) -> str:
        if not session_id:
            return ""
        lang_code = (language or "en").strip().lower()
        if lang_code not in ("en", "hi", "mr"):
            lang_code = "en"
            
        att_id = attempt_id or f"att-{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._lock:
            session = self._sessions.setdefault(session_id, {
                "attempts": 0,
                "attempt_id": att_id,
                "challenge": None,
                "language": "en",
                "created_at": now,
                "expires_at": now + ttl_seconds,
                "status": "ACTIVE"
            })
            session["attempt_id"] = att_id
            session["challenge"] = challenge
            session["language"] = lang_code
            session["created_at"] = now
            session["expires_at"] = now + ttl_seconds
            session["status"] = "ACTIVE"
            return att_id

    def get_challenge(self, session_id: str) -> Optional[str]:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id, {}).get("challenge")

    def get_attempt_id(self, session_id: str) -> Optional[str]:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id, {}).get("attempt_id")

    def get_challenge_record(self, session_id: str) -> Dict[str, Any]:
        if not session_id:
            return {}
        with self._lock:
            rec = self._sessions.get(session_id)
            return dict(rec) if rec else {}

    def set_language(self, session_id: str, language: str) -> None:
        if not session_id:
            return
        lang_code = (language or "en").strip().lower()
        if lang_code not in ("en", "hi", "mr"):
            lang_code = "en"
        with self._lock:
            session = self._sessions.setdefault(session_id, {
                "attempts": 0,
                "attempt_id": f"att-{uuid.uuid4().hex[:10]}",
                "challenge": None,
                "language": "en",
                "created_at": time.time(),
                "expires_at": time.time() + 60,
                "status": "ACTIVE"
            })
            session["language"] = lang_code

    def get_language(self, session_id: str) -> str:
        if not session_id:
            return "en"
        with self._lock:
            return self._sessions.get(session_id, {}).get("language") or "en"

    def validate_attempt(self, session_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates attempt eligibility:
        - Must have a server-assigned challenge
        - Must not be expired (now <= expires_at)
        - Must not have been already consumed (status != 'USED')
        """
        if not session_id:
            return False, "MISSING_SESSION", {}
            
        with self._lock:
            record = self._sessions.get(session_id)
            if not record or not record.get("challenge"):
                return False, "MISSING_CHALLENGE", {}

            now = time.time()
            if record.get("expires_at") and now > record["expires_at"]:
                record["status"] = "EXPIRED"
                return False, "CHALLENGE_EXPIRED", dict(record)

            if record.get("status") == "USED":
                return False, "CHALLENGE_ALREADY_USED", dict(record)

            return True, "VALID", dict(record)

    def consume_attempt(self, session_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Consumes the challenge attempt once verification starts/finishes.
        Prevents replay of the same challenge.
        """
        if not session_id:
            return False, "MISSING_SESSION", {}
            
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return False, "MISSING_SESSION", {}
            record["status"] = "USED"
            record["used_at"] = time.time()
            return True, "CONSUMED", dict(record)

# Global singleton instance
attempt_tracker = InMemoryAttemptTracker()

