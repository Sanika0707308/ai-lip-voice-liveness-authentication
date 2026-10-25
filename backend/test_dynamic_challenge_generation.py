"""
Comprehensive Test Suite for Dynamic Random Challenge Phrase Generation.
Tests:
1. Randomness & Uniqueness across 100 consecutive generations.
2. Grammatical Templates (English, Hindi, Marathi).
3. Supported Languages & fallback for unsupported codes.
4. Duplicate Avoidance Engine (sliding window history).
5. Unique Attempt ID association & storage on the backend.
6. Anti-Tampering (frontend cannot alter server's expected challenge).
7. Anti-Replay / One-time attempt validation.
8. Challenge Expiration (TTL timeout validation).
9. Speech Recognition & Phrase Matching consistency.
"""

import sys
import os
import json
import time
import tempfile
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app import app
from backend.config import settings
from backend.services.attempt_tracker import attempt_tracker
from backend.services.challenge_service import (
    generate_random_challenge,
    generate_english_challenge,
    generate_hindi_challenge,
    generate_marathi_challenge,
    duplicate_filter,
)
from backend.services.verification_service import normalize_text

client = TestClient(app)

def create_temp_audio(duration_sec=3.5, samplerate=16000):
    t = np.linspace(0, duration_sec, int(samplerate * duration_sec), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, sig, samplerate)
    tmp.close()
    return tmp.name

def run_challenge_generation_tests():
    print("=" * 78)
    print("RUNNING DYNAMIC CHALLENGE PHRASE GENERATION TEST SUITE")
    print("=" * 78)

    passed = 0
    total = 0

    # ------------------------------------------------------------------
    # TEST 1: Randomness & High Uniqueness (> 90% unique across 100 generations)
    # ------------------------------------------------------------------
    total += 1
    for lang in ["en", "hi", "mr"]:
        duplicate_filter.clear()
        generated = [generate_random_challenge(language=lang) for _ in range(100)]
        unique_count = len(set(generated))
        assert unique_count >= 90, f"Expected >= 90 unique phrases for {lang}, got {unique_count}"
        for phrase in generated:
            tokens = phrase.split()
            assert 4 <= len(tokens) <= 7, f"Phrase should have 4-7 tokens: '{phrase}'"
            assert not any(ch.isdigit() for ch in phrase), f"Phrase must not contain digits: '{phrase}'"
        print(f"[PASS] 1. Randomness & Uniqueness ({lang.upper()}): {unique_count}/100 unique phrases, all 4-7 words, 0 digits.")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 2: Grammatical Templates - English ('en')
    # Meaningful everyday sentences (4-7 words), strictly NO numbers/digits.
    # e.g., "Today is a beautiful day", "I enjoy reading good books"
    # ------------------------------------------------------------------
    total += 1
    for _ in range(50):
        phrase = generate_english_challenge()
        tokens = phrase.split()
        assert 4 <= len(tokens) <= 7, f"English phrase must have 4-7 tokens: '{phrase}'"
        assert not any(ch.isdigit() for ch in phrase), f"English phrase must not contain digits: '{phrase}'"
        assert phrase[0].isupper(), f"Sentence should be capitalized: '{phrase}'"
    print("[PASS] 2. English Grammatical Templates verified (meaningful 4-7 word natural sentences, 0 digits).")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 3: Grammatical Templates - Hindi ('hi')
    # Meaningful everyday sentences (4-7 words), strictly NO numbers/digits.
    # e.g., "आज का दिन बहुत सुंदर है", "मुझे किताबें पढ़ना बहुत पसंद है"
    # ------------------------------------------------------------------
    total += 1
    for _ in range(50):
        phrase = generate_hindi_challenge()
        tokens = phrase.split()
        assert 4 <= len(tokens) <= 7, f"Hindi phrase must have 4-7 tokens: '{phrase}'"
        assert not any(ch.isdigit() for ch in phrase), f"Hindi phrase must not contain digits: '{phrase}'"
        assert any('\u0900' <= ch <= '\u097F' for ch in phrase), f"Expected Devanagari script: '{phrase}'"
    print("[PASS] 3. Hindi Grammatical Templates verified (meaningful 4-7 word natural sentences, 0 digits).")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 4: Grammatical Templates - Marathi ('mr')
    # Meaningful everyday sentences (4-7 words), strictly NO numbers/digits.
    # e.g., "आजचा दिवस छान आहे", "मला गाणी ऐकायला आवडतात"
    # ------------------------------------------------------------------
    total += 1
    for _ in range(50):
        phrase = generate_marathi_challenge()
        tokens = phrase.split()
        assert 4 <= len(tokens) <= 7, f"Marathi phrase must have 4-7 tokens: '{phrase}'"
        assert not any(ch.isdigit() for ch in phrase), f"Marathi phrase must not contain digits: '{phrase}'"
        assert any('\u0900' <= ch <= '\u097F' for ch in phrase), f"Expected Devanagari script: '{phrase}'"
    print("[PASS] 4. Marathi Grammatical Templates verified (meaningful 4-7 word natural sentences, 0 digits).")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 5: Supported Languages & Fallback Behavior
    # ------------------------------------------------------------------
    total += 1
    res_en = client.get("/api/transcribe/challenge/new?sessionId=test-lang-en&language=en")
    assert res_en.status_code == 200 and res_en.json()["language"] == "en"

    res_hi = client.get("/api/transcribe/challenge/new?sessionId=test-lang-hi&language=hi")
    assert res_hi.status_code == 200 and res_hi.json()["language"] == "hi"

    res_mr = client.get("/api/transcribe/challenge/new?sessionId=test-lang-mr&language=mr")
    assert res_mr.status_code == 200 and res_mr.json()["language"] == "mr"

    # Unsupported language fallback -> 'en'
    res_fallback = client.get("/api/transcribe/challenge/new?sessionId=test-lang-fb&language=german")
    assert res_fallback.status_code == 200 and res_fallback.json()["language"] == "en"
    print("[PASS] 5. Supported languages ('en', 'hi', 'mr') and unsupported fallback ('en') verified.")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 6: Duplicate Avoidance Engine (Consecutive Duplicate Prevention)
    # ------------------------------------------------------------------
    total += 1
    duplicate_filter.clear()
    sample_phrase = "Today is a beautiful day"
    duplicate_filter.record("en", sample_phrase)
    assert duplicate_filter.is_recent("en", sample_phrase) is True
    assert duplicate_filter.is_recent("en", "I enjoy reading good books") is False

    # Consecutive generations never match the immediate predecessor
    prev = None
    for _ in range(30):
        curr = generate_random_challenge("en")
        assert curr != prev, f"Immediate duplicate detected: {curr}"
        prev = curr
    print("[PASS] 6. Duplicate Avoidance Engine verified (sliding window history prevents immediate repetition).")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 7: Attempt Association & Expiration (TTL) Metadata
    # ------------------------------------------------------------------
    total += 1
    sess_meta = "test-attempt-meta-sess"
    res_meta = client.get(f"/api/transcribe/challenge/new?sessionId={sess_meta}&language=hi")
    assert res_meta.status_code == 200
    meta_json = res_meta.json()
    assert "attemptId" in meta_json and meta_json["attemptId"].startswith("att-")
    assert meta_json["expiresInSeconds"] == 60
    assert meta_json["language"] == "hi"

    rec = attempt_tracker.get_challenge_record(sess_meta)
    assert rec["attempt_id"] == meta_json["attemptId"]
    assert rec["challenge"] == meta_json["challengePhrase"]
    assert rec["status"] == "ACTIVE"
    print(f"[PASS] 7. Attempt metadata verified: attemptId='{meta_json['attemptId']}', TTL={meta_json['expiresInSeconds']}s.")
    passed += 1

    # ------------------------------------------------------------------
    # TEST 8: Anti-Tampering (Server's Expected Phrase Is Authoritative)
    # ------------------------------------------------------------------
    total += 1
    sess_tamper = "test-tamper-sess-1"
    res_tamper = client.get(f"/api/transcribe/challenge/new?sessionId={sess_tamper}&language=en")
    server_phrase = res_tamper.json()["challengePhrase"]

    audio_tamper = create_temp_audio(duration_sec=3.5)
    lip_mov = [0.2 + 0.05 * np.sin(i * 0.2) for i in range(100)]
    lip_ts = [100.0 + i * 33.33 for i in range(100)]

    try:
        # Client tries to pass a different challengePhrase in Form data
        tampered_client_phrase = "completely different forged phrase today"
        mock_whisper = {
            "success": True,
            "text": server_phrase,
            "confidence": 0.95,
            "language": "en"
        }
        with patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mock_whisper):
            with open(audio_tamper, "rb") as f_aud:
                resp_tamper = client.post(
                    "/api/sync",
                    data={
                        "sessionId": sess_tamper,
                        "challengePhrase": tampered_client_phrase,
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "language": "en"
                    },
                    files={"file": ("audio.wav", f_aud, "audio/wav")}
                )
            assert resp_tamper.status_code == 200
            data_t = resp_tamper.json()
            # The backend must have verified against server_phrase, not tampered_client_phrase
            assert data_t["challengePhrase"] == server_phrase
            assert data_t["isChallengeMatch"] is True
            print(f"[PASS] 8. Anti-tampering enforced: Server strictly used authoritative phrase '{server_phrase}' instead of client submitted '{tampered_client_phrase}'.")
            passed += 1
    finally:
        if os.path.exists(audio_tamper):
            os.remove(audio_tamper)

    # ------------------------------------------------------------------
    # TEST 9: Anti-Replay / One-Time Attempt Consumption
    # ------------------------------------------------------------------
    total += 1
    # The session sess_tamper was already evaluated and consumed in Test 8!
    audio_replay = create_temp_audio(duration_sec=3.5)
    try:
        with open(audio_replay, "rb") as f_aud:
            resp_replay = client.post(
                "/api/sync",
                data={
                    "sessionId": sess_tamper,
                    "challengePhrase": server_phrase,
                    "lipMovement": json.dumps(lip_mov),
                    "lipTimestamps": json.dumps(lip_ts),
                    "language": "en"
                },
                files={"file": ("audio.wav", f_aud, "audio/wav")}
            )
        assert resp_replay.status_code == 400
        replay_err = resp_replay.json()
        assert replay_err["error"]["code"] == "CHALLENGE_ALREADY_USED"
        print(f"[PASS] 9. Anti-replay enforced: Replayed session correctly blocked with {replay_err['error']['code']}.")
        passed += 1
    finally:
        if os.path.exists(audio_replay):
            os.remove(audio_replay)

    # ------------------------------------------------------------------
    # TEST 10: Expiration (TTL Timeout) Validation
    # ------------------------------------------------------------------
    total += 1
    sess_expire = "test-expire-sess-2"
    client.get(f"/api/transcribe/challenge/new?sessionId={sess_expire}&language=en")
    # Manually expire the session record
    with attempt_tracker._lock:
        attempt_tracker._sessions[sess_expire]["expires_at"] = time.time() - 10.0

    audio_expire = create_temp_audio(duration_sec=3.5)
    try:
        with open(audio_expire, "rb") as f_aud:
            resp_expire = client.post(
                "/api/sync",
                data={
                    "sessionId": sess_expire,
                    "lipMovement": json.dumps(lip_mov),
                    "lipTimestamps": json.dumps(lip_ts),
                    "language": "en"
                },
                files={"file": ("audio.wav", f_aud, "audio/wav")}
            )
        assert resp_expire.status_code == 400
        expire_err = resp_expire.json()
        assert expire_err["error"]["code"] == "CHALLENGE_EXPIRED"
        print(f"[PASS] 10. Challenge Expiration (TTL) enforced: Expired challenge blocked with {expire_err['error']['code']}.")
        passed += 1
    finally:
        if os.path.exists(audio_expire):
            os.remove(audio_expire)

    # ------------------------------------------------------------------
    # TEST 11: End-to-End Spoken Phrase Verification with Normalized Digits
    # ------------------------------------------------------------------
    total += 1
    sess_match = "test-match-sess-3"
    res_m = client.get(f"/api/transcribe/challenge/new?sessionId={sess_match}&language=en")
    expected_p = res_m.json()["challengePhrase"]

    audio_match = create_temp_audio(duration_sec=3.5)
    try:
        mock_whisper_pass = {
            "success": True,
            "text": expected_p,
            "confidence": 0.96,
            "language": "en"
        }
        with patch("backend.routes.liveness.whisper_service.transcribe_audio", return_value=mock_whisper_pass):
            with open(audio_match, "rb") as f_aud:
                resp_match = client.post(
                    "/api/sync",
                    data={
                        "sessionId": sess_match,
                        "lipMovement": json.dumps(lip_mov),
                        "lipTimestamps": json.dumps(lip_ts),
                        "language": "en"
                    },
                    files={"file": ("audio.wav", f_aud, "audio/wav")}
                )
            assert resp_match.status_code == 200
            assert resp_match.json()["isChallengeMatch"] is True
            print(f"[PASS] 11. End-to-End Phrase Matching verified for challenge '{expected_p}'.")
            passed += 1
    finally:
        if os.path.exists(audio_match):
            os.remove(audio_match)

    print("=" * 78)
    print(f"RESULTS: {passed}/{total} DYNAMIC CHALLENGE TESTS PASSED SUCCESSFULLY!")
    print("=" * 78)
    return passed == total

if __name__ == "__main__":
    success = run_challenge_generation_tests()
    sys.exit(0 if success else 1)
