# Lip-Voice Liveness Detection System 🛡️

An advanced, multimodal biometric anti-spoofing and liveness detection system designed to secure authentication processes against video playback, photo representation, and synthetic voice/deepfake attacks. 

## Current milestone

The current frontend milestone runs without the Python backend. It provides camera and microphone permission handling, MediaPipe Face Mesh with lip-landmark rendering, real-time lip-opening metrics, local audio recording, and locally generated random speech challenges. Server-side speech verification and lip--voice synchronization are deferred to the next phase.

The system operates by capturing synchronized video and audio feeds and validating:
1. **Facial & Lip Integrity**: Extracts 3D facial mesh maps to verify structural geometry.
2. **Lip Movement Dynamics**: Measures the changes in lip aperture ratios over time to confirm active speech.
3. **Voice Activity Analysis**: Analyzes acoustic features and processes the verbal challenges.
4. **Cross-Modal Synchronization**: Computes the Pearson cross-correlation coefficient between the speech sound wave energy (audio envelope) and lip openings (visual coordinates).

---

## 🛠️ Technology Stack

### Backend (Python API)
*   **FastAPI**: Modern, high-performance web framework for constructing APIs.
*   **Uvicorn**: Lightning-fast ASGI web server implementation.
*   **OpenCV & MediaPipe** *(Future Phase)*: Decodes video frames and runs facial landmark regression models.
*   **OpenAI Whisper** *(Future Phase)*: Generates local, low-latency speech-to-text transcriptions.
*   **MongoDB** *(Future Phase)*: Stores authentication records, scoring logs, and analytics.

### Frontend (User Interface)
*   **React (v18)**: Component-driven interface library.
*   **Vite**: Frontend toolchain providing instant hot module reloading (HMR).
*   **Vanilla CSS**: Premium dark-mode glassmorphic theme styling with micro-animations.

---

## 📁 Folder Structure

```text
lip-voice-liveness/
├── backend/
│   ├── app.py                  # Server entry point & CORS configuration
│   ├── config.py               # Settings and assessment scoring thresholds
│   ├── requirements.txt        # Backend dependencies list
│   ├── routes/
│   │   ├── health.py           # Endpoint returning router status & loaded services
│   │   └── liveness.py         # Endpoints for biometric challenge and verify runs
│   └── services/
│       ├── face_service.py     # Face coordinates and pose verification (Placeholder)
│       ├── lip_service.py      # Lip aperture ratio and movement tracking (Placeholder)
│       ├── whisper_service.py  # Audio transcription match checking (Placeholder)
│       ├── sync_service.py     # Cross-correlation AV sync computation (Placeholder)
│       └── mongodb_service.py  # Database transaction and logging (Placeholder)
├── frontend/
│   ├── index.html              # HTML structure mounting the React application
│   ├── package.json            # Node project configuration and scripts
│   ├── vite.config.js          # Vite config containing backend endpoint proxy configurations
│   └── src/
│       ├── main.jsx            # React mounting hook
│       ├── App.jsx             # UI design containing layout state definitions
│       └── index.css           # Custom layout theme & animation keyframes
└── README.md                   # Project documentation
```

---

## 🚀 Getting Started

### Prerequisites
*   **Python**: Version `3.10` or higher installed.
*   **Node.js**: Version `18.0` or higher installed.

---

### 1. Setting Up the Backend

1.  Navigate into the `backend/` directory:
    ```bash
    cd backend
    ```
2.  Create a virtual environment (optional but recommended):
    ```bash
    python -m venv venv
    ```
3.  Activate the virtual environment:
    *   **Windows**: `venv\Scripts\activate`
    *   **macOS/Linux**: `source venv/bin/activate`
4.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
5.  Run the API development server:
    ```bash
    python app.py
    ```
    *The API will start at `http://127.0.0.1:8000`. You can access the auto-generated Swagger documentation at `http://127.0.0.1:8000/docs`.*

---

### 2. Setting Up the Frontend

1.  Navigate into the `frontend/` directory:
    ```bash
    cd frontend
    ```
2.  Install packages:
    ```bash
    npm install
    ```
3.  Launch the Vite server:
    ```bash
    npm run dev
    ```
    *The frontend will run at `http://localhost:3000` and automatically forward requests starting with `/api` to the backend.*

For the current milestone, run the frontend on its own and allow camera and microphone permissions in the browser.

---

## 🗺️ Future Development Roadmap

*   **Phase 1: Project Setup** ✅
*   **Phase 2: Webcam and Microphone Access** ✅
*   **Phase 3: Face Mesh and Lip Landmark Detection** ✅
*   **Phase 4: Random Speech Challenges and Local Recording** ✅
*   **Phase 5: Whisper Speech Recognition** (Local OpenAI Whisper transcription)
*   **Phase 6: Lip-Voice Synchronization** (Normalized Cross-Correlation)
*   **Phase 7: MongoDB Integration** (Audit logs and verification logs persistence)
*   **Phase 8: User Authentication** (JWT, login, and gateway endpoints)
*   **Phase 9: Testing & Final Deployment** (Unit/Integration tests & production Docker deployment)
