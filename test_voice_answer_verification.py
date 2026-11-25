import sys
import os
import io
import wave
import struct
import math
from pathlib import Path
from fastapi.testclient import TestClient

# Ensure ai-mock-interviewer/backend is in sys.path
backend_path = Path(__file__).resolve().parent / "ai-mock-interviewer" / "backend"
sys.path.insert(0, str(backend_path))

from main import app
from services.whisper_service import whisper_service

def generate_sine_wave_wav(duration_sec=1.5, sample_rate=16000, freq=440.0):
    """Generates an in-memory 16kHz mono WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        num_samples = int(duration_sec * sample_rate)
        for i in range(num_samples):
            value = int(32767.0 * 0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate))
            data = struct.pack('<h', value)
            wf.writeframesraw(data)
    buf.seek(0)
    return buf.getvalue()

def run_tests():
    print("=================================================================")
    print("Testing Voice Answer Capture & Transcription Endpoints")
    print("=================================================================")

    client = TestClient(app)

    # Test 1: Empty audio file upload
    print("\n[Test 1] Empty Audio Upload (0 bytes)")
    res = client.post(
        "/api/interview/transcribe-answer",
        files={"audio": ("empty.wav", b"", "audio/wav")},
        data={"language": "en"}
    )
    print(f"Status Code: {res.status_code}")
    print(f"Response: {res.json()}")
    assert res.status_code == 200
    assert res.json().get("success") is False
    assert "0 bytes" in res.json().get("error", "") or "Empty" in res.json().get("error", "")
    print("Test 1 PASSED: Empty audio safely caught and rejected without server crash.")

    # Test 2: Valid Audio Waveform Upload
    print("\n[Test 2] Audio Waveform Upload (Tone/Silence)")
    audio_bytes = generate_sine_wave_wav(duration_sec=2.0)
    res = client.post(
        "/api/interview/transcribe-answer",
        files={"audio": ("sample.wav", audio_bytes, "audio/wav")},
        data={"language": "en"}
    )
    print(f"Status Code: {res.status_code}")
    resp_data = res.json()
    print(f"Response: {resp_data}")
    assert res.status_code == 200
    assert resp_data.get("success") is True
    assert "text" in resp_data
    assert "confidence" in resp_data
    assert "duration" in resp_data
    print(f"Test 2 PASSED: Audio processed by Faster-Whisper. Duration: {resp_data.get('duration')}s.")

    # Test 3: Language support parameter
    print("\n[Test 3] Multilingual Language Parameter ('hi', 'mr')")
    for lang in ["hi", "mr", "en"]:
        res = client.post(
            "/interview/transcribe-answer",
            files={"audio": ("sample.wav", audio_bytes, "audio/wav")},
            data={"language": lang}
        )
        assert res.status_code == 200
        assert res.json().get("language") == lang
        print(f"  - Language '{lang}' routed correctly: success={res.json().get('success')}")
    print("Test 3 PASSED: Multilingual language parameters handled properly.")

    # Test 4: Multiple consecutive answer evaluations
    print("\n[Test 4] Consecutive Answer Evaluation Integrity")
    from services.interview_engine import evaluate_answer
    answers = [
        ("Python Developer", "A list in Python is mutable while a tuple is immutable. Tuples are faster and hashable."),
        ("Python Developer", "Dictionaries use hash tables with open addressing or chaining to resolve collisions, achieving average O(1) lookups."),
        ("Python Developer", "I don't know the answer to this question."),
    ]

    for idx, (role, ans) in enumerate(answers, 1):
        evaluation = evaluate_answer(
            question=f"Question {idx}",
            answer=ans,
            role=role,
            difficulty="Medium",
            interview_type="Technical",
            client=None
        )
        score = evaluation.get("score")
        print(f"  - Answer {idx} ('{ans[:35]}...'): Score = {score}/10, Eval Mode = {evaluation.get('evaluation_mode')}")
        if idx == 3:
            assert score == 0.0 or score == 0, f"Expected 0 for 'I don't know', got {score}"
        else:
            assert score > 0, f"Expected positive score for valid answer, got {score}"
    print("Test 4 PASSED: Consecutive answers scored accurately with strict zero-score for refusal.")

    print("\n=================================================================")
    print("ALL TESTS PASSED SUCCESSFULLY (100% PASS RATE)")
    print("=================================================================")

if __name__ == "__main__":
    run_tests()
