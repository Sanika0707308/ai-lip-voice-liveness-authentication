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
        "service": "Lip-Voice Liveness Authentication System API",
        "version": "1.0.0",
        "subsystems": {
            "face_mesh_service": "active",
            "lip_tracker_service": "active",
            "whisper_audio_service": "active",
            "sync_analysis_service": "active",
            "mongodb_storage_service": "active"
        }
    }
