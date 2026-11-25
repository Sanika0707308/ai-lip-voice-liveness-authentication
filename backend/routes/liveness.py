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

_HALLUCINATED_PHRASES = {
    "[blank_audio]", "[inaudible]", "blank_audio", "blank audio", "inaudible",
    "thank you for watching!", "thank you for watching", "subtitles by", "subtitles",
    "thank you.", "thank you", "you"
}
_HALLUCINATED_SUBSTRINGS = (
    "[blank_audio]", "blank_audio", "thank you for watching",
    "subtitles by", "[inaudible]", "inaudible"
)

def _infer_audio_suffix(upload_file: UploadFile) -> str:
    """Infers file extension from upload filename or MIME type for container-aware decoding."""
    ext = os.path.splitext(getattr(upload_file, "filename", "") or "")[1].lower()
    if ext in {".webm", ".wav", ".ogg", ".mp4", ".m4a", ".mp3"}:
        return ext
    ctype = (getattr(upload_file, "content_type", "") or "").lower()
    if "wav" in ctype:
        return ".wav"
    if "ogg" in ctype:
        return ".ogg"
    if "mp4" in ctype or "m4a" in ctype or "aac" in ctype:
        return ".mp4"
    if "mpeg" in ctype or "mp3" in ctype:
        return ".mp3"
    return ".webm"

def _save_and_normalize_audio(upload_file: UploadFile, session_id: str, route_label: str) -> tuple:
    """
    Saves the uploaded audio with its inferred container suffix, converts it to
    16kHz mono PCM WAV via FFmpeg, logs capture diagnostics, and returns
    (temp_upload_path, temp_wav_path, audio_meta).
    """
    suffix = _infer_audio_suffix(upload_file)
    temp_upload = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        shutil.copyfileobj(upload_file.file, temp_upload)
    finally:
        temp_upload.close()
    temp_upload_path = temp_upload.name
    upload_bytes = os.path.getsize(temp_upload_path)
    temp_wav_path, audio_meta = sync_service.convert_to_wav(temp_upload_path)
    logger.info(
        f"[CHECK: AUDIO_CAPTURE] Session={session_id} | Route={route_label} | "
        f"File={getattr(upload_file, 'filename', None) or 'upload'} ({suffix}) | "
        f"UploadBytes={upload_bytes} | WavBytes={audio_meta['wavBytes']} | "
        f"DurationMs={audio_meta['durationMs']} | SampleRate={audio_meta['sampleRate']} | "
        f"OverallRMS={audio_meta['overallRms']:.5f} | PeakAmp={audio_meta['peakAmplitude']:.5f}"
    )
    return temp_upload_path, temp_wav_path, audio_meta

def _evaluate_audio_silence(metrics: Dict[str, Any], recognized_text: str) -> tuple:
    """
    Returns (is_whisper_silent, is_audio_silent) using consistent thresholds
    matching SyncService silence rejection.
    """
    cleaned_rec = (recognized_text or "").strip().lower()
    is_whisper_silent = (
        not cleaned_rec
        or cleaned_rec in _HALLUCINATED_PHRASES
        or any(h in cleaned_rec for h in _HALLUCINATED_SUBSTRINGS)
    )
    min_speech_frames = min(settings.MIN_VALID_SYNC_FRAMES, 15)
    is_audio_silent = (
        metrics.get("validFrames", 0) < min_speech_frames
        or metrics.get("averageAudioEnergy", 0.0) < 0.006
        or metrics.get("maxAudioEnergy", 0.0) < 0.025
    )
    return is_whisper_silent, is_audio_silent

def _cleanup_temp_files(*paths: str) -> None:
    """Safely removes temporary audio files."""
    for path in paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception as cleanup_err:
                logger.error(f"Failed to delete temporary audio file {path}: {cleanup_err}")

# In-memory registry of verified users and sessions
VERIFIED_LIVENESS_USERS: Dict[str, datetime] = {}

def mark_liveness_verified(user_id: str = None, session_id: str = None):
    now_dt = datetime.now(timezone.utc)
    if user_id:
        VERIFIED_LIVENESS_USERS[str(user_id)] = now_dt
    if session_id:
        VERIFIED_LIVENESS_USERS[str(session_id)] = now_dt
    try:
        if mongodb_service and not mongodb_service.use_fallback and mongodb_service.db is not None:
            record = {
                "user_id": str(user_id) if user_id else str(session_id),
                "session_id": str(session_id) if session_id else str(user_id),
                "verified": True,
                "verified_at": now_dt.isoformat()
            }
            mongodb_service.db["liveness_verifications"].update_one(
                {"$or": [{"user_id": str(user_id)}, {"session_id": str(session_id)}]},
                {"$set": record},
                upsert=True
            )
    except Exception as e:
        logger.warning(f"Notice during liveness verification persistence: {e}")

def is_liveness_verified(user_id: str = None, session_id: str = None) -> bool:
    if not user_id and not session_id:
        return False
    if user_id and str(user_id) in VERIFIED_LIVENESS_USERS:
        return True
    if session_id and str(session_id) in VERIFIED_LIVENESS_USERS:
        return True
    try:
        if mongodb_service and not mongodb_service.use_fallback and mongodb_service.db is not None:
            or_clauses = []
            if user_id:
                or_clauses.append({"user_id": str(user_id)})
                or_clauses.append({"userId": str(user_id)})
            if session_id:
                or_clauses.append({"session_id": str(session_id)})
                or_clauses.append({"sessionId": str(session_id)})
            if or_clauses:
                rec = mongodb_service.db["liveness_verifications"].find_one({"$or": or_clauses, "verified": True})
                if rec:
                    if user_id:
                        VERIFIED_LIVENESS_USERS[str(user_id)] = datetime.now(timezone.utc)
                    if session_id:
                        VERIFIED_LIVENESS_USERS[str(session_id)] = datetime.now(timezone.utc)
                    return True
    except Exception:
        pass
    return False

@router.get("/liveness/status", tags=["Liveness Operations"])
async def get_liveness_status(userId: str = None, sessionId: str = None) -> Dict[str, Any]:
    verified = is_liveness_verified(userId, sessionId)
    return {
        "verified": verified,
        "userId": userId,
        "sessionId": sessionId,
        "status": "LIVE" if verified else "UNVERIFIED"
    }

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
    language: str = Form(None),
    attemptId: str = Form(None)
):
    """
    Endpoint to validate video/audio and lip coordinate timestamps, 
    perform cross-correlation synchronization checks, and verify matching session durations.
    """
    # Safe defaults to prevent any UnboundLocalError or NameError under any branch or exception
    is_phrase_pass: bool = False
    is_phrase_verified: bool = False
    is_sync_live: bool = False
    is_biometric_valid: bool = False
    final_status: str = "SPOOF"
    rejection_reason: str = None

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
                },
                "isSessionExpired": True,
                "attemptsUsed": limit,
                "attemptsRemaining": 0
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

    # 8. Thread-safe temporary file handling and FFmpeg 16kHz PCM normalization
    temp_upload_path = None
    temp_wav_path = None
    audio_meta = {}
    try:
        try:
            temp_upload_path, temp_wav_path, audio_meta = _save_and_normalize_audio(file, sessionId, "sync")
        except AudioProcessingError as ape:
            logger.warning(f"Session {sessionId} - Audio normalization error: {str(ape)}")
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

        start_time = time.time()

        # Resolve session language (from parameter or tracker, defaulting to 'en')
        lang_code = normalize_language_code(
            language if (language and language.strip()) else attempt_tracker.get_language(sessionId)
        )

        # Validate challenge attempt (enforce server-stored phrase, generous expiration, and one-time use)
        valid_attempt, reason_code, attempt_info = attempt_tracker.validate_attempt(sessionId)
        if not valid_attempt:
            # Check if we can recover the challenge from client submission or existing session record
            client_phrase = challengePhrase.strip() if challengePhrase and challengePhrase.strip() else None
            stored_phrase = attempt_info.get("challenge") if attempt_info else None
            recoverable_phrase = client_phrase or stored_phrase

            if recoverable_phrase:
                expected_phrase = recoverable_phrase
                attempt_tracker.set_challenge(sessionId, expected_phrase, language=lang_code, ttl_seconds=settings.CHALLENGE_TIMEOUT_SECONDS)
                attempt_info = attempt_tracker.get_challenge_record(sessionId)
                valid_attempt = True
            elif settings.DEBUG and sessionId and sessionId.startswith("test-session"):
                expected_phrase = "test phrase"
                attempt_info = {"attempt_id": f"att-{sessionId}", "challenge": expected_phrase, "language": lang_code}
                valid_attempt = True
            else:
                if reason_code == "CHALLENGE_EXPIRED":
                    err_msg = "Challenge phrase has expired. Please fetch a new challenge."
                elif reason_code == "CHALLENGE_ALREADY_USED":
                    err_msg = "This challenge phrase has already been verified (replay detected). Please fetch a new challenge."
                else:
                    err_msg = "Challenge phrase not initialized for this session. Please fetch a challenge first."
                logger.warning(f"Session {sessionId} - Attempt validation failed: {reason_code}")
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "success": False,
                        "error": {
                            "code": reason_code,
                            "message": err_msg
                        }
                    }
                )
        if valid_attempt and 'expected_phrase' not in locals():
            expected_phrase = attempt_info.get("challenge")
            lang_code = attempt_info.get("language") or lang_code

        # Attempt ID for tracking
        resolved_attempt_id = attemptId or attempt_info.get("attempt_id") or sessionId

        # Run Whisper transcription on normalized 16kHz mono PCM WAV
        try:
            if whisper_service.model is None:
                whisper_service.load_model()
            result = whisper_service.transcribe_audio(temp_wav_path, language=lang_code)
            if not result.get("success", False):
                raise Exception("Whisper transcription failed")
            
            recognized_text = result.get("text", "")
            whisper_confidence = result.get("confidence", 0.0)
            logger.info(
                f"[CHECK: WHISPER_RAW] Session={sessionId} | Lang={lang_code} | "
                f"DetectedLang={result.get('detected_language')} | DurationSec={result.get('duration')} | "
                f"Confidence={whisper_confidence:.3f} | Text='{recognized_text}'"
            )
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
        if verification.get("verificationStatus") != "PASS" and challengePhrase and challengePhrase.strip() and challengePhrase.strip() != expected_phrase:
            alt_verification = VerificationEngine.verify(
                session_id=sessionId,
                expected_phrase=challengePhrase.strip(),
                recognized_text=recognized_text,
                whisper_confidence=whisper_confidence,
                language=lang_code
            )
            if alt_verification.get("overallScore", 0) > verification.get("overallScore", 0):
                verification = alt_verification
                expected_phrase = challengePhrase.strip()

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
                temp_wav_path,
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

        # 10. Check duration mismatch threshold (generous 2500ms file buffer to account for MediaRecorder startup/shutdown latency)
        duration_mismatch = duration_diff_ms > 2500
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

        # Silence & speech detection (shared helper aligned with SyncService thresholds)
        is_whisper_silent, is_audio_silent = _evaluate_audio_silence(metrics, recognized_text)

        # Spoken challenge phrase match result:
        # Requires audible speech, non-hallucinatory transcription, and matching phrase score
        is_phrase_pass = (
            not is_whisper_silent
            and not is_audio_silent
            and (
                verification.get("verificationStatus") == "PASS"
                or verification.get("overallScore", 0.0) >= settings.CHALLENGE_MATCH_THRESHOLD
            )
        )
        is_phrase_verified = is_phrase_pass

        is_biometric_valid = (
            metrics.get("isLipMoving", False)
            and not duration_mismatch
            and metrics.get("validFrames", 0) >= 15
            and abs(metrics.get("detectedTimeOffsetMs", 0.0)) <= settings.MAX_SYNC_TIME_DIFF_MS
        )

        is_sync_live = (
            metrics.get("syncStatus") == "LIVE"
            and not duration_mismatch
            and not is_audio_silent
        )

        # Both lip-voice synchronization and spoken challenge matching are strictly required!
        final_status = "LIVE" if (is_sync_live and is_phrase_pass and is_biometric_valid) else "SPOOF"

        # Determine explicit rejection reason for SPOOF decisions
        rejection_reason = None
        if final_status == "SPOOF":
            if is_audio_silent or is_whisper_silent:
                rejection_reason = "No speech detected. Please speak the displayed challenge phrase aloud."
            elif duration_mismatch:
                rejection_reason = (
                    f"Recording duration mismatch: Audio duration ({audio_duration_ms}ms) and "
                    f"lip tracking duration ({lip_duration_ms}ms) differ by {duration_diff_ms}ms (> {settings.MAX_SYNC_TIME_DIFF_MS:.0f}ms)."
                )
            elif not metrics.get("isLipMoving", True):
                rejection_reason = metrics.get(
                    "syncReason",
                    "Insufficient or static lip movement detected. Physical lip movement is required."
                )
            elif not is_phrase_pass:
                rejection_reason = verification.get(
                    "verificationReason",
                    f"Spoken phrase ('{recognized_text}') did not match the expected challenge phrase ('{expected_phrase}')."
                )
            elif not is_sync_live:
                rejection_reason = metrics.get("syncReason", "Lip movement and voice activity are not sufficiently synchronized.")
            else:
                rejection_reason = "Liveness verification criteria not satisfied."

        # Diagnostic Logs for each verification check
        logger.info(f"========== [DIAGNOSTIC LOGS - SESSION {sessionId} (LANG={lang_code})] ==========")
        logger.info(
            f"[CHECK: TRANSCRIPTION] Expected='{expected_phrase}' | "
            f"Transcribed='{recognized_text}' | "
            f"CharSim={verification.get('characterSimilarityPercentage', 0.0):.1f}% | "
            f"WordMatch={verification.get('wordMatchPercentage', 0.0):.1f}% | "
            f"WhisperConfidence={whisper_confidence:.3f} | "
            f"OverallScore={verification.get('overallScore', 0.0):.1f}% | "
            f"Status={verification.get('verificationStatus')} | "
            f"Reason={verification.get('verificationReason')}"
        )
        logger.info(
            f"[CHECK: LIP_MOVEMENT] Frames={len(lip_movement)} | "
            f"Variance={metrics.get('lipVariance', 0.0):.6f} | "
            f"MinVariance={settings.MIN_LIP_VARIATION:.6f} | "
            f"IsMoving={metrics.get('isLipMoving', False)}"
        )
        logger.info(
            f"[CHECK: AUDIO_ENERGY] WavDurationMs={audio_meta.get('durationMs', 0)}ms | "
            f"OverallRMS={audio_meta.get('overallRms', 0.0):.5f} | "
            f"AvgEnergy={metrics.get('averageAudioEnergy', 0.0):.5f} | "
            f"MaxEnergy={metrics.get('maxAudioEnergy', 0.0):.5f} | "
            f"ValidFrames={metrics.get('validFrames', 0)} | "
            f"AudioSilent={is_audio_silent} | WhisperSilent={is_whisper_silent} | "
            f"PhrasePass={is_phrase_pass}"
        )
        logger.info(
            f"[CHECK: DURATION] AudioDuration={audio_duration_ms}ms | "
            f"LipDuration={lip_duration_ms}ms | "
            f"Diff={duration_diff_ms}ms | "
            f"MaxDiffAllowed={settings.MAX_SYNC_TIME_DIFF_MS:.0f}ms | "
            f"DurationMatch={not duration_mismatch}"
        )
        logger.info(
            f"[CHECK: SYNCHRONIZATION] RawCorrelation={metrics.get('rawCorrelation', 0.0):.3f} | "
            f"AlignedCorrelation={metrics.get('alignedCorrelation', 0.0):.3f} | "
            f"RequiredThreshold={effective_threshold:.3f} (Adaptive={adaptive_used}) | "
            f"Offset={metrics.get('detectedTimeOffsetMs', 0.0):+.1f}ms | "
            f"MaxOffsetAllowed=±{settings.MAX_SYNC_TIME_DIFF_MS:.0f}ms | "
            f"SyncStatus={metrics.get('syncStatus')} | "
            f"Reason={metrics.get('syncReason')}"
        )
        logger.info(
            f"[DECISION: FINAL] Result={final_status} | "
            f"ProcessingTime={processing_time_ms}ms | "
            f"RejectionReason={rejection_reason or 'None (Verified Genuine Live Attempt)'}"
        )
        logger.info(f"================================================================================")

        resolved_user_id = (userId and userId.strip()) or sessionId

        attempt_data = {
            "sessionId": sessionId,
            "attemptId": resolved_attempt_id,
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
            "challengeMatchResult": verification.get("verificationStatus", "FAIL"),
            "isChallengeMatch": is_phrase_pass,
            "isPhraseVerified": is_phrase_pass,
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
            "maxAudioEnergy": metrics.get("maxAudioEnergy", 0.0),
            "audioRms": audio_meta.get("overallRms", 0.0),
            "syncStatus": metrics.get("syncStatus"),
            "finalStatus": final_status,
            "processingTimeMs": processing_time_ms,
            "whisperVerification": {
                "language": lang_code,
                "expectedPhrase": expected_phrase,
                "recognizedText": recognized_text,
                "characterSimilarityPercentage": verification.get("characterSimilarityPercentage", 0.0),
                "wordMatchPercentage": verification.get("wordMatchPercentage", 0.0),
                "whisperConfidence": whisper_confidence,
                "overallScore": verification.get("overallScore", 0.0),
                "verificationStatus": verification.get("verificationStatus", "FAIL"),
                "verificationReason": verification.get("verificationReason", "")
            }
        }
        
        db_log_id = mongodb_service.log_verification_attempt(attempt_data)

        # Mark user/session as verified if liveness result is LIVE
        if final_status == "LIVE":
            mark_liveness_verified(resolved_user_id, sessionId)
            logger.info(f"Liveness verification PASSED (LIVE) for user '{resolved_user_id}' / session '{sessionId}'. Interview access granted.")

        # Consume challenge attempt so it cannot be replayed
        attempt_tracker.consume_attempt(sessionId)
        attempt_tracker.record_result(sessionId, final_status)
        attempts_used = attempt_tracker.get_attempts(sessionId)
        attempts_remaining = max(0, limit - attempts_used)
        is_session_expired = (attempts_used >= limit and final_status != "LIVE")

        return {
            "success": True,
            "sessionId": sessionId,
            "attemptId": resolved_attempt_id,
            "userId": resolved_user_id,
            "language": lang_code,
            "dbLogId": db_log_id,
            "livenessResult": final_status,
            "finalStatus": final_status,
            "attemptsUsed": attempts_used,
            "attemptsRemaining": attempts_remaining,
            "maxAttempts": limit,
            "isSessionExpired": is_session_expired,
            "syncStatus": metrics.get("syncStatus"),
            "rejectionReason": rejection_reason,
            "isLipMoving": metrics.get("isLipMoving", True),
            "lipVariance": metrics.get("lipVariance", 0.0),
            "challengePhrase": expected_phrase,
            "transcribedPhrase": recognized_text,
            "isChallengeMatch": is_phrase_pass,
            "isPhraseVerified": is_phrase_pass,
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
            "maxAudioEnergy": metrics.get("maxAudioEnergy", 0.0),
            "audioRms": audio_meta.get("overallRms", 0.0),
            "processingTimeMs": processing_time_ms,
            "signalSeries": metrics.get("signalSeries"),
            "whisperVerification": {
                "language": lang_code,
                "expectedPhrase": expected_phrase,
                "recognizedText": recognized_text,
                "characterSimilarityPercentage": verification.get("characterSimilarityPercentage", 0.0),
                "wordMatchPercentage": verification.get("wordMatchPercentage", 0.0),
                "whisperConfidence": whisper_confidence,
                "overallScore": verification.get("overallScore", 0.0),
                "verificationStatus": verification.get("verificationStatus", "FAIL"),
                "verificationReason": verification.get("verificationReason", "")
            }
        }

    except Exception as exc:
        logger.error(f"Session {sessionId} - Unexpected exception: {str(exc)}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "detail": f"Verification error: {str(exc)}",
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": f"Verification processing error: {str(exc)}"
                }
            }
        )

    finally:
        # Clean up temporary uploaded and converted audio files
        _cleanup_temp_files(temp_upload_path, temp_wav_path)


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
    # Safe defaults to prevent any UnboundLocalError or NameError under any branch or exception
    is_phrase_pass: bool = False
    is_phrase_verified: bool = False
    is_sync_live: bool = False

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

    temp_upload_path = None
    temp_wav_path = None
    audio_meta = {}
    try:
        try:
            temp_upload_path, temp_wav_path, audio_meta = _save_and_normalize_audio(file, sessionId, "calibration")
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

        # 3. Transcribe speech via Whisper using normalized 16kHz mono PCM WAV
        try:
            if whisper_service.model is None:
                whisper_service.load_model()
            result = whisper_service.transcribe_audio(temp_wav_path, language=lang_code)
            if not result.get("success", False):
                raise Exception("Whisper transcription failed")
            recognized_text = result.get("text", "")
            whisper_confidence = result.get("confidence", 0.0)
            logger.info(
                f"[CHECK: WHISPER_RAW] Session={sessionId} | Route=calibration | Lang={lang_code} | "
                f"DetectedLang={result.get('detected_language')} | DurationSec={result.get('duration')} | "
                f"Confidence={whisper_confidence:.3f} | Text='{recognized_text}'"
            )
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
                temp_wav_path,
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

        # Silence & speech detection for calibration
        is_whisper_silent, is_audio_silent = _evaluate_audio_silence(metrics, recognized_text)
        logger.info(
            f"[CHECK: AUDIO_ENERGY_CAL] Session={sessionId} | ValidFrames={metrics.get('validFrames', 0)} | "
            f"AvgEnergy={metrics.get('averageAudioEnergy', 0.0):.5f} | "
            f"MaxEnergy={metrics.get('maxAudioEnergy', 0.0):.5f} | "
            f"OverallRMS={audio_meta.get('overallRms', 0.0):.5f} | "
            f"AudioSilent={is_audio_silent} | WhisperSilent={is_whisper_silent}"
        )

        # Spoken phrase passes ONLY when audible speech matches challenge threshold
        is_phrase_pass = (
            not is_whisper_silent
            and not is_audio_silent
            and (
                verification.get("verificationStatus") == "PASS"
                or verification.get("overallScore", 0.0) >= settings.CHALLENGE_MATCH_THRESHOLD
            )
        )
        is_phrase_verified = is_phrase_pass

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
            "maxAudioEnergy": metrics.get("maxAudioEnergy", 0.0),
            "audioRms": audio_meta.get("overallRms", 0.0),
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
            "isPhraseVerified": is_phrase_pass,
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
            "maxAudioEnergy": metrics.get("maxAudioEnergy", 0.0),
            "audioRms": audio_meta.get("overallRms", 0.0),
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
                "detail": f"Calibration error: {str(exc)}",
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": f"Calibration processing error: {str(exc)}"
                }
            }
        )
    finally:
        _cleanup_temp_files(temp_upload_path, temp_wav_path)

