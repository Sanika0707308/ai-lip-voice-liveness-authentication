import React, { useState, useEffect, useRef } from 'react';
import {
  startMonitoringSession,
  stopMonitoringSession,
  logMonitoringEvent,
  sendMonitoringPersonFrame,
  sendMonitoringAudioChunk,
  fetchSessionReport
} from '../utils/api';
import SessionReportModal from './SessionReportModal';

export default function MonitoringPanel({
  videoRef,
  streamRef,
  sessionId,
  language = 'en',
  latestLivenessResult,
  onSessionStatusChange,
  clientDetectedPersonCount = 1
}) {
  const [monitoringState, setMonitoringState] = useState('READY'); // 'READY' | 'MONITORING' | 'COMPLETED'
  const [sessionDuration, setSessionDuration] = useState(0);
  const [candidateId, setCandidateId] = useState('candidate-2026');
  const [examTitle, setExamTitle] = useState('Online Assessment & Technical Interview');
  
  // Real-time monitoring metrics
  const [personCount, setPersonCount] = useState(1);
  const [isAdditionalPersonWarning, setIsAdditionalPersonWarning] = useState(false);
  const [speechStatus, setSpeechStatus] = useState({
    isSpeech: false,
    confidence: 0,
    classification: 'SILENCE',
    snippet: '',
    alertMessage: null
  });
  const [cameraHealthy, setCameraHealthy] = useState(true);
  const [micHealthy, setMicHealthy] = useState(true);

  // Alerts history
  const [alerts, setAlerts] = useState([]);
  const [activeBannerAlert, setActiveBannerAlert] = useState(null);

  // Session report modal state
  const [reportData, setReportData] = useState(null);
  const [isReportOpen, setIsReportOpen] = useState(false);
  const [isStopping, setIsStopping] = useState(false);

  // Refs for background monitoring intervals & audio recorder
  const timerIntervalRef = useRef(null);
  const personCheckIntervalRef = useRef(null);
  const audioRecorderRef = useRef(null);
  const audioIntervalRef = useRef(null);
  const recordedAudioChunksRef = useRef([]);

  // Sync client detected person count from MediaPipe
  useEffect(() => {
    if (clientDetectedPersonCount !== undefined && clientDetectedPersonCount !== null) {
      setPersonCount(clientDetectedPersonCount);
      if (clientDetectedPersonCount > 1) {
        setIsAdditionalPersonWarning(true);
      }
    }
  }, [clientDetectedPersonCount]);

  // Monitor device track interruption events
  useEffect(() => {
    if (!streamRef || !streamRef.current) return;
    const stream = streamRef.current;

    const videoTrack = stream.getVideoTracks()[0];
    const audioTrack = stream.getAudioTracks()[0];

    const handleVideoEnded = () => {
      setCameraHealthy(false);
      triggerAlert({
        type: 'CAMERA_INTERRUPTED',
        severity: 'CRITICAL',
        title: 'Camera Input Interrupted',
        message: 'Webcam feed disconnected or halted unexpectedly.'
      });
    };

    const handleAudioEnded = () => {
      setMicHealthy(false);
      triggerAlert({
        type: 'MICROPHONE_INTERRUPTED',
        severity: 'CRITICAL',
        title: 'Microphone Input Interrupted',
        message: 'Microphone capture audio stream disconnected.'
      });
    };

    if (videoTrack) {
      videoTrack.addEventListener('ended', handleVideoEnded);
      videoTrack.addEventListener('mute', handleVideoEnded);
    }
    if (audioTrack) {
      audioTrack.addEventListener('ended', handleAudioEnded);
      audioTrack.addEventListener('mute', handleAudioEnded);
    }

    return () => {
      if (videoTrack) {
        videoTrack.removeEventListener('ended', handleVideoEnded);
        videoTrack.removeEventListener('mute', handleVideoEnded);
      }
      if (audioTrack) {
        audioTrack.removeEventListener('ended', handleAudioEnded);
        audioTrack.removeEventListener('mute', handleAudioEnded);
      }
    };
  }, [streamRef, monitoringState]);

  // Helper to append alerts and broadcast active banner
  const triggerAlert = (alertObj) => {
    const timestamp = new Date().toISOString();
    const alertWithId = {
      id: `alert-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`,
      timestamp,
      ...alertObj
    };

    setAlerts((prev) => [alertWithId, ...prev.slice(0, 49)]); // keep latest 50
    setActiveBannerAlert(alertWithId);

    // Auto-dismiss top banner alert after 6s
    setTimeout(() => {
      setActiveBannerAlert((curr) => (curr?.id === alertWithId.id ? null : curr));
    }, 6000);

    // Log to backend if active monitoring
    if (monitoringState === 'MONITORING' && sessionId) {
      logMonitoringEvent(
        sessionId,
        alertObj.type,
        alertObj.severity,
        alertObj.message || alertObj.title,
        alertObj.metadata || null
      ).catch((err) => console.warn('Failed to log event to backend:', err));
    }
  };

  // Start Monitoring Session
  const handleStartMonitoring = async () => {
    try {
      const activeSessionId = sessionId || `sess-${Date.now()}`;
      await startMonitoringSession(activeSessionId, candidateId, examTitle, language);

      setMonitoringState('MONITORING');
      setSessionDuration(0);
      setAlerts([]);
      setActiveBannerAlert(null);
      if (onSessionStatusChange) onSessionStatusChange('MONITORING');

      // Start duration stopwatch
      timerIntervalRef.current = setInterval(() => {
        setSessionDuration((prev) => prev + 1);
      }, 1000);

      // Start periodic person detection via backend frame snapshot
      startPersonDetectionLoop(activeSessionId);

      // Start rolling background audio chunks monitoring
      startAudioMonitoringLoop(activeSessionId);

      triggerAlert({
        type: 'MONITORING_STARTED',
        severity: 'INFO',
        title: 'Monitoring Active',
        message: `Proctored session started for candidate ${candidateId}.`
      });
    } catch (err) {
      alert(`Could not start monitoring session: ${err.message}`);
    }
  };

  // Periodic frame sampling to backend person detection endpoint
  const startPersonDetectionLoop = (sessId) => {
    if (personCheckIntervalRef.current) clearInterval(personCheckIntervalRef.current);

    personCheckIntervalRef.current = setInterval(async () => {
      if (!videoRef || !videoRef.current) return;
      const video = videoRef.current;
      if (video.readyState < 2) return;

      try {
        // Capture offscreen canvas snapshot
        const canvas = document.createElement('canvas');
        canvas.width = Math.min(640, video.videoWidth || 640);
        canvas.height = Math.min(480, video.videoHeight || 480);
        const ctx = canvas.getContext('2d');
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

        canvas.toBlob(async (blob) => {
          if (!blob) return;
          try {
            const res = await sendMonitoringPersonFrame(sessId, blob, clientDetectedPersonCount);
            if (res.success) {
              setPersonCount(res.personCount);
              setIsAdditionalPersonWarning(res.isWarningActive);

              if (res.shouldAlert) {
                triggerAlert({
                  type: 'ADDITIONAL_PERSON_DETECTED',
                  severity: 'WARNING',
                  title: 'Additional Person Detected',
                  message: res.alertMessage || `Multiple people detected in frame (${res.personCount}).`,
                  metadata: { count: res.personCount, lighting: res.lightingCondition }
                });
              } else if (res.shouldClear) {
                triggerAlert({
                  type: 'PERSON_CLEARED',
                  severity: 'INFO',
                  title: 'Person Cleared',
                  message: 'Additional individual no longer detected. Frame status restored to normal.'
                });
              }
            }
          } catch (e) {
            // Non-fatal background error
          }
        }, 'image/jpeg', 0.65);
      } catch (err) {
        // Frame capture failed
      }
    }, 2500); // Check every 2.5s
  };

  // Rolling audio recorder sending 2-second chunks to backend VAD endpoint
  const startAudioMonitoringLoop = (sessId) => {
    if (!streamRef || !streamRef.current) return;
    const stream = streamRef.current;
    const audioTrack = stream.getAudioTracks()[0];
    if (!audioTrack) return;

    try {
      const audioStream = new MediaStream([audioTrack]);
      let options = { mimeType: 'audio/webm' };
      if (!MediaRecorder.isTypeSupported('audio/webm')) {
        options = {};
      }

      const recorder = new MediaRecorder(audioStream, options);
      audioRecorderRef.current = recorder;

      recorder.ondataavailable = async (e) => {
        if (e.data && e.data.size > 1000) {
          try {
            const res = await sendMonitoringAudioChunk(sessId, e.data, language, true);
            if (res.success) {
              setSpeechStatus({
                isSpeech: res.is_speech_detected,
                confidence: res.confidence,
                classification: res.classification,
                snippet: res.transcription_snippet || '',
                alertMessage: res.alert_message
              });

              if (res.shouldAlert) {
                triggerAlert({
                  type: 'POSSIBLE_BACKGROUND_SPEECH',
                  severity: 'WARNING',
                  title: 'Possible Background Speech Detected',
                  message: res.transcription_snippet 
                    ? `Vocal utterance detected: “${res.transcription_snippet}”`
                    : 'Acoustic vocal activity detected in microphone environment.',
                  metadata: {
                    confidence: res.confidence,
                    acousticMetrics: res.acoustic_metrics,
                    snippet: res.transcription_snippet
                  }
                });
              }
            }
          } catch (err) {
            // Non-fatal background audio chunk error
          }
        }
      };

      // Restart recorder every 2 seconds to emit small clean windows
      recorder.start();
      audioIntervalRef.current = setInterval(() => {
        if (recorder.state === 'recording') {
          recorder.stop();
          recorder.start();
        }
      }, 2200);

    } catch (err) {
      console.warn('Could not initialize background audio monitor:', err);
    }
  };

  // Stop Monitoring Session
  const handleStopMonitoring = async () => {
    setIsStopping(true);
    try {
      // Clear all timers and recorders
      if (timerIntervalRef.current) clearInterval(timerIntervalRef.current);
      if (personCheckIntervalRef.current) clearInterval(personCheckIntervalRef.current);
      if (audioIntervalRef.current) clearInterval(audioIntervalRef.current);

      if (audioRecorderRef.current && audioRecorderRef.current.state === 'recording') {
        try {
          audioRecorderRef.current.stop();
        } catch {}
      }

      // Stop & release all media stream tracks to guarantee resource cleanup!
      if (streamRef && streamRef.current) {
        streamRef.current.getTracks().forEach((track) => {
          try {
            track.stop();
            console.log(`MediaTrack [${track.kind}] released.`);
          } catch {}
        });
        streamRef.current = null;
      }
      if (videoRef && videoRef.current) {
        videoRef.current.srcObject = null;
      }

      setMonitoringState('COMPLETED');
      if (onSessionStatusChange) onSessionStatusChange('COMPLETED');

      // Call stop API and get final session report
      const res = await stopMonitoringSession(sessionId || 'default-session');
      if (res && res.report) {
        setReportData(res.report);
        setIsReportOpen(true);
      } else {
        // Fallback fetch report
        const rep = await fetchSessionReport(sessionId);
        if (rep && rep.report) {
          setReportData(rep.report);
          setIsReportOpen(true);
        }
      }
    } catch (err) {
      alert(`Error finalizing session: ${err.message}`);
    } finally {
      setIsStopping(false);
    }
  };

  const formatTimer = (sec) => {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  return (
    <div className="monitoring-panel-container">
      {/* Top Banner Alert (Floating High-Visibility Alert) */}
      {activeBannerAlert && (
        <div className={`active-alert-banner alert-${activeBannerAlert.severity.toLowerCase()}`}>
          <div className="banner-icon">
            {activeBannerAlert.severity === 'CRITICAL' ? '🛑' : activeBannerAlert.severity === 'WARNING' ? '⚠️' : 'ℹ️'}
          </div>
          <div className="banner-content">
            <strong>{activeBannerAlert.title}:</strong> {activeBannerAlert.message}
            <span className="banner-sub"> · Advisory Proctor Telemetry (Human Review Advised)</span>
          </div>
          <button className="banner-close" onClick={() => setActiveBannerAlert(null)}>
            &times;
          </button>
        </div>
      )}

      {/* Control Strip Card */}
      <div className="monitoring-control-card">
        <div className="control-card-left">
          <div className="session-status-cluster">
            <span className={`status-pulse-dot dot-${monitoringState.toLowerCase()}`}></span>
            <div>
              <div className="session-status-title">
                Session Status: <strong>{monitoringState}</strong>
              </div>
              <div className="session-timer-text">
                {monitoringState === 'MONITORING' ? (
                  <span>⏱️ Active Duration: <strong>{formatTimer(sessionDuration)}</strong></span>
                ) : monitoringState === 'COMPLETED' ? (
                  <span>✅ Session Finalized ({formatTimer(sessionDuration)})</span>
                ) : (
                  <span>Standby &middot; Ready for proctored assessment</span>
                )}
              </div>
            </div>
          </div>
        </div>

        <div className="control-card-middle">
          <div className="session-metadata-fields">
            <label className="meta-field-item">
              <span className="field-label">Candidate</span>
              <input
                type="text"
                disabled={monitoringState === 'MONITORING'}
                value={candidateId}
                onChange={(e) => setCandidateId(e.target.value)}
                className="meta-input"
              />
            </label>
            <label className="meta-field-item">
              <span className="field-label">Exam / Interview</span>
              <input
                type="text"
                disabled={monitoringState === 'MONITORING'}
                value={examTitle}
                onChange={(e) => setExamTitle(e.target.value)}
                className="meta-input"
              />
            </label>
          </div>
        </div>

        <div className="control-card-right">
          {monitoringState !== 'MONITORING' ? (
            <button
              className="btn btn-primary btn-start-monitor"
              onClick={handleStartMonitoring}
            >
              <span>▶</span> Start Proctored Monitoring
            </button>
          ) : (
            <button
              className="btn btn-danger btn-stop-monitor"
              onClick={handleStopMonitoring}
              disabled={isStopping}
            >
              <span>⏹</span> {isStopping ? 'Finalizing Session...' : 'Stop Monitoring & Review'}
            </button>
          )}

          {reportData && (
            <button
              className="btn btn-outline btn-view-report"
              onClick={() => setIsReportOpen(true)}
            >
              📄 View Audit Report
            </button>
          )}
        </div>
      </div>

      {/* Surveillance Status Indicators Grid */}
      <div className="monitoring-status-grid">
        {/* 1. Visible People Count Card */}
        <div className={`status-telemetry-card ${personCount > 1 ? 'card-warning-state' : 'card-normal-state'}`}>
          <div className="card-header-row">
            <span className="card-badge-label">MULTIPLE-PERSON DETECTION</span>
            <span className={`status-dot-indicator ${personCount > 1 ? 'dot-warning' : 'dot-success'}`}></span>
          </div>
          <div className="card-main-stat">
            <span className="stat-icon">{personCount > 1 ? '👥' : '👤'}</span>
            <span className="stat-number">{personCount}</span>
            <span className="stat-unit">{personCount === 1 ? 'Person Detected' : 'People Detected'}</span>
          </div>
          <div className="card-status-detail">
            {personCount > 1 ? (
              <span className="warning-text">⚠️ Warning: Additional individual visible in frame</span>
            ) : personCount === 1 ? (
              <span className="success-text">✅ Primary candidate visible (Normal)</span>
            ) : (
              <span className="muted-text">Candidate out of frame</span>
            )}
          </div>
        </div>

        {/* 2. Background Speech Monitoring Card */}
        <div className={`status-telemetry-card ${speechStatus.isSpeech ? 'card-warning-state' : 'card-normal-state'}`}>
          <div className="card-header-row">
            <span className="card-badge-label">BACKGROUND SPEECH MONITOR</span>
            <span className={`status-dot-indicator ${speechStatus.isSpeech ? 'dot-warning' : 'dot-success'}`}></span>
          </div>
          <div className="card-main-stat">
            <span className="stat-icon">🎙️</span>
            <span className="stat-number">{speechStatus.isSpeech ? 'Speech' : 'Quiet'}</span>
            <span className="stat-unit">
              {speechStatus.isSpeech ? `Conf: ${(speechStatus.confidence * 100).toFixed(0)}%` : 'Ambient Normal'}
            </span>
          </div>
          <div className="card-status-detail">
            {speechStatus.isSpeech ? (
              <span className="warning-text">
                ⚠️ {speechStatus.alertMessage || 'Possible Background Speech Detected'}
              </span>
            ) : (
              <span className="success-text">✅ Acoustic environment clear &middot; Low noise</span>
            )}
          </div>
        </div>

        {/* 3. Hardware Devices Interruption Health */}
        <div className={`status-telemetry-card ${(!cameraHealthy || !micHealthy) ? 'card-danger-state' : 'card-normal-state'}`}>
          <div className="card-header-row">
            <span className="card-badge-label">DEVICE INTEGRITY</span>
            <span className={`status-dot-indicator ${(!cameraHealthy || !micHealthy) ? 'dot-danger' : 'dot-success'}`}></span>
          </div>
          <div className="card-main-stat">
            <span className="stat-icon">📹</span>
            <div className="device-stat-group">
              <span className={`device-pill ${cameraHealthy ? 'pill-healthy' : 'pill-broken'}`}>
                Camera: {cameraHealthy ? 'Active' : 'Interrupted'}
              </span>
              <span className={`device-pill ${micHealthy ? 'pill-healthy' : 'pill-broken'}`}>
                Mic: {micHealthy ? 'Active' : 'Interrupted'}
              </span>
            </div>
          </div>
          <div className="card-status-detail">
            {cameraHealthy && micHealthy ? (
              <span className="success-text">✅ Media streams streaming without interruption</span>
            ) : (
              <span className="danger-text">🛑 Hardware feed interrupted! Reconnect camera/mic</span>
            )}
          </div>
        </div>

        {/* 4. Biometric Liveness Verification Score (Distinctly Visualized!) */}
        <div className="status-telemetry-card card-liveness-highlight">
          <div className="card-header-row">
            <span className="card-badge-label label-liveness">BIOMETRIC LIVENESS RESULT</span>
            <span className="info-chip">Anti-Spoofing</span>
          </div>
          <div className="card-main-stat">
            <span className="stat-icon">🛡️</span>
            <span className={`stat-number ${latestLivenessResult?.livenessResult === 'LIVE' ? 'text-live' : latestLivenessResult?.livenessResult === 'SPOOF' ? 'text-spoof' : 'text-neutral'}`}>
              {latestLivenessResult?.livenessResult || 'STANDBY'}
            </span>
            <span className="stat-unit">
              {latestLivenessResult?.synchronizationScore !== undefined
                ? `Sync: ${(latestLivenessResult.synchronizationScore * 100).toFixed(1)}%`
                : 'Awaiting sync check'}
            </span>
          </div>
          <div className="card-status-detail">
            {latestLivenessResult?.livenessResult === 'LIVE' ? (
              <span className="success-text">✅ Lip movement synchronized with spoken challenge</span>
            ) : latestLivenessResult?.livenessResult === 'SPOOF' ? (
              <span className="danger-text">❌ Synchronization check failed (Spoof risk)</span>
            ) : (
              <span className="muted-text">Conduct challenge phrase test in verification tab</span>
            )}
          </div>
        </div>
      </div>

      {/* Real-time Alerts & Event Stream */}
      <div className="monitoring-alerts-section">
        <div className="alerts-section-header">
          <div className="header-left">
            <h3 className="section-heading">Real-Time Monitoring Event Feed</h3>
            <span className="alerts-counter-badge">{alerts.length} Events</span>
          </div>
          <div className="header-right">
            <span className="advisory-disclaimer-pill">
              ℹ️ Detections are advisory flags for proctors &middot; Not automatic disqualifications
            </span>
          </div>
        </div>

        <div className="alerts-stream-list">
          {alerts.length > 0 ? (
            alerts.map((al) => (
              <div key={al.id} className={`alert-stream-item item-severity-${al.severity.toLowerCase()}`}>
                <div className="alert-item-time">
                  {new Date(al.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </div>
                <div className="alert-item-type">
                  <span className={`badge-pill badge-${al.severity.toLowerCase()}`}>
                    {al.severity}
                  </span>
                </div>
                <div className="alert-item-body">
                  <strong>{al.title}:</strong> {al.message}
                  {al.metadata && al.metadata.snippet && (
                    <span className="alert-snippet-quote">“{al.metadata.snippet}”</span>
                  )}
                </div>
              </div>
            ))
          ) : (
            <div className="empty-alerts-state">
              <span className="empty-icon">🛡️</span>
              <p>No monitoring incidents logged. Candidate activity is continuous and within normal parameters.</p>
            </div>
          )}
        </div>
      </div>

      {/* Session Audit Report Modal */}
      {isReportOpen && reportData && (
        <SessionReportModal
          report={reportData}
          onClose={() => setIsReportOpen(false)}
        />
      )}
    </div>
  );
}
