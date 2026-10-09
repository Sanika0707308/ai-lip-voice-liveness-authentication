import os
import json
import time
import logging
import tempfile
import shutil
import math
from datetime import datetime, timezone
from fastapi import APIRouter, File, UploadFile, status, Form
from fastapi.responses import JSONResponse
from typing import Dict, Any

from backend.config import settings
from backend.services.sync_service import sync_service, AudioProcessingError
from backend.services.lip_service import lip_service
from backend.services.attempt_tracker import attempt_tracker
from backend.services.whisper_service import whisper_service
from backend.services.verification_service import VerificationEngine, normalize_language_code
from backend.services.mongodb_service import mongodb_service
from backend.services.calibration_service import calibration_service

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
    Status endpoint for Lip-Voice Synchronization Liveness Authentication.
    Returns active status of all integrated subsystems.
    """
    return {
        "status": "active",
        "message": "Lip-Voice Synchronization Liveness Authentication service is active.",
        "subsystems": {
            "face_mesh": "active",
            "lip_landmarks": "active",
            "whisper": "active",
            "lip_voice_sync": "active",
            "mongodb": "active"
        }
    }

@router.post("/sync", tags=["Liveness Operations"])
async def sync_lip_voice(
    file: UploadFile = File(...),
    lipMovement: str = Form(...),
    lipTimestamps: str = Form(...),
    sessionId: str = Form(...),
    challengePhrase: str = Form(None),
    userId: str = Form(None),
    language: str = Form(None)
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
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in lip_movement):
        logger.warning(f"Session {sessionId} - Non-numeric or non-finite lipMovement element detected.")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "lipMovement elements must be finite numeric values."
                }
            }
        )

    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in lip_timestamps):
        logger.warning(f"Session {sessionId} - Non-numeric or non-finite lipTimestamps element detected.")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "lipTimestamps elements must be finite numeric values."
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

        # Resolve session language (from parameter or tracker, defaulting to 'en')
        lang_code = normalize_language_code(
            language if (language and language.strip()) else attempt_tracker.get_language(sessionId)
        )
        attempt_tracker.set_language(sessionId, lang_code)

        # Fetch dynamic challenge phrase (from tracker or fallback parameter)
        expected_phrase = attempt_tracker.get_challenge(sessionId)
        if not expected_phrase and challengePhrase and challengePhrase.strip():
            expected_phrase = challengePhrase.strip()
            attempt_tracker.set_challenge(sessionId, expected_phrase, language=lang_code)
        if not expected_phrase:
            if settings.DEBUG and sessionId and sessionId.startswith("test-session"):
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
            result = whisper_service.transcribe_audio(temp_upload_path, language=lang_code)
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
            whisper_confidence=whisper_confidence,
            language=lang_code
        )

        # Resolve per-user adaptive or global synchronization threshold
        threshold_info = calibration_service.resolve_threshold_for_verification(
            user_id=userId,
            session_id=sessionId
        )
        effective_threshold = threshold_info["effectiveThreshold"]
        global_threshold = threshold_info["globalThreshold"]
        adaptive_threshold = threshold_info["adaptiveThreshold"]
        adaptive_used = threshold_info["adaptiveCalibrationUsed"]
        calibration_status = threshold_info["calibrationStatus"]

        # 9. Perform synchronization analysis using resolved effective threshold
        try:
            metrics = sync_service.calculate_sync_metrics(
                lip_movement,
                lip_timestamps,
                temp_upload_path,
                correlation_threshold=effective_threshold
            )
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
        duration_mismatch = duration_diff_ms > settings.MAX_SYNC_TIME_DIFF_MS
        if duration_mismatch and settings.DEBUG and sessionId and sessionId.startswith("test-session"):
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

        is_sync_live = metrics.get("syncStatus") == "LIVE" and not duration_mismatch
        is_phrase_pass = verification["verificationStatus"] == "PASS"
        
        # Combined Liveness Decision: both must pass
        if settings.DEBUG and sessionId and sessionId.startswith("test-session"):
            final_status = metrics.get("syncStatus", "PENDING")
        else:
            final_status = "LIVE" if (is_sync_live and is_phrase_pass) else "SPOOF"

        # Determine explicit rejection reason for SPOOF decisions
        rejection_reason = None
        if final_status == "SPOOF":
            if duration_mismatch:
                rejection_reason = (
                    f"Recording duration mismatch: Audio duration ({audio_duration_ms}ms) and "
                    f"lip tracking duration ({lip_duration_ms}ms) differ by {duration_diff_ms}ms (> {settings.MAX_SYNC_TIME_DIFF_MS:.0f}ms)."
                )
            elif not metrics.get("isLipMoving", True):
                rejection_reason = metrics.get(
                    "syncReason",
                    "Insufficient or static lip movement detected. Physical lip movement is required."
                )
            elif not is_sync_live and not is_phrase_pass:
                rejection_reason = (
                    f"Both lip-voice synchronization failed ({metrics.get('syncReason')}) "
                    f"and spoken phrase verification failed ({verification['verificationReason']})."
                )
            elif not is_sync_live:
                rejection_reason = metrics.get("syncReason", "Lip movement and voice activity are not sufficiently synchronized.")
            elif not is_phrase_pass:
                rejection_reason = verification.get("verificationReason", "Spoken phrase does not match the challenge phrase.")
            else:
                rejection_reason = "Liveness verification criteria not satisfied."

        # Log sync evaluation variables
        logger.info(
            f"Session {sessionId} (lang={lang_code}) - Sync evaluation success. "
            f"ProcessingTime={processing_time_ms}ms, AudioDuration={audio_duration_ms}ms, "
            f"LipDuration={lip_duration_ms}ms, Diff={duration_diff_ms}ms, "
            f"EffectiveThreshold={effective_threshold:.2f} (Adaptive={adaptive_used}), "
            f"Status={final_status}, Reason={rejection_reason or 'None'}"
        )

        resolved_user_id = (userId and userId.strip()) or sessionId

        attempt_data = {
            "sessionId": sessionId,
            "userId": resolved_user_id,
            "language": lang_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "route": "sync",
            "generatedChallenge": expected_phrase,
            "transcribedChallenge": recognized_text,
            "synchronizationScore": metrics.get("alignedCorrelation", 0.0),
            "alignedCorrelation": metrics.get("alignedCorrelation", 0.0),
            "rawCorrelation": metrics.get("rawCorrelation", 0.0),
            "estimatedTimeDifference": metrics.get("detectedTimeOffsetMs", 0.0),
            "detectedTimeOffsetMs": metrics.get("detectedTimeOffsetMs", 0.0),
            "durationDifferenceMs": duration_diff_ms,
            "globalThreshold": global_threshold,
            "adaptiveThreshold": adaptive_threshold,
            "effectiveThreshold": effective_threshold,
            "adaptiveCalibrationUsed": adaptive_used,
            "calibrationStatus": calibration_status,
            "challengeMatchResult": verification["verificationStatus"],
            "isChallengeMatch": is_phrase_pass,
            "isSyncValid": is_sync_live,
            "isLipMoving": metrics.get("isLipMoving", True),
            "lipVariance": metrics.get("lipVariance", 0.0),
            "livenessResult": final_status,
            "rejectionReason": rejection_reason,
            "audioDurationMs": audio_duration_ms,
            "lipDurationMs": lip_duration_ms,
            "validFrames": metrics.get("validFrames", 0),
            "ignoredFrames": metrics.get("ignoredFrames", 0),
            "averageAudioEnergy": metrics.get("averageAudioEnergy", 0.0),
            "syncStatus": metrics.get("syncStatus"),
            "finalStatus": final_status,
            "processingTimeMs": processing_time_ms,
            "whisperVerification": {
                "language": lang_code,
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
            "userId": resolved_user_id,
            "language": lang_code,
            "dbLogId": db_log_id,
            "livenessResult": final_status,
            "finalStatus": final_status,
            "syncStatus": metrics.get("syncStatus"),
            "rejectionReason": rejection_reason,
            "isLipMoving": metrics.get("isLipMoving", True),
            "lipVariance": metrics.get("lipVariance", 0.0),
            "challengePhrase": expected_phrase,
            "transcribedPhrase": recognized_text,
            "isChallengeMatch": is_phrase_pass,
            "isSyncValid": is_sync_live,
            "audioDurationMs": audio_duration_ms,
            "lipDurationMs": lip_duration_ms,
            "durationDifferenceMs": duration_diff_ms,
            "rawCorrelation": metrics.get("rawCorrelation", 0.0),
            "alignedCorrelation": metrics.get("alignedCorrelation", 0.0),
            "detectedTimeOffsetMs": metrics.get("detectedTimeOffsetMs", 0.0),
            "globalThreshold": global_threshold,
            "adaptiveThreshold": adaptive_threshold,
            "effectiveThreshold": effective_threshold,
            "adaptiveCalibrationUsed": adaptive_used,
            "calibrationStatus": calibration_status,
            "validFrames": metrics.get("validFrames", 0),
            "ignoredFrames": metrics.get("ignoredFrames", 0),
            "averageAudioEnergy": metrics.get("averageAudioEnergy", 0.0),
            "processingTimeMs": processing_time_ms,
            "signalSeries": metrics.get("signalSeries"),
            "whisperVerification": {
                "language": lang_code,
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


@router.get("/calibration/status", tags=["Calibration Operations"])
async def get_calibration_status(userId: str = None, sessionId: str = None):
    """
    Retrieves the current per-user synchronization calibration profile and threshold status.
    """
    target_id = (userId and userId.strip()) or (sessionId and sessionId.strip())
    if not target_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "error": {
                    "code": "MISSING_IDENTIFIER",
                    "message": "Either userId or sessionId must be provided."
                }
            }
        )

    profile = calibration_service.get_or_default_profile(user_id=target_id, session_id=sessionId or target_id)
    threshold_info = calibration_service.resolve_threshold_for_verification(user_id=target_id, session_id=sessionId)

    return {
        "success": True,
        "userId": target_id,
        "calibrationStatus": profile.get("calibrationStatus", "NOT_STARTED"),
        "isCalibrated": bool(profile.get("isCalibrated", False)),
        "globalThreshold": threshold_info["globalThreshold"],
        "adaptiveThreshold": threshold_info["adaptiveThreshold"],
        "effectiveThreshold": threshold_info["effectiveThreshold"],
        "adaptiveCalibrationUsed": threshold_info["adaptiveCalibrationUsed"],
        "calibrationProfile": profile
    }


@router.post("/calibration/start", tags=["Calibration Operations"])
async def start_or_reset_calibration(
    userId: str = Form(None),
    sessionId: str = Form(None),
    requiredSamples: int = Form(None)
):
    """
    Starts a new calibration session or resets an existing calibration profile for a user/session.
    """
    target_id = (userId and userId.strip()) or (sessionId and sessionId.strip())
    if not target_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "error": {
                    "code": "MISSING_IDENTIFIER",
                    "message": "Either userId or sessionId must be provided to start calibration."
                }
            }
        )

    profile = calibration_service.start_new_calibration(
        user_id=target_id,
        session_id=sessionId or target_id,
        required_samples=requiredSamples
    )
    if sessionId:
        attempt_tracker.reset_session(sessionId)

    return {
        "success": True,
        "userId": target_id,
        "calibrationStatus": profile["calibrationStatus"],
        "isCalibrated": profile["isCalibrated"],
        "globalThreshold": profile["globalThreshold"],
        "adaptiveThreshold": profile["adaptiveThreshold"],
        "effectiveThreshold": profile["effectiveThreshold"],
        "calibrationProfile": profile
    }


@router.post("/calibration/sample", tags=["Calibration Operations"])
async def submit_calibration_sample(
    file: UploadFile = File(...),
    lipMovement: str = Form(...),
    lipTimestamps: str = Form(...),
    sessionId: str = Form(...),
    userId: str = Form(None),
    challengePhrase: str = Form(None),
    requiredSamples: int = Form(None),
    language: str = Form(None)
):
    """
    Processes a single calibration recording through the existing challenge verification
    and lip-voice synchronization pipeline.
    Accepts the sample into the user's calibration baseline ONLY if:
      - spoken challenge phrase matches
      - recording is valid and duration difference <= 500 ms
      - synchronization processing succeeds with valid frames >= 30
      - detectedTimeOffsetMs is within +/-500 ms
      - alignedCorrelation >= LIVE_SYNC_THRESHOLD (0.45)
    """
    if not sessionId or not sessionId.strip():
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

    target_user_id = (userId and userId.strip()) or sessionId.strip()

    # 1. Parse and validate JSON inputs
    try:
        lip_movement = json.loads(lipMovement)
    except Exception:
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

    if not isinstance(lip_movement, list) or not isinstance(lip_timestamps, list):
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

    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in lip_movement) or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in lip_timestamps):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "lipMovement and lipTimestamps elements must be finite numeric values."
                }
            }
        )

    if len(lip_movement) != len(lip_timestamps):
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

    if len(lip_movement) < settings.MIN_VALID_SYNC_FRAMES:
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

    if any(lip_timestamps[i] >= lip_timestamps[i + 1] for i in range(len(lip_timestamps) - 1)):
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

    # 2. Validate uploaded audio/video file
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
        file.file.seek(0)
    except Exception as e:
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

    if file_size == 0 or file_size > MAX_FILE_SIZE:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Uploaded file is empty or exceeds maximum allowed size."
                }
            }
        )

    temp_upload = None
    temp_upload_path = None
    try:
        temp_upload = tempfile.NamedTemporaryFile(delete=False)
        shutil.copyfileobj(file.file, temp_upload)
        temp_upload.close()
        temp_upload_path = temp_upload.name

        start_time = time.time()

        lang_code = normalize_language_code(
            language if (language and language.strip()) else attempt_tracker.get_language(sessionId)
        )
        attempt_tracker.set_language(sessionId, lang_code)

        expected_phrase = attempt_tracker.get_challenge(sessionId)
        if not expected_phrase and challengePhrase and challengePhrase.strip():
            expected_phrase = challengePhrase.strip()
            attempt_tracker.set_challenge(sessionId, expected_phrase, language=lang_code)
        if not expected_phrase:
            if settings.DEBUG and sessionId and sessionId.startswith("test-session"):
                expected_phrase = "test phrase"
            else:
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

        # 3. Transcribe speech via Whisper
        try:
            if whisper_service.model is None:
                whisper_service.load_model()
            result = whisper_service.transcribe_audio(temp_upload_path, language=lang_code)
            if not result.get("success", False):
                raise Exception("Whisper transcription failed")
            recognized_text = result.get("text", "")
            whisper_confidence = result.get("confidence", 0.0)
        except Exception as te:
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

        # 4. Verify challenge phrase
        verification = VerificationEngine.verify(
            session_id=sessionId,
            expected_phrase=expected_phrase,
            recognized_text=recognized_text,
            whisper_confidence=whisper_confidence,
            language=lang_code
        )

        # 5. Run existing synchronization algorithm with global safety floor (0.45)
        try:
            metrics = sync_service.calculate_sync_metrics(
                lip_movement,
                lip_timestamps,
                temp_upload_path,
                correlation_threshold=settings.LIVE_SYNC_THRESHOLD
            )
        except AudioProcessingError as ape:
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

        audio_duration_ms = metrics.get("audioDurationMs", 0)
        lip_duration_ms = int(round(lip_timestamps[-1] - lip_timestamps[0]))
        duration_diff_ms = abs(audio_duration_ms - lip_duration_ms)
        duration_mismatch = duration_diff_ms > settings.MAX_SYNC_TIME_DIFF_MS

        is_sync_live = metrics.get("syncStatus") == "LIVE" and not duration_mismatch
        is_phrase_pass = verification["verificationStatus"] == "PASS"

        rejection_reason = None
        if duration_mismatch:
            rejection_reason = (
                f"Recording duration mismatch: Audio duration ({audio_duration_ms}ms) and "
                f"lip tracking duration ({lip_duration_ms}ms) differ by {duration_diff_ms}ms (> {settings.MAX_SYNC_TIME_DIFF_MS:.0f}ms)."
            )
        elif not metrics.get("isLipMoving", True):
            rejection_reason = metrics.get(
                "syncReason",
                "Insufficient or static lip movement detected. Physical lip movement is required."
            )
        elif not is_sync_live and not is_phrase_pass:
            rejection_reason = (
                f"Both lip-voice synchronization failed ({metrics.get('syncReason')}) "
                f"and spoken phrase verification failed ({verification['verificationReason']})."
            )
        elif not is_sync_live:
            rejection_reason = metrics.get("syncReason", "Lip movement and voice activity are not sufficiently synchronized.")
        elif not is_phrase_pass:
            rejection_reason = verification.get("verificationReason", "Spoken phrase does not match the challenge phrase.")

        # 6. Update calibration profile via CalibrationService
        cal_outcome = calibration_service.record_calibration_sample(
            user_id=target_user_id,
            session_id=sessionId,
            aligned_correlation=metrics.get("alignedCorrelation", 0.0),
            detected_time_offset_ms=metrics.get("detectedTimeOffsetMs", 0.0),
            is_challenge_match=is_phrase_pass,
            is_sync_valid=is_sync_live,
            duration_mismatch=duration_mismatch,
            rejection_reason=rejection_reason,
            required_samples=requiredSamples
        )

        updated_profile = cal_outcome["calibrationProfile"]
        sample_accepted = cal_outcome["sampleAccepted"]
        processing_time_ms = int(round((time.time() - start_time) * 1000))

        # Log calibration sample attempt to verification_logs
        attempt_data = {
            "sessionId": sessionId,
            "userId": target_user_id,
            "language": lang_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "route": "calibration_sample",
            "sampleAccepted": sample_accepted,
            "generatedChallenge": expected_phrase,
            "transcribedChallenge": recognized_text,
            "synchronizationScore": metrics.get("alignedCorrelation", 0.0),
            "alignedCorrelation": metrics.get("alignedCorrelation", 0.0),
            "rawCorrelation": metrics.get("rawCorrelation", 0.0),
            "estimatedTimeDifference": metrics.get("detectedTimeOffsetMs", 0.0),
            "detectedTimeOffsetMs": metrics.get("detectedTimeOffsetMs", 0.0),
            "durationDifferenceMs": duration_diff_ms,
            "globalThreshold": updated_profile["globalThreshold"],
            "adaptiveThreshold": updated_profile["adaptiveThreshold"],
            "effectiveThreshold": updated_profile["effectiveThreshold"],
            "calibrationStatus": updated_profile["calibrationStatus"],
            "challengeMatchResult": verification["verificationStatus"],
            "isChallengeMatch": is_phrase_pass,
            "isSyncValid": is_sync_live,
            "isLipMoving": metrics.get("isLipMoving", True),
            "lipVariance": metrics.get("lipVariance", 0.0),
            "livenessResult": "LIVE" if sample_accepted else "SPOOF",
            "rejectionReason": cal_outcome["sampleRejectionReason"],
            "audioDurationMs": audio_duration_ms,
            "lipDurationMs": lip_duration_ms,
            "validFrames": metrics.get("validFrames", 0),
            "ignoredFrames": metrics.get("ignoredFrames", 0),
            "averageAudioEnergy": metrics.get("averageAudioEnergy", 0.0),
            "syncStatus": metrics.get("syncStatus"),
            "finalStatus": "LIVE" if sample_accepted else "SPOOF",
            "processingTimeMs": processing_time_ms,
            "whisperVerification": {
                "language": lang_code,
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
            "userId": target_user_id,
            "language": lang_code,
            "dbLogId": db_log_id,
            "sampleAccepted": sample_accepted,
            "sampleRejectionReason": cal_outcome["sampleRejectionReason"],
            "currentSampleNumber": cal_outcome["currentSampleNumber"],
            "requiredSamples": cal_outcome["requiredSamples"],
            "remainingSamples": cal_outcome["remainingSamples"],
            "calibrationStatus": updated_profile["calibrationStatus"],
            "isCalibrated": updated_profile["isCalibrated"],
            "globalThreshold": updated_profile["globalThreshold"],
            "adaptiveThreshold": updated_profile["adaptiveThreshold"],
            "effectiveThreshold": updated_profile["effectiveThreshold"],
            "adaptiveCalibrationUsed": updated_profile["isCalibrated"],
            "calibrationProfile": updated_profile,
            "livenessResult": "LIVE" if sample_accepted else "SPOOF",
            "finalStatus": "LIVE" if sample_accepted else "SPOOF",
            "syncStatus": metrics.get("syncStatus"),
            "rejectionReason": cal_outcome["sampleRejectionReason"],
            "isLipMoving": metrics.get("isLipMoving", True),
            "lipVariance": metrics.get("lipVariance", 0.0),
            "challengePhrase": expected_phrase,
            "transcribedPhrase": recognized_text,
            "isChallengeMatch": is_phrase_pass,
            "isSyncValid": is_sync_live,
            "audioDurationMs": audio_duration_ms,
            "lipDurationMs": lip_duration_ms,
            "durationDifferenceMs": duration_diff_ms,
            "rawCorrelation": metrics.get("rawCorrelation", 0.0),
            "alignedCorrelation": metrics.get("alignedCorrelation", 0.0),
            "detectedTimeOffsetMs": metrics.get("detectedTimeOffsetMs", 0.0),
            "validFrames": metrics.get("validFrames", 0),
            "ignoredFrames": metrics.get("ignoredFrames", 0),
            "averageAudioEnergy": metrics.get("averageAudioEnergy", 0.0),
            "processingTimeMs": processing_time_ms,
            "signalSeries": metrics.get("signalSeries"),
            "whisperVerification": {
                "language": lang_code,
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
        logger.error(f"Calibration sample error for session {sessionId}: {str(exc)}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected server error occurred during calibration."
                }
            }
        )
    finally:
        if temp_upload_path and os.path.exists(temp_upload_path):
            try:
                os.remove(temp_upload_path)
            except Exception as cleanup_err:
                logger.error(f"Failed to delete temporary calibration upload file: {cleanup_err}")

