import os
import json
import tempfile
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

# Add parent directory to sys.path to run directly
import sys
from pathlib import Path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app import app
from backend.config import settings

client = TestClient(app)

def create_temp_audio(duration_sec: float) -> str:
    """Generates a simple sine wave audio file for test cases."""
    samplerate = 16000
    t = np.linspace(0, duration_sec, int(samplerate * duration_sec), endpoint=False)
    data = np.sin(2 * np.pi * 440 * t) * 0.5
    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(temp_wav.name, data, samplerate)
    return temp_wav.name

def run_tests():
    print("Starting integration test scenarios for /api/sync...")
    
    # Track test pass/fail status
    all_passed = True

    # SCENARIO 1: Valid synchronized recording (Durations match)
    audio_path = create_temp_audio(4.2)
    # Generate 120 frames (~4.0s duration: 100 to 4100 ms)
    timestamps = [100.0 + i * 33.33 for i in range(120)]
    movement = [0.1 + 0.05 * np.sin(i * 0.2) for i in range(120)]

    try:
        with open(audio_path, "rb") as audio_file:
            response = client.post(
                "/api/sync",
                files={"file": ("recording.wav", audio_file, "audio/wav")},
                data={
                    "lipMovement": json.dumps(movement),
                    "lipTimestamps": json.dumps(timestamps),
                    "sessionId": "test-session-1"
                }
            )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        res_data = response.json()
        assert res_data["success"] is True
        assert "audioDurationMs" in res_data
        assert "lipDurationMs" in res_data
        assert "durationDifferenceMs" in res_data
        assert res_data["durationDifferenceMs"] <= 500
        print("[PASS] Scenario 1 (Valid Synchronized Recording)")
    except Exception as e:
        print(f"[FAIL] Scenario 1 (Valid Synchronized Recording): {str(e)}")
        if 'response' in locals():
            print(f"Response: {response.status_code} - {response.text}")
        all_passed = False
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


    # SCENARIO 2: Duration mismatch (>500 ms difference)
    audio_path = create_temp_audio(5.0)  # 5000 ms audio duration
    # 3.0s lip duration (100 to 3100 ms = 3000 ms duration, diff = 2000 ms)
    timestamps = [100.0 + i * 33.33 for i in range(90)]
    movement = [0.1] * 90

    try:
        with open(audio_path, "rb") as audio_file:
            response = client.post(
                "/api/sync",
                files={"file": ("recording.wav", audio_file, "audio/wav")},
                data={
                    "lipMovement": json.dumps(movement),
                    "lipTimestamps": json.dumps(timestamps),
                    "sessionId": "test-session-2"
                }
            )
        
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        res_data = response.json()
        assert res_data["success"] is False
        assert res_data["error"]["code"] == "DURATION_MISMATCH"
        assert "durations do not match" in res_data["error"]["message"]
        print("[PASS] Scenario 2 (Duration Mismatch >500ms)")
    except Exception as e:
        print(f"[FAIL] Scenario 2 (Duration Mismatch >500ms): {str(e)}")
        if 'response' in locals():
            print(f"Response: {response.status_code} - {response.text}")
        all_passed = False
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


    # SCENARIO 3: Invalid JSON inputs
    audio_path = create_temp_audio(3.0)
    try:
        with open(audio_path, "rb") as audio_file:
            response = client.post(
                "/api/sync",
                files={"file": ("recording.wav", audio_file, "audio/wav")},
                data={
                    "lipMovement": "{invalid_json}",
                    "lipTimestamps": "[100, 200, 300]",
                    "sessionId": "test-session-3"
                }
            )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        res_data = response.json()
        assert res_data["success"] is False
        assert res_data["error"]["code"] == "INVALID_INPUT"
        print("[PASS] Scenario 3 (Invalid JSON)")
    except Exception as e:
        print(f"[FAIL] Scenario 3 (Invalid JSON): {str(e)}")
        all_passed = False
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


    # SCENARIO 4: Mismatched array lengths
    audio_path = create_temp_audio(3.0)
    try:
        with open(audio_path, "rb") as audio_file:
            response = client.post(
                "/api/sync",
                files={"file": ("recording.wav", audio_file, "audio/wav")},
                data={
                    "lipMovement": json.dumps([0.1, 0.2, 0.3]),
                    "lipTimestamps": json.dumps([100, 200]),
                    "sessionId": "test-session-4"
                }
            )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        res_data = response.json()
        assert res_data["success"] is False
        assert res_data["error"]["code"] == "INVALID_INPUT"
        assert "same length" in res_data["error"]["message"]
        print("[PASS] Scenario 4 (Mismatched Array Lengths)")
    except Exception as e:
        print(f"[FAIL] Scenario 4 (Mismatched Array Lengths): {str(e)}")
        all_passed = False
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


    # SCENARIO 5: Empty arrays / Insufficient samples (< settings.MIN_VALID_SYNC_FRAMES)
    audio_path = create_temp_audio(3.0)
    try:
        # Check settings.MIN_VALID_SYNC_FRAMES limit
        with open(audio_path, "rb") as audio_file:
            response = client.post(
                "/api/sync",
                files={"file": ("recording.wav", audio_file, "audio/wav")},
                data={
                    "lipMovement": json.dumps([0.1] * 5),
                    "lipTimestamps": json.dumps([100 + i*33 for i in range(5)]),
                    "sessionId": "test-session-5"
                }
            )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        res_data = response.json()
        assert res_data["success"] is False
        assert res_data["error"]["code"] == "INVALID_INPUT"
        assert "Insufficient samples" in res_data["error"]["message"]
        print("[PASS] Scenario 5 (Empty/Insufficient Samples)")
    except Exception as e:
        print(f"[FAIL] Scenario 5 (Empty/Insufficient Samples): {str(e)}")
        all_passed = False
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


    # SCENARIO 6: Non-numeric elements
    audio_path = create_temp_audio(3.0)
    try:
        with open(audio_path, "rb") as audio_file:
            response = client.post(
                "/api/sync",
                files={"file": ("recording.wav", audio_file, "audio/wav")},
                data={
                    "lipMovement": json.dumps(["a"] * 40),
                    "lipTimestamps": json.dumps([100 + i*33 for i in range(40)]),
                    "sessionId": "test-session-6"
                }
            )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        res_data = response.json()
        assert res_data["success"] is False
        assert res_data["error"]["code"] == "INVALID_INPUT"
        assert "numeric" in res_data["error"]["message"]
        print("[PASS] Scenario 6 (Non-numeric values)")
    except Exception as e:
        print(f"[FAIL] Scenario 6 (Non-numeric values): {str(e)}")
        all_passed = False
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


    # SCENARIO 7: Non-increasing timestamps
    audio_path = create_temp_audio(3.0)
    try:
        timestamps = [100.0, 133.0, 120.0] + [200.0 + i*33 for i in range(37)]
        with open(audio_path, "rb") as audio_file:
            response = client.post(
                "/api/sync",
                files={"file": ("recording.wav", audio_file, "audio/wav")},
                data={
                    "lipMovement": json.dumps([0.1] * 40),
                    "lipTimestamps": json.dumps(timestamps),
                    "sessionId": "test-session-7"
                }
            )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        res_data = response.json()
        assert res_data["success"] is False
        assert res_data["error"]["code"] == "INVALID_INPUT"
        assert "increasing" in res_data["error"]["message"]
        print("[PASS] Scenario 7 (Non-increasing timestamps)")
    except Exception as e:
        print(f"[FAIL] Scenario 7 (Non-increasing timestamps): {str(e)}")
        all_passed = False
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


    # SCENARIO 8: Corrupted audio / FFmpeg failure
    temp_txt = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    temp_txt.close()
    with open(temp_txt.name, "w") as f:
        f.write("Definitely not an audio or video file content.")
    
    timestamps = [100.0 + i * 33.33 for i in range(40)]
    movement = [0.1] * 40
    
    try:
        with open(temp_txt.name, "rb") as corrupt_file:
            response = client.post(
                "/api/sync",
                # Pass a corrupted text file but pretend it is audio/wav MIME type to bypass MIME check
                files={"file": ("recording.wav", corrupt_file, "audio/wav")},
                data={
                    "lipMovement": json.dumps(movement),
                    "lipTimestamps": json.dumps(timestamps),
                    "sessionId": "test-session-8"
                }
            )
        assert response.status_code == 422, f"Expected 422 for ffmpeg/soundfile fail on corrupted audio, got {response.status_code}"
        res_data = response.json()
        assert res_data["success"] is False
        assert res_data["error"]["code"] == "AUDIO_PROCESSING_ERROR"
        print("[PASS] Scenario 8 (Corrupted Audio / FFmpeg Failure)")
    except Exception as e:
        print(f"[FAIL] Scenario 8 (Corrupted Audio / FFmpeg Failure): {str(e)}")
        if 'response' in locals():
            print(f"Response: {response.status_code} - {response.text}")
        all_passed = False
    finally:
        if os.path.exists(temp_txt.name):
            os.remove(temp_txt.name)


    # SCENARIO 9: Invalid MIME file format checks
    temp_txt = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    temp_txt.close()
    with open(temp_txt.name, "w") as f:
        f.write("Simple text content.")
    
    timestamps = [100.0 + i * 33.33 for i in range(40)]
    movement = [0.1] * 40
    
    try:
        with open(temp_txt.name, "rb") as txt_file:
            response = client.post(
                "/api/sync",
                # Pass plain text file format
                files={"file": ("notes.txt", txt_file, "text/plain")},
                data={
                    "lipMovement": json.dumps(movement),
                    "lipTimestamps": json.dumps(timestamps),
                    "sessionId": "test-session-9"
                }
            )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        res_data = response.json()
        assert res_data["success"] is False
        assert res_data["error"]["code"] == "INVALID_INPUT"
        assert "Unsupported file format" in res_data["error"]["message"]
        print("[PASS] Scenario 9 (Invalid MIME Format Check)")
    except Exception as e:
        print(f"[FAIL] Scenario 9 (Invalid MIME Format Check): {str(e)}")
        all_passed = False
    finally:
        if os.path.exists(temp_txt.name):
            os.remove(temp_txt.name)

    if all_passed:
        print("\nALL SCENARIOS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("\nSOME SCENARIOS FAILED!")
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
