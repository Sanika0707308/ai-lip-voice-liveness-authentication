# LIVESCAN 2.0: AI-Based Liveness Verification & Online Assessment Monitoring System 🛡️

**LIVESCAN 2.0** upgrades multimodal biometric anti-spoofing into a practical, AI-driven monitoring system designed specifically for **remote online examinations, technical interviews, and proctored assessments**.

It couples **dynamic challenge-response lip–voice synchronization** with real-time **multiple-person visual surveillance**, **acoustic background speech detection**, **device interruption auditing**, and **session compliance reporting**.

> **⚠️ Scope, Privacy & Advisory Proctoring Principles**
> * **Advisory Telemetry Only**: Detections represent potential anomalous events flagged for human proctor review. The system does **not** automatically accuse, penalize, or disqualify candidates based on automated triggers.
> * **Zero Facial Identity Matching**: The system performs no face identification, face recognition database matching, or biometric identity comparison.
> * **No Speaker Imputation**: Background speech detection analyzes acoustic vocal tract activity in the room. It does **not** claim to prove candidate cheating or identify specific speakers by audio alone.
> * **Raw Media Protection**: Audiovisual streams are processed in ephemeral rolling windows and are not permanently archived without institutional and candidate consent.
> * **Offline Resilient**: Continues functioning without interruption when MongoDB is offline through automatic self-healing JSONL fallback logging.

---

## 🔍 Architecture & Subsystem Modules

LIVESCAN 2.0 is composed of four integrated architectural pillars:

### 1. Multimodal Liveness Authentication Engine (Preserved from v1.0)
* **Dynamic Multilingual Challenges**: Generates unique, tamper-resistant spoken phrases in **English (`en`)**, **Hindi (`hi`)**, and **Marathi (`mr`)** (`/api/transcribe/challenge/new`).
* **Visual Lip Tracking (MediaPipe Face Mesh)**: Extracts 468-point 3D landmark contours, calculating frame-by-frame Vertical Aperture ($LVD$), Mouth Width ($LHD$), and Normalized Opening Ratio ($LVD / LHD$).
* **Multilingual Speech Recognition (Faster-Whisper)**: Local transcription via Faster-Whisper with Unicode NFC normalization, Devanagari matra/nasal handling, and canonical number mapping (`7` / `७` / `seven` / `सात`).
* **Lag Cross-Correlation (±500ms Rule)**: Correlates physical lip aperture curves with acoustic RMS envelopes.
  * Correlation Threshold: **`0.45`** (strictly preserved)
  * Maximum Allowed Audio–Visual Time Offset: **`±500 ms`** (strictly preserved)
  * Decision: Returns authoritative **`LIVE`** or **`SPOOF`** verdict.

### 2. Multiple-Person Visual Surveillance Module
* **Dual-Layer Person Detection**:
  * **Client Layer**: MediaPipe Face Mesh configured with `maxNumFaces: 4` for real-time browser multi-face landmark tracking and red warning bounding boxes.
  * **Backend Layer (`/api/monitoring/detect-persons`)**: Evaluates captured video frames using OpenCV HOG pedestrian detection and upper-body Haar cascades. Ensures full-person and seated upper-body detection are **not** treated as merely equivalent to frontal face detection.
* **Lighting Adaptation**: Automatically diagnoses underexposed or low-contrast frames and applies Contrast Limited Adaptive Histogram Equalization (CLAHE).
* **Temporal Debouncing**:
  * Requires **3 consecutive confirmation frames** before firing an `ADDITIONAL_PERSON_DETECTED` alert, eliminating momentary single-frame glitches.
  * Applies **5.0s cooldown deduplication** to avoid spamming alerts during continuous presence.
  * Auto-clears warning when count returns to 1, subject to a **2.0s confirmation period**.

### 3. Background Speech & Acoustic Surveillance Module
* **Rolling Audio Window Analysis (`/api/monitoring/audio-chunk`)**: Inspects 1.5–2.5 second audio windows without blocking verification workflows.
* **Acoustic Voice Activity Detection (VAD)**:
  * **RMS Energy**: Distinguishes ambient quiet room baseline from active acoustic energy (`VAD_ENERGY_THRESHOLD = 0.015`).
  * **Zero-Crossing Rate (ZCR)**: Matches natural human vocal tract frequency patterns ($0.04 \le ZCR \le 0.18$).
  * **Speech-Band Spectral Concentration**: Calculates energy in the human formant band ($300\text{ Hz} - 3400\text{ Hz}$) via FFT.
  * **Mechanical Noise Filtering**: Rejects impulsive keyboard typing and mouse clicks using transient crest-factor analysis ($> 6.0$).
  * **Stationary Drone Filtering**: Filters low-frequency air conditioning and fan drone ($< 200\text{ Hz}$).
  * **Whisper Linguistic Confirmation**: Confirms genuine spoken utterances for high-confidence speech segments.
* **Cooldown Deduplication**: Employs a 4.0-second cooldown between repeated speech notifications.

### 4. Unified Proctored Dashboard & Audit Reports
* **Unified Workspace**: Seamless tab navigation between **"Live Assessment Monitoring"** and **"Dynamic Challenge Verification"** while retaining camera and microphone streams.
* **Status Badges**: Real-time display of:
  * Session Status: `Ready`, `Monitoring` (pulsing timer), `Completed`.
  * Visible Person Count (with visual indicator).
  * Background Speech Meter (Quiet vs Speech detected).
  * Hardware Media Stream Health (Camera & Microphone status).
  * Clear visual separation between biometric liveness verdicts and advisory proctor alerts.
* **Interactive Audit Reports**:
  * Total monitoring incidents breakdown (multi-person, speech, hardware interruptions).
  * Chronological event timeline with timestamps, severity levels, and evidence snippets.
  * PDF/Print view and JSON report export.

---

## 🛠️ Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Frontend** | React 18, Vite 5, MediaPipe Face Mesh, Web Audio API, Canvas 2D |
| **Backend** | Python 3.10+, FastAPI, Uvicorn, OpenCV (Headless), Pillow |
| **Speech & Audio** | Faster-Whisper, FFmpeg, SoundFile, NumPy, SciPy (FFT VAD) |
| **Persistence** | MongoDB (`pymongo`) with automatic self-healing JSONL fallback |
| **Testing** | Pytest, FastAPI TestClient, NumPy synthetic test signal generators |

---

## 📁 Repository Structure

```text
lip-voice-liveness/
├── backend/
│   ├── app.py                           # FastAPI application entry point (v2.0.0)
│   ├── config.py                        # System settings & monitoring configurations
│   ├── requirements.txt                 # Backend dependencies
│   ├── test_monitoring_suite.py         # LIVESCAN 2.0 automated test suite (12 test cases)
│   ├── test_sync_endpoint.py            # Lip-voice synchronization endpoint tests
│   ├── test_liveness_pipeline.py        # End-to-end liveness pipeline & MongoDB tests
│   ├── test_adaptive_calibration.py     # Per-user adaptive calibration tests
│   ├── test_multilingual_liveness.py    # Multilingual challenge tests (EN/HI/MR)
│   ├── test_lip_movement_validation.py  # Lip movement variance & static photo attack tests
│   ├── routes/
│   │   ├── health.py                    # Subsystem health status
│   │   ├── liveness.py                  # POST /api/sync & calibration endpoints
│   │   ├── transcription.py             # Spoken challenge generation & transcription
│   │   └── monitoring.py                # LIVESCAN 2.0 session & surveillance routes
│   └── services/
│       ├── person_detection_service.py  # HOG & Upper-body multi-person detection service
│       ├── audio_monitoring_service.py   # Acoustic VAD & background speech monitor
│       ├── face_service.py              # Landmark geometry validation
│       ├── lip_service.py               # Lip aperture ratio & variance analysis
│       ├── whisper_service.py           # Faster-Whisper ASR model manager
│       ├── verification_service.py      # Unicode normalization & phrase matching
│       ├── sync_service.py              # Lag cross-correlation (±500ms rule)
│       └── mongodb_service.py           # MongoDB & JSONL fallback audit logger
├── frontend/
│   ├── index.html                       # HTML5 entry with Outfit & Plus Jakarta Sans typography
│   ├── package.json                     # Scripts & frontend dependencies
│   ├── vite.config.js                   # Vite dev server and /api reverse proxy
│   └── src/
│       ├── main.jsx                     # React DOM root
│       ├── App.jsx                      # LIVESCAN 2.0 header, navigation, and footer
│       ├── index.css                    # Unified dashboard design system
│       ├── components/
│       │   ├── CameraPreview.jsx        # Dual-mode camera, FaceMesh, and overlay canvas
│       │   ├── MonitoringPanel.jsx      # Surveillance control strip, status cards & alerts feed
│       │   └── SessionReportModal.jsx   # Interactive printable audit report dialog
│       └── utils/
│           ├── api.js                   # Client API helpers for monitoring & liveness
│           └── challengePhrases.js      # Session ID generator & fallback phrases
└── README.md                            # Documentation
```

---

## 🚀 Installation & Execution

### Prerequisites
* **Python**: `3.10+` (Tested on Python 3.13)
* **Node.js**: `18.0+`
* **FFmpeg**: Installed and available in system `PATH`
* **MongoDB** *(Optional)*: Runs on `mongodb://localhost:27017` (automatically falls back to `backend/temp/*.jsonl` if offline)

### 1. Setup and Run Backend

```bash
# From workspace root
cd backend

# Create virtual environment (if not already created)
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start backend server
python app.py
```
* Backend runs at: `http://127.0.0.1:8000`
* Interactive API Documentation (Swagger): `http://127.0.0.1:8000/docs`

### 2. Setup and Run Frontend

```bash
# In a separate terminal, from workspace root
cd frontend

# Install npm dependencies
npm install

# Start Vite development server
npm run dev
```
* Frontend runs at: `http://localhost:3000` (proxies `/api` requests to backend at `8000`).

---

## 🧪 Automated Testing Suite

The repository includes comprehensive automated tests covering all 12 core requirements, plus regression tests for the liveness pipeline:

### Run LIVESCAN 2.0 Monitoring Suite (12 Scenarios)
```bash
# Windows PowerShell:
$env:PYTHONIOENCODING="utf-8"
& ".venv\Scripts\python.exe" backend/test_monitoring_suite.py

# Or via pytest:
& ".venv\Scripts\python.exe" -m pytest backend/test_monitoring_suite.py
```

#### Test Cases Covered:
1. **One Person Visible**: Verified single person produces normal state with zero unauthorized person warnings.
2. **Two or More People Visible**: Verified additional person triggers warning after temporal confirmation frames.
3. **Temporary Fluctuation & Deduplication**: Verified 1-frame glitches are ignored and continuous multi-person presence does not repeatedly flood alerts.
4. **Silence Audio Window**: Verified silence (low RMS) produces `SILENCE` status without speech alerts.
5. **Speech-Like Background Audio**: Verified vocal formant signals in the 300–3400 Hz band trigger warning alerts.
6. **Ordinary Background Noise**: Verified fan hum (`BACKGROUND_HUM`) and keyboard typing (`MECHANICAL_NOISE`) are filtered out.
7. **Correct Challenge Spoken**: Verified matching spoken phrase passes verification ($\ge 70\%$).
8. **Incorrect Challenge Spoken**: Verified mismatched phrase fails with explicit rejection reason.
9. **Unsynchronized Audio & Video**: Verified time offset $> 500\text{ ms}$ or duration difference $> 500\text{ ms}$ results in `SPOOF`.
10. **Hardware Interruption**: Verified camera and microphone interruptions trigger critical audit events.
11. **MongoDB Offline Fallback**: Verified offline operation seamlessly logs and retrieves data from local JSONL storage.
12. **Start / Stop Monitoring Lifecycle**: Verified resource cleanup, in-memory state release, and full report generation.

### Run Existing Biometric Regression Suites
```bash
& ".venv\Scripts\python.exe" backend/test_sync_endpoint.py
& ".venv\Scripts\python.exe" backend/test_liveness_pipeline.py
& ".venv\Scripts\python.exe" backend/test_lip_movement_validation.py
$env:PYTHONIOENCODING="utf-8"; & ".venv\Scripts\python.exe" backend/test_multilingual_liveness.py
```

---

## 📋 Privacy, Limitations & Known Boundaries

* **Acoustic Ambiguity**: Background speech detection indicates vocal tract frequency energy. It cannot determine candidate intent or identify the speaker. All audio flags must be reviewed by a human proctor.
* **Occlusion & Lighting**: Extremely dark environments ($< 20\text{ lux}$) or heavy camera occlusion may degrade visual person detection. The CLAHE module mitigates moderate low-light conditions, but adequate lighting is required.
* **Microphone Sensitivity**: Loud music or television speech playing in an adjacent room may be detected as potential background speech.
* **Non-Punitive Design**: LIVESCAN 2.0 is designed as a monitoring aid, not an autonomous disciplinary tool.
