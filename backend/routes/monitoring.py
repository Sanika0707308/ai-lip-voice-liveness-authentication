import os
import json
import tempfile
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, status, Query
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.services.mongodb_service import mongodb_service
from backend.services.person_detection_service import person_detection_service
from backend.services.audio_monitoring_service import audio_monitoring_service

router = APIRouter(prefix="/monitoring", tags=["Monitoring Operations"])
logger = logging.getLogger("monitoring_route")

@router.post("/session/start")
async def start_session(
    sessionId: str = Form(...),
    candidateId: Optional[str] = Form(None),
    examId: Optional[str] = Form(None),
    language: Optional[str] = Form("en")
):
    """
    Initializes a new online examination / interview monitoring session.
    """
    if not sessionId or not sessionId.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required sessionId parameter."
        )

    # Reset any state from previous run with this session ID
    person_detection_service.reset_session_state(sessionId)
    audio_monitoring_service.reset_session(sessionId)

    session_data = {
        "sessionId": sessionId,
        "candidateId": candidateId or "anonymous_candidate",
        "examId": examId or "interview_session_01",
        "language": language or "en",
        "startTime": datetime.now(timezone.utc).isoformat(),
        "status": "IN_PROGRESS"
    }

    mongodb_service.start_monitoring_session(session_data)
    
    # Log session started event
    mongodb_service.log_monitoring_event({
        "sessionId": sessionId,
        "eventType": "SESSION_STARTED",
        "severity": "INFO",
        "details": f"Monitoring session initiated for candidate {session_data['candidateId']}."
    })

    return {
        "success": True,
        "sessionId": sessionId,
        "status": "IN_PROGRESS",
        "message": "Monitoring session initialized successfully."
    }

@router.post("/session/stop")
async def stop_session(
    sessionId: str = Form(...),
    reason: Optional[str] = Form("User ended monitoring session")
):
    """
    Terminates monitoring session and compiles a full session audit report.
    """
    if not sessionId or not sessionId.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing sessionId.")

    # Log session stopped event
    mongodb_service.log_monitoring_event({
        "sessionId": sessionId,
        "eventType": "SESSION_COMPLETED",
        "severity": "INFO",
        "details": reason
    })

    mongodb_service.stop_monitoring_session(sessionId, {"stopReason": reason})
    report = mongodb_service.get_session_report(sessionId)

    # Cleanup in-memory tracking
    person_detection_service.reset_session_state(sessionId)
    audio_monitoring_service.reset_session(sessionId)

    return {
        "success": True,
        "sessionId": sessionId,
        "status": "COMPLETED",
        "report": report
    }

@router.get("/session/{sessionId}/report")
async def get_report(sessionId: str):
    """
    Fetches comprehensive audit report for a given session.
    """
    if not sessionId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing sessionId.")
    
    report = mongodb_service.get_session_report(sessionId)
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
        
    return {
        "success": True,
        "report": report
    }

@router.get("/sessions")
async def list_sessions(limit: int = Query(25, ge=1, le=100)):
    """
    Retrieves recent monitoring sessions for dashboard history.
    """
    sessions = mongodb_service.get_monitoring_sessions(limit=limit)
    return {
        "success": True,
        "sessions": sessions
    }

@router.post("/event")
async def log_event(
    sessionId: str = Form(...),
    eventType: str = Form(...),
    severity: Optional[str] = Form("INFO"),
    details: Optional[str] = Form(""),
    metadata: Optional[str] = Form(None)
):
    """
    Logs an explicit monitoring event (e.g. CAMERA_INTERRUPTED, MICROPHONE_INTERRUPTED).
    """
    if not sessionId or not eventType:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing sessionId or eventType.")

    parsed_metadata = {}
    if metadata:
        try:
            parsed_metadata = json.loads(metadata)
        except Exception:
            parsed_metadata = {"raw": metadata}

    event_data = {
        "sessionId": sessionId,
        "eventType": eventType,
        "severity": severity or "INFO",
        "details": details,
        "metadata": parsed_metadata,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    event_id = mongodb_service.log_monitoring_event(event_data)
    warning_count = mongodb_service.get_session_warning_count(sessionId)
    is_expired = warning_count >= settings.MAX_MONITORING_WARNINGS
    if is_expired and (severity or "INFO") == "WARNING":
        mongodb_service.stop_monitoring_session(sessionId, {
            "status": "TERMINATED_EXCESSIVE_WARNINGS",
            "stopReason": f"Maximum integrity warnings ({settings.MAX_MONITORING_WARNINGS}/{settings.MAX_MONITORING_WARNINGS}) exceeded."
        })

    return {
        "success": True,
        "eventId": event_id,
        "sessionId": sessionId,
        "eventType": eventType,
        "warningCount": warning_count,
        "maxWarnings": settings.MAX_MONITORING_WARNINGS,
        "isSessionExpired": is_expired
    }

@router.post("/detect-persons")
async def detect_persons(
    sessionId: str = Form(...),
    file: UploadFile = File(None),
    clientPersonCount: Optional[int] = Form(None)
):
    """
    Analyzes a video frame for multiple individuals.
    Can accept an image frame file or client-side detection telemetry.
    Applies temporal debouncing, lighting adaptation, and auto-logging.
    """
    if not sessionId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing sessionId.")

    person_count = 1
    detections = []
    faces = []
    lighting_cond = "NORMAL"
    frame_w = 640
    frame_h = 480

    if file:
        try:
            contents = await file.read()
            raw_res = person_detection_service.detect_persons_in_image(contents)
            person_count = raw_res["person_count"]
            detections = raw_res["detections"]
            faces = raw_res.get("faces", [])
            lighting_cond = raw_res["lighting_condition"]
            frame_w = raw_res.get("frame_width", 640)
            frame_h = raw_res.get("frame_height", 480)
        except Exception as e:
            logger.error(f"Error analyzing image frame for persons: {e}")
            person_count = clientPersonCount if clientPersonCount is not None else 1
    elif clientPersonCount is not None:
        person_count = int(clientPersonCount)

    # Process through temporal debouncer
    debounce_res = person_detection_service.process_monitoring_frame(
        session_id=sessionId,
        raw_person_count=person_count,
        detections=detections
    )

    # Auto-log events to MongoDB / fallback if debouncer triggers
    if debounce_res["should_alert"]:
        alert_event_type = debounce_res.get("alert_type") or "ADDITIONAL_PERSON_DETECTED"
        mongodb_service.log_monitoring_event({
            "sessionId": sessionId,
            "eventType": alert_event_type,
            "severity": "WARNING",
            "details": debounce_res["message"],
            "metadata": {
                "detectedCount": person_count,
                "faceCount": len(faces),
                "lightingCondition": lighting_cond,
                "detections": detections,
                "faces": faces
            }
        })
    elif debounce_res["should_clear"]:
        mongodb_service.log_monitoring_event({
            "sessionId": sessionId,
            "eventType": "PERSON_CLEARED",
            "severity": "INFO",
            "details": debounce_res["message"],
            "metadata": {"detectedCount": person_count, "faceCount": len(faces)}
        })

    warning_count = mongodb_service.get_session_warning_count(sessionId)
    is_expired = warning_count >= settings.MAX_MONITORING_WARNINGS
    if is_expired:
        mongodb_service.stop_monitoring_session(sessionId, {
            "status": "TERMINATED_EXCESSIVE_WARNINGS",
            "stopReason": f"Maximum integrity warnings ({settings.MAX_MONITORING_WARNINGS}/{settings.MAX_MONITORING_WARNINGS}) exceeded."
        })

    logger.info(
        f"[MONITORING: FACE/PERSON] Session={sessionId} | RawCount={person_count} | Faces={len(faces)} | "
        f"FaceStatus={debounce_res['face_status']} | Paused={debounce_res['interview_paused']} | "
        f"ConfirmedCount={debounce_res['confirmed_person_count']} | Lighting={lighting_cond} | "
        f"ShouldAlert={debounce_res['should_alert']} | WarningActive={debounce_res['is_warning_active']} | "
        f"TotalSessionWarnings={warning_count}/{settings.MAX_MONITORING_WARNINGS} | Expired={is_expired}"
    )

    return {
        "success": True,
        "sessionId": sessionId,
        "personCount": person_count,
        "faceCount": len(faces),
        "faceStatus": debounce_res.get("face_status", "NORMAL"),
        "interviewPaused": debounce_res.get("interview_paused", False),
        "graceSecondsRemaining": debounce_res.get("grace_seconds_remaining", 0.0),
        "isWarningActive": debounce_res["is_warning_active"],
        "shouldAlert": debounce_res["should_alert"],
        "shouldClear": debounce_res["should_clear"],
        "alertMessage": debounce_res["message"],
        "alertType": debounce_res.get("alert_type"),
        "lightingCondition": lighting_cond,
        "detections": detections,
        "faces": faces,
        "frameWidth": frame_w,
        "frameHeight": frame_h,
        "warningCount": warning_count,
        "maxWarnings": settings.MAX_MONITORING_WARNINGS,
        "isSessionExpired": is_expired
    }

@router.post("/audio-chunk")
async def analyze_audio_chunk(
    sessionId: str = Form(...),
    file: UploadFile = File(...),
    language: Optional[str] = Form("en"),
    verifyWithWhisper: Optional[bool] = Form(True),
    candidateSpeaking: Optional[str] = Form(None)
):
    """
    Analyzes an audio window for background speech activity.
    Distinguishes potential vocal activity from silence and non-vocal noise.
    """
    if not sessionId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing sessionId.")

    # Save uploaded chunk to temp file
    suffix = ".wav" if file.filename and file.filename.endswith(".wav") else ".webm"
    temp_audio = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        content = await file.read()
        temp_audio.write(content)
        temp_audio.close()

        is_cand_speaking = None
        if candidateSpeaking is not None:
            is_cand_speaking = str(candidateSpeaking).lower() in ("true", "1", "yes")

        analysis_res = audio_monitoring_service.process_audio_chunk(
            audio_file_path=temp_audio.name,
            session_id=sessionId,
            language=language or "en",
            verify_with_whisper=bool(verifyWithWhisper),
            candidate_is_speaking=is_cand_speaking
        )

        # If speech confirmed and debouncer allows alert, log event
        if analysis_res.get("should_alert"):
            mongodb_service.log_monitoring_event({
                "sessionId": sessionId,
                "eventType": "POSSIBLE_BACKGROUND_SPEECH",
                "severity": "WARNING",
                "details": "Possible background speech detected in acoustic environment.",
                "metadata": {
                    "confidence": analysis_res.get("confidence"),
                    "acousticMetrics": analysis_res.get("acoustic_metrics"),
                    "transcriptionSnippet": analysis_res.get("transcription_snippet")
                }
            })

        warning_count = mongodb_service.get_session_warning_count(sessionId)
        is_expired = warning_count >= settings.MAX_MONITORING_WARNINGS
        if is_expired:
            mongodb_service.stop_monitoring_session(sessionId, {
                "status": "TERMINATED_EXCESSIVE_WARNINGS",
                "stopReason": f"Maximum integrity warnings ({settings.MAX_MONITORING_WARNINGS}/{settings.MAX_MONITORING_WARNINGS}) exceeded."
            })

        analysis_res["warningCount"] = warning_count
        analysis_res["maxWarnings"] = settings.MAX_MONITORING_WARNINGS
        analysis_res["isSessionExpired"] = is_expired

        logger.info(
            f"[MONITORING: AUDIO/SPEECH] Session={sessionId} | IsSpeech={analysis_res.get('is_speech_detected')} | "
            f"Classification={analysis_res.get('classification')} | Conf={analysis_res.get('confidence')} | "
            f"Snippet='{analysis_res.get('transcription_snippet')}' | ShouldAlert={analysis_res.get('should_alert')} | "
            f"InCooldown={analysis_res.get('in_cooldown')} | TotalSessionWarnings={warning_count}/{settings.MAX_MONITORING_WARNINGS} | "
            f"Expired={is_expired}"
        )

        return analysis_res
    finally:
        if os.path.exists(temp_audio.name):
            try:
                os.unlink(temp_audio.name)
            except OSError:
                pass

@router.get("/session/{sessionId}/status")
async def get_session_status(sessionId: str):
    """
    Returns current monitoring status, warning count, attempt limit, and session expiry state.
    """
    if not sessionId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing sessionId.")

    warning_count = mongodb_service.get_session_warning_count(sessionId)
    is_expired = warning_count >= settings.MAX_MONITORING_WARNINGS
    return {
        "success": True,
        "sessionId": sessionId,
        "warningCount": warning_count,
        "maxWarnings": settings.MAX_MONITORING_WARNINGS,
        "isSessionExpired": is_expired,
        "status": "TERMINATED_EXCESSIVE_WARNINGS" if is_expired else "IN_PROGRESS"
    }
