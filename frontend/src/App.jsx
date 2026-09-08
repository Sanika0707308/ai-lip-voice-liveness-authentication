import React from 'react'
import CameraPreview from './components/CameraPreview'

function App() {
  return (
    <div className="app-container">
      <div className="glass-card">
        <header className="header">
          <div className="logo-badge">LIV / 01 · PRESENCE CHECK</div>
          <h1>Prove you are present.</h1>
          <p className="description">
            A short spoken challenge paired with live lip movement. Your camera and microphone
            are checked together before the session is approved.
          </p>
        </header>

        <main className="content">
          <div className="status-section">
            <div className="status-card">
              <span className="status-label">System Status</span>
              <div className="status-value-container">
                <span className="status-indicator-dot"></span>
                <span className="status-value">Ready for a live check</span>
              </div>
                <p className="status-subtext">Camera, microphone, speech, and lip-motion signals work as one check.</p>
            </div>
          </div>

          <CameraPreview />
        </main>

        <footer className="footer">
          <p>© 2026-2027 Biometric Liveness System Development Group. All Rights Reserved.</p>
        </footer>
      </div>
    </div>
  )
}

export default App

