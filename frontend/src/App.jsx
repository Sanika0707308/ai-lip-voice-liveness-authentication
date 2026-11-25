import React, { useState } from 'react';
import CandidateLogin from './components/CandidateLogin';
import LivenessVerification from './components/LivenessVerification';
import InterviewDashboard from './components/InterviewDashboard';

export default function App() {
  // Workflow stages: 'LOGIN' | 'LIVENESS' | 'INTERVIEW'
  const [workflowStage, setWorkflowStage] = useState('LOGIN');
  const [candidateData, setCandidateData] = useState(() => {
    try {
      const saved = localStorage.getItem('candidateData');
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });
  const [activeMediaStream, setActiveMediaStream] = useState(null);
  const [sessionExpiredNotice, setSessionExpiredNotice] = useState(null);

  // 1. After successful login -> redirect to Liveness Verification
  const handleLoginSuccess = (candidateInfo) => {
    setCandidateData(candidateInfo);
    try {
      localStorage.setItem('candidateData', JSON.stringify(candidateInfo));
    } catch {}
    setWorkflowStage('LIVENESS');
    setSessionExpiredNotice(null);
  };

  // 2. After LIVE verification result -> directly open Interview Dashboard
  const handleVerificationSuccess = ({ stream, sessionId: verifiedSessionId, candidateData: verifiedData }) => {
    if (stream) {
      setActiveMediaStream(stream);
    }
    const updatedCandidate = {
      ...(candidateData || {}),
      ...(verifiedData || {}),
      sessionId: verifiedSessionId || candidateData?.sessionId || 'sess-' + Date.now(),
      livenessVerified: true
    };
    setCandidateData(updatedCandidate);
    try {
      localStorage.setItem('candidateData', JSON.stringify(updatedCandidate));
      localStorage.setItem('liveness_verified', 'true');
      localStorage.setItem('liveness_session_id', updatedCandidate.sessionId);
    } catch {}
    setWorkflowStage('INTERVIEW');
  };

  // 3. If verification session expires after 3 failures
  const handleVerificationSessionExpired = () => {
    setSessionExpiredNotice('Verification session expired: maximum 3 attempts exceeded.');
  };

  // 4. Return to login
  const handleRestartSession = () => {
    setCandidateData(null);
    setActiveMediaStream(null);
    setSessionExpiredNotice(null);
    try {
      localStorage.removeItem('candidateData');
    } catch {}
    setWorkflowStage('LOGIN');
  };

  return (
    <div className="app-shell-light">
      <div className="platform-container">
        {/* Top Executive Header */}
        <header className="platform-header-card">
          <div className="platform-header-left">
            <div className="platform-logo-badge">
              <span className="logo-symbol">🛡️</span>
              <span className="logo-brand-text">LIVESCAN 2.0</span>
            </div>
            <div className="platform-header-text">
              <h1 className="platform-title">AI Online Interview Platform</h1>
              <p className="platform-subtitle">
                Secure Remote Candidate Interviewing & Biometric Verification
              </p>
            </div>
          </div>

          <div className="platform-header-right">
            {/* Step Breadcrumb Indicators */}
            <nav className="workflow-steps-nav" aria-label="Workflow progress">
              <div className={`step-item ${workflowStage === 'LOGIN' ? 'step-active' : 'step-completed'}`}>
                <span className="step-circle">{workflowStage !== 'LOGIN' ? '✓' : '1'}</span>
                <span className="step-label">Candidate Login</span>
              </div>
              <span className="step-separator">&rsaquo;</span>
              <div className={`step-item ${workflowStage === 'LIVENESS' ? 'step-active' : workflowStage === 'INTERVIEW' ? 'step-completed' : 'step-pending'}`}>
                <span className="step-circle">{workflowStage === 'INTERVIEW' ? '✓' : '2'}</span>
                <span className="step-label">Liveness Check</span>
              </div>
              <span className="step-separator">&rsaquo;</span>
              <div className={`step-item ${workflowStage === 'INTERVIEW' ? 'step-active' : 'step-pending'}`}>
                <span className="step-circle">3</span>
                <span className="step-label">Interview Room</span>
              </div>
            </nav>
          </div>
        </header>

        {/* Main Stage Workflow Content */}
        <main className="platform-main-stage">
          {workflowStage === 'LOGIN' && (
            <CandidateLogin onLoginSuccess={handleLoginSuccess} />
          )}

          {workflowStage === 'LIVENESS' && candidateData && (
            <LivenessVerification
              candidateData={candidateData}
              onVerificationSuccess={handleVerificationSuccess}
              onSessionExpired={handleVerificationSessionExpired}
            />
          )}

          {workflowStage === 'INTERVIEW' && (
            <InterviewDashboard
              candidateData={candidateData || {
                candidateName: 'Candidate',
                candidateEmail: 'candidate@example.com',
                interviewRole: 'Software Specialist',
                roleId: 'python',
                language: 'en',
                sessionId: 'sess-' + Date.now()
              }}
              initialStream={activeMediaStream}
              onInterviewEnd={handleRestartSession}
            />
          )}
        </main>

        {/* Global Footer */}
        <footer className="platform-footer-card">
          <div className="footer-content-wrap">
            <p className="footer-copyright">
              &copy; 2026–2027 LIVESCAN 2.0 &middot; AI-Based Lip-Voice Liveness Authentication & Online Interview Platform
            </p>
            <p className="footer-spec-meta">
              MediaPipe Multi-Face Mesh &middot; Faster-Whisper Multilingual ASR (English / Hindi / Marathi) &middot; Pearson Cross-Correlation (&plusmn;500ms max offset)
            </p>
          </div>
        </footer>
      </div>
    </div>
  );
}
