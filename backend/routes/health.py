from fastapi import APIRouter

router = APIRouter()

@router.get("/health", tags=["System Status"])
async def health_check():
    """
    Health check endpoint to verify system status, loaded services, 
    and general API connectivity.
    """
    return {
        "status": "healthy",
        "service": "Lip-Voice Liveness Detection System API",
        "version": "1.0.0",
        "subsystems": {
            "face_mesh_service": "initialized (placeholder)",
            "lip_tracker_service": "initialized (placeholder)",
            "whisper_audio_service": "initialized (placeholder)",
            "sync_analysis_service": "initialized (placeholder)",
            "mongodb_storage_service": "initialized (placeholder)"
        }
    }
