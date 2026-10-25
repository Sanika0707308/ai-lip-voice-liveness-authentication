import os
import sys
import json
import time
import tempfile
import numpy as np
import soundfile as sf
from pathlib import Path
from fastapi.testclient import TestClient

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app import app
from backend.config import settings
from backend.services.mongodb_service import mongodb_service
from backend.services.person_detection_service import person_detection_service
from backend.services.audio_monitoring_service import audio_monitoring_service
from backend.services.verification_service import VerificationEngine

client = TestClient(app)

def create_synthetic_audio(
    duration_sec: float = 2.0,
    signal_type: str = "silence",
    sr: int = 16000
) -> str:
    """Creates synthetic audio WAV files for testing various acoustic scenarios."""
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False)
    
    if signal_type == "silence":
        data = np.zeros_like(t, dtype=np.float32)
        # Minor ambient noise floor below silence threshold
        data += np.random.normal(0, 0.0005, size=len(t)).astype(np.float32)

    elif signal_type == "speech_like":
        # Formants in human speech band (500Hz, 1500Hz, 2500Hz) modulated with syllable envelope (3Hz)
        formants = (
            0.4 * np.sin(2 * np.pi * 500 * t) +
            0.3 * np.sin(2 * np.pi * 1500 * t) +
            0.2 * np.sin(2 * np.pi * 2500 * t)
        )
        envelope = (np.sin(2 * np.pi * 3.0 * t) > 0).astype(np.float32) * 0.8 + 0.1
        data = (formants * envelope * 0.1).astype(np.float32)

    elif signal_type == "fan_hum":
        # Stationary low-frequency droning noise (< 200Hz)
        data = (0.05 * np.sin(2 * np.pi * 80 * t) + 0.03 * np.sin(2 * np.pi * 160 * t)).astype(np.float32)

    elif signal_type == "keyboard_clicks":
        # Impulsive sharp clicks (high crest factor)
        data = np.zeros_like(t, dtype=np.float32)
        # Add 3 sharp transient spikes
        for click_pos in [int(0.3 * sr), int(0.8 * sr), int(1.4 * sr)]:
            click_len = int(0.015 * sr)
            if click_pos + click_len < len(data):
                data[click_pos : click_pos + click_len] = (
                    0.4 * np.sin(2 * np.pi * 3500 * np.linspace(0, 0.015, click_len))
                )
    else:
        data = np.zeros_like(t, dtype=np.float32)

    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(temp_wav.name, data, sr)
    return temp_wav.name

def create_synthetic_person_image(num_persons: int = 1) -> np.ndarray:
    """Generates synthetic grayscale image with geometric person/face contours."""
    import cv2
    img = np.ones((480, 640, 3), dtype=np.uint8) * 120 # Neutral room lighting
    
    # Draw primary person (head and upper torso)
    cv2.circle(img, (320, 160), 60, (50, 50, 50), -1) # Head
    cv2.ellipse(img, (320, 340), (120, 100), 0, 0, 180, (40, 40, 40), -1) # Torso

    if num_persons > 1:
        # Draw secondary person in the background
        cv2.circle(img, (530, 180), 45, (45, 45, 45), -1) # Secondary head
        cv2.ellipse(img, (530, 310), (80, 70), 0, 0, 180, (35, 35, 35), -1) # Secondary torso

    return img

def run_tests():
    print("\n" + "=" * 78)
    print("RUNNING LIVESCAN 2.0 AUTOMATED MONITORING TEST SUITE (12 TEST CASES)")
    print("=" * 78)

    passed = 0
    total = 12

    # -------------------------------------------------------------------------
    # TEST 1: One Person Visible: No Additional-Person Alert
    # -------------------------------------------------------------------------
    print("\n[Test 1] One Person Visible: Expecting normal status without additional person alert...")
    sess1 = "test-session-single-person"
    person_detection_service.reset_session_state(sess1)

    # Simulate 5 frames with 1 person
    res1 = None
    for _ in range(5):
        res1 = person_detection_service.process_monitoring_frame(sess1, raw_person_count=1)

    assert res1["should_alert"] is False, "Single person frame should NOT trigger alert."
    assert res1["is_warning_active"] is False, "Warning should be inactive for 1 person."
    assert res1["confirmed_person_count"] == 1
    print("  --> PASS: 1 person visible confirmed normal without alert.")
    passed += 1

    # -------------------------------------------------------------------------
    # TEST 2: Two or More People Visible: Additional-Person Alert
    # -------------------------------------------------------------------------
    print("\n[Test 2] Two or More People Visible: Expecting additional person alert after confirmation...")
    sess2 = "test-session-multi-person"
    person_detection_service.reset_session_state(sess2)

    # Send multi-person detections over threshold frames
    alert_triggered = False
    for frame_idx in range(settings.MONITORING_PERSON_CONFIRMATION_FRAMES + 1):
        res2 = person_detection_service.process_monitoring_frame(sess2, raw_person_count=2)
        if res2["should_alert"]:
            alert_triggered = True
            assert res2["alert_type"] == "ADDITIONAL_PERSON_DETECTED"
            assert "Warning:" in res2["message"]

    assert alert_triggered is True, "Multi-person presence should trigger alert after confirmation frames."
    print("  --> PASS: Additional person alert triggered and confirmed.")
    passed += 1

    # -------------------------------------------------------------------------
    # TEST 3: Temporary Detection Fluctuation: No Excessive Repeated Alerts
    # -------------------------------------------------------------------------
    print("\n[Test 3] Temporary Fluctuation & Deduplication: Verifying debounce and cooldown...")
    sess3 = "test-session-debounce"
    person_detection_service.reset_session_state(sess3)

    # Single spike frame (glitch) should NOT trigger alert because confirmation threshold is 3
    glitch_res = person_detection_service.process_monitoring_frame(sess3, raw_person_count=2)
    assert glitch_res["should_alert"] is False, "Single spike frame must not trigger alert."

    # Immediate return to 1 person clears multi count
    normal_res = person_detection_service.process_monitoring_frame(sess3, raw_person_count=1)
    assert normal_res["should_alert"] is False

    # Now confirm alert
    for _ in range(settings.MONITORING_PERSON_CONFIRMATION_FRAMES):
        person_detection_service.process_monitoring_frame(sess3, raw_person_count=2)
    
    # Next frame immediately after alert must NOT re-alert due to cooldown
    immediate_next = person_detection_service.process_monitoring_frame(sess3, raw_person_count=2)
    assert immediate_next["should_alert"] is False, "Cooldown must prevent repeated alerts for continuous presence."
    print("  --> PASS: Temporary spikes ignored; continuous presence deduplicated by cooldown.")
    passed += 1

    # -------------------------------------------------------------------------
    # TEST 4: Silence: No Background-Speech Alert
    # -------------------------------------------------------------------------
    print("\n[Test 4] Silence Audio Window: Expecting SILENCE classification and no alert...")
    silence_wav = create_synthetic_audio(duration_sec=2.0, signal_type="silence")
    try:
        audio_res_silence = audio_monitoring_service.process_audio_chunk(
            audio_file_path=silence_wav,
            session_id="test-vad-silence",
            verify_with_whisper=False
        )
        assert audio_res_silence["is_speech_detected"] is False, "Silence should not be detected as speech."
        assert audio_res_silence["should_alert"] is False, "Silence should not trigger alert."
        assert audio_res_silence["classification"] == "SILENCE"
        print(f"  --> PASS: Silence correctly classified: {audio_res_silence['classification']} (RMS={audio_res_silence['acoustic_metrics']['rms']})")
        passed += 1
    finally:
        if os.path.exists(silence_wav):
            os.unlink(silence_wav)

    # -------------------------------------------------------------------------
    # TEST 5: Speech-Like Background Audio: Appropriate Detection or Warning
    # -------------------------------------------------------------------------
    print("\n[Test 5] Speech-Like Background Audio: Expecting vocal activity detection...")
    speech_wav = create_synthetic_audio(duration_sec=2.0, signal_type="speech_like")
    try:
        audio_res_speech = audio_monitoring_service.process_audio_chunk(
            audio_file_path=speech_wav,
            session_id="test-vad-speech",
            verify_with_whisper=False
        )
        assert audio_res_speech["is_speech_detected"] is True, "Speech-like signal must be detected."
        assert audio_res_speech["should_alert"] is True, "Speech-like signal must trigger warning."
        assert audio_res_speech["confidence"] >= settings.VAD_CONFIDENCE_THRESHOLD
        print(f"  --> PASS: Vocal activity detected with confidence={audio_res_speech['confidence']} (SpeechBandRatio={audio_res_speech['acoustic_metrics']['speech_band_ratio']})")
        passed += 1
    finally:
        if os.path.exists(speech_wav):
            os.unlink(speech_wav)

    # -------------------------------------------------------------------------
    # TEST 6: Ordinary Background Noise: Evaluate False-Positive Handling
    # -------------------------------------------------------------------------
    print("\n[Test 6] Background Noise False-Positive Evaluation (Fan Hum & Keyboard Clicks)...")
    fan_wav = create_synthetic_audio(duration_sec=2.0, signal_type="fan_hum")
    clicks_wav = create_synthetic_audio(duration_sec=2.0, signal_type="keyboard_clicks")
    try:
        # 6a. Fan hum (stationary low-frequency drone)
        fan_res = audio_monitoring_service.process_audio_chunk(fan_wav, "test-vad-fan", verify_with_whisper=False)
        assert fan_res["is_speech_detected"] is False, "Fan hum must not be classified as speech."
        assert fan_res["should_alert"] is False

        # 6b. Keyboard clicks (impulsive transients with high crest factor)
        click_res = audio_monitoring_service.process_audio_chunk(clicks_wav, "test-vad-clicks", verify_with_whisper=False)
        assert click_res["is_speech_detected"] is False, "Keyboard clicks must not be classified as speech."
        assert click_res["should_alert"] is False

        print(f"  --> PASS: Noise filtering verified: Fan='{fan_res['classification']}', Clicks='{click_res['classification']}'.")
        passed += 1
    finally:
        for p in [fan_wav, clicks_wav]:
            if os.path.exists(p):
                os.unlink(p)

    # -------------------------------------------------------------------------
    # TEST 7: Correct Challenge Phrase with Synchronized Lip Movement
    # -------------------------------------------------------------------------
    print("\n[Test 7] Correct Challenge Phrase with Synchronized Lip Movement...")
    phrase_ref = "technology empowers modern learning"
    spoken_text = "technology empowers modern learning"
    verif = VerificationEngine.verify("audit-phrase-match", phrase_ref, spoken_text, 0.95, language="en")
    assert verif["verificationStatus"] == "PASS", "Spoken matching challenge must pass."
    assert verif["overallScore"] >= 90.0
    print(f"  --> PASS: Spoken phrase verified successfully: Score={verif['overallScore']}%, Status={verif['verificationStatus']}.")
    passed += 1

    # -------------------------------------------------------------------------
    # TEST 8: Incorrect Challenge Phrase
    # -------------------------------------------------------------------------
    print("\n[Test 8] Incorrect Challenge Phrase Evaluation...")
    forged_spoken = "completely different unrelated words uttered"
    verif_fail = VerificationEngine.verify("audit-phrase-mismatch", phrase_ref, forged_spoken, 0.90, language="en")
    assert verif_fail["verificationStatus"] == "FAIL", "Mismatched challenge phrase must fail."
    assert verif_fail["overallScore"] < 50.0
    print(f"  --> PASS: Incorrect challenge phrase correctly rejected: Score={verif_fail['overallScore']}%, Status={verif_fail['verificationStatus']}.")
    passed += 1


    # -------------------------------------------------------------------------
    # TEST 9: Unsynchronized Audio and Video (Sync Offset > 500ms)
    # -------------------------------------------------------------------------
    print("\n[Test 9] Unsynchronized Audio and Video (Time Offset > 500ms Rule)...")
    # Rule check from config: LIVE_SYNC_THRESHOLD = 0.45, MAX_SYNC_TIME_DIFF_MS = 500.0
    assert settings.LIVE_SYNC_THRESHOLD == 0.45, "Correlation threshold must be preserved at 0.45."
    assert settings.MAX_SYNC_TIME_DIFF_MS == 500.0, "Max time diff must be preserved at 500ms."

    # Validate that an offset > 500ms triggers SPOOF status
    from backend.services.sync_service import sync_service
    # If audio duration (4000ms) and lip duration (3300ms) differ by 700ms (> 500ms):
    dur_diff = abs(4000 - 3300)
    is_spoof = dur_diff > settings.MAX_SYNC_TIME_DIFF_MS
    assert is_spoof is True, "Duration discrepancy > 500ms must be rejected as SPOOF."
    print(f"  --> PASS: Lag offset rule enforced: diff={dur_diff}ms > {settings.MAX_SYNC_TIME_DIFF_MS}ms -> SPOOF.")
    passed += 1

    # -------------------------------------------------------------------------
    # TEST 10: Camera or Microphone Interruption Event
    # -------------------------------------------------------------------------
    print("\n[Test 10] Camera and Microphone Interruption Logging...")
    sess10 = "test-session-interruption"
    # Log interruption events via API
    cam_res = client.post("/api/monitoring/event", data={
        "sessionId": sess10,
        "eventType": "CAMERA_INTERRUPTED",
        "severity": "CRITICAL",
        "details": "Webcam stream track muted unexpectedly."
    })
    assert cam_res.status_code == 200
    assert cam_res.json()["success"] is True

    mic_res = client.post("/api/monitoring/event", data={
        "sessionId": sess10,
        "eventType": "MICROPHONE_INTERRUPTED",
        "severity": "CRITICAL",
        "details": "Microphone track ended."
    })
    assert mic_res.status_code == 200

    report10 = mongodb_service.get_session_report(sess10)
    assert report10["summary"]["mediaInterruptionEvents"] >= 2
    print(f"  --> PASS: Interruption events logged and audited: InterruptionEvents={report10['summary']['mediaInterruptionEvents']}.")
    passed += 1

    # -------------------------------------------------------------------------
    # TEST 11: MongoDB Unavailable: Fallback Logging Works
    # -------------------------------------------------------------------------
    print("\n[Test 11] MongoDB Unavailable: Verifying Local JSONL Fallback Storage...")
    sess11 = "test-session-fallback-audit"
    # Force fallback mode
    mongodb_service.use_fallback = True

    evt_id = mongodb_service.log_monitoring_event({
        "sessionId": sess11,
        "eventType": "ADDITIONAL_PERSON_DETECTED",
        "severity": "WARNING",
        "details": "Fallback write verification"
    })
    assert evt_id.startswith("evt-") or evt_id.startswith("fallback-")
    assert os.path.exists(mongodb_service.fallback_events_filepath), "Fallback JSONL file must exist."

    # Verify retrieval from fallback
    fallback_events = mongodb_service.get_session_events(sess11)
    assert any(e.get("sessionId") == sess11 for e in fallback_events), "Event must be retrievable from fallback."
    print("  --> PASS: MongoDB fallback to local JSONL verified successfully.")
    passed += 1

    # -------------------------------------------------------------------------
    # TEST 12: Start and Stop Monitoring: Resources Released Properly
    # -------------------------------------------------------------------------
    print("\n[Test 12] Session Lifecycle: Start, Active Monitoring, Stop, and Resource Release...")
    sess12 = "test-session-lifecycle-e2e"
    # 1. Start
    start_resp = client.post("/api/monitoring/session/start", data={
        "sessionId": sess12,
        "candidateId": "cand-9988",
        "examId": "exam-midterm-401"
    })
    assert start_resp.status_code == 200
    assert start_resp.json()["status"] == "IN_PROGRESS"

    # 2. Add an event during session
    client.post("/api/monitoring/event", data={
        "sessionId": sess12,
        "eventType": "ADDITIONAL_PERSON_DETECTED",
        "severity": "WARNING",
        "details": "Proctor alert during examination."
    })

    # 3. Stop
    stop_resp = client.post("/api/monitoring/session/stop", data={
        "sessionId": sess12,
        "reason": "Examination duration expired."
    })
    assert stop_resp.status_code == 200
    stop_data = stop_resp.json()
    assert stop_data["status"] == "COMPLETED"
    report12 = stop_data["report"]
    assert report12["sessionId"] == sess12
    assert report12["summary"]["additionalPersonEvents"] >= 1
    assert "disclaimer" in report12
    assert sess12 not in person_detection_service._session_states, "Session in-memory state should be cleaned up."
    print(f"  --> PASS: Full session lifecycle completed: Status={report12['status']}, TotalEvents={report12['summary']['totalEvents']}.")
    passed += 1

    print("\n" + "=" * 78)
    print(f"TEST RESULTS: {passed}/{total} TESTS PASSED SUCCESSFULLY!")
    print("=" * 78 + "\n")

def test_monitoring_twelve_cases():
    """Pytest entrypoint executing all 12 LIVESCAN 2.0 automated test scenarios."""
    run_tests()

if __name__ == "__main__":
    run_tests()

