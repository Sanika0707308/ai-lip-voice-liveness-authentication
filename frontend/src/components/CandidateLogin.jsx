import React, { useState } from 'react';
import { generateSessionId } from '../utils/challengePhrases';

const LANGUAGE_OPTIONS = [
  { code: 'en', label: 'English', native: 'English', desc: 'Default language for technical assessments' },
  { code: 'hi', label: 'हिन्दी', native: 'Hindi', desc: 'हिंदी भाषेतील मुलाखत आणि पडताळणी' },
  { code: 'mr', label: 'मराठी', native: 'Marathi', desc: 'मराठी भाषेतील मुलाखत आणि पडताळणी' }
];

export default function CandidateLogin({ onLoginSuccess }) {
  const [candidateName, setCandidateName] = useState('Alex Morgan');
  const [candidateEmail, setCandidateEmail] = useState('alex.morgan@example.com');
  const [interviewRole, setInterviewRole] = useState('Senior Fullstack Engineer');
  const [language, setLanguage] = useState('en');
  const [sessionId, setSessionId] = useState(() => generateSessionId());
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!candidateName.trim()) {
      setErrorMsg('Please enter your full name.');
      return;
    }
    if (!candidateEmail.trim() || !candidateEmail.includes('@')) {
      setErrorMsg('Please enter a valid email address.');
      return;
    }
    setErrorMsg('');
    setIsSubmitting(true);

    const candidateData = {
      candidateName: candidateName.trim(),
      candidateEmail: candidateEmail.trim(),
      interviewRole: interviewRole.trim(),
      language,
      sessionId: sessionId.trim() || generateSessionId(),
      loginTimestamp: new Date().toISOString()
    };

    setTimeout(() => {
      onLoginSuccess(candidateData);
    }, 400);
  };

  const handleRegenerateSessionId = () => {
    setSessionId(generateSessionId());
  };

  return (
    <div className="login-screen-wrap">
      <div className="login-container-card">
        {/* Brand Banner */}
        <div className="login-header">
          <div className="login-brand-icon">
            <span>🎙️</span>
          </div>
          <h1 className="login-title">LIVESCAN Interview Platform</h1>
          <p className="login-subtitle">
            Welcome to your remote technical interview. Please verify your credentials and proceed to biometric liveness verification.
          </p>
        </div>

        {errorMsg && (
          <div className="login-error-alert" role="alert">
            <span className="error-icon">⚠️</span>
            <span>{errorMsg}</span>
          </div>
        )}

        {/* Candidate Credentials Form */}
        <form onSubmit={handleSubmit} className="login-form">
          <div className="form-group">
            <label htmlFor="candidateName" className="form-label">
              Full Name <span className="req-star">*</span>
            </label>
            <input
              id="candidateName"
              type="text"
              className="form-input"
              placeholder="e.g. Alex Morgan"
              value={candidateName}
              onChange={(e) => setCandidateName(e.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="candidateEmail" className="form-label">
              Email / Candidate ID <span className="req-star">*</span>
            </label>
            <input
              id="candidateEmail"
              type="email"
              className="form-input"
              placeholder="e.g. alex.morgan@example.com"
              value={candidateEmail}
              onChange={(e) => setCandidateEmail(e.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="interviewRole" className="form-label">
              Interview Position / Role
            </label>
            <input
              id="interviewRole"
              type="text"
              className="form-input"
              placeholder="e.g. Senior Fullstack Engineer"
              value={interviewRole}
              onChange={(e) => setInterviewRole(e.target.value)}
            />
          </div>

          {/* Language Selection */}
          <div className="form-group">
            <label className="form-label">
              Verification & Interview Language <span className="req-star">*</span>
            </label>
            <div className="lang-selector-grid">
              {LANGUAGE_OPTIONS.map((item) => (
                <button
                  type="button"
                  key={item.code}
                  className={`lang-option-card ${language === item.code ? 'selected' : ''}`}
                  onClick={() => setLanguage(item.code)}
                >
                  <div className="lang-card-header">
                    <span className="lang-label">{item.label}</span>
                    {language === item.code && <span className="lang-check">✓</span>}
                  </div>
                  <span className="lang-sub">{item.desc}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Session ID */}
          <div className="form-group">
            <div className="session-id-header">
              <label htmlFor="sessionId" className="form-label">
                Interview Session Key
              </label>
              <button
                type="button"
                className="btn-text-link"
                onClick={handleRegenerateSessionId}
                title="Generate new unique session identifier"
              >
                Regenerate ID
              </button>
            </div>
            <input
              id="sessionId"
              type="text"
              className="form-input font-mono"
              value={sessionId}
              onChange={(e) => setSessionId(e.target.value)}
              readOnly
            />
          </div>

          {/* Pre-check Advisories */}
          <div className="precheck-box">
            <h4 className="precheck-title">Interview Integrity Guidelines:</h4>
            <ul className="precheck-list">
              <li>Position your face clearly in good lighting directly in front of the camera.</li>
              <li>Ensure your microphone is enabled and ambient background noise is minimal.</li>
              <li>You will recite a dynamic verification phrase before entering the interview dashboard.</li>
              <li>Maximum 3 verification attempts are allowed per session.</li>
            </ul>
          </div>

          {/* Submit Button */}
          <button
            type="submit"
            className="btn-primary-large"
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <span className="btn-spinner-content">
                <span className="spinner-dot"></span>
                <span>Initializing Session...</span>
              </span>
            ) : (
              <span>Proceed to Liveness Verification &rarr;</span>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
