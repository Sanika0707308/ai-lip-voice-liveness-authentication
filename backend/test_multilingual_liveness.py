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
from backend.routes.transcription import (
    generate_random_challenge,
    HI_ADJECTIVES,
    HI_NOUNS,
    HI_VERBS,
    HI_NUMBERS,
    MR_ADJECTIVES,
    MR_NOUNS,
    MR_VERBS,
    MR_NUMBERS,
)
from backend.services.verification_service import (
    normalize_text,
    normalize_language_code,
    VerificationEngine,
)
from backend.services.calibration_service import calibration_service
from backend.services.mongodb_service import mongodb_service
from backend.services.whisper_service import whisper_service

client = TestClient(app)


def create_modulated_audio_and_lips(
    duration_sec: float = 4.0,
    offset_sec: float = 0.0,
    noise_level: float = 0.0,
    uncorrelated: bool = False,
):
    """
    Generates a synthetic amplitude-modulated WAV file and matching (or uncorrelated)
    lip-opening trajectory with a non-periodic 3-syllable envelope.
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

    num_frames = int(duration_sec * 30)
    lip_timestamps_ms = [100.0 + i * (1000.0 / 30.0) for i in range(num_frames)]
    t_lip = np.array([(ts - lip_timestamps_ms[0]) / 1000.0 for ts in lip_timestamps_ms])

    rng = np.random.default_rng(42)
    if uncorrelated:
        # High-frequency uncorrelated sawtooth/random pattern
        lip_clean = 0.15 + 0.1 * np.cos(2 * np.pi * 2.7 * t_lip + 1.3) * (-1.0)
        lip_clean += rng.uniform(-0.08, 0.08, size=len(t_lip))
    else:
        lip_clean = 0.05 + 0.35 * syllable_envelope(t_lip - offset_sec)
        if noise_level > 0:
            lip_clean = lip_clean + rng.normal(0, noise_level, size=len(t_lip))

    lip_movement = [float(max(0.01, val)) for val in lip_clean]
    return temp_wav.name, lip_movement, lip_timestamps_ms


def run_multilingual_tests():
    print("=" * 78)
    print("RUNNING MULTILINGUAL CHALLENGE-RESPONSE LIVENESS TESTS (EN / HI / MR)")
    print("=" * 78)

    passed = 0
    total = 20

    # ------------------------------------------------------------------
    # TEST 1: Dynamic Challenge Generation for English, Hindi, Marathi
    # ------------------------------------------------------------------
    res_en = client.get("/api/transcribe/challenge/new?sessionId=ml-chal-en&language=en")
    assert res_en.status_code == 200
    data_en = res_en.json()
    assert data_en["language"] == "en"
    tokens_en = data_en["challengePhrase"].split()
    assert len(tokens_en) in (4, 5) and tokens_en[-1].isdigit() and len(tokens_en[-1]) in (3, 4)

    res_hi = client.get("/api/transcribe/challenge/new?sessionId=ml-chal-hi&language=hi")
    assert res_hi.status_code == 200
    data_hi = res_hi.json()
    assert data_hi["language"] == "hi"
    tokens_hi = data_hi["challengePhrase"].split()
    assert len(tokens_hi) in (5, 6)
    assert tokens_hi[0] in HI_ADJECTIVES
    assert tokens_hi[1] in HI_NOUNS
    assert all(t in HI_NUMBERS for t in tokens_hi[-3:])

    res_mr = client.get("/api/transcribe/challenge/new?sessionId=ml-chal-mr&language=mr")
    assert res_mr.status_code == 200
    data_mr = res_mr.json()
    assert data_mr["language"] == "mr"
    tokens_mr = data_mr["challengePhrase"].split()
    assert len(tokens_mr) in (5, 6)
    assert tokens_mr[0] in MR_ADJECTIVES
    assert tokens_mr[1] in MR_NOUNS
    assert all(t in MR_NUMBERS for t in tokens_mr[-3:])

    # Fallback on unsupported language code -> defaults to 'en'
    res_fb = client.get("/api/transcribe/challenge/new?sessionId=ml-chal-fb&language=fr")
    assert res_fb.status_code == 200 and res_fb.json()["language"] == "en"

    print(
        f"[PASS] 1. Challenge generation verified:\n"
        f"         EN: '{data_en['challengePhrase']}'\n"
        f"         HI: '{data_hi['challengePhrase']}'\n"
        f"         MR: '{data_mr['challengePhrase']}'"
    )
    passed += 1

    # ------------------------------------------------------------------
    # TEST 2: Unicode, Matra Preservation, Punctuation & Number Normalization
    # ------------------------------------------------------------------
    # English number normalization
    assert normalize_text("Verify Blue River 4829!", "en") == "verify blue river four eight two nine"
    assert normalize_text("verify blue river four eight two nine", "en") == "verify blue river four eight two nine"

    # Hindi normalization: Devanagari matras preserved, punctuation stripped, digits/Devanagari digits mapped to Hindi words
    hi_norm_1 = normalize_text("नमस्ते! नीला आकाश सात।", "hi")
    hi_norm_2 = normalize_text("नमस्ते नीला आकाश 7", "hi")
    hi_norm_3 = normalize_text("नमस्ते नीला आकाश ७", "hi")
    assert hi_norm_1 == "नमस्ते नीला आकाश सात", f"Got: {hi_norm_1}"
    assert hi_norm_1 == hi_norm_2 == hi_norm_3

    # Hindi chandrabindu canonicalization (पाँच vs पांच)
    assert normalize_text("तेज नदी पाँच", "hi") == normalize_text("तेज नदी पांच", "hi")
    assert normalize_text("तेज नदी 5", "hi") == normalize_text("तेज नदी पांच", "hi")

    # Marathi normalization: Devanagari matras preserved, digits/Devanagari digits mapped to Marathi words
    mr_norm_1 = normalize_text("नवीन पुस्तक दोन, चार, सहा, नऊ!", "mr")
    mr_norm_2 = normalize_text("नवीन पुस्तक 2 4 6 9", "mr")
    mr_norm_3 = normalize_text("नवीन पुस्तक २ ४ ६ ९", "mr")
    assert mr_norm_1 == "नवीन पुस्तक दोन चार सहा नऊ", f"Got: {mr_norm_1}"
    assert mr_norm_1 == mr_norm_2 == mr_norm_3

    print("[PASS] 2. Unicode NFC, Devanagari matra preservation, and controlled number normalization (EN/HI/MR) verified")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 3: English Correct Challenge + Synchronized Lip-Voice -> LIVE
    # ------------------------------------------------------------------
    wav_path, lip_mov, lip_ts = create_modulated_audio_and_lips(duration_sec=4.0, offset_sec=0.03)
    try:
        sess_en_live = "ml-sync-en-live"
        en_phrase = "silent river moves 482"
        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": "Silent river moves four eight two.",
                "language": "en",
                "confidence": 0.94,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("en_live.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": sess_en_live,
                        "userId": "ml-user-en",
                        "challengePhrase": en_phrase,
                        "language": "en",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "en"
        assert body["isChallengeMatch"] is True
        assert body["isSyncValid"] is True
        assert body["livenessResult"] == "LIVE"
        print(f"[PASS] 3. English (en) Correct Challenge + Sync -> LIVE (score={body['alignedCorrelation']:.3f})")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 4: English Incorrect Challenge -> SPOOF
        # ------------------------------------------------------------------
        sess_en_wrong = "ml-sync-en-wrong"
        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": "golden mountain jumps nine one two",
                "language": "en",
                "confidence": 0.92,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("en_wrong.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": sess_en_wrong,
                        "challengePhrase": en_phrase,
                        "language": "en",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["isChallengeMatch"] is False
        assert body["livenessResult"] == "SPOOF"
        print(f"[PASS] 4. English (en) Incorrect Challenge -> SPOOF (reason='{body['rejectionReason']}')")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 5: English Silence / No Speech -> SPOOF
        # ------------------------------------------------------------------
        sess_en_silent = "ml-sync-en-silent"
        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": "",
                "language": "en",
                "confidence": 0.0,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("en_silent.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": sess_en_silent,
                        "challengePhrase": en_phrase,
                        "language": "en",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["isChallengeMatch"] is False
        assert body["livenessResult"] == "SPOOF"
        assert "No speech" in (body["rejectionReason"] or "")
        print("[PASS] 5. English (en) Silence / No Speech -> SPOOF")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 6: Hindi Correct Challenge + Synchronized Lip-Voice -> LIVE
        # ------------------------------------------------------------------
        sess_hi_live = "ml-sync-hi-live"
        hi_phrase = "नीला आकाश बोले सात"
        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": "नीला आकाश बोले 7।",
                "language": "hi",
                "confidence": 0.91,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("hi_live.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": sess_hi_live,
                        "userId": "ml-user-hi",
                        "challengePhrase": hi_phrase,
                        "language": "hi",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "hi"
        assert body["isChallengeMatch"] is True
        assert body["isSyncValid"] is True
        assert body["livenessResult"] == "LIVE"
        print(f"[PASS] 6. Hindi (hi) Correct Challenge ('{hi_phrase}' vs 'नीला आकाश बोले 7।') + Sync -> LIVE")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 7: Hindi Incorrect Challenge -> SPOOF
        # ------------------------------------------------------------------
        sess_hi_wrong = "ml-sync-hi-wrong"
        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": "हरा पर्वत चले तीन",
                "language": "hi",
                "confidence": 0.89,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("hi_wrong.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": sess_hi_wrong,
                        "challengePhrase": hi_phrase,
                        "language": "hi",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "hi"
        assert body["isChallengeMatch"] is False
        assert body["livenessResult"] == "SPOOF"
        print(f"[PASS] 7. Hindi (hi) Incorrect Challenge -> SPOOF (reason='{body['rejectionReason']}')")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 8: Hindi Silence / No Speech -> SPOOF
        # ------------------------------------------------------------------
        sess_hi_silent = "ml-sync-hi-silent"
        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": "",
                "language": "hi",
                "confidence": 0.0,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("hi_silent.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": sess_hi_silent,
                        "challengePhrase": hi_phrase,
                        "language": "hi",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "hi"
        assert body["isChallengeMatch"] is False
        assert body["livenessResult"] == "SPOOF"
        print("[PASS] 8. Hindi (hi) Silence / No Speech -> SPOOF")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 9: Marathi Correct Challenge + Synchronized Lip-Voice -> LIVE
        # ------------------------------------------------------------------
        sess_mr_live = "ml-sync-mr-live"
        mr_phrase = "नवीन पुस्तक वाचा दोन"
        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": "नवीन पुस्तक वाचा २!",
                "language": "mr",
                "confidence": 0.90,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("mr_live.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": sess_mr_live,
                        "userId": "ml-user-mr",
                        "challengePhrase": mr_phrase,
                        "language": "mr",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "mr"
        assert body["isChallengeMatch"] is True
        assert body["isSyncValid"] is True
        assert body["livenessResult"] == "LIVE"
        print(f"[PASS] 9. Marathi (mr) Correct Challenge ('{mr_phrase}' vs 'नवीन पुस्तक वाचा २!') + Sync -> LIVE")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 10: Marathi Incorrect Challenge -> SPOOF
        # ------------------------------------------------------------------
        sess_mr_wrong = "ml-sync-mr-wrong"
        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": "सुंदर आकाश पहा नऊ",
                "language": "mr",
                "confidence": 0.88,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("mr_wrong.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": sess_mr_wrong,
                        "challengePhrase": mr_phrase,
                        "language": "mr",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "mr"
        assert body["isChallengeMatch"] is False
        assert body["livenessResult"] == "SPOOF"
        print(f"[PASS] 10. Marathi (mr) Incorrect Challenge -> SPOOF (reason='{body['rejectionReason']}')")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 11: Marathi Silence / No Speech -> SPOOF
        # ------------------------------------------------------------------
        sess_mr_silent = "ml-sync-mr-silent"
        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": "",
                "language": "mr",
                "confidence": 0.0,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("mr_silent.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": sess_mr_silent,
                        "challengePhrase": mr_phrase,
                        "language": "mr",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "mr"
        assert body["isChallengeMatch"] is False
        assert body["livenessResult"] == "SPOOF"
        print("[PASS] 11. Marathi (mr) Silence / No Speech -> SPOOF")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 12: Microphone Failure (Empty Audio File) -> 422 INVALID_INPUT
        # ------------------------------------------------------------------
        empty_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        empty_wav.close()
        try:
            with open(empty_wav.name, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("empty.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": "ml-mic-fail",
                        "challengePhrase": hi_phrase,
                        "language": "hi",
                    },
                )
            assert resp.status_code == 422
            assert resp.json()["error"]["code"] == "INVALID_INPUT"
            print("[PASS] 12. Microphone Failure (empty audio upload) rejected with 422 INVALID_INPUT")
            passed += 1
        finally:
            if os.path.exists(empty_wav.name):
                os.remove(empty_wav.name)

        # ------------------------------------------------------------------
        # TEST 13: Camera Failure (Insufficient Lip Frames < 30) -> 422 INVALID_INPUT
        # ------------------------------------------------------------------
        with open(wav_path, "rb") as f:
            resp = client.post(
                "/api/sync",
                files={"file": ("short_lips.wav", f, "audio/wav")},
                data={
                    "lipMovement": json.dumps(lip_mov[:10]),
                    "lipTimestamps": json.dumps(lip_ts[:10]),
                    "sessionId": "ml-cam-fail",
                    "challengePhrase": mr_phrase,
                    "language": "mr",
                },
            )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "INVALID_INPUT"
        print("[PASS] 13. Camera Failure (insufficient lip frames < 30) rejected with 422 INVALID_INPUT")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 14: Synchronization Failure (Low Correlation < 0.45 with Valid Hindi Speech) -> SPOOF
        # ------------------------------------------------------------------
        uncorr_wav, uncorr_lip, uncorr_ts = create_modulated_audio_and_lips(
            duration_sec=4.0, uncorrelated=True
        )
        try:
            with patch.object(
                whisper_service,
                "transcribe_audio",
                return_value={
                    "success": True,
                    "text": hi_phrase,
                    "language": "hi",
                    "confidence": 0.95,
                    "duration_ms": 4000,
                },
            ):
                with open(uncorr_wav, "rb") as f:
                    resp = client.post(
                        "/api/sync",
                        files={"file": ("uncorr.wav", f, "audio/wav")},
                        data={
                            "lipMovement": json.dumps(uncorr_lip),
                            "lipTimestamps": json.dumps(uncorr_ts),
                            "sessionId": "ml-sync-fail-hi",
                            "challengePhrase": hi_phrase,
                            "language": "hi",
                        },
                    )
            assert resp.status_code == 200
            body = resp.json()
            assert body["isChallengeMatch"] is True
            assert body["isSyncValid"] is False
            assert body["livenessResult"] == "SPOOF"
            print(
                f"[PASS] 14. Synchronization Failure (alignedCorrelation={body['alignedCorrelation']:.3f} < 0.45) -> SPOOF"
            )
            passed += 1
        finally:
            if os.path.exists(uncorr_wav):
                os.remove(uncorr_wav)

        # ------------------------------------------------------------------
        # TEST 15: Offset > 500 ms (800 ms Lip-Voice Lag with Valid Marathi Speech) -> SPOOF
        # ------------------------------------------------------------------
        lag_wav, lag_lip, lag_ts = create_modulated_audio_and_lips(
            duration_sec=4.0, offset_sec=0.80
        )
        try:
            with patch.object(
                whisper_service,
                "transcribe_audio",
                return_value={
                    "success": True,
                    "text": mr_phrase,
                    "language": "mr",
                    "confidence": 0.95,
                    "duration_ms": 4000,
                },
            ):
                with open(lag_wav, "rb") as f:
                    resp = client.post(
                        "/api/sync",
                        files={"file": ("lag.wav", f, "audio/wav")},
                        data={
                            "lipMovement": json.dumps(lag_lip),
                            "lipTimestamps": json.dumps(lag_ts),
                            "sessionId": "ml-offset-fail-mr",
                            "challengePhrase": mr_phrase,
                            "language": "mr",
                        },
                    )
            assert resp.status_code == 200
            body = resp.json()
            assert body["isChallengeMatch"] is True
            assert abs(body["detectedTimeOffsetMs"]) > 500
            assert body["isSyncValid"] is False
            assert body["livenessResult"] == "SPOOF"
            print(
                f"[PASS] 15. Offset > 500ms ({body['detectedTimeOffsetMs']}ms) rejected as SPOOF"
            )
            passed += 1
        finally:
            if os.path.exists(lag_wav):
                os.remove(lag_wav)

        # ------------------------------------------------------------------
        # TEST 16: Adaptive Threshold Usage with Multilingual Verification
        # ------------------------------------------------------------------
        cal_user = "ml-calibrated-user"
        mongodb_service.delete_calibration_profile(cal_user)
        calibration_service.start_new_calibration(user_id=cal_user, session_id="ml-cal-init", required_samples=3)
        for idx, s_val in enumerate([0.72, 0.75, 0.72], start=1):
            calibration_service.record_calibration_sample(
                user_id=cal_user,
                session_id=f"ml-cal-{idx}",
                aligned_correlation=s_val,
                detected_time_offset_ms=33.33,
                is_challenge_match=True,
                is_sync_valid=True,
                duration_mismatch=False,
                required_samples=3,
            )

        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": hi_phrase,
                "language": "hi",
                "confidence": 0.95,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("hi_cal.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": "ml-cal-verify-hi",
                        "userId": cal_user,
                        "challengePhrase": hi_phrase,
                        "language": "hi",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["adaptiveCalibrationUsed"] is True
        assert body["effectiveThreshold"] > 0.45
        assert body["livenessResult"] == "LIVE"
        print(
            f"[PASS] 16. Adaptive Threshold Usage verified in Hindi session: effectiveThreshold={body['effectiveThreshold']:.2f} (adaptiveCalibrationUsed=True)"
        )
        passed += 1

        # ------------------------------------------------------------------
        # TEST 17: Global Threshold Fallback for Uncalibrated User
        # ------------------------------------------------------------------
        uncal_user = "ml-uncalibrated-user"
        mongodb_service.delete_calibration_profile(uncal_user)
        with patch.object(
            whisper_service,
            "transcribe_audio",
            return_value={
                "success": True,
                "text": mr_phrase,
                "language": "mr",
                "confidence": 0.93,
                "duration_ms": 4000,
            },
        ):
            with open(wav_path, "rb") as f:
                resp = client.post(
                    "/api/sync",
                    files={"file": ("mr_uncal.wav", f, "audio/wav")},
                    data={
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "sessionId": "ml-uncal-verify-mr",
                        "userId": uncal_user,
                        "challengePhrase": mr_phrase,
                        "language": "mr",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["adaptiveCalibrationUsed"] is False
        assert body["effectiveThreshold"] == 0.45
        assert body["globalThreshold"] == 0.45
        print("[PASS] 17. Global Threshold Fallback (0.45) verified for uncalibrated user in Marathi session")
        passed += 1

        # ------------------------------------------------------------------
        # TEST 18, 19, 20: MongoDB Verification Record Persistence for EN, HI, MR
        # ------------------------------------------------------------------
        for idx, (uid, expected_lang, expected_chal) in enumerate(
            [
                ("ml-user-en", "en", en_phrase),
                ("ml-user-hi", "hi", hi_phrase),
                ("ml-user-mr", "mr", mr_phrase),
            ],
            start=18,
        ):
            history = mongodb_service.fetch_user_history(uid)
            assert len(history) > 0, f"No MongoDB/fallback history found for {uid}"
            doc = history[0]
            assert doc.get("language") == expected_lang, f"Expected language={expected_lang}, got {doc.get('language')}"
            assert doc.get("generatedChallenge") == expected_chal
            assert "transcribedChallenge" in doc
            assert doc.get("challengeMatchResult") == "PASS"
            assert "synchronizationScore" in doc
            assert "alignedCorrelation" in doc
            assert "detectedTimeOffsetMs" in doc
            assert doc.get("livenessResult") == "LIVE"
            assert "rejectionReason" in doc
            print(
                f"[PASS] {idx}. MongoDB record verified for language='{expected_lang}': "
                f"challenge='{doc['generatedChallenge']}', match={doc['challengeMatchResult']}, "
                f"syncScore={doc['synchronizationScore']:.3f}, offset={doc['detectedTimeOffsetMs']}ms, "
                f"result={doc['livenessResult']}"
            )
            passed += 1

    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

    print("=" * 78)
    print(f"RESULTS: {passed}/{total} MULTILINGUAL LIVENESS TESTS PASSED SUCCESSFULLY!")
    print("=" * 78)


if __name__ == "__main__":
    run_multilingual_tests()
