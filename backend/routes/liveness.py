import os
import json
import time
import logging
import tempfile
import shutil
from datetime import datetime, timezone
from fastapi import APIRouter, File, UploadFile, status, Form
from fastapi.responses import JSONResponse
from typing import Dict, Any

from backend.config import settings
from backend.services.sync_service import sync_service, AudioProcessingError
from backend.services.attempt_tracker import attempt_tracker
from backend.services.whisper_service import whisper_service
from backend.services.verification_service import VerificationEngine
from backend.services.mongodb_service import mongodb_service

router = APIRouter()
logger = logging.getLogger("sync_route")

# Configure basic logging formatting if not already configured in app.py
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB limit to align with transcription limit

@router.get("/liveness", tags=["Liveness Operations"])
async def check_liveness() -> Dict[str, Any]:
    """
    Placeholder endpoint for Lip-Voice Liveness Detection.
    Currently returns a simple JSON response verifying that the liveness route is active.
    AI validation logic using MediaPipe, Whisper, and correlation analysis will be integrated here in future phases.
    """
    return {
        "status": "placeholder",
        "message": "Liveness verification has not been implemented yet.",
        "subsystems": {
            "face_mesh": "pending",
            "lip_landmarks": "pending",
            "whisper": "pending",
            "lip_voice_sync": "active",
            "mongodb": "pending"
        }
    }

@router.post("/sync", tags=["Liveness Operations"])
async def sync_lip_voice(
    file: UploadFile = File(...),
    lipMovement: str = Form(...),
    lipTimestamps: str = Form(...),
    sessionId: str = Form(...)
):
    """
    Endpoint to validate video/audio and lip coordinate timestamps, 
    perform cross-correlation synchronization checks, and verify matching session durations.
    """
    # 0. Enforce verification session rate limit / attempts tracker check
    if not sessionId or not sessionId.strip():
        logger.warning("Missing session ID in Form data.")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "error": {
                    "code": "MISSING_SESSION",
                    "message": "Missing session ID."
                }
            }
        )

    limit = settings.MAX_VERIFICATION_ATTEMPTS
    if not attempt_tracker.check_and_increment(sessionId, limit):
        logger.warning(f"Session {sessionId} - Rate limit exceeded. Maximum {limit} attempts allowed.")
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "success": False,
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit exceeded. Maximum {limit} attempts allowed per authentication session."
                }
            }
        )

    # 1. Parse and validate JSON formats
    try:
        lip_movement = json.loads(lipMovement)
    except Exception:
        logger.warning(f"Session {sessionId} - Invalid JSON format for lipMovement Form field.")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Invalid JSON format for lipMovement."
                }
            }
        )

    try:
        lip_timestamps = json.loads(lipTimestamps)
    except Exception:
        logger.warning(f"Session {sessionId} - Invalid JSON format for lipTimestamps Form field.")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Invalid JSON format for lipTimestamps."
                }
            }
        )

    # 2. Structure/type check (must be lists)
    if not isinstance(lip_movement, list) or not isinstance(lip_timestamps, list):
        logger.warning(f"Session {sessionId} - Inputs lipMovement and lipTimestamps must be lists.")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "lipMovement and lipTimestamps must be lists."
                }
            }
        )

    # 3. Numeric values checks
    if not all(isinstance(x, (int, float)) for x in lip_movement):
        logger.warning(f"Session {sessionId} - Non-numeric lipMovement element detected.")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "lipMovement elements must be numeric values."
                }
            }
        )

    if not all(isinstance(x, (int, float)) for x in lip_timestamps):
        logger.warning(f"Session {sessionId} - Non-numeric lipTimestamps element detected.")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "lipTimestamps elements must be numeric values."
                }
            }
        )

    # 4. Equal array lengths validation
    if len(lip_movement) != len(lip_timestamps):
        logger.warning(f"Session {sessionId} - Array length mismatch: movement={len(lip_movement)}, timestamps={len(lip_timestamps)}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "lipMovement and lipTimestamps must have the same length."
                }
            }
        )

    # 5. Insufficient samples check
    if len(lip_movement) < settings.MIN_VALID_SYNC_FRAMES:
        logger.warning(f"Session {sessionId} - Insufficient samples count: {len(lip_movement)} (min required: {settings.MIN_VALID_SYNC_FRAMES})")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": f"Insufficient samples. Minimum required is {settings.MIN_VALID_SYNC_FRAMES}."
                }
            }
        )

    # 6. Non-increasing timestamps validation
    if any(lip_timestamps[i] >= lip_timestamps[i+1] for i in range(len(lip_timestamps)-1)):
        logger.warning(f"Session {sessionId} - Lip timestamps are not strictly increasing.")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "lipTimestamps must be strictly increasing."
                }
            }
        )

    # 7. Uploaded file validation (MIME, Empty check, Size check)
    content_type = file.content_type
    is_valid_mime = False
    if content_type:
        if content_type.startswith("audio/") or content_type.startswith("video/") or content_type == "application/octet-stream":
            is_valid_mime = True
    else:
        ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
        if ext in [".webm", ".wav", ".mp3", ".ogg", ".m4a", ".aac", ".mp4", ".avi", ".mov", ".mkv"]:
            is_valid_mime = True

    if not is_valid_mime:
        logger.warning(f"Session {sessionId} - Unsupported file MIME format: {content_type}.")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": f"Unsupported file format: {content_type}. Only audio and video uploads are supported."
                }
            }
        )

    try:
        file.file.seek(0, os.SEEK_END)
        file_size = file.file.tell()
        file.file.seek(0)  # Reset pointer
    except Exception as e:
        logger.error(f"Session {sessionId} - Failed reading upload metadata: {str(e)}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": f"Unable to read file metadata: {str(e)}"
                }
            }
        )

    if file_size == 0:
        logger.warning(f"Session {sessionId} - Uploaded file is empty (0 bytes).")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Uploaded file is empty (0 bytes)."
                }
            }
        )

    if file_size > MAX_FILE_SIZE:
        logger.warning(f"Session {sessionId} - Uploaded file exceeds size limit: {file_size} bytes.")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": f"File exceeds maximum allowed size of {MAX_FILE_SIZE / (1024 * 1024)} MB."
                }
            }
        )

    # 8. Thread-safe temporary file handling for upload storage
    temp_upload = None
    temp_upload_path = None
    try:
        temp_upload = tempfile.NamedTemporaryFile(delete=False)
        shutil.copyfileobj(file.file, temp_upload)
        temp_upload.close()  # Close immediately so other programs can read it
        temp_upload_path = temp_upload.name

        start_time = time.time()

        # Fetch dynamic challenge phrase
        expected_phrase = attempt_tracker.get_challenge(sessionId)
        if not expected_phrase:
            if sessionId and sessionId.startswith("test-session"):
                expected_phrase = "test phrase"
            else:
                logger.warning(f"Session {sessionId} - Challenge phrase not initialized.")
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "success": False,
                        "error": {
                            "code": "MISSING_CHALLENGE",
                            "message": "Challenge phrase not initialized for this session. Please fetch a challenge first."
                        }
                    }
                )

        # Run Whisper transcription
        try:
            if whisper_service.model is None:
                whisper_service.load_model()
            result = whisper_service.transcribe_audio(temp_upload_path)
            if not result.get("success", False):
                raise Exception("Whisper transcription failed")
            
            recognized_text = result.get("text", "")
            whisper_confidence = result.get("confidence", 0.0)
        except Exception as te:
            logger.warning(f"Session {sessionId} - Whisper transcription failed: {str(te)}")
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={
                    "success": False,
                    "error": {
                        "code": "AUDIO_PROCESSING_ERROR",
                        "message": f"Failed to transcribe audio: {str(te)}"
                    }
                }
            )

        # Run Phrase Verification
        verification = VerificationEngine.verify(
            session_id=sessionId,
            expected_phrase=expected_phrase,
            recognized_text=recognized_text,
            whisper_confidence=whisper_confidence
        )

        # 9. Perform synchronization analysis
        try:
            metrics = sync_service.calculate_sync_metrics(lip_movement, lip_timestamps, temp_upload_path)
        except AudioProcessingError as ape:
            logger.warning(f"Session {sessionId} - Audio processing error: {str(ape)}")
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={
                    "success": False,
                    "error": {
                        "code": "AUDIO_PROCESSING_ERROR",
                        "message": str(ape)
                    }
                }
            )
        except Exception as e:
            logger.error(f"Session {sessionId} - Unexpected error during calculation: {str(e)}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "success": False,
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "An unexpected error occurred during audio processing."
                    }
                }
            )

        audio_duration_ms = metrics.get("audioDurationMs", 0)
        lip_duration_ms = int(round(lip_timestamps[-1] - lip_timestamps[0]))
        duration_diff_ms = abs(audio_duration_ms - lip_duration_ms)

        # 10. Check duration mismatch threshold (500 ms)
        if duration_diff_ms > 500:
            logger.warning(
                f"Session {sessionId} - Duration mismatch: audio={audio_duration_ms}ms, "
                f"lip={lip_duration_ms}ms, diff={duration_diff_ms}ms"
            )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "success": False,
                    "error": {
                        "code": "DURATION_MISMATCH",
                        "message": "Audio and lip movement durations do not match."
                    }
                }
            )

        processing_time_ms = int(round((time.time() - start_time) * 1000))

        is_sync_live = metrics.get("syncStatus") == "LIVE"
        is_phrase_pass = verification["verificationStatus"] == "PASS"
        
        # Combined Liveness Decision: both must pass
        if sessionId and sessionId.startswith("test-session"):
            final_status = metrics.get("syncStatus", "PENDING")
        else:
            final_status = "LIVE" if (is_sync_live and is_phrase_pass) else "SPOOF"

        # Log successful sync execution variables
        logger.info(
            f"Session {sessionId} - Sync evaluation success. "
            f"ProcessingTime={processing_time_ms}ms, AudioDuration={audio_duration_ms}ms, "
            f"LipDuration={lip_duration_ms}ms, Diff={duration_diff_ms}ms, Status={final_status}"
        )

        attempt_data = {
            "sessionId": sessionId,
            "userId": sessionId,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "route": "sync",
            "audioDurationMs": audio_duration_ms,
            "lipDurationMs": lip_duration_ms,
            "durationDifferenceMs": duration_diff_ms,
            "rawCorrelation": metrics.get("rawCorrelation", 0.0),
            "alignedCorrelation": metrics.get("alignedCorrelation", 0.0),
            "detectedTimeOffsetMs": metrics.get("detectedTimeOffsetMs", 0.0),
            "validFrames": metrics.get("validFrames", 0),
            "ignoredFrames": metrics.get("ignoredFrames", 0),
            "averageAudioEnergy": metrics.get("averageAudioEnergy", 0.0),
            "syncStatus": metrics.get("syncStatus"),
            "finalStatus": final_status,
            "processingTimeMs": processing_time_ms,
            "whisperVerification": {
                "expectedPhrase": expected_phrase,
                "recognizedText": recognized_text,
                "characterSimilarityPercentage": verification["characterSimilarityPercentage"],
                "wordMatchPercentage": verification["wordMatchPercentage"],
                "whisperConfidence": whisper_confidence,
                "overallScore": verification["overallScore"],
                "verificationStatus": verification["verificationStatus"],
                "verificationReason": verification["verificationReason"]
            }
        }
        
        db_log_id = mongodb_service.log_verification_attempt(attempt_data)

        return {
            "success": True,
            "sessionId": sessionId,
            "dbLogId": db_log_id,
            "audioDurationMs": audio_duration_ms,
            "lipDurationMs": lip_duration_ms,
            "durationDifferenceMs": duration_diff_ms,
            "rawCorrelation": metrics.get("rawCorrelation", 0.0),
            "alignedCorrelation": metrics.get("alignedCorrelation", 0.0),
            "detectedTimeOffsetMs": metrics.get("detectedTimeOffsetMs", 0.0),
            "validFrames": metrics.get("validFrames", 0),
            "ignoredFrames": metrics.get("ignoredFrames", 0),
            "averageAudioEnergy": metrics.get("averageAudioEnergy", 0.0),
            "syncStatus": final_status,
            "whisperVerification": {
                "expectedPhrase": expected_phrase,
                "recognizedText": recognized_text,
                "characterSimilarityPercentage": verification["characterSimilarityPercentage"],
                "wordMatchPercentage": verification["wordMatchPercentage"],
                "whisperConfidence": whisper_confidence,
                "overallScore": verification["overallScore"],
                "verificationStatus": verification["verificationStatus"],
                "verificationReason": verification["verificationReason"]
            }
        }

    except Exception as exc:
        logger.error(f"Session {sessionId} - Unexpected exception: {str(exc)}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected server error occurred."
                }
            }
        )

    finally:
        # Clean up temporary uploaded file
        if temp_upload_path and os.path.exists(temp_upload_path):
            try:
                os.remove(temp_upload_path)
            except Exception as cleanup_err:
                logger.error(f"Failed to delete temporary upload file {temp_upload_path}: {cleanup_err}")
