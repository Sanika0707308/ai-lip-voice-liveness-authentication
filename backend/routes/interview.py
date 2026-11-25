import os
import json
import uuid
import tempfile
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, status, Depends, Request

from backend.config import settings
from backend.services.whisper_service import whisper_service
from backend.services.mongodb_service import mongodb_service
from backend.services.interview_service import interview_service, QUESTION_BANK
from backend.services.verification_service import normalize_language_code

router = APIRouter(prefix="/interview", tags=["Interview Operations"])
logger = logging.getLogger("interview_route")

# In-memory interview session manager (tracks active question index and configuration per session)
_interview_sessions: Dict[str, Dict[str, Any]] = {}

def _get_or_create_interview_state(
    session_id: str,
    role: str = "python",
    experience_level: str = "fresher",
    language: str = "en",
    candidate_name: str = "Candidate",
    candidate_email: str = "candidate@example.com",
    skills: str = "",
    custom_role: str = "",
    question_count: int = 5
) -> Dict[str, Any]:
    if session_id not in _interview_sessions:
        effective_role = custom_role.strip() if custom_role and custom_role.strip() else role
        questions = interview_service.get_questions_for_session(
            role=effective_role,
            experience_level=experience_level,
            skills=skills,
            custom_role=custom_role,
            question_count=question_count
        )
        _interview_sessions[session_id] = {
            "sessionId": session_id,
            "role": effective_role,
            "rawRole": role,
            "experienceLevel": experience_level,
            "language": language,
            "candidateName": candidate_name,
            "candidateEmail": candidate_email,
            "skills": skills,
            "customRole": custom_role,
            "currentIndex": 0,
            "questions": questions,
            "questionCount": question_count,
            "answers": {},
            "startTime": datetime.now(timezone.utc).isoformat(),
            "isCompleted": False
        }
    return _interview_sessions[session_id]

@router.post("/init")
async def init_interview(
    sessionId: str = Form(...),
    role: str = Form("python"),
    experienceLevel: str = Form("fresher"),
    language: str = Form("en"),
    candidateName: Optional[str] = Form("Candidate"),
    candidateEmail: Optional[str] = Form("candidate@example.com"),
    skills: Optional[str] = Form(""),
    customRole: Optional[str] = Form(""),
    questionCount: Optional[int] = Form(5)
):
    """
    Initializes a role-based AI interview session with curated or dynamically generated questions.
    """
    if not sessionId or not sessionId.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing sessionId.")

    # Register in memory and update monitoring session record in database
    effective_qcount = int(questionCount) if questionCount and int(questionCount) > 0 else 5
    state = _get_or_create_interview_state(
        session_id=sessionId,
        role=role,
        experience_level=experienceLevel,
        language=normalize_language_code(language),
        candidate_name=candidateName,
        candidate_email=candidateEmail,
        skills=skills or "",
        custom_role=customRole or "",
        question_count=effective_qcount
    )

    # Persist session metadata into MongoDB / JSONL fallback
    mongodb_service.start_monitoring_session({
        "sessionId": sessionId,
        "candidateName": candidateName,
        "candidateId": candidateEmail,
        "candidateEmail": candidateEmail,
        "interviewRole": state["role"],
        "examId": state["role"],
        "experienceLevel": experienceLevel,
        "language": normalize_language_code(language),
        "skills": skills or "",
        "customRole": customRole or "",
        "startTime": state["startTime"]
    })

    # Prepare client-facing question list (scrub internal scoring keywords)
    sanitized_questions = []
    for idx, q in enumerate(state["questions"]):
        sanitized_questions.append({
            "index": idx,
            "id": q["id"],
            "category": q["category"],
            "question": q["question"],
            "timeLimit": q.get("time_limit", 120)
        })

    first_question = sanitized_questions[0] if sanitized_questions else None

    logger.info(f"Initialized interview for session {sessionId} (Role={state['role']}, Level={experienceLevel}, Questions={len(sanitized_questions)})")

    return {
        "success": True,
        "sessionId": sessionId,
        "interview_id": sessionId,
        "role": state["role"],
        "experienceLevel": experienceLevel,
        "skills": skills or "",
        "totalQuestions": len(sanitized_questions),
        "currentIndex": 0,
        "currentQuestion": first_question,
        "question": first_question["question"] if first_question else "",
        "questions": sanitized_questions
    }

@router.post("/start")
async def start_interview_endpoint(
    request: Request = None,
    sessionId: Optional[str] = Form(None),
    role: Optional[str] = Form(None),
    experienceLevel: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    candidateName: Optional[str] = Form(None),
    candidateEmail: Optional[str] = Form(None),
    skills: Optional[str] = Form(None),
    customRole: Optional[str] = Form(None)
):
    """
    Unified start interview endpoint supporting JSON body and form submissions.
    """
    resolved_session_id = sessionId
    resolved_role = role or "python"
    resolved_level = experienceLevel or "fresher"
    resolved_lang = language or "en"
    resolved_name = candidateName or "Candidate"
    resolved_email = candidateEmail or "candidate@example.com"
    resolved_skills = skills or ""
    resolved_custom_role = customRole or ""

    if request:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.json()
                resolved_session_id = body.get("session_id") or body.get("sessionId") or resolved_session_id
                resolved_role = body.get("role") or resolved_role
                resolved_level = body.get("difficulty") or body.get("experienceLevel") or resolved_level
                resolved_lang = body.get("language") or resolved_lang
                resolved_skills = body.get("skills") or resolved_skills
            except Exception:
                pass

    if not resolved_session_id:
        resolved_session_id = f"sess-{uuid.uuid4().hex[:10]}"

    state = _get_or_create_interview_state(
        session_id=resolved_session_id,
        role=resolved_role,
        experience_level=resolved_level,
        language=normalize_language_code(resolved_lang),
        candidate_name=resolved_name,
        candidate_email=resolved_email,
        skills=resolved_skills,
        custom_role=resolved_custom_role
    )

    mongodb_service.start_monitoring_session({
        "sessionId": resolved_session_id,
        "candidateName": resolved_name,
        "candidateId": resolved_email,
        "candidateEmail": resolved_email,
        "interviewRole": state["role"],
        "examId": state["role"],
        "experienceLevel": resolved_level,
        "language": normalize_language_code(resolved_lang),
        "skills": resolved_skills,
        "customRole": resolved_custom_role,
        "startTime": state["startTime"]
    })

    sanitized_questions = []
    for idx, q in enumerate(state["questions"]):
        sanitized_questions.append({
            "index": idx,
            "id": q["id"],
            "category": q["category"],
            "question": q["question"],
            "timeLimit": q.get("time_limit", 120)
        })

    first_question = sanitized_questions[0] if sanitized_questions else None

    return {
        "success": True,
        "sessionId": resolved_session_id,
        "interview_id": resolved_session_id,
        "role": state["role"],
        "experienceLevel": resolved_level,
        "totalQuestions": len(sanitized_questions),
        "currentIndex": 0,
        "currentQuestion": first_question,
        "question": first_question["question"] if first_question else "",
        "questions": sanitized_questions
    }

@router.get("/{sessionId}/state")
async def get_interview_state(sessionId: str):
    """
    Returns current interview state: active question index, answered count, and total.
    """
    if not sessionId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing sessionId.")

    # Check memory first, or reconstruct from database
    state = _interview_sessions.get(sessionId)
    if not state:
        session_doc = mongodb_service.get_session_by_id(sessionId)
        role = session_doc.get("interviewRole", "python") if session_doc else "python"
        level = session_doc.get("experienceLevel", "fresher") if session_doc else "fresher"
        lang = session_doc.get("language", "en") if session_doc else "en"
        state = _get_or_create_interview_state(sessionId, role=role, experience_level=level, language=lang)
        
        # Load existing answers from DB
        past_answers = mongodb_service.get_interview_answers(sessionId)
        state["currentIndex"] = len(past_answers)
        state["isCompleted"] = len(past_answers) >= len(state["questions"])

    current_idx = state["currentIndex"]
    questions = state["questions"]
    is_completed = current_idx >= len(questions) or state["isCompleted"]
    current_q = questions[current_idx] if not is_completed and current_idx < len(questions) else None

    # Check if session is expired due to monitoring warnings
    warning_count = mongodb_service.get_session_warning_count(sessionId)
    is_expired = warning_count >= settings.MAX_MONITORING_WARNINGS

    return {
        "success": True,
        "sessionId": sessionId,
        "role": state["role"],
        "experienceLevel": state["experienceLevel"],
        "currentIndex": current_idx,
        "totalQuestions": len(questions),
        "isCompleted": is_completed,
        "isExpired": is_expired,
        "warningCount": warning_count,
        "maxWarnings": settings.MAX_MONITORING_WARNINGS,
        "currentQuestion": {
            "index": current_idx,
            "id": current_q["id"],
            "category": current_q["category"],
            "question": current_q["question"],
            "timeLimit": current_q.get("time_limit", 120)
        } if current_q else None
    }


@router.post("/transcribe-answer")
async def transcribe_interview_audio(
    audio: UploadFile = File(...),
    language: Optional[str] = Form("en")
):
    """
    Direct endpoint to transcribe recorded candidate voice answer using Whisper.
    """
    tmp_path = os.path.join(tempfile.gettempdir(), f"ans_{uuid.uuid4().hex[:10]}.webm")
    try:
        audio.file.seek(0, os.SEEK_END)
        size = audio.file.tell()
        audio.file.seek(0)
        if size == 0:
            return {"success": False, "text": "", "error": "Empty audio recording."}

        with open(tmp_path, "wb") as f:
            import shutil
            shutil.copyfileobj(audio.file, f)

        res = whisper_service.transcribe_audio(tmp_path, language=language or "en")
        text = (res.get("text") or "").strip()
        confidence = res.get("confidence", 0.0)
        duration = res.get("duration", 0.0)
        return {
            "success": True,
            "text": text,
            "confidence": confidence,
            "duration": duration,
            "language": language or "en"
        }
    except Exception as ex:
        logger.error(f"Whisper transcription error: {ex}")
        return {"success": False, "text": "", "error": str(ex)}
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


@router.post("/{sessionId}/submit-answer")
async def submit_answer(
    sessionId: str,
    questionIndex: int = Form(...),
    questionId: str = Form(...),
    audioFile: Optional[UploadFile] = File(None),
    transcriptText: Optional[str] = Form(None),
    language: Optional[str] = Form("en")
):
    """
    Receives candidate's voice answer, transcribes it via Faster-Whisper,
    evaluates it against the question rubric, stores it, and advances to the next question.
    """
    if not sessionId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing sessionId.")

    # Check if session has expired due to warnings
    warning_count = mongodb_service.get_session_warning_count(sessionId)
    if warning_count >= settings.MAX_MONITORING_WARNINGS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Interview session has expired due to maximum integrity warnings."
        )

    state = _interview_sessions.get(sessionId)
    if not state:
        session_doc = mongodb_service.get_session_by_id(sessionId)
        role = session_doc.get("interviewRole", "python") if session_doc else "python"
        level = session_doc.get("experienceLevel", "fresher") if session_doc else "fresher"
        state = _get_or_create_interview_state(sessionId, role=role, experience_level=level)

    questions = state["questions"]
    if questionIndex < 0 or questionIndex >= len(questions):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid question index {questionIndex}.")

    question_data = questions[questionIndex]

    # 1. Transcribe voice audio using Faster-Whisper
    recognized_transcript = (transcriptText or "").strip()
    transcription_confidence = 0.90

    if audioFile:
        suffix = os.path.splitext(audioFile.filename)[1] or ".webm"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
            try:
                content = await audioFile.read()
                tmp.write(content)
                tmp.flush()

                # Transcribe with Whisper
                lang_code = normalize_language_code(language or state.get("language", "en"))
                whisper_res = whisper_service.transcribe_audio_file(tmp_path, language=lang_code)
                transcribed_text = whisper_res.get("transcription", "").strip()
                if transcribed_text:
                    recognized_transcript = transcribed_text
                    transcription_confidence = float(whisper_res.get("confidence", 0.90))
            except Exception as ex:
                logger.error(f"Whisper transcription failed during answer evaluation: {ex}")
            finally:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

    # 2. Evaluate answer against rubric and expected concepts
    eval_result = interview_service.evaluate_answer(
        question_data=question_data,
        candidate_transcript=recognized_transcript,
        role=state["role"],
        experience_level=state["experienceLevel"]
    )

    # 3. Build answer record
    answer_record = {
        "sessionId": sessionId,
        "questionIndex": questionIndex,
        "questionId": questionId,
        "category": question_data["category"],
        "questionText": question_data["question"],
        "candidateTranscript": recognized_transcript,
        "transcriptionConfidence": transcription_confidence,
        "evaluation": eval_result,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # 4. Persist to MongoDB / JSONL fallback
    mongodb_service.save_interview_answer(sessionId, answer_record)

    # Log event
    mongodb_service.log_monitoring_event({
        "sessionId": sessionId,
        "eventType": "INTERVIEW_ANSWER_SUBMITTED",
        "severity": "INFO",
        "details": f"Question {questionIndex + 1}/{len(questions)} evaluated. Score: {eval_result['score']}/10.",
        "metadata": {
            "questionId": questionId,
            "score": eval_result["score"],
            "category": question_data["category"]
        }
    })

    # Advance index
    next_index = questionIndex + 1
    state["currentIndex"] = next_index
    state["answers"][questionIndex] = answer_record

    is_all_completed = next_index >= len(questions)
    state["isCompleted"] = is_all_completed

    next_question = None
    if not is_all_completed:
        nq = questions[next_index]
        next_question = {
            "index": next_index,
            "id": nq["id"],
            "category": nq["category"],
            "question": nq["question"],
            "timeLimit": nq.get("time_limit", 120)
        }
    else:
        # Mark session as completed in database
        mongodb_service.stop_monitoring_session(sessionId, {
            "status": "COMPLETED",
            "stopReason": "Candidate completed all interview questions."
        })

    logger.info(f"Evaluated Q{questionIndex + 1} for session {sessionId}: Score={eval_result['score']}/10 | NextQ={next_index if not is_all_completed else 'COMPLETE'}")

    return {
        "success": True,
        "sessionId": sessionId,
        "questionIndex": questionIndex,
        "transcript": recognized_transcript,
        "evaluation": eval_result,
        "nextIndex": next_index,
        "isCompleted": is_all_completed,
        "nextQuestion": next_question
    }

@router.get("/{sessionId}/results")
async def get_interview_results(sessionId: str):
    """
    Returns full interview assessment: all questions, candidate transcripts,
    question-wise feedback, scores, strengths, improvement areas, and overall score.
    """
    if not sessionId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing sessionId.")

    report = mongodb_service.get_session_report(sessionId)
    answers = mongodb_service.get_interview_answers(sessionId)

    overall_score = 0.0
    if answers:
        total_sc = sum(a.get("evaluation", {}).get("score", 0.0) for a in answers)
        overall_score = round(total_sc / len(answers), 1)

    # Get role and experience level for tailored recommendations
    state = _interview_sessions.get(sessionId)
    role = state.get("role", "Software Specialist") if state else (report.get("interviewRole") if report else "Software Specialist")
    level = state.get("experienceLevel", "fresher") if state else (report.get("experienceLevel") if report else "fresher")

    recommendations = interview_service.generate_recommendations(
        overall_score=overall_score,
        answers=answers,
        role=role,
        experience_level=level
    )

    if report:
        report["recommendations"] = recommendations

    return {
        "success": True,
        "sessionId": sessionId,
        "overallScore": overall_score,
        "percentage": int(round(overall_score * 10)),
        "totalAnswered": len(answers),
        "answers": answers,
        "report": report,
        "recommendations": recommendations
    }
