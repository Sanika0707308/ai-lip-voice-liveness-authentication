import React from 'react';
import CameraPreview from './components/CameraPreview';

function App() {
  return (
    <div className="app-shell">
      <div className="app-container">
        <header className="app-header-card">
          <div className="app-header-left">
            <div className="brand-logo-badge">
              <span className="brand-icon">🛡️</span>
              <span className="brand-name">LIVESCAN</span>
            </div>
            <div className="brand-text">
              <h1 className="brand-title">AI Lip-Voice Liveness Authentication</h1>
              <p className="brand-subtitle">
                Multimodal biometric presence verification correlating webcam lip movement, speech audio, and dynamic challenge phrases.
              </p>
            </div>
          </div>
          <div className="app-header-right">
            <div className="status-badge-chip">
              <span className="status-chip-dot"></span>
              <span className="status-chip-text">System Active &middot; Ready</span>
            </div>
          </div>
        </header>

        <main className="app-main-content">
          <CameraPreview />
        </main>

        <footer className="app-footer-card">
          <p>© 2026–2027 LIVESCAN · AI-Based Lip-Voice Synchronization for Liveness Authentication</p>
          <p className="footer-meta">
            MediaPipe Face Mesh · Faster-Whisper Multilingual ASR · Pearson Cross-Correlation (&plusmn;500ms Rule)
          </p>
        </footer>
      </div>
    </div>
  );
}

export default App;
