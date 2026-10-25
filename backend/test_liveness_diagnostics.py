"""
Comprehensive diagnostic test suite for Lip-Voice Liveness Verification.
Validates all 5 conditions specified in User Requirements:
1. Genuine speech (displayed phrase + synchronized lip movement) -> LIVE
2. Incorrect phrases (different phrase spoken) -> SPOOF
3. No lip movement (static face photo attack) -> SPOOF
4. Silent audio / video (no speech detected) -> SPOOF
5. Unsynchronized audio/video (lag offset > 500ms or correlation < 0.45) -> SPOOF
"""

import io
import json
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
import os
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app import app
from backend.config import settings
from backend.services.attempt_tracker import attempt_tracker
from backend.services.verification_service import VerificationEngine

client = TestClient(app)

def create_synthetic_audio(duration_sec: float = 4.0, sr: int = 16000, speech_pattern: bool = True) -> bytes:
    """Generates an in-memory mono WAV file."""
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False)
    if speech_pattern:
        # Multi-syllable speech envelope with distinct syllabic bursts at 1.0s, 1.8s, 2.6s
        env = (
            0.8 * np.exp(-0.5 * ((t - 1.0) / 0.15) ** 2) +
            0.9 * np.exp(-0.5 * ((t - 1.8) / 0.15) ** 2) +
            0.7 * np.exp(-0.5 * ((t - 2.6) / 0.15) ** 2)
        )
        carrier = (
            0.6 * np.sin(2 * np.pi * 220 * t) +
            0.3 * np.sin(2 * np.pi * 440 * t)
        )
        audio = env * carrier * 0.7
    else:
        # Silent audio
        audio = np.zeros_like(t)

    buf = io.BytesIO()
    sf.write(buf, audio.astype(np.float32), sr, format='WAV', subtype='PCM_16')
    return buf.getvalue()

def create_synthetic_lip_movement(
    num_frames: int = 120,
    dt_ms: float = 33.33,
    active: bool = True,
    lag_ms: float = 0.0
) -> tuple[list[float], list[float]]:
    """Generates synthetic normalized lip aperture ratio values and timestamps."""
    timestamps = [round(i * dt_ms) for i in range(num_frames)]
    if not active:
        # Static lip opening (e.g. photo attack)
        movement = [0.15 for _ in range(num_frames)]
    else:
        # Dynamic lip opening (LVD / LHD ratio matching syllabic bursts)
        lag_sec = lag_ms / 1000.0
        t = np.array([ts / 1000.0 - lag_sec for ts in timestamps])
        movement = [
            float(
                0.18 +
                0.08 * np.exp(-0.5 * ((ti - 1.0) / 0.15) ** 2) +
                0.09 * np.exp(-0.5 * ((ti - 1.8) / 0.15) ** 2) +
                0.07 * np.exp(-0.5 * ((ti - 2.6) / 0.15) ** 2)
            )
            for ti in t
        ]
    return movement, timestamps

def run_diagnostic_suite():
    print("\n" + "=" * 80)
    print("RUNNING COMPREHENSIVE LIVENESS VERIFICATION DIAGNOSTIC TEST SUITE")
    print("=" * 80)

    # 1. Genuine Speech with natural minor speaking variation
    # Expected: "artificial intelligence shapes future technology"
    # Transcribed: "artificial intelligence shape future technology" (minor singular/plural variation)
    print("\n--- TEST 1: Genuine Speech + Natural Variation + Synchronized Movement ---")
    session_id = "diag-genuine-live-1"
    challenge = "Artificial intelligence shapes future technology"
    attempt_tracker.set_challenge(session_id, challenge, language="en")

    audio_bytes = create_synthetic_audio(duration_sec=4.0, speech_pattern=True)
    lip_move, lip_ts = create_synthetic_lip_movement(num_frames=120, dt_ms=33.33, active=True, lag_ms=50.0)

    # Mock whisper to return natural speech transcription
    from unittest.mock import patch
    with patch("backend.services.whisper_service.whisper_service.transcribe_audio") as mock_transcribe:
        mock_transcribe.return_value = {
            "success": True,
            "text": "Artificial intelligence shape future technology",
            "confidence": 0.85
        }
        resp = client.post(
            "/api/sync",
            data={
                "lipMovement": json.dumps(lip_move),
                "lipTimestamps": json.dumps(lip_ts),
                "sessionId": session_id,
                "challengePhrase": challenge,
                "language": "en"
            },
            files={"file": ("speech.wav", audio_bytes, "audio/wav")}
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    print(f"Status: {data.get('livenessResult')} | Correlation: {data.get('alignedCorrelation')} | Reason: {data.get('rejectionReason')}")
    assert data["livenessResult"] == "LIVE", f"Expected LIVE, got {data['livenessResult']}"
    assert data["isSyncValid"] is True
    assert data["isChallengeMatch"] is True
    print("[PASS] Test 1: Genuine speech with natural variation verified as LIVE!")

    # 2. Incorrect Phrase Spoken
    print("\n--- TEST 2: Incorrect Phrase Spoken (Phrase Spoof) ---")
    session_id = "diag-wrong-phrase-2"
    challenge = "Artificial intelligence shapes future technology"
    attempt_tracker.set_challenge(session_id, challenge, language="en")

    lip_move, lip_ts = create_synthetic_lip_movement(num_frames=120, dt_ms=33.33, active=True, lag_ms=50.0)
    with patch("backend.services.whisper_service.whisper_service.transcribe_audio") as mock_transcribe:
        mock_transcribe.return_value = {
            "success": True,
            "text": "Today the weather is very pleasant outside",
            "confidence": 0.90
        }
        resp = client.post(
            "/api/sync",
            data={
                "lipMovement": json.dumps(lip_move),
                "lipTimestamps": json.dumps(lip_ts),
                "sessionId": session_id,
                "challengePhrase": challenge,
                "language": "en"
            },
            files={"file": ("speech.wav", audio_bytes, "audio/wav")}
        )

    assert resp.status_code == 200
    data = resp.json()
    print(f"Status: {data.get('livenessResult')} | Reason: {data.get('rejectionReason')}")
    assert data["livenessResult"] == "SPOOF", f"Expected SPOOF, got {data['livenessResult']}"
    assert data["isChallengeMatch"] is False
    print("[PASS] Test 2: Incorrect phrase correctly rejected as SPOOF!")

    # 3. No Lip Movement (Static Photo Attack)
    print("\n--- TEST 3: No Lip Movement / Static Photo Attack ---")
    session_id = "diag-static-photo-3"
    challenge = "Artificial intelligence shapes future technology"
    attempt_tracker.set_challenge(session_id, challenge, language="en")

    # Static lip movement (variance = 0.0)
    lip_move, lip_ts = create_synthetic_lip_movement(num_frames=120, dt_ms=33.33, active=False)
    with patch("backend.services.whisper_service.whisper_service.transcribe_audio") as mock_transcribe:
        mock_transcribe.return_value = {
            "success": True,
            "text": challenge,
            "confidence": 0.95
        }
        resp = client.post(
            "/api/sync",
            data={
                "lipMovement": json.dumps(lip_move),
                "lipTimestamps": json.dumps(lip_ts),
                "sessionId": session_id,
                "challengePhrase": challenge,
                "language": "en"
            },
            files={"file": ("speech.wav", audio_bytes, "audio/wav")}
        )

    assert resp.status_code == 200
    data = resp.json()
    print(f"Status: {data.get('livenessResult')} | Variance: {data.get('lipVariance')} | Reason: {data.get('rejectionReason')}")
    assert data["livenessResult"] == "SPOOF", f"Expected SPOOF, got {data['livenessResult']}"
    assert data["isLipMoving"] is False
    print("[PASS] Test 3: Static photo attack correctly rejected as SPOOF!")

    # 4. Silent Audio (No Speech Detected)
    print("\n--- TEST 4: Silent Audio / Video ---")
    session_id = "diag-silent-audio-4"
    challenge = "Artificial intelligence shapes future technology"
    attempt_tracker.set_challenge(session_id, challenge, language="en")

    silent_audio_bytes = create_synthetic_audio(duration_sec=4.0, speech_pattern=False)
    lip_move, lip_ts = create_synthetic_lip_movement(num_frames=120, dt_ms=33.33, active=True)
    with patch("backend.services.whisper_service.whisper_service.transcribe_audio") as mock_transcribe:
        mock_transcribe.return_value = {
            "success": True,
            "text": "",
            "confidence": 0.0
        }
        resp = client.post(
            "/api/sync",
            data={
                "lipMovement": json.dumps(lip_move),
                "lipTimestamps": json.dumps(lip_ts),
                "sessionId": session_id,
                "challengePhrase": challenge,
                "language": "en"
            },
            files={"file": ("speech.wav", silent_audio_bytes, "audio/wav")}
        )

    assert resp.status_code == 200
    data = resp.json()
    print(f"Status: {data.get('livenessResult')} | Reason: {data.get('rejectionReason')}")
    assert data["livenessResult"] == "SPOOF", f"Expected SPOOF, got {data['livenessResult']}"
    print("[PASS] Test 4: Silent audio/video correctly rejected as SPOOF!")

    # 5. Unsynchronized Audio / Video (Lag > 500ms)
    print("\n--- TEST 5: Unsynchronized Audio/Video (Lag Offset = 800ms > 500ms) ---")
    session_id = "diag-unsync-lag-5"
    challenge = "Artificial intelligence shapes future technology"
    attempt_tracker.set_challenge(session_id, challenge, language="en")

    # Lip movement delayed by 800ms
    lip_move, lip_ts = create_synthetic_lip_movement(num_frames=120, dt_ms=33.33, active=True, lag_ms=800.0)
    with patch("backend.services.whisper_service.whisper_service.transcribe_audio") as mock_transcribe:
        mock_transcribe.return_value = {
            "success": True,
            "text": challenge,
            "confidence": 0.95
        }
        resp = client.post(
            "/api/sync",
            data={
                "lipMovement": json.dumps(lip_move),
                "lipTimestamps": json.dumps(lip_ts),
                "sessionId": session_id,
                "challengePhrase": challenge,
                "language": "en"
            },
            files={"file": ("speech.wav", audio_bytes, "audio/wav")}
        )

    assert resp.status_code == 200
    data = resp.json()
    print(f"Status: {data.get('livenessResult')} | Detected Offset: {data.get('detectedTimeOffsetMs')}ms | Reason: {data.get('rejectionReason')}")
    assert data["livenessResult"] == "SPOOF", f"Expected SPOOF, got {data['livenessResult']}"
    assert data["isSyncValid"] is False
    print("[PASS] Test 5: Unsynchronized lag (> 500ms) correctly rejected as SPOOF!")

    print("\n" + "=" * 80)
    print("ALL 5 DIAGNOSTIC VERIFICATION CHECKS PASSED WITH FULL LOGGING!")
    print("=" * 80)

if __name__ == "__main__":
    run_diagnostic_suite()
