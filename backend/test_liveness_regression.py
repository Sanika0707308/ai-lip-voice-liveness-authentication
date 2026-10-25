"""
Regression test suite for Lip-Voice Liveness Authentication.
Verifies the fix for false SPOOF rejections:
1. Natural conversational speech lip variance (~0.0003 - 0.0006) is accepted as dynamic speech.
2. Static photo attacks (variance < 0.0001, ptp < 0.01) are strictly rejected as SPOOF.
3. Correct challenge phrase + synchronized audio-visual signals yields LIVE.
4. Spoken number homophones (e.g. 'for' -> 'four', 'to' -> 'two') match correctly.
5. Audio-visual lag > 500ms is strictly rejected as SPOOF.
6. Uncorrelated lip-voice audio (< 0.45 correlation) is strictly rejected as SPOOF.
7. Incorrect challenge phrase is strictly rejected as SPOOF.
8. Silence / no speech is strictly rejected as SPOOF.
"""

import sys
import os
import json
import tempfile
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

# Ensure backend can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app import app
from backend.config import settings
from backend.services.sync_service import sync_service
from backend.services.lip_service import lip_service
from backend.services.verification_service import normalize_text, VerificationEngine
from unittest.mock import patch

client = TestClient(app)

def create_synthetic_audio(duration_sec=4.0, samplerate=16000, envelope_fn=None):
    """Creates a temporary WAV file with a synthetic modulated tone."""
    t = np.linspace(0, duration_sec, int(samplerate * duration_sec), endpoint=False)
    if envelope_fn is not None:
        env = envelope_fn(t)
    else:
        # Default envelope: syllables at 1.0s, 1.8s, 2.6s
        env = (
            0.8 * np.exp(-0.5 * ((t - 1.0) / 0.15) ** 2) +
            0.9 * np.exp(-0.5 * ((t - 1.8) / 0.15) ** 2) +
            0.7 * np.exp(-0.5 * ((t - 2.6) / 0.15) ** 2)
        )
    carrier = np.sin(2 * np.pi * 300.0 * t) + 0.3 * np.sin(2 * np.pi * 600.0 * t)
    audio = (env * carrier * 0.7).astype(np.float32)

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, audio, samplerate)
    tmp.close()
    return tmp.name

def run_regression_tests():
    print("=" * 75)
    print("RUNNING LIVENESS REGRESSION TEST SUITE (FALSE SPOOF FIX VERIFICATION)")
    print("=" * 75)

    passed = 0
    total = 0

    # ------------------------------------------------------------------
    # Test 1: Natural Conversational Speech Lip Variance Acceptance
    # In real user sessions, normalized mouth aperture variance is ~0.00035 - 0.0006
    # ------------------------------------------------------------------
    total += 1
    natural_speech_apertures = [
        0.20 + 0.04 * np.sin(2 * np.pi * 1.5 * (i / 30.0)) + 0.005 * np.sin(2 * np.pi * 3.0 * (i / 30.0))
        for i in range(120)
    ]
    var_natural = float(np.var(natural_speech_apertures))
    is_active, var_out = lip_service.verify_lip_movement(natural_speech_apertures)
    assert is_active is True, f"Expected natural speech to be active, got {is_active} (var={var_natural:.6f})"
    print(f"[PASS] 1. Natural speech lip variance ({var_out:.6f}) accepted as active speech (>= {settings.MIN_LIP_VARIATION})")
    passed += 1

    # ------------------------------------------------------------------
    # Test 2: Static Photo Attack Strict Rejection
    # Static webcam frame / printed photo has sensor jitter variance < 1e-5
    # ------------------------------------------------------------------
    total += 1
    np.random.seed(42)
    static_photo_apertures = [0.22 + float(np.random.normal(0, 0.0005)) for _ in range(120)]
    var_static = float(np.var(static_photo_apertures))
    is_active_static, var_out_static = lip_service.verify_lip_movement(static_photo_apertures)
    assert is_active_static is False, f"Expected static photo to be rejected, got {is_active_static} (var={var_static:.6f})"
    print(f"[PASS] 2. Static photo attack strictly rejected: is_active=False (variance={var_out_static:.6f})")
    passed += 1

    # ------------------------------------------------------------------
    # Test 3: Spoken Number Homophone Normalization
    # Whisper often transcribes 'for 667' instead of '4667', or 'to' instead of '2'
    # ------------------------------------------------------------------
    total += 1
    # Test English homophone mapping in verification_service
    norm_tokens = normalize_text("spoken phrase for 667 to won", "en")
    assert "four" in norm_tokens or "4" in norm_tokens, f"Expected 'for' to normalize to 'four' or '4', got {norm_tokens}"
    assert "two" in norm_tokens or "2" in norm_tokens, f"Expected 'to' to normalize to 'two' or '2', got {norm_tokens}"
    print(f"[PASS] 3. Number homophone normalization verified: 'for 667 to won' -> '{norm_tokens}'")
    passed += 1

    # ------------------------------------------------------------------
    # Test 4: End-to-End LIVE Verification (Correct Phrase + Synchronized Signals)
    # ------------------------------------------------------------------
    total += 1
    # Syllables at 1.0s, 1.8s, 2.6s
    audio_path = create_synthetic_audio(duration_sec=4.0)
    timestamps = [100.0 + i * (1000.0 / 30.0) for i in range(120)]
    t_frames = [(ts - timestamps[0]) / 1000.0 for ts in timestamps]
    lip_sync_movement = [
        float(
            0.18 +
            0.08 * np.exp(-0.5 * ((t - 1.0) / 0.15) ** 2) +
            0.09 * np.exp(-0.5 * ((t - 1.8) / 0.15) ** 2) +
            0.07 * np.exp(-0.5 * ((t - 2.6) / 0.15) ** 2)
        )
        for t in t_frames
    ]

    try:
        mock_whisper_result = {
            "success": True,
            "text": "Today is a beautiful day",
            "confidence": 0.96,
            "language": "en"
        }
        with patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mock_whisper_result):
            with open(audio_path, "rb") as f_aud:
                response = client.post(
                    "/api/sync",
                    data={
                        "sessionId": "regression-live-sync-1",
                        "challengePhrase": "Today is a beautiful day",
                        "lipMovement": json.dumps(lip_sync_movement),
                        "lipTimestamps": json.dumps(timestamps),
                        "language": "en"
                    },
                    files={"file": ("audio.wav", f_aud, "audio/wav")}
                )
            assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
            res_json = response.json()
            assert res_json["livenessResult"] == "LIVE", f"Expected LIVE, got {res_json}"
            assert res_json["alignedCorrelation"] >= 0.45
            assert abs(res_json["detectedTimeOffsetMs"]) <= 500.0
            print(
                f"[PASS] 4. End-to-End LIVE verification: Result={res_json['livenessResult']}, "
                f"Correlation={res_json['alignedCorrelation']:.3f} (>= 0.45), "
                f"Offset={res_json['detectedTimeOffsetMs']:.1f}ms (<= 500ms)"
            )
            passed += 1
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)

    # ------------------------------------------------------------------
    # Test 5: Lagged Audio-Video Mismatch (> 500ms, e.g. 700ms) -> SPOOF
    # ------------------------------------------------------------------
    total += 1
    audio_path_lag = create_synthetic_audio(duration_sec=4.0)
    # Visual movements shifted by +700ms (at 1.7s, 2.5s, 3.3s)
    lip_lagged_movement = [
        float(
            0.18 +
            0.08 * np.exp(-0.5 * ((t - 1.7) / 0.15) ** 2) +
            0.09 * np.exp(-0.5 * ((t - 2.5) / 0.15) ** 2) +
            0.07 * np.exp(-0.5 * ((t - 3.3) / 0.15) ** 2)
        )
        for t in t_frames
    ]

    try:
        mock_whisper_result = {
            "success": True,
            "text": "Today is a beautiful day",
            "confidence": 0.95,
            "language": "en"
        }
        with patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mock_whisper_result):
            with open(audio_path_lag, "rb") as f_aud:
                response = client.post(
                    "/api/sync",
                    data={
                        "sessionId": "regression-lag-spoof-2",
                        "challengePhrase": "Today is a beautiful day",
                        "lipMovement": json.dumps(lip_lagged_movement),
                        "lipTimestamps": json.dumps(timestamps),
                        "language": "en"
                    },
                    files={"file": ("audio.wav", f_aud, "audio/wav")}
                )
            assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
            res_json = response.json()
            assert res_json["livenessResult"] == "SPOOF", f"Expected SPOOF for lag attack, got {res_json}"
            assert abs(res_json["detectedTimeOffsetMs"]) > 500.0 or res_json["isSyncValid"] is False
            print(
                f"[PASS] 5. Lagged audio-visual attack (> 500ms) strictly rejected: "
                f"Result={res_json['livenessResult']}, Offset={res_json['detectedTimeOffsetMs']:.1f}ms"
            )
            passed += 1
    finally:
        if os.path.exists(audio_path_lag):
            os.remove(audio_path_lag)

    # ------------------------------------------------------------------
    # Test 6: Uncorrelated Lip-Voice Signals (< 0.45 correlation) -> SPOOF
    # ------------------------------------------------------------------
    total += 1
    audio_path_uncorr = create_synthetic_audio(duration_sec=4.0)
    # Random uncorrelated mouth movements
    np.random.seed(123)
    lip_uncorr = [
        float(0.20 + 0.05 * np.sin(2 * np.pi * 0.7 * t) + 0.02 * np.random.randn())
        for t in t_frames
    ]
    try:
        mock_whisper_result = {
            "success": True,
            "text": "Today is a beautiful day",
            "confidence": 0.95,
            "language": "en"
        }
        with patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mock_whisper_result):
            with open(audio_path_uncorr, "rb") as f_aud:
                response = client.post(
                    "/api/sync",
                    data={
                        "sessionId": "regression-uncorr-spoof-3",
                        "challengePhrase": "Today is a beautiful day",
                        "lipMovement": json.dumps(lip_uncorr),
                        "lipTimestamps": json.dumps(timestamps),
                        "language": "en"
                    },
                    files={"file": ("audio.wav", f_aud, "audio/wav")}
                )
            assert response.status_code == 200
            res_json = response.json()
            assert res_json["livenessResult"] == "SPOOF", f"Expected SPOOF for uncorrelated movement, got {res_json}"
            assert res_json["isSyncValid"] is False
            print(
                f"[PASS] 6. Uncorrelated lip-voice movement strictly rejected: "
                f"Result={res_json['livenessResult']}, isSyncValid={res_json['isSyncValid']}, Reason='{res_json['rejectionReason']}'"
            )
            passed += 1
    finally:
        if os.path.exists(audio_path_uncorr):
            os.remove(audio_path_uncorr)

    # ------------------------------------------------------------------
    # Test 7: Wrong Spoken Phrase -> SPOOF
    # ------------------------------------------------------------------
    total += 1
    audio_path_wrong = create_synthetic_audio(duration_sec=4.0)
    try:
        mock_whisper_wrong = {
            "success": True,
            "text": "Good friends enjoy reading together",
            "confidence": 0.95,
            "language": "en"
        }
        with patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mock_whisper_wrong):
            with open(audio_path_wrong, "rb") as f_aud:
                response = client.post(
                    "/api/sync",
                    data={
                        "sessionId": "regression-wrong-phrase-4",
                        "challengePhrase": "Today is a beautiful day",
                        "lipMovement": json.dumps(lip_sync_movement),
                        "lipTimestamps": json.dumps(timestamps),
                        "language": "en"
                    },
                    files={"file": ("audio.wav", f_aud, "audio/wav")}
                )
            assert response.status_code == 200
            res_json = response.json()
            assert res_json["livenessResult"] == "SPOOF", f"Expected SPOOF for wrong phrase, got {res_json}"
            assert res_json["isChallengeMatch"] is False
            print(f"[PASS] 7. Wrong challenge phrase rejected: Result=SPOOF, Reason='{res_json['rejectionReason']}'")
            passed += 1
    finally:
        if os.path.exists(audio_path_wrong):
            os.remove(audio_path_wrong)

    # ------------------------------------------------------------------
    # Test 8: Silence / No Speech Detected -> SPOOF
    # ------------------------------------------------------------------
    total += 1
    audio_path_silent = create_synthetic_audio(
        duration_sec=4.0,
        envelope_fn=lambda t: np.zeros_like(t)
    )
    try:
        mock_whisper_silent = {
            "success": True,
            "text": "",
            "confidence": 0.0,
            "language": "en"
        }
        with patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mock_whisper_silent):
            with open(audio_path_silent, "rb") as f_aud:
                response = client.post(
                    "/api/sync",
                    data={
                        "sessionId": "regression-silence-5",
                        "challengePhrase": "Today is a beautiful day",
                        "lipMovement": json.dumps(lip_sync_movement),
                        "lipTimestamps": json.dumps(timestamps),
                        "language": "en"
                    },
                    files={"file": ("audio.wav", f_aud, "audio/wav")}
                )
            assert response.status_code == 200
            res_json = response.json()
            assert res_json["livenessResult"] == "SPOOF"
            assert "No speech detected" in (res_json["rejectionReason"] or "")
            print(f"[PASS] 8. Silence / no speech rejected: Result=SPOOF, Reason='{res_json['rejectionReason']}'")
            passed += 1
    finally:
        if os.path.exists(audio_path_silent):
            os.remove(audio_path_silent)

    print("=" * 75)
    print(f"RESULTS: {passed}/{total} REGRESSION TESTS PASSED SUCCESSFULLY!")
    print("=" * 75)
    return passed == total

if __name__ == "__main__":
    success = run_regression_tests()
    sys.exit(0 if success else 1)
