import os
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

# Ensure root directory is on sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app import app
from backend.config import settings
from backend.services.calibration_service import CalibrationService, calibration_service
from backend.services.sync_service import SyncService
from backend.services.mongodb_service import mongodb_service

client = TestClient(app)


def create_modulated_audio_and_lips(duration_sec: float = 4.0, offset_sec: float = 0.0, noise_level: float = 0.0):
    """
    Generates a synthetic amplitude-modulated WAV file and matching lip-opening
    trajectory with a non-periodic 3-syllable envelope so that cross-correlation
    and time offset have a unique peak at `offset_sec`.
    """
    samplerate = 16000
    t_audio = np.linspace(0, duration_sec, int(samplerate * duration_sec), endpoint=False)

    def syllable_envelope(t_arr):
        return (
            0.08
            + 0.85 * np.exp(-0.5 * ((t_arr - 0.9) / 0.16) ** 2)
            + 0.65 * np.exp(-0.5 * ((t_arr - 1.95) / 0.22) ** 2)
            + 0.92 * np.exp(-0.5 * ((t_arr - 3.05) / 0.18) ** 2)
        )

    envelope = syllable_envelope(t_audio)
    carrier = np.sin(2 * np.pi * 440.0 * t_audio)
    audio_signal = (envelope * carrier * 0.8).astype(np.float32)

    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(temp_wav.name, audio_signal, samplerate)
    temp_wav.close()

    # 30 FPS lip timestamps (120 frames over ~4.0 seconds)
    num_frames = int(duration_sec * 30)
    lip_timestamps_ms = [100.0 + i * (1000.0 / 30.0) for i in range(num_frames)]
    t_lip = np.array([(ts - lip_timestamps_ms[0]) / 1000.0 for ts in lip_timestamps_ms])

    rng = np.random.default_rng(42)
    lip_clean = 0.05 + 0.35 * syllable_envelope(t_lip - offset_sec)
    if noise_level > 0:
        lip_clean = lip_clean + rng.normal(0, noise_level, size=len(t_lip))
    lip_movement = [float(max(0.0, val)) for val in lip_clean]

    return temp_wav.name, lip_movement, lip_timestamps_ms


def run_adaptive_calibration_tests():
    print("=" * 74)
    print("RUNNING ADAPTIVE PER-USER SYNCHRONIZATION THRESHOLD CALIBRATION TESTS")
    print("=" * 74)

    passed = 0
    total = 14

    test_user = "test-cal-user-e2e"
    low_score_user = "test-cal-user-low-floor"
    incomplete_user = "test-cal-user-incomplete"

    # Clean up test profiles before starting
    mongodb_service.delete_calibration_profile(test_user)
    mongodb_service.delete_calibration_profile(low_score_user)
    mongodb_service.delete_calibration_profile(incomplete_user)

    # ------------------------------------------------------------------
    # TEST 1, 2, 3: Calibration with 3 valid samples (0.62, 0.68, 0.65),
    # Mean / Median / StdDev calculation, and Adaptive Threshold = 0.57
    # ------------------------------------------------------------------
    unstarted_prof = calibration_service.get_or_default_profile(test_user)
    assert unstarted_prof["calibrationStatus"] == "NOT_STARTED"

    cal_Profile_start = calibration_service.start_new_calibration(
        user_id=test_user, session_id="cal-sess-start", required_samples=3
    )
    assert cal_Profile_start["calibrationStatus"] == "IN_PROGRESS"
    assert cal_Profile_start["isCalibrated"] is False
    assert cal_Profile_start["effectiveThreshold"] == 0.45

    sample_scores = [0.62, 0.68, 0.65]
    out = None
    for idx, score in enumerate(sample_scores, start=1):
        out = calibration_service.record_calibration_sample(
            user_id=test_user,
            session_id=f"cal-sess-{idx}",
            aligned_correlation=score,
            detected_time_offset_ms=33.33,
            is_challenge_match=True,
            is_sync_valid=True,
            duration_mismatch=False,
            required_samples=3,
        )
        assert out["sampleAccepted"] is True, f"Sample {idx} should be accepted"

    prof = out["calibrationProfile"]
    assert prof["isCalibrated"] is True
    assert prof["calibrationStatus"] == "COMPLETE"
    assert prof["validSamplesCount"] == 3
    assert prof["validScores"] == [0.62, 0.68, 0.65]
    print("[PASS] 1. Calibration with 3 valid samples (0.62, 0.68, 0.65) completed")
    passed += 1

    assert abs(prof["meanScore"] - 0.65) < 1e-4, f"Expected mean 0.65, got {prof['meanScore']}"
    assert abs(prof["medianScore"] - 0.65) < 1e-4, f"Expected median 0.65, got {prof['medianScore']}"
    assert abs(prof["stdDeviation"] - 0.0245) < 1e-3, f"Expected std 0.0245, got {prof['stdDeviation']}"
    print(
        f"[PASS] 2. Mean/Median/StdDev calculation verified: "
        f"mean={prof['meanScore']}, median={prof['medianScore']}, std={prof['stdDeviation']}"
    )
    passed += 1

    assert abs(prof["adaptiveThreshold"] - 0.57) < 1e-4, f"Expected 0.57, got {prof['adaptiveThreshold']}"
    assert abs(prof["effectiveThreshold"] - 0.57) < 1e-4, f"Expected 0.57, got {prof['effectiveThreshold']}"
    assert abs(prof["globalThreshold"] - 0.45) < 1e-4
    print(
        f"[PASS] 3. Adaptive threshold calculation verified: "
        f"adaptiveThreshold={prof['adaptiveThreshold']}, effectiveThreshold={prof['effectiveThreshold']}"
    )
    passed += 1

    # ------------------------------------------------------------------
    # TEST 4: Rejection of invalid calibration sample (alignedCorrelation < 0.45)
    # ------------------------------------------------------------------
    calibration_service.start_new_calibration(incomplete_user, session_id="inc-0", required_samples=3)
    bad_corr_res = calibration_service.record_calibration_sample(
        user_id=incomplete_user,
        session_id="inc-bad-corr",
        aligned_correlation=0.31,  # Below global 0.45 minimum
        detected_time_offset_ms=20.0,
        is_challenge_match=True,
        is_sync_valid=False,
        duration_mismatch=False,
        required_samples=3,
    )
    assert bad_corr_res["sampleAccepted"] is False
    assert bad_corr_res["calibrationProfile"]["validSamplesCount"] == 0
    assert "below the minimum global threshold" in bad_corr_res["sampleRejectionReason"]
    print(f"[PASS] 4. Rejection of invalid calibration sample (score 0.31 < 0.45): '{bad_corr_res['sampleRejectionReason']}'")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 5: Rejection of failed phrase match during calibration
    # ------------------------------------------------------------------
    bad_phrase_res = calibration_service.record_calibration_sample(
        user_id=incomplete_user,
        session_id="inc-bad-phrase",
        aligned_correlation=0.72,
        detected_time_offset_ms=15.0,
        is_challenge_match=False,  # Failed spoken challenge
        is_sync_valid=True,
        duration_mismatch=False,
        required_samples=3,
    )
    assert bad_phrase_res["sampleAccepted"] is False
    assert bad_phrase_res["calibrationProfile"]["validSamplesCount"] == 0
    assert "Spoken challenge phrase did not match" in bad_phrase_res["sampleRejectionReason"]
    print(f"[PASS] 5. Rejection of failed phrase match during calibration: '{bad_phrase_res['sampleRejectionReason']}'")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 6: Rejection of offset > 500 ms during calibration
    # ------------------------------------------------------------------
    bad_offset_res = calibration_service.record_calibration_sample(
        user_id=incomplete_user,
        session_id="inc-bad-offset",
        aligned_correlation=0.75,
        detected_time_offset_ms=633.33,  # Exceeds 500 ms rule
        is_challenge_match=True,
        is_sync_valid=False,
        duration_mismatch=False,
        required_samples=3,
    )
    assert bad_offset_res["sampleAccepted"] is False
    assert bad_offset_res["calibrationProfile"]["validSamplesCount"] == 0
    assert "500ms" in bad_offset_res["sampleRejectionReason"]
    print(f"[PASS] 6. Rejection of offset > 500 ms during calibration: '{bad_offset_res['sampleRejectionReason']}'")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 7: Fallback to 0.45 when calibration is not started / unavailable
    # ------------------------------------------------------------------
    uncalibrated_res = calibration_service.resolve_threshold_for_verification("non-existent-user-xyz")
    assert uncalibrated_res["adaptiveCalibrationUsed"] is False
    assert uncalibrated_res["adaptiveThreshold"] is None
    assert uncalibrated_res["effectiveThreshold"] == 0.45
    assert uncalibrated_res["globalThreshold"] == 0.45
    print("[PASS] 7. Fallback to global threshold (0.45) when calibration is unavailable/incomplete")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 8: Fallback to 0.45 when fewer than 3 valid samples exist
    # ------------------------------------------------------------------
    partial_1 = calibration_service.record_calibration_sample(
        user_id=incomplete_user,
        session_id="inc-valid-1",
        aligned_correlation=0.66,
        detected_time_offset_ms=0.0,
        is_challenge_match=True,
        is_sync_valid=True,
        duration_mismatch=False,
        required_samples=3,
    )
    partial_2 = calibration_service.record_calibration_sample(
        user_id=incomplete_user,
        session_id="inc-valid-2",
        aligned_correlation=0.70,
        detected_time_offset_ms=0.0,
        is_challenge_match=True,
        is_sync_valid=True,
        duration_mismatch=False,
        required_samples=3,
    )
    assert partial_2["calibrationProfile"]["validSamplesCount"] == 2
    assert partial_2["calibrationProfile"]["isCalibrated"] is False
    assert partial_2["calibrationProfile"]["calibrationStatus"] == "IN_PROGRESS"
    resolved_partial = calibration_service.resolve_threshold_for_verification(incomplete_user)
    assert resolved_partial["adaptiveCalibrationUsed"] is False
    assert resolved_partial["effectiveThreshold"] == 0.45
    print("[PASS] 8. Fallback to 0.45 when fewer than 3 valid samples exist (2/3 recorded)")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 9 & 10: Protection preventing adaptive threshold from going below 0.45,
    # specifically testing raw computed threshold = 0.38 -> clamped to 0.45
    # ------------------------------------------------------------------
    stats_low = CalibrationService.compute_adaptive_threshold([0.46, 0.46, 0.46])
    # Mean = 0.46, std = 0.0, margin = max(0.0, 0.08) = 0.08 -> raw = 0.46 - 0.08 = 0.38
    assert abs(stats_low["rawCalculatedThreshold"] - 0.38) < 1e-4, f"Expected raw 0.38, got {stats_low['rawCalculatedThreshold']}"
    assert stats_low["adaptiveThreshold"] == 0.45
    assert stats_low["effectiveThreshold"] == 0.45

    # Also verify SyncService.calculate_sync_metrics enforces max(correlation_threshold, 0.45)
    for idx in range(3):
        calibration_service.record_calibration_sample(
            user_id=low_score_user,
            session_id=f"low-{idx}",
            aligned_correlation=0.46,
            detected_time_offset_ms=0.0,
            is_challenge_match=True,
            is_sync_valid=True,
            duration_mismatch=False,
            required_samples=3,
        )
    resolved_low = calibration_service.resolve_threshold_for_verification(low_score_user)
    assert resolved_low["adaptiveCalibrationUsed"] is True
    assert resolved_low["adaptiveThreshold"] == 0.45
    assert resolved_low["effectiveThreshold"] == 0.45
    print("[PASS] 9. Global minimum protection prevents adaptive threshold from dropping below 0.45")
    passed += 1
    print(
        f"[PASS] 10. Example verified: raw computed threshold = {stats_low['rawCalculatedThreshold']:.2f} "
        f"-> adaptiveThreshold = {resolved_low['adaptiveThreshold']:.2f}, effectiveThreshold = {resolved_low['effectiveThreshold']:.2f}"
    )
    passed += 1

    # ------------------------------------------------------------------
    # TEST 11: LIVE acceptance when score >= effectiveThreshold and |offset| <= 500 ms
    # ------------------------------------------------------------------
    from backend.services.sync_service import sync_service

    wav_live, lip_mov_live, lip_ts_live = create_modulated_audio_and_lips(duration_sec=4.0, offset_sec=0.0)
    try:
        sync_metrics_live = sync_service.calculate_sync_metrics(
            lip_movement=lip_mov_live,
            lip_timestamps=lip_ts_live,
            audio_filepath=wav_live,
            correlation_threshold=0.57,
        )
        assert sync_metrics_live["alignedCorrelation"] >= 0.57, f"Expected >= 0.57, got {sync_metrics_live['alignedCorrelation']}"
        assert abs(sync_metrics_live["detectedTimeOffsetMs"]) <= 500.0
        assert sync_metrics_live["isSynchronized"] is True
        assert sync_metrics_live["syncStatus"] == "LIVE"
        assert sync_metrics_live["effectiveThreshold"] == 0.57
        print(
            f"[PASS] 11. LIVE acceptance when score ({sync_metrics_live['alignedCorrelation']:.2f}) >= "
            f"effectiveThreshold (0.57) and |offset| ({sync_metrics_live['detectedTimeOffsetMs']:.1f}ms) <= 500ms"
        )
        passed += 1
    finally:
        if os.path.exists(wav_live):
            os.remove(wav_live)

    # ------------------------------------------------------------------
    # TEST 12: SPOOF rejection when score < effectiveThreshold (even if >= 0.45)
    # ------------------------------------------------------------------
    # Test via /api/sync with calibrated user test_user (effectiveThreshold = 0.57)
    # where alignedCorrelation is mocked to 0.52 (>= 0.45 global, < 0.57 adaptive)
    wav_mid, lip_mov_mid, lip_ts_mid = create_modulated_audio_and_lips(duration_sec=4.0, offset_sec=0.0)
    try:
        mocked_sync_52 = {
            "audioDurationMs": 4000,
            "rawCorrelation": 0.50,
            "alignedCorrelation": 0.52,
            "detectedTimeOffsetMs": 33.33,
            "effectiveThreshold": 0.57,
            "isSynchronized": False,
            "syncStatus": "SPOOF",
            "validFrames": 120,
            "ignoredFrames": 0,
            "averageAudioEnergy": 0.25,
            "syncReason": "Lip-voice cross-correlation (0.52) is below the required threshold (0.57).",
            "signalSeries": {"timestampsMs": [0.0, 1000.0], "normalizedLip": [0.2, 0.8], "normalizedAudio": [0.2, 0.8]},
        }
        mocked_whisper_res = {
            "success": True,
            "text": "blue tiger jumps 42",
            "confidence": 0.95,
        }
        with patch("backend.routes.liveness.sync_service.calculate_sync_metrics", return_value=mocked_sync_52), \
             patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mocked_whisper_res):
            with open(wav_mid, "rb") as f_audio:
                resp_12 = client.post(
                    "/api/sync",
                    files={"file": ("recording.wav", f_audio, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov_mid),
                        "lipTimestamps": json.dumps(lip_ts_mid),
                        "sessionId": "test-adaptive-spoof-12",
                        "userId": test_user,
                        "challengePhrase": "blue tiger jumps 42",
                    },
                )
        assert resp_12.status_code == 200
        data_12 = resp_12.json()
        assert data_12["adaptiveCalibrationUsed"] is True
        assert data_12["globalThreshold"] == 0.45
        assert data_12["adaptiveThreshold"] == 0.57
        assert data_12["effectiveThreshold"] == 0.57
        assert data_12["livenessResult"] == "SPOOF"
        assert "0.52" in data_12["rejectionReason"] and "0.57" in data_12["rejectionReason"]
        print(
            f"[PASS] 12. SPOOF rejection when score (0.52) < effectiveThreshold (0.57): "
            f"livenessResult={data_12['livenessResult']}, reason='{data_12['rejectionReason']}'"
        )
        passed += 1
    finally:
        if os.path.exists(wav_mid):
            os.remove(wav_mid)

    # ------------------------------------------------------------------
    # TEST 13: SPOOF rejection when |offset| > 500 ms even if score >= effectiveThreshold
    # ------------------------------------------------------------------
    # Use a single Gaussian pulse centered at t = 1.5s in audio, and at t = 2.2s in lips (+700ms lag)
    samplerate = 16000
    dur_13 = 4.5
    t_aud_13 = np.linspace(0, dur_13, int(samplerate * dur_13), endpoint=False)
    aud_env_13 = np.exp(-0.5 * ((t_aud_13 - 1.5) / 0.22) ** 2)
    aud_sig_13 = (aud_env_13 * np.sin(2 * np.pi * 440.0 * t_aud_13) * 0.8).astype(np.float32)
    tmp_13 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp_13.name, aud_sig_13, samplerate)
    tmp_13.close()
    wav_lag = tmp_13.name

    num_frames_13 = int(dur_13 * 30)
    lip_ts_lag = [100.0 + i * (1000.0 / 30.0) for i in range(num_frames_13)]
    t_lip_13 = np.array([(ts - lip_ts_lag[0]) / 1000.0 for ts in lip_ts_lag])
    lip_mov_lag = [float(0.1 + 0.3 * np.exp(-0.5 * ((t - 2.2) / 0.22) ** 2)) for t in t_lip_13]

    try:
        sync_metrics_lag = sync_service.calculate_sync_metrics(
            lip_movement=lip_mov_lag,
            lip_timestamps=lip_ts_lag,
            audio_filepath=wav_lag,
            correlation_threshold=0.57,
        )
        assert abs(sync_metrics_lag["detectedTimeOffsetMs"]) > 500.0, (
            f"Expected |offset| > 500ms, got {sync_metrics_lag['detectedTimeOffsetMs']}"
        )
        assert sync_metrics_lag["alignedCorrelation"] >= 0.57
        assert sync_metrics_lag["isSynchronized"] is False
        assert sync_metrics_lag["syncStatus"] == "SPOOF"
        assert "exceeds the maximum allowed threshold (500ms)" in sync_metrics_lag["syncReason"]
        print(
            f"[PASS] 13. SPOOF rejection when |offset| ({sync_metrics_lag['detectedTimeOffsetMs']:.1f}ms) > 500ms "
            f"even with high aligned correlation ({sync_metrics_lag['alignedCorrelation']:.2f})"
        )
        passed += 1
    finally:
        if os.path.exists(wav_lag):
            os.remove(wav_lag)

    # ------------------------------------------------------------------
    # TEST 14: MongoDB / Persistence storage of calibration profile + API endpoints
    # ------------------------------------------------------------------
    api_user = "test-api-cal-user-14"
    mongodb_service.delete_calibration_profile(api_user)

    # 14a. Start calibration via API
    start_resp = client.post(
        "/api/calibration/start",
        data={"userId": api_user, "sessionId": "api-cal-start", "requiredSamples": 3},
    )
    assert start_resp.status_code == 200
    assert start_resp.json()["calibrationStatus"] == "IN_PROGRESS"

    # 14b. Submit 3 valid calibration samples via POST /api/calibration/sample
    wav_cal, lip_mov_cal, lip_ts_cal = create_modulated_audio_and_lips(duration_sec=4.0, offset_sec=0.0)
    try:
        for idx, mocked_score in enumerate([0.62, 0.68, 0.65], start=1):
            mock_sync = {
                "audioDurationMs": 4000,
                "rawCorrelation": mocked_score,
                "alignedCorrelation": mocked_score,
                "detectedTimeOffsetMs": 0.0,
                "effectiveThreshold": 0.45,
                "isSynchronized": True,
                "syncStatus": "LIVE",
                "validFrames": 120,
                "ignoredFrames": 0,
                "averageAudioEnergy": 0.25,
                "syncReason": "Lip-voice movement synchronized.",
                "signalSeries": {"timestampsMs": [0.0, 1000.0], "normalizedLip": [0.2, 0.8], "normalizedAudio": [0.2, 0.8]},
            }
            mock_whisper_cal = {
                "success": True,
                "text": "green river flows 99",
                "confidence": 0.96,
            }
            with patch("backend.routes.liveness.sync_service.calculate_sync_metrics", return_value=mock_sync), \
                 patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mock_whisper_cal):
                with open(wav_cal, "rb") as f_audio:
                    sample_resp = client.post(
                        "/api/calibration/sample",
                        files={"file": ("recording.wav", f_audio, "audio/wav")},
                        data={
                            "lipMovement": json.dumps(lip_mov_cal),
                            "lipTimestamps": json.dumps(lip_ts_cal),
                            "sessionId": f"api-cal-sess-{idx}",
                            "userId": api_user,
                            "challengePhrase": "green river flows 99",
                            "requiredSamples": 3,
                        },
                    )
            assert sample_resp.status_code == 200
            s_data = sample_resp.json()
            assert s_data["sampleAccepted"] is True
            assert s_data["currentSampleNumber"] == idx

        # 14c. Verify persisted profile via GET /api/calibration/status and mongodb_service
        status_resp = client.get(f"/api/calibration/status?userId={api_user}")
        assert status_resp.status_code == 200
        st_data = status_resp.json()
        assert st_data["isCalibrated"] is True
        assert st_data["calibrationStatus"] == "COMPLETE"
        assert st_data["calibrationProfile"]["validSamplesCount"] == 3
        assert st_data["calibrationProfile"]["validScores"] == [0.62, 0.68, 0.65]
        assert st_data["adaptiveThreshold"] == 0.57
        assert st_data["effectiveThreshold"] == 0.57
        assert st_data["globalThreshold"] == 0.45

        stored_doc = mongodb_service.get_calibration_profile(api_user)
        assert stored_doc is not None
        assert stored_doc["userId"] == api_user
        assert stored_doc["isCalibrated"] is True
        assert stored_doc["adaptiveThreshold"] == 0.57
        assert stored_doc["effectiveThreshold"] == 0.57
        print(
            f"[PASS] 14. Calibration profile persisted & retrieved via MongoDB service and API endpoints "
            f"(userId='{api_user}', recordId='{stored_doc.get('recordId')}')"
        )
        passed += 1
    finally:
        if os.path.exists(wav_cal):
            os.remove(wav_cal)
        mongodb_service.delete_calibration_profile(test_user)
        mongodb_service.delete_calibration_profile(low_score_user)
        mongodb_service.delete_calibration_profile(incomplete_user)
        mongodb_service.delete_calibration_profile(api_user)

    print("=" * 74)
    print(f"RESULTS: {passed}/{total} ADAPTIVE CALIBRATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 74)


if __name__ == "__main__":
    run_adaptive_calibration_tests()
