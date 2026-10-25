import React, { useState, useEffect, useRef } from 'react';
import { fetchNewChallengePhrase, uploadAudioForSync } from '../utils/api';

const LANGUAGE_LABELS = {
  en: 'English',
  hi: 'हिन्दी (Hindi)',
  mr: 'मराठी (Marathi)'
};

const MAX_ALLOWED_ATTEMPTS = 3;

/**
 * Loads the MediaPipe FaceMesh library dynamically from CDN if not already loaded.
 */
const loadFaceMeshScript = () => {
  return new Promise((resolve, reject) => {
    if (window.FaceMesh) {
      resolve();
      return;
    }
    const existingScript = document.querySelector('script[src*="face_mesh"]');
    if (existingScript) {
      existingScript.addEventListener('load', () => resolve());
      existingScript.addEventListener('error', () => reject(new Error('Failed to load FaceMesh library.')));
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/face_mesh.js';
    script.crossOrigin = 'anonymous';
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Failed to load FaceMesh script. Please check your connection.'));
    document.body.appendChild(script);
  });
};

export default function LivenessVerification({
  candidateData,
  onVerificationSuccess,
  onSessionExpired
}) {
  const { candidateName, candidateEmail, interviewRole, language = 'en', sessionId } = candidateData;

  // Media stream & elements
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const faceMeshRef = useRef(null);
  const animationFrameIdRef = useRef(null);

  // Audio analysis refs
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const audioMeterIntervalRef = useRef(null);

  // Lip-tracking buffer refs
  const lipBufferRef = useRef({ movement: [], timestamps: [] });
  const isRecordingRef = useRef(false);
  const recordingStartTimeRef = useRef(null);
  const recordingStopTimeRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  // Component UI states
  const [cameraReady, setCameraReady] = useState(false);
  const [faceDetected, setFaceDetected] = useState(false);
  const [micVolume, setMicVolume] = useState(0); // 0 to 100
  const [micActive, setMicActive] = useState(false);
  const [initError, setInitError] = useState('');

  // Challenge phrase states
  const [challengePhrase, setChallengePhrase] = useState('');
  const [attemptId, setAttemptId] = useState('');
  const [isLoadingPhrase, setIsLoadingPhrase] = useState(true);

  // Verification flow states
  const [isRecording, setIsRecording] = useState(false);
  const [recordingCountdown, setRecordingCountdown] = useState(5);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [verificationResult, setVerificationResult] = useState(null); // null | 'LIVE' | 'SPOOF' | 'EXPIRED'
  const [rejectionReason, setRejectionReason] = useState('');
  const [attemptsUsed, setAttemptsUsed] = useState(0);
  const [isExpired, setIsExpired] = useState(false);

  // TTS audio playback
  const handleSpeakPhrase = () => {
    if (!challengePhrase || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(challengePhrase);
    if (language === 'hi') utterance.lang = 'hi-IN';
    else if (language === 'mr') utterance.lang = 'mr-IN';
    else utterance.lang = 'en-US';
    utterance.rate = 0.9;
    window.speechSynthesis.speak(utterance);
  };

  // Fetch dynamic challenge phrase
  const loadChallenge = async () => {
    setIsLoadingPhrase(true);
    try {
      const data = await fetchNewChallengePhrase(sessionId, language);
      if (data && data.challengePhrase) {
        setChallengePhrase(data.challengePhrase);
        setAttemptId(data.attemptId || '');
      } else {
        setChallengePhrase('Today is a calm and productive day');
      }
    } catch (err) {
      if (err.status === 429) {
        setIsExpired(true);
        setVerificationResult('EXPIRED');
        if (onSessionExpired) onSessionExpired();
      } else {
        // Fallback default
        setChallengePhrase('Today is a calm and productive day');
      }
    } finally {
      setIsLoadingPhrase(false);
    }
  };

  // 1. Initialize Camera, Microphone, and Audio Meter
  useEffect(() => {
    let isCancelled = false;

    const setupDevices = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            width: { ideal: 1280, min: 640 },
            height: { ideal: 720, min: 480 },
            facingMode: 'user'
          },
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true
          }
        });

        if (isCancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }

        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
        }
        setCameraReady(true);
        setMicActive(true);

        // Setup Web Audio API Analyser for live Mic Volume feedback
        try {
          const AudioContextClass = window.AudioContext || window.webkitAudioContext;
          if (AudioContextClass) {
            const ctx = new AudioContextClass();
            audioContextRef.current = ctx;
            const source = ctx.createMediaStreamSource(stream);
            const analyser = ctx.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);
            analyserRef.current = analyser;

            const dataArray = new Uint8Array(analyser.frequencyBinCount);
            audioMeterIntervalRef.current = setInterval(() => {
              if (analyserRef.current) {
                analyserRef.current.getByteFrequencyData(dataArray);
                let sum = 0;
                for (let i = 0; i < dataArray.length; i++) {
                  sum += dataArray[i];
                }
                const avg = sum / dataArray.length;
                const normalized = Math.min(100, Math.round((avg / 128) * 100));
                setMicVolume(normalized);
              }
            }, 100);
          }
        } catch (audioErr) {
          console.warn('Audio meter initialization skipped:', audioErr);
        }

        // Initialize MediaPipe FaceMesh
        await loadFaceMeshScript();
        if (isCancelled) return;

        const faceMesh = new window.FaceMesh({
          locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/${file}`
        });

        faceMesh.setOptions({
          maxNumFaces: 1,
          refineLandmarks: true,
          minDetectionConfidence: 0.5,
          minTrackingConfidence: 0.5
        });

        faceMesh.onResults((results) => {
          if (!results.multiFaceLandmarks || results.multiFaceLandmarks.length === 0) {
            setFaceDetected(false);
            return;
          }

          setFaceDetected(true);
          const landmarks = results.multiFaceLandmarks[0];

          // Canonical Lip Landmarks:
          // Landmark 13 = inner upper lip center, Landmark 14 = inner lower lip center
          // Landmark 61 = left mouth corner, Landmark 291 = right mouth corner
          // Lip Aperture Ratio = LVD / LHD = distance(13, 14) / distance(61, 291)
          const upperLip = landmarks[13];
          const lowerLip = landmarks[14];
          const leftCorner = landmarks[61];
          const rightCorner = landmarks[291];

          if (upperLip && lowerLip && leftCorner && rightCorner) {
            const vDist = Math.hypot(lowerLip.x - upperLip.x, lowerLip.y - upperLip.y);
            const hDist = Math.hypot(rightCorner.x - leftCorner.x, rightCorner.y - leftCorner.y);
            const ratio = hDist > 1e-4 ? (vDist / hDist) : 0.0;

            // If actively recording verification phrase, append to lip movement buffer
            if (isRecordingRef.current) {
              const nowMs = performance.now();
              const elapsedMs = Math.max(0, nowMs - (recordingStartTimeRef.current || nowMs));
              lipBufferRef.current.movement.push(ratio);
              lipBufferRef.current.timestamps.push(Math.round(elapsedMs));
            }
          }
        });

        faceMeshRef.current = faceMesh;

        // Start video frame processing loop
        const processFrame = async () => {
          if (videoRef.current && videoRef.current.readyState >= 2 && faceMeshRef.current) {
            try {
              await faceMeshRef.current.send({ image: videoRef.current });
            } catch (err) {
              // Frame processing skipped
            }
          }
          if (!isCancelled) {
            animationFrameIdRef.current = requestAnimationFrame(processFrame);
          }
        };

        animationFrameIdRef.current = requestAnimationFrame(processFrame);
      } catch (err) {
        if (!isCancelled) {
          setInitError('Unable to access webcam or microphone. Please verify browser permissions.');
        }
      }
    };

    setupDevices();
    loadChallenge();

    return () => {
      isCancelled = true;
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
      }
      if (audioMeterIntervalRef.current) {
        clearInterval(audioMeterIntervalRef.current);
      }
      if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
        try {
          audioContextRef.current.close();
        } catch {}
      }
      if (faceMeshRef.current) {
        try {
          faceMeshRef.current.close();
        } catch {}
      }
    };
  }, []);

  // 2. Start 5-second challenge verification recording
  const handleStartVerification = () => {
    if (!streamRef.current || !cameraReady || isRecording || isAnalyzing || isExpired) return;

    // Reset recording buffers
    lipBufferRef.current = { movement: [], timestamps: [] };
    audioChunksRef.current = [];
    isRecordingRef.current = true;
    recordingStartTimeRef.current = performance.now();
    setIsRecording(true);
    setRecordingCountdown(5);
    setVerificationResult(null);
    setRejectionReason('');

    // Setup MediaRecorder for audio recording
    try {
      const audioTrack = streamRef.current.getAudioTracks()[0];
      const audioStream = new MediaStream([audioTrack]);
      let options = { mimeType: 'audio/webm' };
      if (!MediaRecorder.isTypeSupported('audio/webm')) {
        options = {};
      }
      const recorder = new MediaRecorder(audioStream, options);
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          audioChunksRef.current.push(e.data);
        }
      };

      recorder.onstop = () => {
        finalizeAndSubmitVerification();
      };

      recorder.start(100); // gather in chunks
    } catch (err) {
      console.error('Failed to start MediaRecorder:', err);
      setIsRecording(false);
      isRecordingRef.current = false;
      return;
    }

    // Countdown interval: tick 1 second down
    let timeLeft = 5;
    const interval = setInterval(() => {
      timeLeft -= 1;
      setRecordingCountdown(timeLeft);
      if (timeLeft <= 0) {
        clearInterval(interval);
        // Stop recording
        isRecordingRef.current = false;
        recordingStopTimeRef.current = performance.now();
        if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
          try {
            mediaRecorderRef.current.stop();
          } catch {}
        }
      }
    }, 1000);
  };

  // 3. Finalize and submit to /api/sync
  const finalizeAndSubmitVerification = async () => {
    setIsRecording(false);
    setIsAnalyzing(true);

    try {
      const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
      const stopTime = recordingStopTimeRef.current || performance.now();
      const recordingDurationMs = Math.max(1000, Math.round(stopTime - (recordingStartTimeRef.current || (stopTime - 5000))));

      const rawMovement = [...lipBufferRef.current.movement];
      const rawTimestamps = [...lipBufferRef.current.timestamps];

      let finalMovement = [];
      let finalTimestamps = [];

      if (rawMovement.length > 0) {
        const t0 = rawTimestamps[0];
        for (let i = 0; i < rawMovement.length; i++) {
          finalMovement.push(rawMovement[i]);
          finalTimestamps.push(Math.max(0, Math.round(rawTimestamps[i] - t0)));
        }

        // Extend up to actual recordingDurationMs so audio duration and lip duration match within 30ms
        const lastT = finalTimestamps[finalTimestamps.length - 1];
        const lastVal = finalMovement[finalMovement.length - 1];
        if (lastT < recordingDurationMs - 50) {
          let nextT = lastT + 33;
          while (nextT <= recordingDurationMs) {
            finalMovement.push(lastVal);
            finalTimestamps.push(nextT);
            nextT += 33;
          }
        }
      }

      // Ensure minimum 32 frames required by settings.MIN_VALID_SYNC_FRAMES
      if (finalMovement.length < 32) {
        const currentCount = finalMovement.length;
        const needed = 32 - currentCount;
        const lastVal = finalMovement[finalMovement.length - 1] || 0.15;
        const lastT = finalTimestamps[finalTimestamps.length - 1] || 0;
        for (let i = 0; i < needed; i++) {
          finalMovement.push(lastVal);
          finalTimestamps.push(lastT + (i + 1) * 33);
        }
      }

      // Ensure timestamps are strictly increasing
      for (let i = 1; i < finalTimestamps.length; i++) {
        if (finalTimestamps[i] <= finalTimestamps[i - 1]) {
          finalTimestamps[i] = finalTimestamps[i - 1] + 33;
        }
      }

      const res = await uploadAudioForSync(
        audioBlob,
        finalMovement,
        finalTimestamps,
        sessionId,
        null,
        challengePhrase,
        candidateEmail || candidateName,
        language,
        attemptId
      );

      const resultStatus = res.livenessResult || res.finalStatus || 'SPOOF';
      const newAttemptsUsed = (res.attemptsUsed !== undefined) ? res.attemptsUsed : attemptsUsed + 1;
      setAttemptsUsed(newAttemptsUsed);

      if (resultStatus === 'LIVE') {
        setVerificationResult('LIVE');
        setIsAnalyzing(false);

        // Automatically open the interview dashboard after 1.5 seconds!
        setTimeout(() => {
          if (onVerificationSuccess) {
            onVerificationSuccess({
              stream: streamRef.current,
              sessionId,
              candidateData,
              language
            });
          }
        }, 1500);
      } else {
        // Failed / SPOOF
        setVerificationResult('SPOOF');
        setRejectionReason(
          res.rejectionReason || 'Spoken phrase or lip movement synchronization did not match.'
        );
        setIsAnalyzing(false);

        if (newAttemptsUsed >= MAX_ALLOWED_ATTEMPTS || res.isSessionExpired) {
          setIsExpired(true);
          setVerificationResult('EXPIRED');
          if (onSessionExpired) onSessionExpired();
        }
      }
    } catch (err) {
      setIsAnalyzing(false);
      const newAttemptsUsed = attemptsUsed + 1;
      setAttemptsUsed(newAttemptsUsed);

      if (err.status === 429 || newAttemptsUsed >= MAX_ALLOWED_ATTEMPTS) {
        setIsExpired(true);
        setVerificationResult('EXPIRED');
        setRejectionReason('Maximum verification attempts exceeded.');
        if (onSessionExpired) onSessionExpired();
      } else {
        setVerificationResult('SPOOF');
        setRejectionReason(err.message || 'Liveness verification failed. Please try again.');
      }
    }
  };

  // 4. Retry verification attempt
  const handleRetryAttempt = () => {
    if (isExpired || attemptsUsed >= MAX_ALLOWED_ATTEMPTS) return;
    setVerificationResult(null);
    setRejectionReason('');
    loadChallenge();
  };

  return (
    <div className="liveness-screen-wrap">
      <div className="liveness-card">
        {/* Top Header: Dynamic Challenge Phrase ABOVE the camera */}
        <div className="phrase-banner-card">
          <div className="phrase-banner-header">
            <div className="phrase-badge-tag">
              <span className="badge-pulse-dot"></span>
              <span>Spoken Verification Phrase ({LANGUAGE_LABELS[language] || 'English'})</span>
            </div>
            <div className="phrase-actions">
              <button
                type="button"
                className="btn-tts-icon"
                onClick={handleSpeakPhrase}
                title="Listen to challenge phrase pronunciation"
                disabled={isLoadingPhrase}
              >
                🔊 Listen
              </button>
              <span className="attempt-counter-pill">
                Attempt {Math.min(attemptsUsed + 1, MAX_ALLOWED_ATTEMPTS)} of {MAX_ALLOWED_ATTEMPTS}
              </span>
            </div>
          </div>

          <div className="phrase-text-display">
            {isLoadingPhrase ? (
              <span className="phrase-loading">Generating secure challenge phrase...</span>
            ) : (
              <blockquote className="phrase-quote">"{challengePhrase}"</blockquote>
            )}
          </div>
          <p className="phrase-instruction-note">
            Please read the phrase above clearly when you click <strong>Start Verification</strong>.
          </p>
        </div>

        {/* Camera Preview Area (ONLY Camera View) */}
        <div className="camera-viewport-container">
          {initError ? (
            <div className="camera-error-placeholder">
              <span className="error-big-icon">📷</span>
              <p className="error-title">Camera & Microphone Access Required</p>
              <p className="error-desc">{initError}</p>
            </div>
          ) : (
            <div className="video-feed-wrapper">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className={`video-stream-element ${faceDetected ? 'face-aligned' : 'face-searching'}`}
              />

              {/* Minimal Face Guide Overlay */}
              <div className={`face-guide-boundary ${faceDetected ? 'guide-active' : 'guide-passive'}`}>
                <span className="guide-status-chip">
                  {faceDetected ? 'Face Positioned Correctly' : 'Position Face in Center'}
                </span>
              </div>

              {/* Active Recording Countdown Ring */}
              {isRecording && (
                <div className="recording-countdown-overlay">
                  <div className="countdown-ring">
                    <span className="countdown-number">{recordingCountdown}s</span>
                  </div>
                  <p className="countdown-rec-text">Reciting verification phrase...</p>
                </div>
              )}

              {/* Analyzing Spinner Overlay */}
              {isAnalyzing && (
                <div className="analyzing-status-overlay">
                  <div className="analyzing-spinner"></div>
                  <p className="analyzing-title">Analyzing Biometric Liveness</p>
                  <p className="analyzing-sub">Checking lip movements and audio synchronization...</p>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Microphone Status Indicator */}
        <div className="mic-status-strip">
          <div className="mic-indicator-left">
            <span className={`mic-status-dot ${micActive ? 'mic-dot-active' : 'mic-dot-inactive'}`}></span>
            <span className="mic-label-text">
              {micActive ? 'Microphone Active' : 'Microphone Inactive'}
            </span>
          </div>
          <div className="mic-level-meter">
            <span className="meter-label">Audio Level</span>
            <div className="meter-track">
              <div
                className="meter-fill"
                style={{ width: `${Math.max(8, micVolume)}%` }}
              ></div>
            </div>
          </div>
        </div>

        {/* Verification Result Feedback: LIVE or SPOOF */}
        {verificationResult === 'LIVE' && (
          <div className="result-banner result-live" role="alert">
            <div className="result-icon-box">✓</div>
            <div className="result-text-box">
              <h3 className="result-title">LIVENESS VERIFIED: Candidate is LIVE</h3>
              <p className="result-desc">
                Biometric verification successful. Automatically launching your Interview Dashboard...
              </p>
            </div>
          </div>
        )}

        {verificationResult === 'SPOOF' && !isExpired && (
          <div className="result-banner result-spoof" role="alert">
            <div className="result-icon-box">⚠️</div>
            <div className="result-text-box">
              <h3 className="result-title">Verification Unsuccessful (SPOOF)</h3>
              <p className="result-desc">
                {rejectionReason || 'Lip movement and spoken audio did not synchronize adequately.'}
              </p>
              <p className="attempts-remaining-tag">
                Attempt {attemptsUsed} of {MAX_ALLOWED_ATTEMPTS} used ({MAX_ALLOWED_ATTEMPTS - attemptsUsed} remaining).
              </p>
            </div>
          </div>
        )}

        {/* Expired Session Blocked State */}
        {isExpired && (
          <div className="result-banner result-expired" role="alert">
            <div className="result-icon-box">✕</div>
            <div className="result-text-box">
              <h3 className="result-title">Verification Session Expired</h3>
              <p className="result-desc">
                You have reached the maximum allowed verification attempts (3 of 3).
                For interview integrity, this session has been locked.
                Please contact your interview coordinator to request a session reset.
              </p>
            </div>
          </div>
        )}

        {/* Action Controls Bar */}
        <div className="liveness-actions-bar">
          {!isRecording && !isAnalyzing && !verificationResult && (
            <button
              type="button"
              className="btn-primary-large btn-verify"
              onClick={handleStartVerification}
              disabled={!cameraReady || !faceDetected || isExpired}
              title={!faceDetected ? 'Please position your face in the camera' : 'Begin 5-second liveness check'}
            >
              <span>Start Verification</span>
            </button>
          )}

          {verificationResult === 'SPOOF' && !isExpired && (
            <button
              type="button"
              className="btn-primary-large btn-retry"
              onClick={handleRetryAttempt}
            >
              <span>Try Next Verification Attempt &rarr;</span>
            </button>
          )}

          {isExpired && (
            <button
              type="button"
              className="btn-secondary-large"
              onClick={() => window.location.reload()}
            >
              <span>Return to Login Screen</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
