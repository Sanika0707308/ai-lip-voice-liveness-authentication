import React, { useState } from 'react';
import { generateSessionId } from '../utils/challengePhrases';

const LANGUAGE_OPTIONS = [
  { code: 'en', label: 'English', native: 'English', desc: 'Default language for technical assessments' },
  { code: 'hi', label: 'हिन्दी', native: 'Hindi', desc: 'हिंदी भाषेतील मुलाखत आणि पडताळणी' },
  { code: 'mr', label: 'मराठी', native: 'Marathi', desc: 'मराठी भाषेतील मुलाखत आणि पडताळणी' }
];

export const INTERVIEW_ROLES = [
  { id: 'python', title: 'Python Developer', icon: '🐍', desc: 'Data structures, OOP, exceptions, decorators, APIs, and practical scenarios' },
  { id: 'java', title: 'Java Developer', icon: '☕', desc: 'OOP, Collections, JVM architecture, Spring Boot, concurrency, and performance' },
  { id: 'fullstack', title: 'Full Stack Developer', icon: '💻', desc: 'React, Node/Python, REST APIs, database architecture, authentication, and scaling' },
  { id: 'frontend', title: 'Frontend Developer', icon: '🎨', desc: 'HTML5/CSS3, JavaScript ES6+, React, DOM, Core Web Vitals, and UI performance' },
  { id: 'backend', title: 'Backend Developer', icon: '⚙️', desc: 'Microservices, database design, caching, system design, API security, and queues' },
  { id: 'data_analyst', title: 'Data Analyst', icon: '📊', desc: 'SQL, Pandas, data cleaning, visualization, statistical modeling, and ETL pipelines' },
  { id: 'ai_ml', title: 'AI / ML Engineer', icon: '🧠', desc: 'Machine Learning, Deep Learning, PyTorch, Transformers, LLMs, and model deployment' },
  { id: 'cybersecurity', title: 'Cybersecurity Analyst', icon: '🔒', desc: 'Threat modeling, network security, penetration testing, SIEM, cryptography, and OWASP' },
  { id: 'tester', title: 'Software Tester (QA)', icon: '🧪', desc: 'Test automation, Selenium/Playwright, API testing, test lifecycle, and defect triage' },
  { id: 'devops', title: 'DevOps Engineer', icon: '🚀', desc: 'Docker, Kubernetes, CI/CD pipelines, Terraform, cloud infrastructure, and monitoring' },
  { id: 'cloud', title: 'Cloud Engineer', icon: '☁️', desc: 'AWS/Azure/GCP architecture, VPC networking, IAM security, and serverless compute' },
  { id: 'hr', title: 'HR & Behavioral', icon: '🤝', desc: 'Introduction, teamwork, crisis management, leadership, ethics, and career vision' },
  { id: 'custom', title: 'Custom Role', icon: '✨', desc: 'Specify any custom IT/CS specialization or target job title' }
];

export const EXPERIENCE_LEVELS = [
  { id: 'fresher', label: 'Fresher (0–2 Years)', desc: 'Core fundamentals, conceptual clarity, and academic/entry scenarios' },
  { id: 'experienced', label: 'Experienced (3+ Years)', desc: 'Architecture, design patterns, edge-cases, optimization, and leadership' }
];

export const QUESTION_COUNT_OPTIONS = [
  { count: 3, label: '3 Questions', desc: 'Quick assessment (~6 mins)' },
  { count: 5, label: '5 Questions', desc: 'Standard technical round (~10 mins) — Recommended' },
  { count: 7, label: '7 Questions', desc: 'Comprehensive technical round (~14 mins)' },
  { count: 10, label: '10 Questions', desc: 'In-depth senior evaluation (~20 mins)' }
];

export default function CandidateLogin({ onLoginSuccess }) {
  const [candidateName, setCandidateName] = useState('Alex Morgan');
  const [candidateEmail, setCandidateEmail] = useState('alex.morgan@example.com');
  const [selectedRole, setSelectedRole] = useState('python');
  const [customRoleText, setCustomRoleText] = useState('');
  const [candidateSkills, setCandidateSkills] = useState('Python, FastAPI, SQL, Git');
  const [experienceLevel, setExperienceLevel] = useState('fresher');
  const [questionCount, setQuestionCount] = useState(5);
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
    if (selectedRole === 'custom' && !customRoleText.trim()) {
      setErrorMsg('Please enter your custom role title.');
      return;
    }
    setErrorMsg('');
    setIsSubmitting(true);

    const roleObj = INTERVIEW_ROLES.find((r) => r.id === selectedRole) || INTERVIEW_ROLES[0];
    const effectiveRoleTitle = selectedRole === 'custom' ? customRoleText.trim() : roleObj.title;

    const candidateData = {
      candidateName: candidateName.trim(),
      candidateEmail: candidateEmail.trim(),
      roleId: selectedRole,
      interviewRole: effectiveRoleTitle,
      customRole: selectedRole === 'custom' ? customRoleText.trim() : '',
      skills: candidateSkills.trim(),
      experienceLevel,
      questionCount: parseInt(questionCount, 10) || 5,
      language,
      sessionId: sessionId.trim() || generateSessionId(),
      loginTimestamp: new Date().toISOString()
    };

    setTimeout(() => {
      onLoginSuccess(candidateData);
    }, 300);
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
          <h1 className="login-title">LIVESCAN 2.0 Interview Platform</h1>
          <p className="login-subtitle">
            Welcome to your remote technical interview. Verify your profile, select your technical specialization, and proceed to biometric verification.
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
          <div className="form-row-dual">
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
          </div>

          {/* Selectable Interview Track / Role */}
          <div className="form-group">
            <label className="form-label">
              Select Technical Track / Role <span className="req-star">*</span>
            </label>
            <div className="role-selector-grid">
              {INTERVIEW_ROLES.map((role) => (
                <button
                  type="button"
                  key={role.id}
                  className={`role-option-card ${selectedRole === role.id ? 'selected' : ''}`}
                  onClick={() => setSelectedRole(role.id)}
                >
                  <div className="role-card-top">
                    <span className="role-icon">{role.icon}</span>
                    <span className="role-title">{role.title}</span>
                    {selectedRole === role.id && <span className="role-check">✓</span>}
                  </div>
                  <p className="role-desc">{role.desc}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Custom Role Input if 'custom' selected */}
          {selectedRole === 'custom' && (
            <div className="form-group custom-role-highlight-box">
              <label htmlFor="customRoleText" className="form-label">
                Custom Role / Job Title <span className="req-star">*</span>
              </label>
              <input
                id="customRoleText"
                type="text"
                className="form-input"
                placeholder="e.g. Embedded Firmware Engineer, Blockchain Architect, Mobile iOS Developer..."
                value={customRoleText}
                onChange={(e) => setCustomRoleText(e.target.value)}
                required
              />
              <span className="form-input-hint">
                The interview engine will dynamically formulate technical questions tailored to this specialization.
              </span>
            </div>
          )}

          {/* Candidate Technical Skills */}
          <div className="form-group">
            <label htmlFor="candidateSkills" className="form-label">
              Core Technical Skills & Tools (Optional)
            </label>
            <input
              id="candidateSkills"
              type="text"
              className="form-input"
              placeholder="e.g. Python, Docker, React, PostgreSQL, Kubernetes, AWS"
              value={candidateSkills}
              onChange={(e) => setCandidateSkills(e.target.value)}
            />
            <span className="form-input-hint">
              List the frameworks, languages, or libraries you specialize in to guide question focus.
            </span>
          </div>

          {/* Selectable Experience Level */}
          <div className="form-group">
            <label className="form-label">
              Experience Level <span className="req-star">*</span>
            </label>
            <div className="level-selector-grid">
              {EXPERIENCE_LEVELS.map((lvl) => (
                <button
                  type="button"
                  key={lvl.id}
                  className={`level-option-card ${experienceLevel === lvl.id ? 'selected' : ''}`}
                  onClick={() => setExperienceLevel(lvl.id)}
                >
                  <div className="level-card-header">
                    <span className="level-label">{lvl.label}</span>
                    {experienceLevel === lvl.id && <span className="level-check">✓</span>}
                  </div>
                  <span className="level-sub">{lvl.desc}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Number of Questions Selection */}
          <div className="form-group">
            <label className="form-label">
              Number of Technical Questions <span className="req-star">*</span>
            </label>
            <div className="qcount-selector-grid">
              {QUESTION_COUNT_OPTIONS.map((opt) => (
                <button
                  type="button"
                  key={opt.count}
                  className={`qcount-option-card ${questionCount === opt.count ? 'selected' : ''}`}
                  onClick={() => setQuestionCount(opt.count)}
                >
                  <div className="qcount-card-header">
                    <span className="qcount-label">{opt.label}</span>
                    {questionCount === opt.count && <span className="qcount-check">✓</span>}
                  </div>
                  <span className="qcount-desc">{opt.desc}</span>
                </button>
              ))}
            </div>
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
              <li>Ensure your microphone is enabled and background speech/voices are minimized.</li>
              <li>Remain on this browser tab throughout the session (tab switches are monitored).</li>
              <li>Liveness verification must be completed successfully before entering the interview room.</li>
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
                <span>Configuring Interview Track...</span>
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
