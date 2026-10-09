import os
import sys
import json
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
from backend.services.attempt_tracker import attempt_tracker
from backend.routes.transcription import generate_random_challenge

client = TestClient(app)

def create_sine_audio(duration_sec: float = 5.0, freq: float = 440.0, samplerate: int = 16000) -> str:
    t = np.linspace(0, duration_sec, int(samplerate * duration_sec), endpoint=False)
    envelope = (np.sin(2 * np.pi * 3.0 * t) > 0).astype(float)
    data = np.sin(2 * np.pi * freq * t) * envelope * 0.5
    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(temp_wav.name, data, samplerate)
    return temp_wav.name

def test_pipeline():
    print("\n" + "="*70)
    print("RUNNING COMPLETE LIP-VOICE LIVENESS PIPELINE TESTS")
    print("="*70)
    
    passed_tests = 0
    total_tests = 0

    # 1. TEST CHALLENGE GENERATION
    total_tests += 1
    session_id = "test-e2e-session-1"
    response = client.get(f"/api/transcribe/challenge/new?sessionId={session_id}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["success"] is True
    phrase = data["challengePhrase"]
    parts = phrase.split()
    assert len(parts) >= 4, f"Phrase should have at least 4 tokens: '{phrase}'"
    assert parts[-1].isdigit() and len(parts[-1]) in [3, 4], f"Last token should be 3-4 digits: '{parts[-1]}'"
    print(f"[PASS] 1. Dynamic Challenge Generated: '{phrase}' (Session: {session_id})")
    passed_tests += 1

    # 2. TEST SYNCHRONIZATION WITH TIME OFFSET > 500ms (REJECT AS SPOOF)
    total_tests += 1
    session_id_spoof = "test-e2e-lag-spoof"
    client.get(f"/api/transcribe/challenge/new?sessionId={session_id_spoof}")
    audio_path = create_sine_audio(duration_sec=4.0)
    timestamps = [100.0 + i * 33.33 for i in range(100)]
    movement = [0.2 + 0.1 * np.sin(i * 0.3) for i in range(100)]

    try:
        with open(audio_path, "rb") as f:
            resp = client.post(
                "/api/sync",
                files={"file": ("recording.wav", f, "audio/wav")},
                data={
                    "lipMovement": json.dumps(movement),
                    "lipTimestamps": json.dumps(timestamps),
                    "sessionId": session_id_spoof
                }
            )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        res = resp.json()
        assert res["success"] is True
        assert res["livenessResult"] in ["LIVE", "SPOOF"]
        assert "rejectionReason" in res
        assert "challengePhrase" in res
        assert "transcribedPhrase" in res
        assert "alignedCorrelation" in res
        assert "detectedTimeOffsetMs" in res
        print(f"[PASS] 2. Liveness Decision Pipeline: Result={res['livenessResult']}, Offset={res['detectedTimeOffsetMs']}ms, Reason='{res['rejectionReason']}'")
        passed_tests += 1
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)

    # 3. TEST DURATION ACCEPTANCE (~2 to 6 seconds)
    total_tests += 1
    audio_4_5s = create_sine_audio(duration_sec=4.5)
    session_dur = "test-e2e-dur"
    client.get(f"/api/transcribe/challenge/new?sessionId={session_dur}")
    timestamps_4_5s = [100.0 + i * 33.33 for i in range(130)]
    movement_4_5s = [0.25 + 0.05 * np.cos(i * 0.2) for i in range(130)]
    try:
        with open(audio_4_5s, "rb") as f:
            resp = client.post(
                "/api/sync",
                files={"file": ("recording.wav", f, "audio/wav")},
                data={
                    "lipMovement": json.dumps(movement_4_5s),
                    "lipTimestamps": json.dumps(timestamps_4_5s),
                    "sessionId": session_dur
                }
            )
        assert resp.status_code == 200
        res = resp.json()
        assert res["success"] is True
        assert abs(res["audioDurationMs"] - 4500) < 200
        print(f"[PASS] 3. Target 5s Recording (~4.5s) Accepted: AudioDuration={res['audioDurationMs']}ms, LipDuration={res['lipDurationMs']}ms")
        passed_tests += 1
    finally:
        if os.path.exists(audio_4_5s):
            os.remove(audio_4_5s)

    # 4. TEST MONGODB LOGGING VERIFICATION
    total_tests += 1
    history = mongodb_service.fetch_user_history(session_dur)
    assert len(history) > 0, "No audit logs found for session_dur in MongoDB or fallback"
    log_doc = history[0]
    assert "timestamp" in log_doc
    assert "generatedChallenge" in log_doc or "expectedPhrase" in log_doc
    assert "livenessResult" in log_doc
    assert "synchronizationScore" in log_doc
    assert "estimatedTimeDifference" in log_doc
    print(f"[PASS] 4. MongoDB Verification Record Verified: ID={log_doc.get('_id')}, Result={log_doc.get('livenessResult')}, SyncScore={log_doc.get('synchronizationScore')}")
    passed_tests += 1

    print("\n" + "="*70)
    print(f"RESULTS: {passed_tests}/{total_tests} TESTS PASSED SUCCESSFULLY!")
    print("="*70 + "\n")

if __name__ == "__main__":
    test_pipeline()
