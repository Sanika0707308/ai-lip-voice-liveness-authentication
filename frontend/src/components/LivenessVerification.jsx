import React, { useState, useEffect, useRef } from 'react';
import { fetchNewChallengePhrase, uploadAudioForSync } from '../utils/api';

const LANGUAGE_LABELS = {
  en: 'English',
  hi: 'हिन्दी (Hindi)',
  mr: 'मराठी (Marathi)'
};

const MAX_ALLOWED_ATTEMPTS = 5;

/**
 * Loads the MediaPipe FaceMesh library dynamically from CDN if not already loaded.
 * Safely guards against script re-execution and ensures promise resolves without hanging.
 */
const loadFaceMeshScript = () => {
  return new Promise((resolve) => {
    if (window.FaceMesh) {
      resolve();
      return;
    }
    const existingScript = document.querySelector('script[src*="face_mesh"]');
    if (existingScript) {
      if (window.FaceMesh) {
        resolve();
        return;
      }
      let checks = 0;
      const interval = setInterval(() => {
        checks++;
        if (window.FaceMesh || checks >= 30) {
          clearInterval(interval);
          resolve();
        }
      }, 100);
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/face_mesh.js';
    script.crossOrigin = 'anonymous';
    script.onload = () => resolve();
    script.onerror = () => {
      console.warn('FaceMesh CDN script load failed, continuing with fallback.');
      resolve();
    };
    // Guard timeout: never hang more than 3.5s
    setTimeout(() => resolve(), 3500);
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
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const faceMeshRef = useRef(null);
  const animationFrameIdRef = useRef(null);
  const isUnmountedRef = useRef(false);
  const isAnalyzingRef = useRef(false);
  const isProcessingFrameRef = useRef(false);
  const hasFinalizedRef = useRef(false);
  const processFrameRef = useRef(null);
  const countdownIntervalRef = useRef(null);
  const setupDevicesRef = useRef(null);
  const faceDetectedRef = useRef(false);
  const recordedMimeRef = useRef('audio/webm');

  // Audio analysis refs
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const audioMeterIntervalRef = useRef(null);

  // Lip-tracking buffer refs
  const lipBufferRef = useRef({ movement: [], timestamps: [] });
  const recordingSamplerIntervalRef = useRef(null);
  const latestMicVolumeRef = useRef(0);
  const isRecordingRef = useRef(false);
  const recordingStartTimeRef = useRef(null);
  const recordingStopTimeRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const lipsDetectedRef = useRef(false);

  // Component UI states
  const [cameraReady, setCameraReady] = useState(false);
  const [faceDetected, setFaceDetected] = useState(false);
  const [lipsDetected, setLipsDetected] = useState(false);
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
  const [verificationMetrics, setVerificationMetrics] = useState(null);
  const verifiedPayloadRef = useRef(null);
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

  const ensureTrackingLoopActive = () => {
    if (
      !isUnmountedRef.current &&
      streamRef.current &&
      animationFrameIdRef.current === null &&
      processFrameRef.current
    ) {
      animationFrameIdRef.current = requestAnimationFrame(processFrameRef.current);
    }
  };

  // 1. Initialize Camera, Microphone, and Audio Meter
  useEffect(() => {
    let isCancelled = false;
    isUnmountedRef.current = false;
    isAnalyzingRef.current = false;
    isProcessingFrameRef.current = false;

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

        if (isCancelled || isUnmountedRef.current) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }

        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          videoRef.current.play().catch(() => {});
        }
        setCameraReady(true);
        setMicActive(true);

        // Setup Web Audio API Analyser for live Mic Volume feedback
        try {
          const AudioContextClass = window.AudioContext || window.webkitAudioContext;
          if (AudioContextClass) {
            const ctx = new AudioContextClass();
            audioContextRef.current = ctx;
            if (ctx.state === 'suspended') {
              ctx.resume().catch(() => {});
            }
            const source = ctx.createMediaStreamSource(stream);
            const analyser = ctx.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);
            analyserRef.current = analyser;

            const dataArray = new Uint8Array(analyser.frequencyBinCount);
            audioMeterIntervalRef.current = setInterval(() => {
              if (isCancelled || isUnmountedRef.current) return;
              if (analyserRef.current) {
                analyserRef.current.getByteFrequencyData(dataArray);
                let sum = 0;
                for (let i = 0; i < dataArray.length; i++) {
                  sum += dataArray[i];
                }
                const avg = sum / dataArray.length;
                const normalized = Math.min(100, Math.round((avg / 128) * 100));
                setMicVolume(normalized);
                latestMicVolumeRef.current = normalized;
              }
            }, 50);
          }
        } catch (audioErr) {
          console.warn('Audio meter initialization skipped:', audioErr);
        }

        // Initialize MediaPipe FaceMesh
        await loadFaceMeshScript();
        if (isCancelled || isUnmountedRef.current) return;

        const faceMesh = new window.FaceMesh({
          locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/${file}`
        });

        faceMesh.setOptions({
          maxNumFaces: 1,
          refineLandmarks: false,
          minDetectionConfidence: 0.5,
          minTrackingConfidence: 0.5
        });

        faceMesh.onResults((results) => {
          if (isCancelled || isUnmountedRef.current) return;
          const canvas = canvasRef.current;
          const video = videoRef.current;
          let ctx = null;

          if (canvas && video) {
            const vWidth = video.videoWidth || video.clientWidth || 640;
            const vHeight = video.videoHeight || video.clientHeight || 480;
            if (canvas.width !== vWidth || canvas.height !== vHeight) {
              canvas.width = vWidth;
              canvas.height = vHeight;
            }
            ctx = canvas.getContext("2d");
            if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
          }

          if (!results.multiFaceLandmarks || results.multiFaceLandmarks.length === 0) {
            if (faceDetectedRef.current) {
              faceDetectedRef.current = false;
              setFaceDetected(false);
            }
            if (lipsDetectedRef.current) {
              lipsDetectedRef.current = false;
              setLipsDetected(false);
            }
            return;
          }

          if (!faceDetectedRef.current) {
            faceDetectedRef.current = true;
            setFaceDetected(true);
          }
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
            if (!lipsDetectedRef.current) {
              lipsDetectedRef.current = true;
              setLipsDetected(true);
            }

            const dxV = lowerLip.x - upperLip.x;
            const dyV = lowerLip.y - upperLip.y;
            const dzV = (lowerLip.z !== undefined && upperLip.z !== undefined) ? lowerLip.z - upperLip.z : 0;
            const vDist = Math.sqrt(dxV * dxV + dyV * dyV + dzV * dzV);

            const dxH = rightCorner.x - leftCorner.x;
            const dyH = rightCorner.y - leftCorner.y;
            const dzH = (rightCorner.z !== undefined && leftCorner.z !== undefined) ? rightCorner.z - leftCorner.z : 0;
            const hDist = Math.sqrt(dxH * dxH + dyH * dyH + dzH * dzH);

            const ratio = hDist > 1e-4 ? (vDist / hDist) : 0.0;

            // Render visible dots over landmarks 13, 14, 61, 291
            if (ctx && canvas) {
              const xUpper = upperLip.x * canvas.width;
              const yUpper = upperLip.y * canvas.height;
              const xLower = lowerLip.x * canvas.width;
              const yLower = lowerLip.y * canvas.height;
              const xLeft = leftCorner.x * canvas.width;
              const yLeft = leftCorner.y * canvas.height;
              const xRight = rightCorner.x * canvas.width;
              const yRight = rightCorner.y * canvas.height;

              // Vertical aperture line (13 to 14) in emerald green
              ctx.strokeStyle = "rgba(16, 185, 129, 0.85)";
              ctx.lineWidth = 2.5;
              ctx.beginPath();
              ctx.moveTo(xUpper, yUpper);
              ctx.lineTo(xLower, yLower);
              ctx.stroke();

              // Horizontal mouth width line (61 to 291) in cyan
              ctx.strokeStyle = "rgba(6, 182, 212, 0.85)";
              ctx.lineWidth = 2.5;
              ctx.beginPath();
              ctx.moveTo(xLeft, yLeft);
              ctx.lineTo(xRight, yRight);
              ctx.stroke();

              // Draw high-visibility landmark dots with halo
              const drawDot = (x, y, fillCol, ringCol, radius = 5) => {
                ctx.beginPath();
                ctx.arc(x, y, radius + 3, 0, 2 * Math.PI);
                ctx.strokeStyle = ringCol;
                ctx.lineWidth = 2;
                ctx.stroke();

                ctx.beginPath();
                ctx.arc(x, y, radius, 0, 2 * Math.PI);
                ctx.fillStyle = fillCol;
                ctx.fill();
              };

              // 13: Upper lip center
              drawDot(xUpper, yUpper, "#10B981", "#FFFFFF", 5);
              // 14: Lower lip center
              drawDot(xLower, yLower, "#10B981", "#FFFFFF", 5);
              // 61: Left mouth corner
              drawDot(xLeft, yLeft, "#06B6D4", "#FFFFFF", 5);
              // 291: Right mouth corner
              drawDot(xRight, yRight, "#06B6D4", "#FFFFFF", 5);
            }

            // Capture genuine lip movement data and strictly increasing timestamps during active recording
            if (isRecordingRef.current) {
              const nowMs = performance.now();
              const elapsedMs = Math.max(0, Math.round(nowMs - (recordingStartTimeRef.current || nowMs)));
              const lastT = lipBufferRef.current.timestamps[lipBufferRef.current.timestamps.length - 1];
              if (lastT === undefined || elapsedMs > lastT) {
                lipBufferRef.current.movement.push(ratio);
                lipBufferRef.current.timestamps.push(elapsedMs);
              }
            }
          } else {
            if (lipsDetectedRef.current) {
              lipsDetectedRef.current = false;
              setLipsDetected(false);
            }
          }
        });

        faceMeshRef.current = faceMesh;

        // Continuous video frame processing loop (keeps dots live before, during, and across retries)
        const processFrame = async () => {
          animationFrameIdRef.current = null;
          if (isCancelled || isUnmountedRef.current || !streamRef.current) return;
          const video = videoRef.current;
          const vTrack = streamRef.current.getVideoTracks()[0];
          if (
            video &&
            vTrack &&
            vTrack.readyState === 'live' &&
            video.readyState >= 2 &&
            video.videoWidth > 0 &&
            video.videoHeight > 0 &&
            faceMeshRef.current &&
            !isProcessingFrameRef.current
          ) {
            isProcessingFrameRef.current = true;
            try {
              await faceMeshRef.current.send({ image: video });
            } catch (err) {
              // Frame processing skipped
            } finally {
              isProcessingFrameRef.current = false;
            }
          }
          if (!isCancelled && !isUnmountedRef.current && streamRef.current) {
            animationFrameIdRef.current = requestAnimationFrame(processFrame);
          }
        };

        processFrameRef.current = processFrame;
        if (animationFrameIdRef.current) {
          cancelAnimationFrame(animationFrameIdRef.current);
        }
        animationFrameIdRef.current = requestAnimationFrame(processFrame);
      } catch (err) {
        if (!isCancelled && !isUnmountedRef.current) {
          setInitError('Unable to access webcam or microphone. Please verify browser permissions.');
        }
      }
    };

    setupDevicesRef.current = setupDevices;
    setupDevices();
    loadChallenge();

    return () => {
      isCancelled = true;
      isUnmountedRef.current = true;
      setupDevicesRef.current = null;
      processFrameRef.current = null;
      if (countdownIntervalRef.current) {
        clearInterval(countdownIntervalRef.current);
        countdownIntervalRef.current = null;
      }
      if (recordingSamplerIntervalRef.current) {
        clearInterval(recordingSamplerIntervalRef.current);
        recordingSamplerIntervalRef.current = null;
      }
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
        animationFrameIdRef.current = null;
      }
      if (audioMeterIntervalRef.current) {
        clearInterval(audioMeterIntervalRef.current);
        audioMeterIntervalRef.current = null;
      }
      if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
        try {
          audioContextRef.current.close();
        } catch {}
      }
      const fm = faceMeshRef.current;
      faceMeshRef.current = null;
      if (fm) {
        setTimeout(() => {
          try {
            fm.close();
          } catch {}
        }, 150);
      }
      if (!verifiedPayloadRef.current && streamRef.current) {
        try {
          streamRef.current.getTracks().forEach((t) => t.stop());
        } catch {}
        streamRef.current = null;
      }
    };
  }, []);

  // 2. Start 5-second challenge verification recording
  const handleStartVerification = async () => {
    if (isRecording || isAnalyzing || isExpired) return;

    const hasLiveVideo = streamRef.current &&
      streamRef.current.getVideoTracks().some((t) => t.readyState === 'live' && t.enabled);
    if (!streamRef.current || !cameraReady || !hasLiveVideo) {
      if (setupDevicesRef.current) {
        await setupDevicesRef.current();
      }
      return;
    }

    ensureTrackingLoopActive();

    if (audioContextRef.current && audioContextRef.current.state === 'suspended') {
      try {
        await audioContextRef.current.resume();
      } catch (e) {}
    }

    // Reset recording buffers
    if (recordingSamplerIntervalRef.current) {
      clearInterval(recordingSamplerIntervalRef.current);
      recordingSamplerIntervalRef.current = null;
    }
    lipBufferRef.current = { movement: [], timestamps: [] };
    audioChunksRef.current = [];
    isRecordingRef.current = true;
    isAnalyzingRef.current = false;
    hasFinalizedRef.current = false;
    recordingStartTimeRef.current = performance.now();
    recordingStopTimeRef.current = null;
    setIsRecording(true);
    setRecordingCountdown(5);
    setVerificationResult(null);
    setRejectionReason('');

    // Setup MediaRecorder for audio recording with live track safeguard
    try {
      let liveAudioTracks = streamRef.current
        .getAudioTracks()
        .filter((t) => t.readyState === 'live' && t.enabled);

      if (liveAudioTracks.length === 0) {
        streamRef.current.getAudioTracks().forEach((oldTrack) => {
          try {
            streamRef.current.removeTrack(oldTrack);
            oldTrack.stop();
          } catch (e) {}
        });
        let aStream = null;
        try {
          aStream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
          });
        } catch (optErr) {
          aStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        }
        aStream.getAudioTracks().forEach((track) => streamRef.current.addTrack(track));
        liveAudioTracks = streamRef.current
          .getAudioTracks()
          .filter((t) => t.readyState === 'live' && t.enabled);
      }

      if (!liveAudioTracks || liveAudioTracks.length === 0) {
        throw new Error('No live audio track available on media stream.');
      }

      const audioStream = new MediaStream(liveAudioTracks);
      const candidateMimes = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
        'audio/mp4'
      ];
      let options = {};
      for (const mime of candidateMimes) {
        if (typeof MediaRecorder.isTypeSupported === 'function' && MediaRecorder.isTypeSupported(mime)) {
          options = { mimeType: mime };
          break;
        }
      }

      let recorder;
      try {
        recorder = new MediaRecorder(audioStream, options);
      } catch (optErr) {
        recorder = new MediaRecorder(audioStream);
      }
      recordedMimeRef.current = recorder.mimeType || options.mimeType || 'audio/webm';
      mediaRecorderRef.current = recorder;

      console.info(
        `[LIVENESS] Starting recording | track=${liveAudioTracks[0]?.label || 'mic'} (${liveAudioTracks[0]?.readyState}) | mime=${recordedMimeRef.current}`
      );

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          audioChunksRef.current.push(e.data);
        }
      };

      recorder.onerror = (evt) => {
        console.error('[LIVENESS] MediaRecorder error:', evt);
      };

      recorder.onstop = () => {
        safeFinalizeAndSubmit();
      };

      recorder.start(100); // gather in chunks
    } catch (err) {
      console.error('Failed to start MediaRecorder:', err);
      setIsRecording(false);
      isRecordingRef.current = false;
      setVerificationResult('SPOOF');
      setRejectionReason('Microphone recording error. Please verify microphone permissions.');
      return;
    }

    // Countdown interval: tick 1 second down
    let timeLeft = 5;
    if (countdownIntervalRef.current) {
      clearInterval(countdownIntervalRef.current);
    }
    countdownIntervalRef.current = setInterval(() => {
      timeLeft -= 1;
      setRecordingCountdown(timeLeft);
      if (timeLeft <= 0) {
        clearInterval(countdownIntervalRef.current);
        countdownIntervalRef.current = null;
        isRecordingRef.current = false;
        recordingStopTimeRef.current = performance.now();

        let stoppedViaRecorder = false;
        if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
          try {
            mediaRecorderRef.current.requestData();
          } catch (e) {}
          try {
            mediaRecorderRef.current.stop();
            stoppedViaRecorder = true;
          } catch (e) {
            console.warn('Error stopping mediaRecorder:', e);
          }
        }

        // Guaranteed fallback if recorder onstop does not trigger within 500ms
        setTimeout(() => {
          safeFinalizeAndSubmit();
        }, stoppedViaRecorder ? 500 : 0);
      }
    }, 1000);
  };

  const safeFinalizeAndSubmit = () => {
    if (hasFinalizedRef.current) return;
    hasFinalizedRef.current = true;
    finalizeAndSubmitVerification();
  };

  // 3. Finalize and submit to /api/sync
  const finalizeAndSubmitVerification = async () => {
    setIsRecording(false);
    isRecordingRef.current = false;
    setIsAnalyzing(true);
    isAnalyzingRef.current = true;

    try {
      const mimeType = recordedMimeRef.current || 'audio/webm';
      const audioBlob = new Blob(audioChunksRef.current, { type: mimeType });
      const stopTime = recordingStopTimeRef.current || performance.now();
      const recordingDurationMs = Math.max(1000, Math.round(stopTime - (recordingStartTimeRef.current || (stopTime - 5000))));

      const rawMovement = [...lipBufferRef.current.movement];
      const rawTimestamps = [...lipBufferRef.current.timestamps];

      console.info(
        `[LIVENESS] Submitting attempt | chunks=${audioChunksRef.current.length} | bytes=${audioBlob.size} | mime=${mimeType} | lipFrames=${rawMovement.length} | durationMs=${recordingDurationMs}`
      );

      if (audioBlob.size < 512) {
        setIsAnalyzing(false);
        isAnalyzingRef.current = false;
        setVerificationResult('SPOOF');
        setRejectionReason(
          'No speech audio was recorded from your microphone. Please check your microphone device/permissions and speak the displayed challenge phrase aloud.'
        );
        const newAttemptsUsed = attemptsUsed + 1;
        setAttemptsUsed(newAttemptsUsed);
        if (newAttemptsUsed >= MAX_ALLOWED_ATTEMPTS) {
          setIsExpired(true);
          setVerificationResult('EXPIRED');
          if (onSessionExpired) onSessionExpired();
        }
        return;
      }

      // Validate that lips were actually tracked during the recording
      if (rawMovement.length < 15 || rawTimestamps.length < 15) {
        setIsAnalyzing(false);
        isAnalyzingRef.current = false;
        setVerificationResult('SPOOF');
        setRejectionReason(
          'Lips were not consistently detected during recording. Please face the camera directly, ensure your lips are clearly visible with tracking dots, and speak the displayed challenge phrase aloud.'
        );
        const newAttemptsUsed = attemptsUsed + 1;
        setAttemptsUsed(newAttemptsUsed);
        if (newAttemptsUsed >= MAX_ALLOWED_ATTEMPTS) {
          setIsExpired(true);
          setVerificationResult('EXPIRED');
          if (onSessionExpired) onSessionExpired();
        }
        return;
      }

      let finalMovement = [...rawMovement];
      let finalTimestamps = [...rawTimestamps];

      // Ensure recording window starts at 0ms
      if (finalTimestamps[0] > 0) {
        finalTimestamps.unshift(0);
        finalMovement.unshift(finalMovement[0]);
      }
      // Ensure recording window covers full duration
      const lastIdx = finalTimestamps.length - 1;
      if (finalTimestamps[lastIdx] < recordingDurationMs) {
        finalTimestamps.push(recordingDurationMs);
        finalMovement.push(finalMovement[lastIdx]);
      }

      // If fewer than 30 frames were captured due to browser FPS throttling, interpolate to ~30 FPS
      if (finalMovement.length < 30) {
        const interpMovement = [];
        const interpTimestamps = [];
        const stepMs = 33.333;
        const targetCount = Math.max(35, Math.round(recordingDurationMs / stepMs));
        for (let i = 0; i < targetCount; i++) {
          const t = Math.min(recordingDurationMs, Math.round(i * stepMs));
          interpTimestamps.push(t);
          let val = finalMovement[0];
          for (let j = 0; j < finalTimestamps.length - 1; j++) {
            if (t >= finalTimestamps[j] && t <= finalTimestamps[j + 1]) {
              const span = finalTimestamps[j + 1] - finalTimestamps[j];
              const frac = span > 0 ? (t - finalTimestamps[j]) / span : 0;
              val = finalMovement[j] + frac * (finalMovement[j + 1] - finalMovement[j]);
              break;
            }
          }
          if (t > finalTimestamps[finalTimestamps.length - 1]) {
            val = finalMovement[finalMovement.length - 1];
          }
          interpMovement.push(parseFloat(val.toFixed(4)));
        }
        finalMovement = interpMovement;
        finalTimestamps = interpTimestamps;
      }

      // Enforce strictly increasing timestamps
      for (let i = 1; i < finalTimestamps.length; i++) {
        if (finalTimestamps[i] <= finalTimestamps[i - 1]) {
          finalTimestamps[i] = finalTimestamps[i - 1] + 15;
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
        setVerificationMetrics({
          alignedCorrelation: res.alignedCorrelation || 0,
          rawCorrelation: res.rawCorrelation || 0,
          detectedTimeOffsetMs: res.detectedTimeOffsetMs || 0,
          whisperVerification: res.whisperVerification || null
        });
        setIsAnalyzing(false);
        isAnalyzingRef.current = false;

        try {
          localStorage.setItem('liveness_verified', 'true');
          localStorage.setItem('liveness_session_id', sessionId);
        } catch (storageErr) {}

        // Store payload for user to proceed to interview platform on button click
        verifiedPayloadRef.current = {
          stream: streamRef.current,
          sessionId,
          candidateData,
          language
        };
      } else {
        // Failed / SPOOF: display required "Spoof detected. Please try again" and allow retry
        setVerificationResult('SPOOF');
        setRejectionReason(
          res.rejectionReason || 'Spoken phrase or lip movement synchronization did not match.'
        );
        setIsAnalyzing(false);
        isAnalyzingRef.current = false;

        if (newAttemptsUsed >= MAX_ALLOWED_ATTEMPTS || res.isSessionExpired) {
          setIsExpired(true);
          setVerificationResult('EXPIRED');
          if (onSessionExpired) onSessionExpired();
        }
      }
    } catch (err) {
      setIsAnalyzing(false);
      isAnalyzingRef.current = false;
      const newAttemptsUsed = attemptsUsed + 1;
      setAttemptsUsed(newAttemptsUsed);

      if (err.status === 429 || newAttemptsUsed >= MAX_ALLOWED_ATTEMPTS) {
        setIsExpired(true);
        setVerificationResult('EXPIRED');
        setRejectionReason('Maximum verification attempts exceeded.');
        if (onSessionExpired) onSessionExpired();
      } else {
        setVerificationResult('SPOOF');
        setRejectionReason(err.message || 'Spoof detected. Please try again.');
      }
    } finally {
      ensureTrackingLoopActive();
    }
  };

  // 4. Candidate proceeds to the Technical Interview page on click
  const handleProceedToInterview = () => {
    if (onVerificationSuccess && verifiedPayloadRef.current) {
      onVerificationSuccess(verifiedPayloadRef.current);
    }
  };

  // 5. Retry verification attempt: cleanly resets states and reloads challenge phrase
  const handleRetryAttempt = async () => {
    if (isExpired || attemptsUsed >= MAX_ALLOWED_ATTEMPTS) return;
    if (countdownIntervalRef.current) {
      clearInterval(countdownIntervalRef.current);
      countdownIntervalRef.current = null;
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      try {
        mediaRecorderRef.current.stop();
      } catch (e) {}
    }
    mediaRecorderRef.current = null;
    setVerificationResult(null);
    setRejectionReason('');
    setIsRecording(false);
    setIsAnalyzing(false);
    isRecordingRef.current = false;
    isAnalyzingRef.current = false;
    isProcessingFrameRef.current = false;
    hasFinalizedRef.current = false;
    setRecordingCountdown(5);
    if (recordingSamplerIntervalRef.current) {
      clearInterval(recordingSamplerIntervalRef.current);
      recordingSamplerIntervalRef.current = null;
    }
    lipBufferRef.current = { movement: [], timestamps: [] };
    audioChunksRef.current = [];
    loadChallenge();

    const hasLiveVideo = streamRef.current &&
      streamRef.current.getVideoTracks().some((t) => t.readyState === 'live' && t.enabled);
    if (!hasLiveVideo && setupDevicesRef.current) {
      await setupDevicesRef.current();
    } else {
      ensureTrackingLoopActive();
    }
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
                onLoadedMetadata={(e) => e.target.play().catch(() => {})}
                className={`video-stream-element ${faceDetected ? 'face-aligned' : 'face-searching'}`}
              />

              {/* Real-time lip landmarks canvas overlay */}
              <canvas
                ref={canvasRef}
                className="camera-canvas"
              />

              {/* Minimal Face & Lip Guide Overlay */}
              <div className={`face-guide-boundary ${lipsDetected ? 'guide-active' : faceDetected ? 'guide-pending' : 'guide-passive'}`}>
                <span className={`guide-status-chip ${lipsDetected ? 'chip-lips-detected' : ''}`}>
                  {lipsDetected
                    ? '● Lips Detected (13, 14, 61, 291)'
                    : faceDetected
                    ? 'Face Aligned (Lips Not Detected)'
                    : 'Position Face in Center'}
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
              <h3 className="result-title">Liveness Verified: Candidate is LIVE</h3>
              <p className="result-desc">
                Lip-voice synchronization and speech verification passed successfully. You are verified as a genuine candidate.
              </p>
              <div className="live-metrics-summary">
                <span className="live-metric-chip">
                  Lip-Voice Sync: <strong>{verificationMetrics?.alignedCorrelation !== undefined ? Number(verificationMetrics.alignedCorrelation).toFixed(2) : '0.65'}</strong> (Min: &ge; 0.45)
                </span>
                <span className="live-metric-chip">
                  Time Offset: <strong>{verificationMetrics?.detectedTimeOffsetMs !== undefined ? `${verificationMetrics.detectedTimeOffsetMs > 0 ? '+' : ''}${Number(verificationMetrics.detectedTimeOffsetMs).toFixed(1)}ms` : '0.0ms'}</strong> (&plusmn;500ms)
                </span>
                <span className="live-metric-chip">
                  Speech Verification: <strong>PASS</strong>
                </span>
              </div>
            </div>
          </div>
        )}

        {verificationResult === 'SPOOF' && !isExpired && (
          <div className="result-banner result-spoof" role="alert">
            <div className="result-icon-box">⚠️</div>
            <div className="result-text-box">
              <h3 className="result-title">Spoof detected. Please try again</h3>
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
          {verificationResult === 'LIVE' && (
            <button
              type="button"
              className="btn-primary-large btn-start-interview"
              onClick={handleProceedToInterview}
            >
              <span>Start Interview &rarr;</span>
            </button>
          )}

          {!isRecording && !isAnalyzing && !verificationResult && (
            <button
              type="button"
              className="btn-primary-large btn-verify"
              onClick={handleStartVerification}
              disabled={!cameraReady || isExpired}
              title={!cameraReady ? 'Waiting for camera initialization' : 'Begin 5-second liveness check'}
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
              <span>Try Again &rarr;</span>
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
