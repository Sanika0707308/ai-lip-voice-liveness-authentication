import threading

class BaseAttemptTracker:
    """
    Abstract interface defining how attempt tracking must be structured.
    This enables seamless database (e.g. MongoDB) migration without changing api routing.
    """
    def check_and_increment(self, session_id: str, limit: int) -> bool:
        """
        Check if current attempts for a session are below the limit.
        If yes, increment and return True. If limit is exceeded, return False.
        """
        raise NotImplementedError()

    def reset_session(self, session_id: str) -> None:
        """
        Clear/reset the session verification attempts counter.
        """
        raise NotImplementedError()

    def set_challenge(self, session_id: str, challenge: str) -> None:
        """
        Set/cache the dynamic challenge phrase for the session.
        """
        raise NotImplementedError()

    def get_challenge(self, session_id: str) -> str:
        """
        Retrieve the dynamic challenge phrase for the session.
        """
        raise NotImplementedError()


class InMemoryAttemptTracker(BaseAttemptTracker):
    """
    Thread-safe in-memory implementation of attempt tracking.
    """
    def __init__(self):
        self._sessions = {} # Maps session_id -> {"attempts": count, "challenge": phrase}
        self._lock = threading.Lock()

    def check_and_increment(self, session_id: str, limit: int) -> bool:
        if not session_id:
            return False
            
        with self._lock:
            session = self._sessions.setdefault(session_id, {"attempts": 0, "challenge": None})
            current_attempts = session["attempts"]
            if current_attempts >= limit:
                return False
            session["attempts"] = current_attempts + 1
            return True

    def reset_session(self, session_id: str) -> None:
        if not session_id:
            return
            
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]

    def set_challenge(self, session_id: str, challenge: str) -> None:
        if not session_id:
            return
        with self._lock:
            session = self._sessions.setdefault(session_id, {"attempts": 0, "challenge": None})
            session["challenge"] = challenge

    def get_challenge(self, session_id: str) -> str:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id, {}).get("challenge")

# Global singleton instance
attempt_tracker = InMemoryAttemptTracker()
