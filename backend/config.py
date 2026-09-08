import os

class Settings:
    # API Configurations
    PROJECT_NAME: str = "Lip-Voice Liveness Detection System"
    API_V1_STR: str = "/api"
    DEBUG: bool = True
    WHISPER_MODEL: str = "tiny"
    
    # Server configuration
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    
    # Biometric Assessment Thresholds (For future CV & Audio processing tuning)
    LIP_CORRELATION_THRESHOLD: float = 0.65  # Target threshold for audio-lip motion alignment
    MIN_FACE_CONFIDENCE: float = 0.5         # MediaPipe minimum tracking confidence
    MIN_LIP_VARIATION: float = 0.05          # Threshold to verify lip is actually moving
    
    # Synchronization parameters
    SYNC_WINDOW_MS: int = 300
    SYNC_MAX_LAG_MS: int = 300
    SYNC_SMOOTHING_WINDOW: int = 5
    MIN_AUDIO_ENERGY: float = 0.02
    LIVE_SYNC_THRESHOLD: float = 0.45
    MIN_VALID_SYNC_FRAMES: int = 30

    
    # Challenge Phrase Verification Configurations
    MAX_VERIFICATION_ATTEMPTS: int = 3
    AUDIO_MIN_DURATION_SEC: float = 2.0
    AUDIO_MAX_DURATION_SEC: float = 6.0
    WHISPER_CONFIDENCE_THRESHOLD: float = 0.60

    # Score weights
    WEIGHT_CHARACTER_SIMILARITY: float = 0.40
    WEIGHT_WORD_SIMILARITY: float = 0.40
    WEIGHT_WHISPER_CONFIDENCE: float = 0.20

    # Database Configurations (MongoDB)
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    DATABASE_NAME: str = "liveness_db"

settings = Settings()

