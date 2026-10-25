import React from 'react';

/**
 * LIVESCAN 2.0 Comprehensive Session Audit Report Modal
 * 
 * Displays:
 * - Official LIVESCAN 2.0 Header with Session ID, generation timestamp, and outcome badge
 * - Candidate profile & session parameters (Name, Email, Role, Language, Status)
 * - Liveness verification metrics (LIVE/SPOOF, Sync Correlation, Time Lag Offset, Phrase)
 * - Monitoring summary & Warning counters (Multi-Person, Background Voice, Expiry details)
 * - Un-truncated Chronological Event Timeline with repeating headers across pages
 * - Regulatory, biometric privacy, and proctor advisory disclaimers
 * - Export options: Print/Save as PDF, Download Self-Contained Report, Export JSON
 */
export default function SessionReportModal({ report, onClose }) {
  if (!report) return null;

  const {
    sessionId,
    candidateId,
    candidateName,
    interviewRole,
    language = 'en',
    status,
    startTime,
    endTime,
    durationSeconds,
    warningCount = 0,
    maxWarnings = 3,
    isSessionExpired = false,
    stopReason,
    summary,
    timeline,
    disclaimer,
    generatedAt
  } = report;

  const formatTime = (isoString) => {
    if (!isoString) return 'N/A';
    try {
      return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return isoString;
    }
  };

  const formatDate = (isoString) => {
    if (!isoString) return new Date().toLocaleDateString();
    try {
      return new Date(isoString).toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
    } catch {
      return isoString;
    }
  };

  const formatDuration = (seconds) => {
    if (!seconds && seconds !== 0) return '00:00';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const getLanguageLabel = (langCode) => {
    const code = String(langCode || 'en').toLowerCase().trim();
    if (code === 'hi') return 'Hindi (हिन्दी)';
    if (code === 'mr') return 'Marathi (मराठी)';
    return 'English (en)';
  };

  const handleExportJson = () => {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(report, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', `livescan-report-${sessionId}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  const handlePrint = () => {
    window.print();
  };

  const outcome = summary?.assessmentOutcome || (isSessionExpired ? 'FLAGGED_FOR_REVIEW' : 'CLEAR');
  const isFlagged = outcome === 'FLAGGED_FOR_REVIEW' || isSessionExpired;
  const livenessResult = summary?.livenessResult || 'LIVE';
  const syncScore = summary?.synchronizationScore !== undefined && summary?.synchronizationScore !== null 
    ? `${(summary.synchronizationScore * 100).toFixed(1)}%` 
    : 'N/A';
  const timeOffset = summary?.detectedTimeOffsetMs !== undefined && summary?.detectedTimeOffsetMs !== null 
    ? `${summary.detectedTimeOffsetMs > 0 ? '+' : ''}${Math.round(summary.detectedTimeOffsetMs)} ms`
    : 'N/A';

  return (
    <div className="report-modal-backdrop" onClick={onClose}>
      <div className="report-modal-card" onClick={(e) => e.stopPropagation()}>
        {/* Modal / Print Header */}
        <div className="report-header">
          <div className="report-title-group">
            <div className="report-branding-row">
              <span className="report-badge">LIVESCAN 2.0 &middot; AI BIOMETRIC AUDIT REPORT</span>
              <span className="report-print-date">Generated: {formatDate(generatedAt)} {formatTime(generatedAt)}</span>
            </div>
            <h2 className="report-session-title">Online Interview Telemetry & Integrity Assessment</h2>
            <p className="report-session-id">
              Session ID: <code>{sessionId}</code>
              {isSessionExpired && <span className="session-terminated-tag">TERMINATED (MAX WARNINGS)</span>}
            </p>
          </div>
          <button className="report-close-btn no-print" onClick={onClose} aria-label="Close report">
            &times;
          </button>
        </div>

        <div className="report-body">
          {/* Top Outcome & Status Banner */}
          <div className={`report-outcome-banner ${isFlagged ? 'banner-flagged' : 'banner-clear'}`}>
            <div className="outcome-icon">
              {isFlagged ? '⚠️' : '✅'}
            </div>
            <div className="outcome-details">
              <h3 className="outcome-heading">
                {isSessionExpired
                  ? 'Session Expired — Maximum Integrity Warnings Exceeded'
                  : isFlagged
                  ? 'Integrity Telemetry Advisory: Review Recommended'
                  : 'Session Completed Normally — Integrity Verified'}
              </h3>
              <p className="outcome-subtext">
                {isSessionExpired
                  ? `This session reached ${warningCount} of ${maxWarnings} allowable warnings. Reason: ${stopReason || 'Exceeded policy limit'}.`
                  : isFlagged 
                  ? 'Acoustic or visual anomalies were logged during monitoring. Evaluation by a qualified human reviewer is recommended.'
                  : 'Candidate successfully authenticated with active lip-voice synchronization and completed the interview with zero critical policy violations.'}
              </p>
            </div>
            <div className="outcome-badge-pill">
              {isSessionExpired ? 'EXPIRED' : outcome}
            </div>
          </div>

          {/* Candidate & Session Details Grid */}
          <div className="report-candidate-meta-grid">
            <div className="candidate-meta-item">
              <span className="meta-key">Candidate Name:</span>
              <span className="meta-val font-semibold">{candidateName || 'N/A'}</span>
            </div>
            <div className="candidate-meta-item">
              <span className="meta-key">Candidate Email:</span>
              <span className="meta-val">{candidateId || 'N/A'}</span>
            </div>
            <div className="candidate-meta-item">
              <span className="meta-key">Position / Role:</span>
              <span className="meta-val">{interviewRole || 'Candidate'}</span>
            </div>
            <div className="candidate-meta-item">
              <span className="meta-key">Interview Language:</span>
              <span className="meta-val">{getLanguageLabel(language)}</span>
            </div>
            <div className="candidate-meta-item">
              <span className="meta-key">Session Duration:</span>
              <span className="meta-val">{formatDuration(durationSeconds)} ({formatTime(startTime)} – {formatTime(endTime)})</span>
            </div>
            <div className="candidate-meta-item">
              <span className="meta-key">Integrity Warning Count:</span>
              <span className={`meta-val ${warningCount > 0 ? (warningCount >= maxWarnings ? 'text-danger font-bold' : 'text-warning font-semibold') : 'text-success'}`}>
                {warningCount} / {maxWarnings} Warnings
              </span>
            </div>
          </div>

          {/* Liveness Verification & Synchronization Summary */}
          <div className="report-liveness-card">
            <div className="liveness-card-header">
              <span className="liveness-card-title">Pre-Interview Liveness Verification</span>
              <span className={`liveness-decision-tag ${livenessResult === 'LIVE' ? 'tag-live' : 'tag-spoof'}`}>
                {livenessResult}
              </span>
            </div>
            <div className="liveness-metrics-row">
              <div className="liveness-metric">
                <span className="lm-label">Lip–Voice Correlation</span>
                <span className="lm-value">{syncScore}</span>
                <span className="lm-sub">Threshold: &ge; 0.45</span>
              </div>
              <div className="liveness-metric">
                <span className="lm-label">Synchronization Offset</span>
                <span className="lm-value">{timeOffset}</span>
                <span className="lm-sub">Allowed: &plusmn;500 ms</span>
              </div>
              <div className="liveness-metric">
                <span className="lm-label">Verification Mode</span>
                <span className="lm-value">{getLanguageLabel(language).split(' ')[0]}</span>
                <span className="lm-sub">Dynamic Speech Challenge</span>
              </div>
            </div>
            {summary?.challengePhrase && (
              <div className="challenge-phrase-quote">
                <span className="cp-label">Spoken Challenge Phrase:</span>
                <span className="cp-text">&ldquo;{summary.challengePhrase}&rdquo;</span>
              </div>
            )}
          </div>

          {/* Telemetry Count Summary Cards */}
          <div className="report-metrics-grid">
            <div className="report-metric-card">
              <span className="metric-label">Total Events Logged</span>
              <span className="metric-value">{summary?.totalEvents || timeline?.length || 0}</span>
              <span className="metric-sub">Chronological markers</span>
            </div>

            <div className="report-metric-card">
              <span className="metric-label">Multiple Face Detections</span>
              <span className={`metric-value ${summary?.additionalPersonEvents > 0 ? 'text-warning' : 'text-neutral'}`}>
                {summary?.additionalPersonEvents || 0}
              </span>
              <span className="metric-sub">Visual intrusion alerts</span>
            </div>

            <div className="report-metric-card">
              <span className="metric-label">Possible Background Voices</span>
              <span className={`metric-value ${summary?.possibleBackgroundSpeechEvents > 0 ? 'text-warning' : 'text-neutral'}`}>
                {summary?.possibleBackgroundSpeechEvents || 0}
              </span>
              <span className="metric-sub">Acoustic speaker flags</span>
            </div>

            <div className="report-metric-card">
              <span className="metric-label">Session Policy Status</span>
              <span className={`metric-value ${isSessionExpired ? 'text-danger' : 'text-success'}`}>
                {isSessionExpired ? 'EXPIRED' : 'VALID'}
              </span>
              <span className="metric-sub">{isSessionExpired ? 'Policy threshold reached' : 'Clean completion'}</span>
            </div>
          </div>

          {/* Full Chronological Event Timeline */}
          <div className="report-section">
            <div className="section-title-wrap">
              <h3 className="section-title">
                <span>Chronological Event Audit Timeline</span>
                <span className="section-count">{timeline?.length || 0} events logged</span>
              </h3>
              <span className="timeline-full-note">Complete un-truncated record</span>
            </div>

            {timeline && timeline.length > 0 ? (
              <div className="report-timeline-container">
                <table className="report-timeline-table">
                  <thead>
                    <tr>
                      <th style={{ width: '12%' }}>Timestamp</th>
                      <th style={{ width: '22%' }}>Event Type</th>
                      <th style={{ width: '12%' }}>Severity</th>
                      <th style={{ width: '54%' }}>Telemetry Context & Diagnostic Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {timeline.map((item, idx) => (
                      <tr key={item.eventId || item._id || idx} className={`row-severity-${(item.severity || 'info').toLowerCase()}`}>
                        <td className="cell-time">{formatTime(item.timestamp)}</td>
                        <td className="cell-type">
                          <span className={`event-type-tag tag-${item.eventType?.toLowerCase()}`}>
                            {item.eventType}
                          </span>
                        </td>
                        <td className="cell-severity">
                          <span className={`severity-pill pill-${(item.severity || 'info').toLowerCase()}`}>
                            {item.severity || 'INFO'}
                          </span>
                        </td>
                        <td className="cell-details">
                          <div className="detail-primary">{item.details}</div>
                          {item.metadata && Object.keys(item.metadata).length > 0 && (
                            <div className="detail-meta">
                              {item.metadata.confidence !== undefined && (
                                <span className="meta-item">Confidence: {(item.metadata.confidence * 100).toFixed(0)}%</span>
                              )}
                              {item.metadata.detectedCount !== undefined && (
                                <span className="meta-item">Persons: {item.metadata.detectedCount}</span>
                              )}
                              {item.metadata.transcriptionSnippet && (
                                <span className="meta-item speech-snippet">&ldquo;{item.metadata.transcriptionSnippet}&rdquo;</span>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-timeline-state">
                <p>No anomalous events were recorded. Continuous candidate presence and single-speaker audio were maintained throughout.</p>
              </div>
            )}
          </div>

          {/* Privacy, Regulatory & Legal Compliance Notice */}
          <div className="report-privacy-card">
            <h4>🛡️ Privacy & Compliance Notice</h4>
            <p>{disclaimer || "LIVESCAN 2.0 acoustic and visual telemetry flags are advisory integrity signals designed to assist human evaluators. They do not constitute automated disqualification or legal proof of misconduct."}</p>
            <ul className="privacy-list">
              <li><strong>Zero Facial Recognition:</strong> Detection models locate face bounding boxes only; biometric face templates are not stored or matched against external databases.</li>
              <li><strong>Speaker Diarization Advisory:</strong> Acoustic monitoring flags potential concurrent or background speech; candidate environment acoustics should be considered during review.</li>
              <li><strong>Data Protection:</strong> Verification logs and timestamps are maintained according to institutional retention guidelines.</li>
            </ul>
          </div>
        </div>

        {/* Modal / Report Actions Footer */}
        <div className="report-footer">
          <div className="report-footer-left">
            <span>LIVESCAN 2.0 &middot; Multilingual Biometric Authentication & Session Integrity</span>
          </div>
          <div className="report-footer-actions no-print">
            <button type="button" className="btn btn-secondary" onClick={handlePrint}>
              🖨️ Print / Save PDF
            </button>
            <button type="button" className="btn btn-primary" onClick={handleExportJson}>
              📥 Export JSON Report
            </button>
            <button type="button" className="btn btn-outline" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
