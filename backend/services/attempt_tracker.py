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

    def set_challenge(self, session_id: str, challenge: str, language: str = "en") -> None:
        """
        Set/cache the dynamic challenge phrase and language for the session.
        """
        raise NotImplementedError()

    def get_challenge(self, session_id: str) -> str:
        """
        Retrieve the dynamic challenge phrase for the session.
        """
        raise NotImplementedError()

    def set_language(self, session_id: str, language: str) -> None:
        """
        Set/cache the language code ('en', 'hi', 'mr') for the session.
        """
        raise NotImplementedError()

    def get_language(self, session_id: str) -> str:
        """
        Retrieve the language code for the session (defaults to 'en').
        """
        raise NotImplementedError()


class InMemoryAttemptTracker(BaseAttemptTracker):
    """
    Thread-safe in-memory implementation of attempt tracking.
    """
    def __init__(self):
        self._sessions = {} # Maps session_id -> {"attempts": count, "challenge": phrase, "language": lang}
        self._lock = threading.Lock()

    def check_and_increment(self, session_id: str, limit: int) -> bool:
        if not session_id:
            return False
            
        with self._lock:
            session = self._sessions.setdefault(session_id, {"attempts": 0, "challenge": None, "language": "en"})
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

    def set_challenge(self, session_id: str, challenge: str, language: str = "en") -> None:
        if not session_id:
            return
        lang_code = (language or "en").strip().lower()
        if lang_code not in ("en", "hi", "mr"):
            lang_code = "en"
        with self._lock:
            session = self._sessions.setdefault(session_id, {"attempts": 0, "challenge": None, "language": "en"})
            session["challenge"] = challenge
            session["language"] = lang_code

    def get_challenge(self, session_id: str) -> str:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id, {}).get("challenge")

    def set_language(self, session_id: str, language: str) -> None:
        if not session_id:
            return
        lang_code = (language or "en").strip().lower()
        if lang_code not in ("en", "hi", "mr"):
            lang_code = "en"
        with self._lock:
            session = self._sessions.setdefault(session_id, {"attempts": 0, "challenge": None, "language": "en"})
            session["language"] = lang_code

    def get_language(self, session_id: str) -> str:
        if not session_id:
            return "en"
        with self._lock:
            return self._sessions.get(session_id, {}).get("language") or "en"

# Global singleton instance
attempt_tracker = InMemoryAttemptTracker()
