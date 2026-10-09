import os

class Settings:
    # API Configurations
    PROJECT_NAME: str = "Lip-Voice Liveness Detection System"
    API_V1_STR: str = "/api"
    DEBUG: bool = os.getenv("DEBUG", "true").lower() in ("true", "1", "t", "yes")
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "tiny")
    WHISPER_MULTILINGUAL_MODEL: str = os.getenv("WHISPER_MULTILINGUAL_MODEL", "small")
    
    # Server configuration
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    
    # Biometric Assessment Thresholds
    LIP_CORRELATION_THRESHOLD: float = float(os.getenv("LIP_CORRELATION_THRESHOLD", "0.65"))
    MIN_FACE_CONFIDENCE: float = float(os.getenv("MIN_FACE_CONFIDENCE", "0.5"))
    MIN_LIP_VARIATION: float = float(os.getenv("MIN_LIP_VARIATION", "0.001"))
    
    # Synchronization parameters
    SYNC_WINDOW_MS: int = int(os.getenv("SYNC_WINDOW_MS", "300"))
    SYNC_MAX_LAG_MS: int = int(os.getenv("SYNC_MAX_LAG_MS", "500"))            # Search lag up to 500ms
    MAX_SYNC_TIME_DIFF_MS: float = float(os.getenv("MAX_SYNC_TIME_DIFF_MS", "500.0"))  # Reject as SPOOF if sync diff > 500ms
    SYNC_SMOOTHING_WINDOW: int = int(os.getenv("SYNC_SMOOTHING_WINDOW", "5"))
    MIN_AUDIO_ENERGY: float = float(os.getenv("MIN_AUDIO_ENERGY", "0.02"))
    LIVE_SYNC_THRESHOLD: float = float(os.getenv("LIVE_SYNC_THRESHOLD", "0.45"))
    MIN_VALID_SYNC_FRAMES: int = int(os.getenv("MIN_VALID_SYNC_FRAMES", "30"))

    # Adaptive Per-User Calibration Configurations
    CALIBRATION_REQUIRED_SAMPLES: int = int(os.getenv("CALIBRATION_REQUIRED_SAMPLES", "3"))
    CALIBRATION_MIN_SAMPLES: int = int(os.getenv("CALIBRATION_MIN_SAMPLES", "3"))
    CALIBRATION_MAX_SAMPLES: int = int(os.getenv("CALIBRATION_MAX_SAMPLES", "5"))
    CALIBRATION_STD_MULTIPLIER: float = float(os.getenv("CALIBRATION_STD_MULTIPLIER", "1.0"))
    CALIBRATION_DEFAULT_MARGIN: float = float(os.getenv("CALIBRATION_DEFAULT_MARGIN", "0.08"))
    CALIBRATION_MAX_THRESHOLD: float = float(os.getenv("CALIBRATION_MAX_THRESHOLD", "0.85"))
    
    # Challenge Phrase Verification Configurations
    SUPPORTED_LANGUAGES: tuple = ("en", "hi", "mr")
    DEFAULT_LANGUAGE: str = "en"
    LANGUAGE_NAMES: dict = {
        "en": "English",
        "hi": "Hindi",
        "mr": "Marathi"
    }
    MAX_VERIFICATION_ATTEMPTS: int = int(os.getenv("MAX_VERIFICATION_ATTEMPTS", "5"))
    AUDIO_MIN_DURATION_SEC: float = float(os.getenv("AUDIO_MIN_DURATION_SEC", "2.0"))
    AUDIO_MAX_DURATION_SEC: float = float(os.getenv("AUDIO_MAX_DURATION_SEC", "6.0"))
    TARGET_RECORDING_DURATION_SEC: float = 5.0
    WHISPER_CONFIDENCE_THRESHOLD: float = float(os.getenv("WHISPER_CONFIDENCE_THRESHOLD", "0.60"))
    CHALLENGE_MATCH_THRESHOLD: float = float(os.getenv("CHALLENGE_MATCH_THRESHOLD", "70.0"))

    # Score weights
    WEIGHT_CHARACTER_SIMILARITY: float = 0.40
    WEIGHT_WORD_SIMILARITY: float = 0.40
    WEIGHT_WHISPER_CONFIDENCE: float = 0.20

    # Database Configurations (MongoDB)
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "liveness_db")

settings = Settings()


