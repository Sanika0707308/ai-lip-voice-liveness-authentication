# AI-Based Lip–Voice Synchronization for Liveness Authentication 🛡️

A multimodal liveness authentication system that verifies whether a person is physically present and speaking live in front of the camera by correlating **webcam-based lip movement**, **microphone-based speech energy**, and a **dynamically generated spoken challenge phrase**.

> **System Scope & Capabilities**
> * This is a **Lip–Voice Synchronization Based Liveness Authentication System**.
> * It verifies **live human presence** during a challenge-response session (`LIVE` vs `SPOOF`).
> * It is **not** a biometric face recognition or user identity verification system, and does **not** claim universal deepfake detection outside of challenge-response lip–voice synchronization.

---

## 🔍 Core Verification Pipeline

During each 5-second authentication attempt, the system evaluates three coupled signals:

1. **Dynamic Multilingual Challenge Generation (`/api/transcribe/challenge/new?sessionId=...&language=en|hi|mr`)**:
   Generates a unique session-bound spoken challenge phrase in **English (`en`)**, **Hindi (`hi`)**, or **Marathi (`mr`)**:
   * **English (`en`)**: 3–4 random words + 3–4 digit number (e.g., `"silent river moves 482"`).
   * **Hindi (`hi`)**: Natural Hindi words + Hindi number words in Devanagari script (e.g., `"नीला आकाश सात चार दो"`).
   * **Marathi (`mr`)**: Natural Marathi words + Marathi number words in Devanagari script (e.g., `"निळे आकाश सात चार दोन"`).
2. **Visual Lip Movement Tracking (MediaPipe Face Mesh)**:
   Extracts 3D lip contour landmarks in real time, computing vertical aperture ($LVD$), horizontal mouth width ($LHD$), and normalized lip opening ratio ($LVD / LHD$) with frame timestamps.
3. **Multilingual Speech Recognition & Phrase Verification (`WhisperService` + `VerificationEngine`)**:
   Converts uploaded audio to 16 kHz mono PCM WAV via FFmpeg, transcribes speech locally using Faster-Whisper (`tiny` for English, `small` with Devanagari prompt conditioning for Hindi and Marathi), normalizes Unicode NFC, Devanagari matras/nasals, punctuation, and controlled number representations (`7` / `७` / `seven` / `सात`), and computes a weighted match score (40% character similarity, 40% word match, 20% Whisper confidence; minimum threshold: `70.0%`).
4. **Language-Independent Lip–Voice Cross-Correlation & 500 ms Synchronization Rule (`SyncService`)**:
   Extracts the 30 ms RMS audio energy envelope aligned to lip frame timestamps, smooths and min-max normalizes both signals, and performs lag-adjusted Pearson cross-correlation:
   * **Minimum Global Synchronization Floor (`LIVE_SYNC_THRESHOLD`)**: `0.45` (or personalized per-user adaptive threshold $\ge 0.45$)
   * **Maximum Allowed Audio–Visual Time Offset (`MAX_SYNC_TIME_DIFF_MS`)**: `500 ms`
   * **Maximum Allowed Recording Duration Difference**: `500 ms`
   * **Minimum Valid Speech Frames (`MIN_VALID_SYNC_FRAMES`)**: `30`
5. **Final Decision & Audit Logging (MongoDB)**:
   Classifies the attempt as **`LIVE`** only if both challenge phrase verification and lip–voice synchronization pass; otherwise classifies as **`SPOOF`** with an explicit rejection reason, and logs the full diagnostic record (including `language`) to MongoDB (`liveness_db.verification_logs`, with local UTF-8 JSONL fallback).

---

## ⚠️ Multilingual Speech Recognition Limitations

* **Not 100% Recognition Accuracy**: Local speech recognition accuracy using Faster-Whisper varies across **English (`en`)**, **Hindi (`hi`)**, and **Marathi (`mr`)** depending on speaker accent, pronunciation speed, microphone hardware quality, and background noise.
* **Devanagari Phonetic & Orthographic Variations**: Whisper may occasionally transcribe phonetically close consonants (e.g., retroflex vs. dental stops or vowel length variations in Hindi/Marathi). While `normalize_text()` normalizes Unicode NFC, chandrabindu/anusvara, homorganic nasal conjuncts, punctuation, and digit/number words (`0`–`9` / `०`–`९`), severe acoustic distortion or heavy ambient noise can reduce the phrase similarity score below the `70.0%` threshold and trigger a conservative `SPOOF` rejection.
* **Language-Independent Synchronization**: The core lip–voice synchronization algorithm (`SyncService`) correlates physical mouth opening ratios with speech RMS energy envelopes and remains completely language-independent.

---

## 🛠️ Technology Stack

### Backend (Python API)
* **FastAPI & Uvicorn**: Asynchronous REST API for multilingual challenge generation, audio/visual sync analysis, adaptive calibration, and verification.
* **Faster-Whisper**: Local multilingual speech-to-text transcription (`en`, `hi`, `mr`) and confidence scoring.
* **FFmpeg & SoundFile**: Audio stream transcoding to 16 kHz mono PCM WAV and frame-level RMS envelope extraction.
* **NumPy & SciPy**: Signal smoothing, normalization, and lag cross-correlation analysis.
* **MongoDB (`pymongo`)**: Persistent audit logging of `LIVE` and `SPOOF` verification attempts and per-user calibration profiles.

### Frontend (User Interface)
* **React 18 & Vite**: Component-driven UI with language selector (`English`, `हिन्दी`, `मराठी`), real-time waveform telemetry, and post-session authentication summary cards.
* **MediaPipe Face Mesh**: Browser-side 468-point facial landmark detection and lip contour tracking.
* **Web Audio API & MediaRecorder**: Live microphone RMS metering, 5-second synchronized recording, and immediate hardware stream release after verification.

---

## 📁 Folder Structure

```text
lip-voice-liveness/
├── backend/
│   ├── app.py                           # FastAPI server entry point & startup hooks
│   ├── config.py                        # Thresholds, languages, timing rules, and database settings
│   ├── requirements.txt                 # Backend Python dependencies
│   ├── test_sync_endpoint.py            # Integration test suite for /api/sync (9 scenarios)
│   ├── test_liveness_pipeline.py        # End-to-end liveness pipeline & MongoDB tests
│   ├── test_adaptive_calibration.py     # Per-user adaptive threshold calibration tests (14 scenarios)
│   ├── test_multilingual_liveness.py    # Multilingual (EN/HI/MR) & failure mode tests (20 scenarios)
│   ├── routes/
│   │   ├── health.py                    # System & subsystem health check endpoint
│   │   ├── liveness.py                  # POST /api/sync & /api/calibration/* endpoints
│   │   └── transcription.py             # Multilingual challenge generation & Whisper routes
│   └── services/
│       ├── attempt_tracker.py           # Session rate limiting, active challenge & language binding
│       ├── calibration_service.py       # Per-user adaptive synchronization threshold service
│       ├── face_service.py              # Facial landmark & head-pose geometry validation
│       ├── lip_service.py               # Lip aperture ratio & movement variance analysis
│       ├── whisper_service.py           # Faster-Whisper multilingual model loading & transcription
│       ├── verification_service.py      # Multilingual Unicode/number normalization & similarity scoring
│       ├── sync_service.py              # Language-independent FFmpeg & lag cross-correlation engine
│       └── mongodb_service.py           # MongoDB verification_logs & calibration_profiles persistence
├── frontend/
│   ├── index.html                       # Root HTML template
│   ├── package.json                     # Frontend scripts and dependencies
│   ├── vite.config.js                   # Vite dev server and /api proxy configuration
│   └── src/
│       ├── main.jsx                     # React entry point
│       ├── App.jsx                      # Application layout and header
│       ├── index.css                    # Glassmorphic dark theme & language selector styles
│       ├── components/
│       │   └── CameraPreview.jsx        # Language selector, FaceMesh, 5s recording & Sync Analysis UI
│       └── utils/
│           ├── api.js                   # Backend API client helpers (language-aware)
│           └── challengePhrases.js      # Session ID and fallback challenge utilities
└── README.md                            # Project documentation
```

---

## 🚀 Getting Started

### Prerequisites
* **Python**: `3.10+`
* **Node.js**: `18.0+`
* **FFmpeg**: Installed and available in system `PATH`
* **MongoDB**: Running locally on `mongodb://localhost:27017` (automatically falls back to `backend/temp/db_fallback_logs.jsonl` if offline)

### 1. Start the Backend Server

```bash
cd backend
venv\Scripts\activate      # Windows (or source venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
python app.py
```
*The FastAPI backend runs at `http://127.0.0.1:8000` (Swagger UI: `http://127.0.0.1:8000/docs`).*

### 2. Start the Frontend Application

```bash
cd frontend
npm install
npm run dev
```
*The frontend runs at `http://localhost:3000` and proxies `/api` requests to `http://127.0.0.1:8000`.*

### 3. Run Automated Tests

```bash
backend\venv\Scripts\python.exe backend\test_sync_endpoint.py
backend\venv\Scripts\python.exe backend\test_liveness_pipeline.py
backend\venv\Scripts\python.exe backend\test_adaptive_calibration.py
backend\venv\Scripts\python.exe backend\test_multilingual_liveness.py
```

