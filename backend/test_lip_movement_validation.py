import os
import sys
import json
import math
import tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app import app
from backend.config import settings
from backend.services.lip_service import lip_service, LipService
from backend.services.sync_service import sync_service
from backend.services.mongodb_service import mongodb_service
from backend.services.calibration_service import calibration_service

client = TestClient(app)


def create_temp_sine_audio(duration_sec: float = 4.0, samplerate: int = 16000) -> str:
    """Generates a synthetic speech-like amplitude modulated WAV file."""
    t = np.linspace(0, duration_sec, int(samplerate * duration_sec), endpoint=False)
    # 2 Hz modulated carrier envelope
    envelope = 0.5 * (1.0 + np.sin(2 * np.pi * 2.0 * t))
    carrier = np.sin(2 * np.pi * 440.0 * t)
    audio = (envelope * carrier * 0.8).astype(np.float32)
    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(temp_wav.name, audio, samplerate)
    temp_wav.close()
    return temp_wav.name


def run_lip_movement_validation_tests():
    print("\n" + "=" * 74)
    print("RUNNING BACKEND LIP-MOVEMENT VALIDATION TEST SUITE")
    print("=" * 74)

    passed = 0
    total = 0

    # ------------------------------------------------------------------
    # 1. UNIT TESTS: lip_service.verify_lip_movement()
    # ------------------------------------------------------------------
    print("\n--- Section 1: LipService.verify_lip_movement Unit Tests ---")

    # 1.1 Valid dynamic movement (sine variation)
    total += 1
    dynamic_movement = [0.2 + 0.08 * math.sin(i * 0.25) for i in range(120)]
    is_active, var = lip_service.verify_lip_movement(dynamic_movement)
    assert is_active is True, f"Expected active movement, got is_active={is_active}, var={var}"
    assert var >= settings.MIN_LIP_VARIATION, f"Expected var >= {settings.MIN_LIP_VARIATION}, got {var}"
    print(f"[PASS] 1.1 Valid dynamic lip movement verified (variance={var:.6f} >= {settings.MIN_LIP_VARIATION})")
    passed += 1

    # 1.2 Zero-variance / static movement (all identical aperture, e.g. static photo)
    total += 1
    static_movement = [0.18] * 120
    is_active_static, var_static = lip_service.verify_lip_movement(static_movement)
    assert is_active_static is False, f"Expected static rejection, got is_active={is_active_static}"
    assert var_static < 1e-12, f"Expected near 0.0 variance, got {var_static}"
    print(f"[PASS] 1.2 Zero-variance / static movement correctly rejected (is_active={is_active_static}, variance={var_static:.6f})")
    passed += 1

    # 1.3 Near-static movement below MIN_LIP_VARIATION (e.g. sensor noise 0.0001 < 0.001)
    total += 1
    near_static_movement = [0.18 + 0.0005 * math.sin(i) for i in range(120)]
    is_active_near, var_near = lip_service.verify_lip_movement(near_static_movement)
    assert is_active_near is False, f"Expected near-static rejection, got {is_active_near}"
    assert var_near < settings.MIN_LIP_VARIATION, f"Expected variance < {settings.MIN_LIP_VARIATION}, got {var_near}"
    print(f"[PASS] 1.3 Near-static movement below threshold correctly rejected (is_active={is_active_near}, variance={var_near:.6f})")
    passed += 1

    # 1.4 Empty list handling
    total += 1
    is_active_empty, var_empty = lip_service.verify_lip_movement([])
    assert is_active_empty is False
    assert var_empty == 0.0
    print("[PASS] 1.4 Empty list safely handled without exceptions")
    passed += 1

    # 1.5 Insufficient samples (< 2)
    total += 1
    is_active_one, var_one = lip_service.verify_lip_movement([0.25])
    assert is_active_one is False
    assert var_one == 0.0
    print("[PASS] 1.5 Single-element list safely handled (< 2 samples)")
    passed += 1

    # 1.6 Non-finite / NaN values handling
    total += 1
    nan_movement = [0.2, float("nan"), 0.25, 0.3]
    is_active_nan, var_nan = lip_service.verify_lip_movement(nan_movement)
    assert is_active_nan is False
    assert var_nan == 0.0
    print("[PASS] 1.6 Non-finite / NaN values safely rejected")
    passed += 1

    # ------------------------------------------------------------------
    # 2. INTEGRATION TESTS: SyncService.calculate_sync_metrics()
    # ------------------------------------------------------------------
    print("\n--- Section 2: SyncService Integration Tests ---")

    audio_file = create_temp_sine_audio(duration_sec=4.0)
    timestamps = [100.0 + i * 33.33 for i in range(120)]

    try:
        # 2.1 Valid lip movement in calculate_sync_metrics
        total += 1
        valid_mov = [0.2 + 0.1 * math.sin(i * 0.2) for i in range(120)]
        metrics_valid = sync_service.calculate_sync_metrics(
            lip_movement=valid_mov,
            lip_timestamps=timestamps,
            audio_filepath=audio_file,
        )
        assert "isLipMoving" in metrics_valid
        assert metrics_valid["isLipMoving"] is True
        assert metrics_valid["lipVariance"] >= settings.MIN_LIP_VARIATION
        print(f"[PASS] 2.1 SyncService accepts valid lip movement: isLipMoving={metrics_valid['isLipMoving']}, var={metrics_valid['lipVariance']:.6f}")
        passed += 1

        # 2.2 Static lip movement in calculate_sync_metrics (rejects as SPOOF)
        total += 1
        static_mov = [0.2] * 120
        metrics_static = sync_service.calculate_sync_metrics(
            lip_movement=static_mov,
            lip_timestamps=timestamps,
            audio_filepath=audio_file,
        )
        assert metrics_static["isLipMoving"] is False
        assert metrics_static["syncStatus"] == "SPOOF"
        assert metrics_static["isSynchronized"] is False
        assert "static lip movement" in metrics_static["syncReason"].lower()
        print(f"[PASS] 2.2 SyncService rejects static lip movement as SPOOF: reason='{metrics_static['syncReason']}'")
        passed += 1

    finally:
        if os.path.exists(audio_file):
            os.remove(audio_file)

    # ------------------------------------------------------------------
    # 3. ENDPOINT TESTS: POST /api/sync
    # ------------------------------------------------------------------
    print("\n--- Section 3: /api/sync Endpoint Validation Tests ---")

    audio_test_wav = create_temp_sine_audio(duration_sec=4.0)
    timestamps_endpoint = [100.0 + i * 33.33 for i in range(120)]

    try:
        # 3.1 Static photo attack (static lip movement with valid audio) -> rejected as SPOOF
        total += 1
        static_photo_mov = [0.22] * 120
        with open(audio_test_wav, "rb") as f_aud:
            resp_static = client.post(
                "/api/sync",
                files={"file": ("recording.wav", f_aud, "audio/wav")},
                data={
                    "lipMovement": json.dumps(static_photo_mov),
                    "lipTimestamps": json.dumps(timestamps_endpoint),
                    "sessionId": "test-lip-static-spoof",
                    "challengePhrase": "any challenge phrase"
                }
            )
        assert resp_static.status_code == 200, f"Expected 200, got {resp_static.status_code}"
        data_static = resp_static.json()
        assert data_static["success"] is True
        assert data_static["livenessResult"] == "SPOOF"
        assert data_static["finalStatus"] == "SPOOF"
        assert data_static["isLipMoving"] is False
        assert "static lip movement" in data_static["rejectionReason"].lower() or "insufficient" in data_static["rejectionReason"].lower()
        print(f"[PASS] 3.1 Static photo attack rejected as SPOOF: livenessResult={data_static['livenessResult']}, reason='{data_static['rejectionReason']}'")
        passed += 1

        # 3.2 Invalid empty lip movement array -> HTTP 422
        total += 1
        with open(audio_test_wav, "rb") as f_aud:
            resp_empty = client.post(
                "/api/sync",
                files={"file": ("recording.wav", f_aud, "audio/wav")},
                data={
                    "lipMovement": json.dumps([]),
                    "lipTimestamps": json.dumps([]),
                    "sessionId": "test-lip-empty"
                }
            )
        assert resp_empty.status_code == 422, f"Expected 422, got {resp_empty.status_code}"
        data_empty = resp_empty.json()
        assert data_empty["success"] is False
        assert data_empty["error"]["code"] == "INVALID_INPUT"
        print(f"[PASS] 3.2 Empty lip movement safely rejected: code={data_empty['error']['code']}, msg='{data_empty['error']['message']}'")
        passed += 1

        # 3.3 Non-numeric / NaN values in lip movement -> HTTP 422
        total += 1
        with open(audio_test_wav, "rb") as f_aud:
            resp_nan = client.post(
                "/api/sync",
                files={"file": ("recording.wav", f_aud, "audio/wav")},
                data={
                    "lipMovement": json.dumps(["invalid_str"] * 60),
                    "lipTimestamps": json.dumps(timestamps_endpoint[:60]),
                    "sessionId": "test-lip-non-numeric"
                }
            )
        assert resp_nan.status_code == 422, f"Expected 422, got {resp_nan.status_code}"
        data_nan = resp_nan.json()
        assert data_nan["success"] is False
        assert "numeric" in data_nan["error"]["message"].lower()
        print(f"[PASS] 3.3 Non-numeric lip movement safely rejected with 422: '{data_nan['error']['message']}'")
        passed += 1

        # 3.4 Valid dynamic movement with phrase match mock -> accepted for sync evaluation
        total += 1
        valid_endpoint_mov = [0.2 + 0.08 * math.sin(i * 0.25) for i in range(120)]
        mock_whisper_pass = {
            "success": True,
            "text": "silver eagle flies 400",
            "confidence": 0.95
        }
        with patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mock_whisper_pass):
            with open(audio_test_wav, "rb") as f_aud:
                resp_valid = client.post(
                    "/api/sync",
                    files={"file": ("recording.wav", f_aud, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(valid_endpoint_mov),
                        "lipTimestamps": json.dumps(timestamps_endpoint),
                        "sessionId": "test-lip-valid-e2e",
                        "challengePhrase": "silver eagle flies 400"
                    }
                )
        assert resp_valid.status_code == 200
        data_valid = resp_valid.json()
        assert data_valid["isLipMoving"] is True
        assert data_valid["lipVariance"] >= settings.MIN_LIP_VARIATION
        print(f"[PASS] 3.4 Valid dynamic movement accepted for sync evaluation: isLipMoving={data_valid['isLipMoving']}, var={data_valid['lipVariance']:.6f}")
        passed += 1

    finally:
        if os.path.exists(audio_test_wav):
            os.remove(audio_test_wav)

    # ------------------------------------------------------------------
    # 4. CALIBRATION SAMPLE TESTS: POST /api/calibration/sample
    # ------------------------------------------------------------------
    print("\n--- Section 4: Calibration Sample Static Rejection Tests ---")

    audio_cal_wav = create_temp_sine_audio(duration_sec=4.0)
    cal_user = "test-cal-lip-user"
    mongodb_service.delete_calibration_profile(cal_user)

    try:
        # Start calibration
        client.post(
            "/api/calibration/start",
            data={"userId": cal_user, "sessionId": "cal-lip-start", "requiredSamples": 3}
        )

        # 4.1 Static photo sample during calibration -> sampleAccepted MUST be False
        total += 1
        static_cal_mov = [0.22] * 120
        mock_whisper_cal = {"success": True, "text": "blue lake shines 99", "confidence": 0.96}
        with patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mock_whisper_cal):
            with open(audio_cal_wav, "rb") as f_aud:
                resp_cal_static = client.post(
                    "/api/calibration/sample",
                    files={"file": ("recording.wav", f_aud, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(static_cal_mov),
                        "lipTimestamps": json.dumps(timestamps_endpoint),
                        "sessionId": "cal-lip-sample-static",
                        "userId": cal_user,
                        "challengePhrase": "blue lake shines 99",
                        "requiredSamples": 3
                    }
                )
        assert resp_cal_static.status_code == 200
        data_cal_static = resp_cal_static.json()
        assert data_cal_static["sampleAccepted"] is False, "Static calibration sample MUST NOT be accepted!"
        assert data_cal_static["isLipMoving"] is False
        assert "static lip movement" in data_cal_static["sampleRejectionReason"].lower() or "insufficient" in data_cal_static["sampleRejectionReason"].lower()
        assert data_cal_static["currentSampleNumber"] == 0, "Valid samples count must remain 0"
        print(f"[PASS] 4.1 Static sample rejected during calibration: sampleAccepted=False, reason='{data_cal_static['sampleRejectionReason']}'")
        passed += 1

    finally:
        if os.path.exists(audio_cal_wav):
            os.remove(audio_cal_wav)
        mongodb_service.delete_calibration_profile(cal_user)

    print("\n" + "=" * 74)
    print(f"RESULTS: {passed}/{total} LIP-MOVEMENT VALIDATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 74 + "\n")
    return passed == total


if __name__ == "__main__":
    success = run_lip_movement_validation_tests()
    sys.exit(0 if success else 1)
