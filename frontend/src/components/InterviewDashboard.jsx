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

const MAX_INTEGRITY_WARNINGS = 3;

export default function InterviewDashboard({
  candidateData,
  initialStream,
  onInterviewEnd
}) {
  const { candidateName, candidateEmail, interviewRole, language = 'en', sessionId } = candidateData;

  // Stream & video refs
  const videoRef = useRef(null);
  const streamRef = useRef(initialStream || null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);

  // Background monitoring interval & recorder refs
  const sessionStartTimeRef = useRef(null);
  const timerIntervalRef = useRef(null);
  const audioMeterIntervalRef = useRef(null);
  const personCheckIntervalRef = useRef(null);
  const audioRecorderRef = useRef(null);
  const audioIntervalRef = useRef(null);
  const audioSliceTimeoutRef = useRef(null);
  const isAudioMonitoringActiveRef = useRef(false);

  // Call states
  const [isMicMuted, setIsMicMuted] = useState(false);
  const [isVideoOff, setIsVideoOff] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [audioLevel, setAudioLevel] = useState(0);

  // Integrity monitoring states
  const [personCount, setPersonCount] = useState(1);
  const [warningCount, setWarningCount] = useState(0);
  const [warningsList, setWarningsList] = useState([]);
  const [isSessionExpired, setIsSessionExpired] = useState(false);
  const [speechStatusText, setSpeechStatusText] = useState('Clear (No background voice)');
  const [bannerAlert, setBannerAlert] = useState(null);

  // Face Detection Bounding Boxes Overlay Refs
  const overlayCanvasRef = useRef(null);
  const facesListRef = useRef([]);
  const frameDimRef = useRef({ width: 640, height: 480 });
  const audioLevelRef = useRef(0);

  // Final session audit report modal
  const [reportData, setReportData] = useState(null);
  const [isReportOpen, setIsReportOpen] = useState(false);
  const [isEnding, setIsEnding] = useState(false);

  // Format stopwatch seconds MM:SS
  const formatTimer = (totalSec) => {
    const mins = Math.floor(totalSec / 60).toString().padStart(2, '0');
    const secs = (totalSec % 60).toString().padStart(2, '0');
    return `${mins}:${secs}`;
  };

  // Format ISO timestamp to local time HH:MM:SS
  const formatTimeOnly = (date = new Date()) => {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  // Draw smooth face bounding boxes aligned with mirrored webcam feed
  const drawFaceBoundingBoxes = () => {
    const canvas = overlayCanvasRef.current;
    const video = videoRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const width = canvas.clientWidth || canvas.parentElement?.clientWidth || 640;
    const height = canvas.clientHeight || canvas.parentElement?.clientHeight || 480;

    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    ctx.clearRect(0, 0, width, height);

    if (isVideoOff || !video || video.readyState < 2) {
      return;
    }

    const faces = facesListRef.current;
    if (!faces || faces.length === 0) {
      return;
    }

    const videoNativeW = video.videoWidth || frameDimRef.current.width || 640;
    const videoNativeH = video.videoHeight || frameDimRef.current.height || 480;
    const frameW = frameDimRef.current.width || 640;
    const frameH = frameDimRef.current.height || 480;

    // Calculate object-fit: cover mapping
    const containerAspect = width / height;
    const videoAspect = videoNativeW / videoNativeH;

    let renderW, renderH, offsetX, offsetY;
    if (videoAspect > containerAspect) {
      renderH = height;
      renderW = height * videoAspect;
      offsetX = (renderW - width) / 2;
      offsetY = 0;
    } else {
      renderW = width;
      renderH = width / videoAspect;
      offsetX = 0;
      offsetY = (renderH - height) / 2;
    }

    const scaleX = renderW / frameW;
    const scaleY = renderH / frameH;

    faces.forEach((face, index) => {
      const [fx, fy, fw, fh] = face.box || [0, 0, 0, 0];
      if (fw <= 0 || fh <= 0) return;

      const boxW = fw * scaleX;
      const boxH = fh * scaleY;
      const unmirroredX = fx * scaleX - offsetX;
      const unmirroredY = fy * scaleY - offsetY;

      // Because video has CSS transform: scaleX(-1), mirror X coordinate:
      const mirroredX = width - (unmirroredX + boxW);
      const mirroredY = unmirroredY;

      const isPrimary = index === 0;
      const strokeColor = isPrimary ? '#10B981' : '#EF4444'; // Emerald for Face 1, Crimson for Face 2+
      const badgeBg = isPrimary ? '#059669' : '#DC2626';
      const labelText = face.label || `Face ${index + 1}${isPrimary ? ' (Candidate)' : ' (Alert)'}`;

      ctx.save();

      // Rounded bounding box rectangle
      const radius = 8;
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(mirroredX, mirroredY, boxW, boxH, radius);
      } else {
        ctx.rect(mirroredX, mirroredY, boxW, boxH);
      }
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = strokeColor;
      ctx.shadowColor = isPrimary ? 'rgba(16, 185, 129, 0.45)' : 'rgba(239, 68, 68, 0.6)';
      ctx.shadowBlur = 8;
      ctx.stroke();

      // Subtle translucent fill
      ctx.fillStyle = isPrimary ? 'rgba(16, 185, 129, 0.06)' : 'rgba(239, 68, 68, 0.08)';
      ctx.fill();

      // Unmirrored crisp label pill tag
      ctx.shadowBlur = 0;
      ctx.font = 'bold 12px "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif';
      const textMetrics = ctx.measureText(labelText);
      const padX = 10;
      const tagH = 22;
      const tagW = textMetrics.width + padX * 2;
      const tagX = Math.max(4, Math.min(width - tagW - 4, mirroredX));
      const tagY = mirroredY > 26 ? mirroredY - 26 : mirroredY + 4;

      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(tagX, tagY, tagW, tagH, 5);
      } else {
        ctx.rect(tagX, tagY, tagW, tagH);
      }
      ctx.fillStyle = badgeBg;
      ctx.fill();

      ctx.fillStyle = '#FFFFFF';
      ctx.textBaseline = 'middle';
      ctx.fillText(labelText, tagX + padX, tagY + tagH / 2);

      ctx.restore();
    });
  };

  // Add confirmed warning
  const recordWarning = (warningObj) => {
    const timeStr = formatTimeOnly();
    setWarningCount((prevCount) => {
      const newCount = (warningObj.serverCount !== undefined) ? warningObj.serverCount : prevCount + 1;
      const item = {
        id: `warn-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`,
        timeStr,
        count: newCount,
        ...warningObj
      };

      setWarningsList((prev) => [item, ...prev]);
      setBannerAlert(item);

      setTimeout(() => {
        setBannerAlert((curr) => (curr?.id === item.id ? null : curr));
      }, 5000);

      // If 3 warnings reached, expire session!
      if (newCount >= MAX_INTEGRITY_WARNINGS) {
        handleExpireSession("Exceeded maximum integrity warnings (3 of 3).");
      }
      return newCount;
    });
  };

  // Session termination on 3 warnings
  const handleExpireSession = async (reason) => {
    setIsSessionExpired(true);
    cleanupMonitoring();

    try {
      const res = await stopMonitoringSession(sessionId, reason);
      if (res && res.report) {
        setReportData(res.report);
        setIsReportOpen(true);
      }
    } catch (err) {
      console.warn('Failed to stop session gracefully on expiry:', err);
    }
  };

  // Cleanup all timers and media tracks
  const cleanupMonitoring = () => {
    if (timerIntervalRef.current) {
      clearInterval(timerIntervalRef.current);
      timerIntervalRef.current = null;
    }
    if (audioMeterIntervalRef.current) {
      clearInterval(audioMeterIntervalRef.current);
      audioMeterIntervalRef.current = null;
    }
    if (personCheckIntervalRef.current) {
      clearInterval(personCheckIntervalRef.current);
      personCheckIntervalRef.current = null;
    }
    if (audioIntervalRef.current) {
      clearTimeout(audioIntervalRef.current);
      audioIntervalRef.current = null;
    }
    if (audioSliceTimeoutRef.current) {
      clearTimeout(audioSliceTimeoutRef.current);
      audioSliceTimeoutRef.current = null;
    }
    isAudioMonitoringActiveRef.current = false;

    if (audioRecorderRef.current && audioRecorderRef.current.state === 'recording') {
      try {
        audioRecorderRef.current.stop();
      } catch {}
    }

    facesListRef.current = [];
    if (overlayCanvasRef.current) {
      const ctx = overlayCanvasRef.current.getContext('2d');
      if (ctx) ctx.clearRect(0, 0, overlayCanvasRef.current.width, overlayCanvasRef.current.height);
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => {
        try {
          track.stop();
        } catch {}
      });
    }

    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      try {
        audioContextRef.current.close();
      } catch {}
    }
  };

  // Initialize camera stream, audio analyzer, and monitoring loops
  useEffect(() => {
    let isCancelled = false;

    const initInterview = async () => {
      // 1. Ensure stream is active
      let stream = streamRef.current;
      if (!stream || stream.getVideoTracks().length === 0 || !stream.active) {
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
            audio: { echoCancellation: true, noiseSuppression: true }
          });
          streamRef.current = stream;
        } catch (e) {
          console.warn('Could not re-acquire stream:', e);
        }
      }

      if (isCancelled) return;

      if (videoRef.current && stream) {
        videoRef.current.srcObject = stream;
      }

      // Audio volume monitoring for candidate speaking feedback
      try {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (AudioContextClass && stream && stream.getAudioTracks().length > 0) {
          const ctx = new AudioContextClass();
          audioContextRef.current = ctx;
          const source = ctx.createMediaStreamSource(stream);
          const analyser = ctx.createAnalyser();
          analyser.fftSize = 256;
          source.connect(analyser);
          analyserRef.current = analyser;

          const dataArray = new Uint8Array(analyser.frequencyBinCount);
          if (audioMeterIntervalRef.current) clearInterval(audioMeterIntervalRef.current);
          audioMeterIntervalRef.current = setInterval(() => {
            if (analyserRef.current && !isMicMuted) {
              analyserRef.current.getByteFrequencyData(dataArray);
              let sum = 0;
              for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
              const avg = sum / dataArray.length;
              const lvl = Math.min(100, Math.round((avg / 128) * 100));
              setAudioLevel(lvl);
              audioLevelRef.current = lvl;
            } else {
              setAudioLevel(0);
              audioLevelRef.current = 0;
            }
          }, 150);
        }
      } catch (audioErr) {
        console.warn('Audio meter init error:', audioErr);
      }

      // 2. Start monitoring session on backend
      try {
        await startMonitoringSession(sessionId, candidateName, interviewRole, language);
      } catch (err) {
        console.warn('Failed to register monitoring session:', err);
      }

      // 3. Start stopwatch timer
      sessionStartTimeRef.current = Date.now();
      if (timerIntervalRef.current) clearInterval(timerIntervalRef.current);
      timerIntervalRef.current = setInterval(() => {
        if (sessionStartTimeRef.current) {
          const elapsed = Math.floor((Date.now() - sessionStartTimeRef.current) / 1000);
          setElapsedSeconds(elapsed);
        }
      }, 1000);

      // 4. Attach resize listener for responsive bounding box alignment
      window.addEventListener('resize', drawFaceBoundingBoxes);

      // 5. Start background Person Detection sampling
      startPersonDetectionLoop();

      // 6. Start background Audio/VAD sampling
      startAudioMonitoringLoop();
    };

    initInterview();

    return () => {
      isCancelled = true;
      window.removeEventListener('resize', drawFaceBoundingBoxes);
      cleanupMonitoring();
    };
  }, []);

  // Periodic person & face detection sampling (snappy 800ms rate)
  const startPersonDetectionLoop = () => {
    if (personCheckIntervalRef.current) clearInterval(personCheckIntervalRef.current);

    personCheckIntervalRef.current = setInterval(async () => {
      if (isSessionExpired || isEnding) return;
      if (!videoRef.current || videoRef.current.readyState < 2) return;

      try {
        const video = videoRef.current;
        const canvas = document.createElement('canvas');
        canvas.width = Math.min(640, video.videoWidth || 640);
        canvas.height = Math.min(480, video.videoHeight || 480);
        const ctx = canvas.getContext('2d');
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

        canvas.toBlob(async (blob) => {
          if (!blob || isSessionExpired || isEnding) return;
          try {
            const res = await sendMonitoringPersonFrame(sessionId, blob, null);
            if (res.success) {
              const detectedFaceCount = res.faceCount !== undefined ? res.faceCount : (res.personCount || 1);
              setPersonCount(detectedFaceCount);

              // Update face bounding box coordinates and redraw overlay
              facesListRef.current = res.faces || [];
              frameDimRef.current = {
                width: res.frameWidth || canvas.width,
                height: res.frameHeight || canvas.height
              };
              drawFaceBoundingBoxes();

              if (res.shouldAlert) {
                recordWarning({
                  type: 'ADDITIONAL_PERSON_DETECTED',
                  title: 'Additional Face / Person Detected',
                  message: res.alertMessage || `Multiple individuals (${detectedFaceCount}) detected in camera view.`,
                  serverCount: res.warningCount
                });
              }

              if (res.isSessionExpired) {
                handleExpireSession("Terminated by backend: maximum warnings exceeded.");
              }
            }
          } catch (e) {
            // Non-fatal background snapshot error
          }
        }, 'image/jpeg', 0.70);
      } catch (err) {}
    }, 800);
  };

  // Periodic continuous audio monitoring (self-contained 2.5s slices with dual-speaker separation)
  const startAudioMonitoringLoop = () => {
    if (!streamRef.current) return;
    const audioTrack = streamRef.current.getAudioTracks()[0];
    if (!audioTrack) return;

    isAudioMonitoringActiveRef.current = true;
    const audioStream = new MediaStream([audioTrack]);
    let mimeType = 'audio/webm';
    if (!MediaRecorder.isTypeSupported('audio/webm')) {
      mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
    }
    const options = mimeType ? { mimeType } : {};

    const recordNextAudioSlice = () => {
      if (!isAudioMonitoringActiveRef.current || isSessionExpired || isEnding) return;

      try {
        const recorder = new MediaRecorder(audioStream, options);
        audioRecorderRef.current = recorder;
        const chunks = [];

        recorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) chunks.push(e.data);
        };

        recorder.onstop = async () => {
          if (chunks.length > 0 && !isMicMuted && !isSessionExpired && !isEnding) {
            const audioBlob = new Blob(chunks, { type: mimeType || 'audio/webm' });
            if (audioBlob.size > 1500) {
              try {
                // Forward whether candidate is currently answering based on real-time audio level
                const candidateSpeakingNow = audioLevelRef.current > 18;
                const res = await sendMonitoringAudioChunk(sessionId, audioBlob, language, candidateSpeakingNow);
                if (res.success) {
                  if (res.is_speech_detected) {
                    if (res.speaker_classification === 'DUAL_SPEAKERS') {
                      setSpeechStatusText('Dual Simultaneous Voices Detected');
                    } else if (res.speaker_classification === 'CANDIDATE_ANSWERING') {
                      setSpeechStatusText('Candidate Speaking (Normal)');
                    } else if (res.transcription_snippet) {
                      setSpeechStatusText(`Possible Background Voice: "${res.transcription_snippet}"`);
                    } else {
                      setSpeechStatusText('Possible background vocal activity');
                    }
                  } else {
                    setSpeechStatusText('Acoustic Environment Normal');
                  }

                  if (res.shouldAlert) {
                    recordWarning({
                      type: 'POSSIBLE_BACKGROUND_SPEECH',
                      title: 'Possible Background Speech Detected',
                      message: res.transcription_snippet
                        ? `Acoustic utterance flagged: "${res.transcription_snippet}"`
                        : (res.alert_message || 'Possible background speech detected in environment.'),
                      serverCount: res.warningCount
                    });
                  }

                  if (res.isSessionExpired) {
                    handleExpireSession("Terminated by backend: maximum warnings exceeded.");
                  }
                }
              } catch (e) {}
            }
          }

          if (isAudioMonitoringActiveRef.current && !isSessionExpired && !isEnding) {
            audioSliceTimeoutRef.current = setTimeout(recordNextAudioSlice, 150);
          }
        };

        recorder.start();
        audioIntervalRef.current = setTimeout(() => {
          if (recorder.state === 'recording') {
            try { recorder.stop(); } catch {}
          }
        }, 2500);
      } catch (err) {
        console.warn('Audio slice creation failed:', err);
      }
    };

    recordNextAudioSlice();
  };

  // Toggle Microphone Mute
  const handleToggleMic = () => {
    if (!streamRef.current) return;
    const audioTrack = streamRef.current.getAudioTracks()[0];
    if (audioTrack) {
      const nextState = !isMicMuted;
      audioTrack.enabled = !nextState;
      setIsMicMuted(nextState);
      setSpeechStatusText(nextState ? 'Microphone Muted' : 'Acoustic Environment Normal');

      logMonitoringEvent(
        sessionId,
        nextState ? 'MICROPHONE_MUTED' : 'MICROPHONE_UNMUTED',
        'INFO',
        `Candidate toggled microphone: ${nextState ? 'Muted' : 'Unmuted'}`
      ).catch(() => {});
    }
  };

  // Toggle Video Track
  const handleToggleVideo = () => {
    if (!streamRef.current) return;
    const videoTrack = streamRef.current.getVideoTracks()[0];
    if (videoTrack) {
      const nextState = !isVideoOff;
      videoTrack.enabled = !nextState;
      setIsVideoOff(nextState);
      if (nextState) {
        facesListRef.current = [];
        drawFaceBoundingBoxes();
      }

      logMonitoringEvent(
        sessionId,
        nextState ? 'CAMERA_DISABLED' : 'CAMERA_ENABLED',
        'INFO',
        `Candidate toggled camera: ${nextState ? 'Disabled' : 'Enabled'}`
      ).catch(() => {});
    }
  };

  // Gracefully Complete Interview
  const handleCompleteInterview = async () => {
    const confirmEnd = window.confirm('Are you sure you want to complete and submit this interview session?');
    if (!confirmEnd) return;

    setIsEnding(true);
    cleanupMonitoring();

    try {
      const res = await stopMonitoringSession(sessionId, 'Candidate ended interview session normally');
      if (res && res.report) {
        setReportData(res.report);
        setIsReportOpen(true);
      } else {
        const fallback = await fetchSessionReport(sessionId);
        if (fallback && fallback.report) {
          setReportData(fallback.report);
          setIsReportOpen(true);
        }
      }
    } catch (err) {
      alert(`Session report compilation completed. Status: normal.`);
      setIsEnding(false);
    }
  };

  return (
    <div className="interview-dashboard-wrap">
      {/* Top Navigation & Status Bar */}
      <header className="dashboard-header-bar">
        <div className="header-brand-info">
          <div className="brand-pill">
            <span className="brand-dot-pulse"></span>
            <span className="brand-role-title">{interviewRole}</span>
          </div>
          <span className="header-divider">/</span>
          <span className="candidate-name-header">{candidateName}</span>
        </div>

        <div className="header-telemetry-controls">
          <div className="timer-badge-chip">
            <span className="timer-icon">⏱️</span>
            <span className="timer-value">{formatTimer(elapsedSeconds)}</span>
          </div>

          <div className="session-status-chip">
            <span className={`status-indicator-dot ${isSessionExpired ? 'dot-terminated' : 'dot-live'}`}></span>
            <span className="status-indicator-text">
              {isSessionExpired ? 'Session Expired' : 'Interview In Progress'}
            </span>
          </div>
        </div>
      </header>

      {/* Floating Active Warning Banner */}
      {bannerAlert && (
        <div className="dashboard-floating-alert" role="alert">
          <span className="alert-badge-icon">⚠️</span>
          <div className="alert-content-box">
            <span className="alert-title-text">
              Warning {bannerAlert.count} of {MAX_INTEGRITY_WARNINGS}: {bannerAlert.title}
            </span>
            <span className="alert-message-text">{bannerAlert.message}</span>
          </div>
        </div>
      )}

      {/* Main Dashboard Grid */}
      <div className="dashboard-main-grid">
        {/* Large Video Area */}
        <div className="video-conference-stage">
          <div className="video-viewport-card">
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className={`dashboard-video-element ${isVideoOff ? 'video-hidden' : ''}`}
            />

            {/* Face Detection Bounding Boxes Canvas Layer */}
            <canvas
              ref={overlayCanvasRef}
              className="face-boxes-overlay-canvas"
            />

            {isVideoOff && (
              <div className="video-off-placeholder">
                <div className="avatar-circle">
                  <span>{candidateName.charAt(0).toUpperCase()}</span>
                </div>
                <p className="avatar-name">{candidateName}</p>
                <span className="avatar-status">Camera is paused</span>
              </div>
            )}

            {/* In-Video Overlays */}
            <div className="video-overlay-top-left">
              <span className="candidate-video-badge">{candidateName}</span>
              {audioLevel > 15 && !isMicMuted && (
                <span className="audio-active-badge">Speaking</span>
              )}
            </div>

            <div className="video-overlay-top-right">
              <span className={`person-count-badge ${personCount > 1 ? 'badge-alert' : 'badge-ok'}`}>
                {personCount <= 1 ? (personCount === 1 ? '1 Face Detected' : 'No Face Detected') : `${personCount} Faces Detected (Warning)`}
              </span>
            </div>

            {/* In-Call Controls Toolbar */}
            <div className="video-controls-toolbar">
              <button
                type="button"
                className={`control-btn ${isMicMuted ? 'btn-control-muted' : 'btn-control-active'}`}
                onClick={handleToggleMic}
                title={isMicMuted ? 'Unmute Microphone' : 'Mute Microphone'}
              >
                <span className="control-icon">{isMicMuted ? '🔇' : '🎙️'}</span>
                <span className="control-label">{isMicMuted ? 'Unmute' : 'Mute'}</span>
              </button>

              <button
                type="button"
                className={`control-btn ${isVideoOff ? 'btn-control-muted' : 'btn-control-active'}`}
                onClick={handleToggleVideo}
                title={isVideoOff ? 'Turn Video On' : 'Turn Video Off'}
              >
                <span className="control-icon">{isVideoOff ? '🚫' : '📹'}</span>
                <span className="control-label">{isVideoOff ? 'Start Video' : 'Stop Video'}</span>
              </button>

              <button
                type="button"
                className="control-btn btn-control-end"
                onClick={handleCompleteInterview}
                disabled={isEnding || isSessionExpired}
                title="Finish and submit interview session"
              >
                <span className="control-icon">⏹</span>
                <span className="control-label">{isEnding ? 'Ending...' : 'Complete Interview'}</span>
              </button>
            </div>
          </div>
        </div>

        {/* Right Sidebar: Details and Small Alerts Panel */}
        <aside className="dashboard-sidebar-panel">
          {/* Interview Details Card */}
          <div className="sidebar-card details-card">
            <h3 className="sidebar-card-title">Interview Details</h3>
            <div className="details-list">
              <div className="detail-item">
                <span className="detail-key">Candidate:</span>
                <span className="detail-val">{candidateName}</span>
              </div>
              <div className="detail-item">
                <span className="detail-key">Email:</span>
                <span className="detail-val text-truncate">{candidateEmail}</span>
              </div>
              <div className="detail-item">
                <span className="detail-key">Position:</span>
                <span className="detail-val">{interviewRole}</span>
              </div>
              <div className="detail-item">
                <span className="detail-key">Session Key:</span>
                <code className="detail-val font-mono">{sessionId.slice(0, 14)}...</code>
              </div>
            </div>
          </div>

          {/* Monitoring Integrity Summary */}
          <div className="sidebar-card status-metrics-card">
            <h3 className="sidebar-card-title">Integrity Status</h3>
            <div className="integrity-status-rows">
              <div className="status-row">
                <span className="status-row-icon">👤</span>
                <div className="status-row-content">
                  <span className="status-row-label">Visual Verification</span>
                  <span className={`status-row-value ${personCount > 1 ? 'text-warn' : 'text-ok'}`}>
                    {personCount === 1 ? 'Primary candidate visible' : `Multiple faces detected (${personCount})`}
                  </span>
                </div>
              </div>

              <div className="status-row">
                <span className="status-row-icon">🔊</span>
                <div className="status-row-content">
                  <span className="status-row-label">Acoustic Feed</span>
                  <span className="status-row-value">{speechStatusText}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Timestamped Warnings Alerts Panel */}
          <div className="sidebar-card alerts-panel-card">
            <div className="alerts-card-header">
              <h3 className="sidebar-card-title">Session Warnings</h3>
              <span className={`warning-counter-tag ${warningCount >= MAX_INTEGRITY_WARNINGS ? 'tag-danger' : warningCount > 0 ? 'tag-warning' : 'tag-neutral'}`}>
                {warningCount} / {MAX_INTEGRITY_WARNINGS} Warnings
              </span>
            </div>

            {isSessionExpired && (
              <div className="session-terminated-card" role="alert">
                <span className="terminated-icon">🚫</span>
                <h4 className="terminated-title">Session Expired</h4>
                <p className="terminated-sub">
                  The interview session was terminated because the maximum allowed integrity warnings ({MAX_INTEGRITY_WARNINGS}) was reached.
                </p>
                <button
                  type="button"
                  className="btn-view-report"
                  onClick={() => setIsReportOpen(true)}
                >
                  View Final Audit Summary
                </button>
              </div>
            )}

            <div className="warnings-history-list">
              {warningsList.length === 0 ? (
                <div className="no-warnings-placeholder">
                  <span className="check-green">✓</span>
                  <p>No warnings recorded.</p>
                  <span className="sub-hint">Session integrity is optimal.</span>
                </div>
              ) : (
                warningsList.map((warn) => (
                  <div key={warn.id} className="warning-item-row">
                    <div className="warning-item-meta">
                      <span className="warning-timestamp">[{warn.timeStr}]</span>
                      <span className="warning-number">Warning {warn.count}/{MAX_INTEGRITY_WARNINGS}</span>
                    </div>
                    <p className="warning-text-msg">{warn.message || warn.title}</p>
                  </div>
                ))
              )}
            </div>
          </div>
        </aside>
      </div>

      {/* Audit Report Modal */}
      {isReportOpen && reportData && (
        <SessionReportModal
          report={reportData}
          onClose={() => {
            setIsReportOpen(false);
            if (onInterviewEnd) onInterviewEnd();
          }}
        />
      )}
    </div>
  );
}
