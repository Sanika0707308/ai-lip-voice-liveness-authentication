import os
import shutil
import uuid
import time
import subprocess
import random
from datetime import datetime, timezone
import soundfile as sf
from fastapi import APIRouter, File, UploadFile, HTTPException, status, Form

from backend.config import settings
from backend.services.whisper_service import whisper_service
from backend.services.attempt_tracker import attempt_tracker
from backend.services.verification_service import VerificationEngine, normalize_language_code
from backend.services.mongodb_service import mongodb_service

router = APIRouter()

# Validation constraints
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_MIME_PREFIXES = ["audio/", "video/", "application/octet-stream"]

def get_audio_duration_seconds(filepath: str) -> float:
    """
    Calculates audio duration in seconds using ffprobe or soundfile.
    """
    # Try ffprobe first (since browser uploads webm files and ffprobe can parse webm directly)
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            filepath
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=5)
        duration_str = result.stdout.strip()
        if duration_str:
            return float(duration_str)
    except Exception:
        # Fallback to soundfile if it's a WAV/FLAC/etc.
        try:
            info = sf.info(filepath)
            return info.duration
        except Exception:
            pass
    return -1.0

from backend.services.challenge_service import (
    ADJECTIVES,
    NOUNS,
    VERBS,
    HI_ADJECTIVES,
    HI_NOUNS,
    HI_VERBS,
    HI_NUMBERS,
    MR_ADJECTIVES,
    MR_NOUNS,
    MR_VERBS,
    MR_NUMBERS,
    generate_random_challenge,
    challenge_service
)

@router.get("/transcribe/challenge/new", tags=["Transcription Operations"])
async def get_new_challenge(sessionId: str, language: str = "en"):
    """
    Generates a dynamic grammatical challenge phrase in the selected language ('en', 'hi', 'mr'),
    maps it to the session with a unique attempt ID and short expiration, and returns it to the client.
    """
    if not sessionId or not sessionId.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing session ID."
        )

    limit = settings.MAX_VERIFICATION_ATTEMPTS
    if attempt_tracker.is_session_expired(sessionId, limit):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Verification session expired. Maximum {limit} attempts exceeded."
        )
    
    lang_code = normalize_language_code(language)
    phrase, attempt_id, ttl_seconds = challenge_service.generate_and_track_challenge(
        session_id=sessionId,
        language=lang_code
    )
    return {
        "success": True,
        "sessionId": sessionId,
        "attemptId": attempt_id,
        "language": lang_code,
        "challengePhrase": phrase,
        "expiresInSeconds": ttl_seconds
    }

@router.post("/transcribe", tags=["Transcription Operations"])
async def transcribe_audio(
    file: UploadFile = File(...),
    challengePhrase: str = Form(None),
    sessionId: str = Form(None),
    language: str = Form(None)
):
    """
    Endpoint to receive an uploaded audio file, validate its structure,
    verify the challenge phrase, and perform local transcription.
    """
    start_total_time = time.time()

    # 1. Validation checks on query/form params
    if not challengePhrase or not challengePhrase.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing challenge phrase."
        )
    if not sessionId or not sessionId.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing session ID."
        )

    # 2. Rate Limiting Check
    limit = settings.MAX_VERIFICATION_ATTEMPTS
    if not attempt_tracker.check_and_increment(sessionId, limit):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {limit} attempts allowed per authentication session."
        )

    # 3. Validation checks on uploaded file
    try:
        file.file.seek(0, os.SEEK_END)
        file_size = file.file.tell()
        file.file.seek(0)  # Reset pointer
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to read file metadata: {str(e)}"
        )

    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes)."
        )

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {MAX_FILE_SIZE / (1024 * 1024)} MB."
        )

    content_type = file.content_type
    is_valid_mime = False
    if content_type:
        for prefix in ALLOWED_MIME_PREFIXES:
            if content_type.startswith(prefix):
                is_valid_mime = True
                break
    else:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext in [".webm", ".wav", ".mp3", ".ogg", ".m4a", ".aac"]:
            is_valid_mime = True

    if not is_valid_mime:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file format: {content_type}. Only audio uploads are supported."
        )

    # Save temporary file
    temp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    unique_filename = f"{uuid.uuid4()}_{file.filename}"
    temp_filepath = os.path.join(temp_dir, unique_filename)

    try:
        with open(temp_filepath, "wb") as temp_file:
            shutil.copyfileobj(file.file, temp_file)
            
        # 4. Audio Duration Validation
        duration = get_audio_duration_seconds(temp_filepath)
        if duration < 0:
            # Fallback to Whisper's quick transcribe info inspect without segment processing
            try:
                if whisper_service.model is None:
                    whisper_service.load_model()
                _, info = whisper_service.model.transcribe(temp_filepath, beam_size=1)
                duration = info.duration
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Unable to determine audio file duration."
                )

        if duration < settings.AUDIO_MIN_DURATION_SEC or duration > settings.AUDIO_MAX_DURATION_SEC:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Audio duration ({duration:.2f}s) must be between {settings.AUDIO_MIN_DURATION_SEC} and {settings.AUDIO_MAX_DURATION_SEC} seconds."
            )

        # 5. Resolve language and Transcribe Audio
        if language and language.strip():
            lang_code = normalize_language_code(language)
            attempt_tracker.set_language(sessionId, lang_code)
        else:
            lang_code = attempt_tracker.get_language(sessionId)

        start_transcribe = time.time()
        result = whisper_service.transcribe_audio(temp_filepath, language=lang_code)
        transcription_time_ms = int((time.time() - start_transcribe) * 1000)

        if not result.get("success", False):
            return result

        recognized_text = result.get("text", "")
        whisper_confidence = result.get("confidence", 0.0)

        # 6. Verify Phrase
        # Prioritize server-stored expected phrase if available to prevent client tampering
        stored_phrase = attempt_tracker.get_challenge(sessionId)
        active_expected_phrase = stored_phrase if (stored_phrase and stored_phrase.strip()) else challengePhrase

        start_verify = time.time()
        verification = VerificationEngine.verify(
            session_id=sessionId,
            expected_phrase=active_expected_phrase,
            recognized_text=recognized_text,
            whisper_confidence=whisper_confidence,
            language=lang_code
        )
        verification_time_ms = int((time.time() - start_verify) * 1000)

        speech_start_sec = result.get("speech_start_sec")
        speech_end_sec = result.get("speech_end_sec")
        
        speech_start_ms = int(speech_start_sec * 1000) if speech_start_sec is not None else None
        speech_end_ms = int(speech_end_sec * 1000) if speech_end_sec is not None else None
        recording_duration_ms = int(duration * 1000)

        total_processing_time_ms = int((time.time() - start_total_time) * 1000)

        attempt_data = {
            "sessionId": sessionId,
            "userId": sessionId,
            "language": lang_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "route": "transcribe",
            "audioDurationMs": recording_duration_ms,
            "processingTimeMs": total_processing_time_ms,
            "transcriptionTimeMs": transcription_time_ms,
            "verificationTimeMs": verification_time_ms,
            "speechStartTimeMs": speech_start_ms,
            "speechEndTimeMs": speech_end_ms,
            "expectedPhrase": active_expected_phrase,
            "recognizedText": recognized_text,
            "generatedChallenge": active_expected_phrase,
            "transcribedChallenge": recognized_text,
            "challengeMatchResult": verification["verificationStatus"],
            "whisperVerification": {
                "language": lang_code,
                "expectedPhrase": active_expected_phrase,
                "recognizedText": recognized_text,
                "characterSimilarityPercentage": verification["characterSimilarityPercentage"],
                "wordMatchPercentage": verification["wordMatchPercentage"],
                "whisperConfidence": whisper_confidence,
                "overallScore": verification["overallScore"],
                "verificationStatus": verification["verificationStatus"],
                "verificationReason": verification["verificationReason"]
            },
            "finalStatus": verification["verificationStatus"]
        }
        
        db_log_id = mongodb_service.log_verification_attempt(attempt_data)

        return {
            "success": True,
            "sessionId": sessionId,
            "language": lang_code,
            "dbLogId": db_log_id,
            "expectedPhrase": active_expected_phrase,
            "recognizedText": recognized_text,
            "characterSimilarityPercentage": verification["characterSimilarityPercentage"],
            "wordMatchPercentage": verification["wordMatchPercentage"],
            "whisperConfidence": whisper_confidence,
            "overallScore": verification["overallScore"],
            "verificationStatus": verification["verificationStatus"],
            "verificationReason": verification["verificationReason"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "processingTimeMs": total_processing_time_ms,
            "transcriptionTimeMs": transcription_time_ms,
            "verificationTimeMs": verification_time_ms,
            "recordingDurationMs": recording_duration_ms,
            "speechStartTimeMs": speech_start_ms,
            "speechEndTimeMs": speech_end_ms
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        return {
            "success": False,
            "error": f"Transcription failed: {str(e)}"
        }
    finally:
        # Clean up temporary file
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                print(f"Removed temporary audio file: {temp_filepath}")
            except Exception as cleanup_err:
                print(f"Failed to delete temporary audio file {temp_filepath}: {cleanup_err}")

@router.post("/transcribe/reset", tags=["Transcription Operations"])
async def reset_session_attempts(sessionId: str = Form(None)):
    """
    Endpoint to explicitly reset the verification attempts counter for a session.
    """
    if not sessionId or not sessionId.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing session ID."
        )
    attempt_tracker.reset_session(sessionId)
    return {
        "success": True,
        "message": f"Attempts counter reset for session {sessionId}"
    }

