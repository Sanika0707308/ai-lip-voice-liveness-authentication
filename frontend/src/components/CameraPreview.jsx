import React, { useState, useEffect, useRef } from 'react';
import { generateSessionId, getRandomChallengePhrase } from '../utils/challengePhrases';
import { fetchNewChallengePhrase, uploadAudioForSync } from '../utils/api';

// Day 16 additions: Configurable constants for Lip-Voice Synchronization Engine
const MIN_SYNC_SAMPLES = 15;
const MAX_ALIGNMENT_DIFF_MS = 100;
const DEFAULT_SYNC_THRESHOLD = 0.75;
const SYNC_SCORE_HISTORY_SIZE = 10;
const SYNC_WINDOW_MS = 5000;
const MIN_SIGNAL_VARIANCE = 1e-6;
const LIVE_CONFIRMATION_FRAMES = 3;
const SPOOF_CONFIRMATION_FRAMES = 3;

// Day 17 additions: Configurable constants for Challenge Phrase Library
const RECENT_CHALLENGE_HISTORY_SIZE = 5;
const RECORDING_DURATION_MS = 5000; // Auto-stop recording after 5 seconds
const MIN_LOCAL_AUDIO_ENERGY = 0.08;
const MIN_LOCAL_FACE_FRAMES = 20;
const MIN_LOCAL_AUDIO_FRAMES = 30;
const MIN_LOCAL_LIP_VARIATION = 0.02;




// Configurable constants for MediaPipe Face Mesh custom adjustments
const SHOW_LIP_LANDMARKS = true;
const LIP_OUTER_COLOR = "#FF5500"; // Neon Orange
const LIP_INNER_COLOR = "#FF0055"; // Neon Pink/Red
const LANDMARK_RADIUS = 2;
const MAX_NUM_FACES = 1;

// Lip landmark indices mapping the full concentric contours in MediaPipe Face Mesh
const OUTER_LIP_INDICES = [61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291, 375, 321, 405, 314, 17, 84, 181, 91, 146];
const INNER_LIP_INDICES = [78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308, 324, 318, 402, 317, 14, 87, 178, 88, 95];

// Key lip landmarks pre-defined for future Lip Movement Tracking module
const UPPER_LIP_CENTER = 13;
const LOWER_LIP_CENTER = 14;
const LEFT_LIP_CORNER = 61;
const RIGHT_LIP_CORNER = 291;

/**
 * Calculates the Euclidean distance between two 3D landmarks.
 * @param {Object} p1 First landmark point
 * @param {Object} p2 Second landmark point
 * @returns {number} Euclidean distance
 */
const calculateDistance = (p1, p2) => {
  if (!p1 || !p2) return 0;
  const dx = p1.x - p2.x;
  const dy = p1.y - p2.y;
  const dz = (p1.z !== undefined && p2.z !== undefined) ? p1.z - p2.z : 0;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
};

/**
 * Loads the MediaPipe Face Mesh script dynamically from jsDelivr CDN.
 * Ensures the script is added only once to the document head.
 * @returns {Promise<void>} Resolves when script is successfully loaded.
 */
const loadMediaPipeScript = () => {
  return new Promise((resolve, reject) => {
    if (window.FaceMesh) {
      resolve();
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/face_mesh.js';
    script.crossOrigin = 'anonymous';
    script.onload = () => {
      console.log('MediaPipe Face Mesh script loaded.');
      resolve();
    };
    script.onerror = (err) => {
      console.error('Failed to load MediaPipe Face Mesh script:', err);
      reject(new Error('Failed to load the face detection library. Check your internet connection.'));
    };
    document.head.appendChild(script);
  });
};

/**
 * Aligns rolling lip and audio sample arrays using nearest-neighbor timestamp matching.
 * Also tracks the min/max timestamp of successfully aligned samples to calculate window time.
 * @param {Array} lipSamples Lip opening ratio history
 * @param {Array} audioSamples Audio energy history
 * @param {number} maxDiffMs Max allowed difference in ms
 * @returns {Object} Aligned lip values, audio values, and time span of window
 */
const alignSamples = (lipSamples, audioSamples, maxDiffMs = 100) => {
  const alignedLip = [];
  const alignedAudio = [];
  let minTimestamp = Infinity;
  let maxTimestamp = -Infinity;

  for (const lip of lipSamples) {
    let closestAudio = null;
    let minDiff = Infinity;

    for (const audio of audioSamples) {
      const diff = Math.abs(lip.timestamp - audio.timestamp);
      if (diff < minDiff) {
        minDiff = diff;
        closestAudio = audio;
      }
    }

    if (closestAudio && minDiff <= maxDiffMs) {
      alignedLip.push(lip.ratio);
      alignedAudio.push(closestAudio.energy);
      if (lip.timestamp < minTimestamp) minTimestamp = lip.timestamp;
      if (lip.timestamp > maxTimestamp) maxTimestamp = lip.timestamp;
    }
  }

  const timeSpanSec = (alignedLip.length > 1) ? (maxTimestamp - minTimestamp) / 1000 : 0;
  return { alignedLip, alignedAudio, timeSpanSec };
};

/**
 * Min-Max normalizes signal values to the range [0, 1].
 * @param {Array<number>} values Input numbers
 * @returns {Array<number>} Normalized numbers
 */
const normalizeSignal = (values) => {
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  if (range === 0) {
    return values.map(() => 0);
  }
  return values.map(v => (v - min) / range);
};

/**
 * Calculates variance of an array of numbers.
 * @param {Array<number>} values Input numbers
 * @returns {number} Variance
 */
const calculateVariance = (values) => {
  const n = values.length;
  if (n === 0) return 0;
  const mean = values.reduce((sum, v) => sum + v, 0) / n;
  const sumSqDiff = values.reduce((sum, v) => sum + Math.pow(v - mean, 2), 0);
  return sumSqDiff / n;
};

/**
 * Computes the Pearson Correlation coefficient between two equal-length arrays.
 * @param {Array<number>} x First variable array
 * @param {Array<number>} y Second variable array
 * @returns {number} Pearson correlation coefficient (-1 to 1)
 */
const calculatePearsonCorrelation = (x, y) => {
  const n = x.length;
  if (n < 2 || n !== y.length) return 0;

  let sumX = 0;
  let sumY = 0;
  for (let i = 0; i < n; i++) {
    sumX += x[i];
    sumY += y[i];
  }
  const meanX = sumX / n;
  const meanY = sumY / n;

  let num = 0;
  let denX = 0;
  let denY = 0;

  for (let i = 0; i < n; i++) {
    const diffX = x[i] - meanX;
    const diffY = y[i] - meanY;
    num += diffX * diffY;
    denX += diffX * diffX;
    denY += diffY * diffY;
  }

  if (denX === 0 || denY === 0) return 0;
  return num / Math.sqrt(denX * denY);
};

/**
 * Encapsulated synchronization processing pipeline.
 * Performs time window filtering, alignment, variance validation, correlation, history tracking, and liveness scoring.
 */
const processSync = (lipSamples, audioSamples, threshold, currentHistory, options = {}) => {
  const {
    maxDiffMs = 100,
    minSamples = 15,
    historySize = 10,
    timeWindowMs = 5000,
    minVariance = 1e-6
  } = options;

  const now = performance.now();
  // Filter both buffers to use only the most recent synchronization window (default 5 seconds)
  const recentLips = lipSamples.filter(s => now - s.timestamp <= timeWindowMs);
  const recentAudio = audioSamples.filter(s => now - s.timestamp <= timeWindowMs);

  // Align samples
  const { alignedLip, alignedAudio, timeSpanSec } = alignSamples(recentLips, recentAudio, maxDiffMs);
  const count = alignedLip.length;

  // Correlation Quality Validation: Check MIN_SYNC_SAMPLES count
  if (count < minSamples) {
    return {
      rawScore: 0,
      smoothedScore: 0,
      history: [], // Reset history if we don't have enough samples
      count,
      status: 'PENDING',
      timeSpanSec,
      lowVariance: false
    };
  }

  // Normalize signals
  const normLip = normalizeSignal(alignedLip);
  const normAudio = normalizeSignal(alignedAudio);

  // Correlation Quality Validation: Check signal variance
  const lipVar = calculateVariance(normLip);
  const audioVar = calculateVariance(normAudio);

  if (lipVar < minVariance || audioVar < minVariance) {
    return {
      rawScore: 0,
      smoothedScore: 0,
      history: [], // Clear score buffer on low variance
      count,
      status: 'PENDING',
      timeSpanSec,
      lowVariance: true
    };
  }

  // Compute Pearson Correlation
  const rawScore = calculatePearsonCorrelation(normLip, normAudio);

  // Update history buffer
  const updatedHistory = [...currentHistory, rawScore];
  if (updatedHistory.length > historySize) {
    updatedHistory.shift();
  }

  // Compute rolling average
  const smoothedScore = updatedHistory.reduce((sum, s) => sum + s, 0) / updatedHistory.length;

  // Classify authentication status using smoothed score
  const status = smoothedScore >= threshold ? 'LIVE' : 'SPOOF';

  return {
    rawScore,
    smoothedScore,
    history: updatedHistory,
    count,
    status,
    timeSpanSec,
    lowVariance: false
  };
};


/**
 * CameraPreview Component
 * Manages camera feed lifecycle, runs real-time MediaPipe Face Mesh detection,
 * draws canvas landmark overlays, and calculates real-time FPS.
 */
function CameraPreview() {
  // State to track webcam status
  // Options: 'off' | 'requesting' | 'active' | 'denied' | 'unavailable' | 'unsupported'
  const [status, setStatus] = useState('off');
  
  // Specific user-friendly error details
  const [errorMsg, setErrorMsg] = useState('');

  // Day 11 additions: Detection status, canvas, and FPS tracking states
  const [faceDetected, setFaceDetected] = useState(false);
  const [fps, setFps] = useState(0);

  // Day 13 additions: Real-time lip movement metrics state
  const [metrics, setMetrics] = useState({
    verticalDistance: 0,
    horizontalDistance: 0,
    lipOpeningRatio: 0
  });

  // Day 14 additions: Microphone connection status and audio energy states
  const [micStatus, setMicStatus] = useState('off'); // 'off' | 'requesting' | 'active' | 'denied' | 'unavailable'
  const [audioEnergy, setAudioEnergy] = useState(0); // Normalized live energy (0 to 1)

  // Day 15 additions: Recording and transcription states
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0); // in seconds
  const [transcriptionText, setTranscriptionText] = useState('');
  const [detectedLanguage, setDetectedLanguage] = useState('');
  const [processingTimeMs, setProcessingTimeMs] = useState(0);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [transcriptionError, setTranscriptionError] = useState('');

  // Day 17 additions: Challenge Phrase and Session ID states
  const [challengePhrase, setChallengePhrase] = useState("");
  const [displayedChallenge, setDisplayedChallenge] = useState("");
  const [challengeId, setChallengeId] = useState(null);
  const [challengeStatus, setChallengeStatus] = useState("WAITING"); // WAITING | READY | RECORDING | COMPLETED | EXPIRED
  const [challengeTimeLeft, setChallengeTimeLeft] = useState(30);

  // Day 18 additions: Challenge Phrase Verification states
  const [recognizedText, setRecognizedText] = useState('');
  const [characterSimilarity, setCharacterSimilarity] = useState(0);
  const [wordSimilarity, setWordSimilarity] = useState(0);
  const [whisperConfidence, setWhisperConfidence] = useState(0);
  const [phraseSimilarity, setPhraseSimilarity] = useState(0);
  const [phraseVerificationStatus, setPhraseVerificationStatus] = useState('');
  const [phraseVerificationLoading, setPhraseVerificationLoading] = useState(false);
  const [verificationStage, setVerificationStage] = useState('');
  const [verificationError, setVerificationError] = useState('');
  const [verificationHistory, setVerificationHistory] = useState([]);
  const [backendProcessingTime, setBackendProcessingTime] = useState(0);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const [isHistoryCollapsed, setIsHistoryCollapsed] = useState(true);

  // Liveness session verification state
  const [isVerifying, setIsVerifying] = useState(false);
  const isVerifyingRef = useRef(false);
  useEffect(() => {
    isVerifyingRef.current = isVerifying;
  }, [isVerifying]);

  const recordingDurationTimeoutRef = useRef(null);


  // Sync ref for phraseVerificationStatus to prevent interval closure issues
  const phraseVerificationStatusRef = useRef('');
  useEffect(() => {
    phraseVerificationStatusRef.current = phraseVerificationStatus;
  }, [phraseVerificationStatus]);

  // Day 18 helpers for resetting states
  const resetVerificationStates = () => {
    setRecognizedText('');
    setCharacterSimilarity(0);
    setWordSimilarity(0);
    setWhisperConfidence(0);
    setPhraseSimilarity(0);
    setPhraseVerificationStatus('');
    setPhraseVerificationLoading(false);
    setVerificationStage('');
    setVerificationError('');
    setBackendProcessingTime(0);
    setRecordingDuration(0);
  };

  // Day 16 additions: Lip-Voice Synchronization Engine states
  const [syncScore, setSyncScore] = useState(0);
  const [rawSyncScore, setRawSyncScore] = useState(0);
  const [syncConfidence, setSyncConfidence] = useState(0);
  const [syncStatus, setSyncStatus] = useState('INACTIVE'); // 'INACTIVE' | 'PENDING' | 'LIVE' | 'SPOOF'
  const [syncSamplesCount, setSyncSamplesCount] = useState(0);
  const [syncThreshold, setSyncThreshold] = useState(DEFAULT_SYNC_THRESHOLD);
  const [diagnostics, setDiagnostics] = useState({
    lipSamples: 0,
    audioSamples: 0,
    alignedSamples: 0,
    syncWindow: 0
  });

  // Refs for threshold tracking and history to prevent loop closure issues
  const syncThresholdRef = useRef(syncThreshold);
  useEffect(() => {
    syncThresholdRef.current = syncThreshold;
  }, [syncThreshold]);

  // Day 17: Lifecycle trigger to generate challenge phrase when camera and mic become active
  useEffect(() => {
    if (status === 'active' && micStatus === 'active') {
      if (!challengePhrase && challengeStatus === 'WAITING') {
        generateChallengePhrase();
      }
    }
  }, [status, micStatus, challengePhrase, challengeStatus]);

  // Automate recording start once phrase is READY
  useEffect(() => {
    if (challengeStatus === 'READY' && status === 'active' && micStatus === 'active' && !isRecording && isVerifying) {
      startRecording();
    }
  }, [challengeStatus, status, micStatus, isRecording, isVerifying]);

  // Day 17: Sync challengeStatus with isRecording state and manage expiry timer
  useEffect(() => {
    if (isRecording) {
      setChallengeStatus("RECORDING");
      clearExpiryTimer();
    } else if (challengeStatus === "RECORDING" && !isRecording) {
      setChallengeStatus("COMPLETED");
    }
  }, [isRecording, challengeStatus]);

  const syncScoresHistoryRef = useRef([]);

  // Refs for tracking previous state values to avoid unnecessary renders
  const prevSyncScoreRef = useRef(0);
  const prevSyncStatusRef = useRef('INACTIVE');
  const prevSamplesCountRef = useRef(0);

  // Day 16 additions: Synchronization History Buffer and Confirmation Refs
  const syncHistoryRef = useRef([]);
  const liveConfirmCountRef = useRef(0);
  const spoofConfirmCountRef = useRef(0);

  // Day 16 additions: Session summary states and refs
  const [showSummaryCard, setShowSummaryCard] = useState(false);
  const [finalSummary, setFinalSummary] = useState(null);
  const [finalResult, setFinalResult] = useState('');
  const sessionSummaryRef = useRef({
    averageScore: 0,
    maxScore: -Infinity,
    minScore: Infinity,
    liveCount: 0,
    spoofCount: 0,
    totalEvaluations: 0,
    startTime: null,
    duration: 0,
    scoresSum: 0
  });




  // Refs for video element, canvas element, and stream tracks
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);

  // Day 11 references for MediaPipe, animation loop, and FPS counters
  const faceMeshRef = useRef(null);
  const animationFrameIdRef = useRef(null);
  const lastFrameTimeRef = useRef(performance.now());
  const fpsValuesRef = useRef([]);

  // Day 13 reference: FIFO rolling buffer for the last 100 Lip Opening Ratio values
  const lipRatioBufferRef = useRef([]);

  // Day 14 references: Web Audio nodes, frame loop and audio buffer ref
  const audioContextRef = useRef(null);
  const audioStreamRef = useRef(null);
  const audioAnalyserRef = useRef(null);
  const audioAnimationFrameIdRef = useRef(null);
  const audioBufferRef = useRef([]); // FIFO buffer of last 100 entries: { timestamp, energy }

  // Day 15 references: Recording refs for timer and timestamps
  const mediaRecorderRef = useRef(null);
  const speechRecognitionRef = useRef(null);
  const spokenPhraseRef = useRef('');
  const recordingTimerRef = useRef(null);
  const recordingTimeRef = useRef(0);
  const recordingStartTimeRef = useRef(0);
  const recordingEndTimeRef = useRef(0);

  // Day 17 references: Challenge Expiration and Session ID refs
  const challengeStartTimeRef = useRef(null);
  const recentChallengeIdsRef = useRef([]);
  const challengeExpiryTimerRef = useRef(null);
  const challengeSessionIdRef = useRef(null);

  // Day 18 additions: Challenge Phrase Verification references
  const activeChallengeRef = useRef('');
  const activeSessionIdRef = useRef('');
  const abortControllerRef = useRef(null);

  const isRecordingRef = useRef(false);
  const recordingLipMovementRef = useRef([]);
  const recordingLipTimestampsRef = useRef([]);
  const localEvidenceRef = useRef({ faceFrames: 0, activeAudioFrames: 0 });
  const [backendSyncResult, setBackendSyncResult] = useState(null);

  // Ref to track status across asynchronous frame loop executions without closure staleness
  const statusRef = useRef(status);
  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  // Sync ref for micStatus to prevent loop closure staleness
  const micStatusRef = useRef(micStatus);
  useEffect(() => {
    micStatusRef.current = micStatus;
  }, [micStatus]);

  /**
   * Day 17: Helper to clear challenge expiration timer.
   */
  const clearExpiryTimer = () => {
    if (challengeExpiryTimerRef.current) {
      clearInterval(challengeExpiryTimerRef.current);
      challengeExpiryTimerRef.current = null;
    }
  };

  const startLocalSpeechRecognition = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      spokenPhraseRef.current = 'Speech recognition is unavailable in this browser.';
      setRecognizedText(spokenPhraseRef.current);
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = 'en-US';
    recognition.onresult = (event) => {
      let transcript = '';
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        if (event.results[index].isFinal) {
          transcript += ` ${event.results[index][0].transcript}`;
        }
      }
      if (transcript.trim()) {
        spokenPhraseRef.current = `${spokenPhraseRef.current} ${transcript}`.trim();
        setRecognizedText(spokenPhraseRef.current);
      }
    };
    recognition.onerror = (event) => {
      console.warn('Local speech recognition error:', event.error);
    };
    speechRecognitionRef.current = recognition;
    try {
      recognition.start();
    } catch (error) {
      console.warn('Unable to start local speech recognition:', error);
    }
  };

  const stopLocalSpeechRecognition = () => {
    if (speechRecognitionRef.current) {
      try {
        speechRecognitionRef.current.stop();
      } catch (error) {
        console.warn('Unable to stop local speech recognition:', error);
      }
      speechRecognitionRef.current = null;
    }
  };

  const transcriptContainsChallengeCode = (transcript, challenge) => {
    const expectedCode = (challenge.match(/\d{4}/) || [])[0];
    const digitWords = {
      zero: '0', one: '1', two: '2', three: '3', four: '4',
      five: '5', six: '6', seven: '7', eight: '8', nine: '9'
    };
    const recognizedDigits = transcript
      .toLowerCase()
      .replace(/\b(zero|one|two|three|four|five|six|seven|eight|nine)\b/g, (_, word) => digitWords[word])
      .replace(/\D/g, '');
    return Boolean(expectedCode && recognizedDigits.includes(expectedCode));
  };

  /**
   * Day 17: Helper to start/restart challenge expiration timer.
   */
  const startExpiryTimer = () => {
    clearExpiryTimer();
    setChallengeTimeLeft(30);
    let timeLeft = 30;
    challengeExpiryTimerRef.current = setInterval(() => {
      if (phraseVerificationStatusRef.current || challengeStatus === 'COMPLETED' || isRecording) {
        clearExpiryTimer();
        return;
      }
      timeLeft -= 1;
      if (timeLeft <= 0) {
        clearExpiryTimer();
        setChallengeStatus("EXPIRED");
        // Automatically generate a new challenge
        generateChallengePhrase();
      } else {
        setChallengeTimeLeft(timeLeft);
      }
    }, 1000);
  };

  /**
   * Day 17: Helper to generate a new unique Session ID.
   * Session ID must only be generated once per authentication session start.
   */
  const ensureSessionId = () => {
    if (!challengeSessionIdRef.current) {
      challengeSessionIdRef.current = generateSessionId();
      if (import.meta.env.DEV) {
        console.log('Generated unique Session ID:', challengeSessionIdRef.current);
      }
    }
  };

  /**
   * Day 17: Helper to randomly choose a challenge phrase and configure the authentication session.
   */
  const generateChallengePhrase = async () => {
    if (challengeStatus === "RECORDING" || isRecording) {
      console.warn("Cannot generate new challenge phrase during active recording.");
      return;
    }

    // 1. Ensure unique Session ID exists
    ensureSessionId();
    setChallengeStatus("WAITING");
    try {
      const serverChallenge = await fetchNewChallengePhrase(challengeSessionIdRef.current);
      const phrase = serverChallenge.challengePhrase;
      setChallengePhrase(phrase);
      setDisplayedChallenge(phrase);
      setChallengeId(null);
      setChallengeStatus("READY");
      resetVerificationStates();
      spokenPhraseRef.current = '';
      setRecognizedText('');
      challengeStartTimeRef.current = performance.now();
      startExpiryTimer();
    } catch (error) {
      console.error('Failed to initialize challenge:', error);
      setChallengeStatus("EXPIRED");
      setVerificationError('Could not prepare a server challenge. Check that the backend is running.');
      setVerificationStage('ERROR');
    }
  };

  // Day 18 additions: Recording and Verification logic
  const startRecording = () => {
    if (!audioStreamRef.current) {
      setVerificationError("No audio stream available. Please enable microphone.");
      setVerificationStage('ERROR');
      return;
    }
    
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();
    
    activeChallengeRef.current = challengePhrase;
    activeSessionIdRef.current = challengeSessionIdRef.current;
    
    resetVerificationStates();
    setBackendSyncResult(null);
    recordingLipMovementRef.current = [];
    recordingLipTimestampsRef.current = [];
    localEvidenceRef.current = { faceFrames: 0, activeAudioFrames: 0 };
    
    const chunks = [];
    try {
      const mediaRecorder = new MediaRecorder(audioStreamRef.current, { mimeType: 'audio/webm' });
      mediaRecorderRef.current = mediaRecorder;
      
      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          chunks.push(e.data);
        }
      };
      
      mediaRecorder.onstop = async () => {
        const durationMs = Math.round(performance.now() - recordingStartTimeRef.current);
        setRecordingDuration(durationMs);
        setChallengeStatus("COMPLETED");
        const mimeType = mediaRecorder.mimeType || 'audio/webm';
        const audioBlob = new Blob(chunks, { type: mimeType });
        await uploadAudioAndVerify(audioBlob);
      };
      
      setIsRecording(true);
      isRecordingRef.current = true;
      setChallengeStatus("RECORDING");
      clearExpiryTimer();
      
      recordingStartTimeRef.current = performance.now();
      setRecordingTime(0);
      recordingTimerRef.current = setInterval(() => {
        setRecordingTime(prev => prev + 1);
      }, 1000);
      
      mediaRecorder.start();
      startLocalSpeechRecognition();
      console.log("MediaRecorder started");

      // Auto-stop recording after RECORDING_DURATION_MS
      if (recordingDurationTimeoutRef.current) {
        clearTimeout(recordingDurationTimeoutRef.current);
      }
      recordingDurationTimeoutRef.current = setTimeout(() => {
        stopRecording();
      }, RECORDING_DURATION_MS);

    } catch (err) {
      console.error("Failed to start MediaRecorder:", err);
      setVerificationError("Failed to start audio recording: " + err.message);
      setVerificationStage('ERROR');
      isRecordingRef.current = false;
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
      console.log("MediaRecorder stopped");
    }
    stopLocalSpeechRecognition();
    setIsRecording(false);
    isRecordingRef.current = false;
    setChallengeStatus("COMPLETED");
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    if (recordingDurationTimeoutRef.current) {
      clearTimeout(recordingDurationTimeoutRef.current);
      recordingDurationTimeoutRef.current = null;
    }
  };

  const stopStreamsImmediately = async () => {
    // 1. Set verification state to false so frame/audio processing loop terminates
    setIsVerifying(false);
    isVerifyingRef.current = false;
    stopLocalSpeechRecognition();

    // 2. Terminate animation frame loops
    if (animationFrameIdRef.current) {
      cancelAnimationFrame(animationFrameIdRef.current);
      animationFrameIdRef.current = null;
    }
    if (audioAnimationFrameIdRef.current) {
      cancelAnimationFrame(audioAnimationFrameIdRef.current);
      audioAnimationFrameIdRef.current = null;
    }

    // 3. Stop all webcam tracks
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => {
        track.stop();
        console.log(`Stopped video track: ${track.label}`);
      });
      streamRef.current = null;
    }

    // 4. Stop all microphone tracks
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach(track => {
        track.stop();
        console.log(`Stopped audio track: ${track.label}`);
      });
      audioStreamRef.current = null;
    }

    // 5. Close AudioContext
    if (audioContextRef.current) {
      try {
        if (audioContextRef.current.state !== 'closed') {
          await audioContextRef.current.close();
        }
      } catch (err) {
        console.error('Error closing AudioContext:', err);
      }
      audioContextRef.current = null;
    }

    // 6. Reset audio analyzer and energy
    audioAnalyserRef.current = null;
    setAudioEnergy(0);
    audioBufferRef.current = [];
    setMicStatus('off');

    // 7. Clear video element source
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }

    // 8. Clear Canvas overlay
    if (canvasRef.current) {
      const canvas = canvasRef.current;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    // 9. Close FaceMesh model instance to release resources
    if (faceMeshRef.current) {
      try {
        await faceMeshRef.current.close();
        console.log('MediaPipe FaceMesh resources released.');
      } catch (err) {
        console.error('Error during FaceMesh disposal:', err);
      }
      faceMeshRef.current = null;
    }

    // 10. Clear timers
    clearExpiryTimer();
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    if (recordingDurationTimeoutRef.current) {
      clearTimeout(recordingDurationTimeoutRef.current);
      recordingDurationTimeoutRef.current = null;
    }

    // 11. Set camera status to off
    setStatus('off');
    setFaceDetected(false);
    setFps(0);
    fpsValuesRef.current = [];
    setMetrics({
      verticalDistance: 0,
      horizontalDistance: 0,
      lipOpeningRatio: 0
    });
    lipRatioBufferRef.current = [];
  };

  const uploadAudioAndVerify = async (audioBlob) => {
    // Immediately release camera and microphone streams!
    await stopStreamsImmediately();

    const boundChallenge = activeChallengeRef.current;
    const boundSessionId = activeSessionIdRef.current;
    
    if (!boundSessionId) {
      console.warn("Missing bound session ID for verification upload");
      return;
    }
    
    setPhraseVerificationLoading(true);
    setVerificationStage('VERIFYING');
    setVerificationError('');
    
    try {
      const response = await uploadAudioForSync(
        audioBlob,
        recordingLipMovementRef.current,
        recordingLipTimestampsRef.current,
        boundSessionId,
        abortControllerRef.current ? abortControllerRef.current.signal : null
      );
      
      if (response.sessionId !== activeSessionIdRef.current) {
        console.warn("Discarding response for stale session ID:", response.sessionId);
        return;
      }
      
      setBackendSyncResult(response);
      
      // Update UI metrics with response
      const isLive = response.syncStatus === 'LIVE';
      setSyncScore(response.alignedCorrelation || 0);
      setRawSyncScore(response.rawCorrelation || 0);
      setSyncStatus(response.syncStatus || 'SPOOF');
      setSyncConfidence(Math.max(0, Math.min((response.alignedCorrelation || 0) * 100, 100)));
      setSyncSamplesCount(response.validFrames || 0);
      
      const whisper = response.whisperVerification || {};
      const recognized = whisper.recognizedText || '';
      const charSim = whisper.characterSimilarityPercentage || 0;
      const wordSim = whisper.wordMatchPercentage || 0;
      const confidence = whisper.whisperConfidence || 0;
      const phraseSim = whisper.overallScore || 0;
      const verifStatus = whisper.verificationStatus || 'FAIL';
      
      setRecognizedText(recognized);
      setCharacterSimilarity(charSim);
      setWordSimilarity(wordSim);
      setWhisperConfidence(confidence);
      setPhraseSimilarity(phraseSim);
      setPhraseVerificationStatus(verifStatus);
      setBackendProcessingTime(response.detectedTimeOffsetMs || 0);
      setRecordingDuration(response.audioDurationMs || 0);
      
      setVerificationStage('COMPLETED');
      
      // Save history item
      const newHistoryItem = {
        timestamp: new Date().toLocaleTimeString(),
        overallScore: phraseSim,
        characterSimilarity: charSim,
        wordSimilarity: wordSim,
        whisperConfidence: confidence,
        status: verifStatus,
        recognizedText: recognized,
        expectedPhrase: boundChallenge
      };
      setVerificationHistory(prev => [newHistoryItem, ...prev].slice(0, 5));

      // Construct final summary statistics for summary card display
      setFinalSummary({
        averageScore: response.alignedCorrelation || 0,
        maxScore: response.alignedCorrelation || 0,
        minScore: response.alignedCorrelation || 0,
        liveCount: isLive ? 1 : 0,
        spoofCount: isLive ? 0 : 1,
        totalEvaluations: 1,
        duration: (response.audioDurationMs || 0) / 1000,
        phraseVerificationStatus: verifStatus,
        overallScore: phraseSim,
        characterSimilarity: charSim,
        wordSimilarity: wordSim,
        whisperConfidence: confidence,
        processingTime: response.detectedTimeOffsetMs || 0,
        recordingDuration: response.audioDurationMs || 0,
        recognizedText: recognized,
        expectedPhrase: boundChallenge,
        backendSyncResult: response
      });
      
      setFinalResult(isLive ? 'LIVE' : 'SPOOF');
      setShowSummaryCard(true);

      // Immediately release camera and microphone streams!
      await stopStreamsImmediately();
      
    } catch (err) {
      if (err.name === 'AbortError') {
        console.log("Upload request was aborted.");
        return;
      }
      console.error("Verification upload failed:", err);
      
      setVerificationStage('ERROR');
      
      let errMsg = "Failed to complete synchronization verification.";
      if (err.message) {
        errMsg = err.message;
      }
      setVerificationError(errMsg);
      setSyncStatus('SPOOF');

      // Construct final summary for error/spoof case
      setFinalSummary({
        averageScore: 0,
        maxScore: 0,
        minScore: 0,
        liveCount: 0,
        spoofCount: 1,
        totalEvaluations: 1,
        duration: 0,
        phraseVerificationStatus: 'FAIL',
        overallScore: 0,
        characterSimilarity: 0,
        wordSimilarity: 0,
        whisperConfidence: 0.0,
        processingTime: 0,
        recordingDuration: 0,
        recognizedText: '',
        expectedPhrase: boundChallenge,
        backendSyncResult: null
      });
      setFinalResult('SPOOF');
      setShowSummaryCard(true);

      // Immediately release camera and microphone streams!
      await stopStreamsImmediately();
    } finally {
      setPhraseVerificationLoading(false);
    }
  };

  /**
   * Resets all synchronization states, score buffers, history and diagnostics
   */
  const resetSyncState = (initialStatus = 'INACTIVE') => {
    lipRatioBufferRef.current = [];
    audioBufferRef.current = [];
    syncScoresHistoryRef.current = [];
    syncHistoryRef.current = [];
    liveConfirmCountRef.current = 0;
    spoofConfirmCountRef.current = 0;
    sessionSummaryRef.current = {
      averageScore: 0,
      maxScore: -Infinity,
      minScore: Infinity,
      liveCount: 0,
      spoofCount: 0,
      totalEvaluations: 0,
      startTime: null,
      duration: 0,
      scoresSum: 0
    };
    setSyncScore(0);
    setRawSyncScore(0);
    setSyncConfidence(0);
    setSyncStatus(initialStatus);
    setSyncSamplesCount(0);
    setDiagnostics({
      lipSamples: 0,
      audioSamples: 0,
      alignedSamples: 0,
      syncWindow: 0
    });
    prevSyncScoreRef.current = 0;
    prevSyncStatusRef.current = initialStatus;
    prevSamplesCountRef.current = 0;
  };

  /**
   * Updates Lip-Voice synchronization metrics, calculates score and confidence,
   * classifications liveness, and handles throttled UI state updates.
   */
  const updateSyncCorrelation = (hasFace) => {
    // If camera or mic is not active, force INACTIVE state
    if (statusRef.current !== 'active' || micStatusRef.current !== 'active') {
      if (prevSyncStatusRef.current !== 'INACTIVE') {
        resetSyncState('INACTIVE');
      }
      return;
    }

    // If face is not detected, force PENDING state
    if (!hasFace) {
      if (prevSyncStatusRef.current !== 'PENDING') {
        resetSyncState('PENDING');
      }
      return;
    }

    // Process synchronization metrics
    const result = processSync(
      lipRatioBufferRef.current,
      audioBufferRef.current,
      syncThresholdRef.current,
      syncScoresHistoryRef.current,
      {
        maxDiffMs: MAX_ALIGNMENT_DIFF_MS,
        minSamples: MIN_SYNC_SAMPLES,
        historySize: SYNC_SCORE_HISTORY_SIZE,
        timeWindowMs: SYNC_WINDOW_MS,
        minVariance: MIN_SIGNAL_VARIANCE
      }
    );

    // Save history back
    syncScoresHistoryRef.current = result.history;

    // Stable Status Confirmation logic using refs
    const candidateStatus = result.status;
    let confirmedStatus = prevSyncStatusRef.current;

    if (candidateStatus === 'LIVE') {
      liveConfirmCountRef.current += 1;
      spoofConfirmCountRef.current = 0;
      if (liveConfirmCountRef.current >= LIVE_CONFIRMATION_FRAMES) {
        confirmedStatus = 'LIVE';
      }
    } else if (candidateStatus === 'SPOOF') {
      spoofConfirmCountRef.current += 1;
      liveConfirmCountRef.current = 0;
      if (spoofConfirmCountRef.current >= SPOOF_CONFIRMATION_FRAMES) {
        confirmedStatus = 'SPOOF';
      }
    } else {
      liveConfirmCountRef.current = 0;
      spoofConfirmCountRef.current = 0;
      confirmedStatus = candidateStatus;
    }

    // Append to rolling synchronization history buffer (FIFO limit 100)
    syncHistoryRef.current.push({
      timestamp: performance.now(),
      rawScore: result.rawScore,
      smoothedScore: result.smoothedScore,
      status: confirmedStatus
    });
    if (syncHistoryRef.current.length > 100) {
      syncHistoryRef.current.shift();
    }

    // Update session summary if we are actively evaluating liveness
    if (confirmedStatus === 'LIVE' || confirmedStatus === 'SPOOF') {
      if (!sessionSummaryRef.current.startTime) {
        sessionSummaryRef.current.startTime = performance.now();
      }

      const currentScore = result.smoothedScore;
      const summary = sessionSummaryRef.current;

      summary.totalEvaluations += 1;
      summary.scoresSum += currentScore;
      summary.averageScore = summary.scoresSum / summary.totalEvaluations;
      summary.maxScore = Math.max(summary.maxScore, currentScore);
      summary.minScore = Math.min(summary.minScore, currentScore);

      if (confirmedStatus === 'LIVE') {
        summary.liveCount += 1;
      } else if (confirmedStatus === 'SPOOF') {
        summary.spoofCount += 1;
      }

      summary.duration = (performance.now() - summary.startTime) / 1000;
    }

    // Calculate diagnostics values
    const currentDiagnostics = {
      lipSamples: lipRatioBufferRef.current.length,
      audioSamples: audioBufferRef.current.length,
      alignedSamples: result.count,
      syncWindow: result.timeSpanSec
    };

    // Diagnostics are updated with synchronization logic updates
    setDiagnostics(currentDiagnostics);

    // Performance Optimization: Check thresholds for state changes before triggering render
    const scoreDiff = Math.abs(result.smoothedScore - prevSyncScoreRef.current);
    const statusChanged = confirmedStatus !== prevSyncStatusRef.current;
    const samplesCountChanged = result.count !== prevSamplesCountRef.current;

    // Only trigger React updates when score changes by > 0.01, status transitions,
    // or when sample count changes during PENDING (so progress updates)
    const shouldUpdate = scoreDiff > 0.01 || statusChanged || (confirmedStatus === 'PENDING' && samplesCountChanged);

    if (shouldUpdate) {
      setSyncScore(result.smoothedScore);
      setRawSyncScore(result.rawScore);
      setSyncStatus(confirmedStatus);
      setSyncConfidence(Math.max(0, Math.min(result.smoothedScore * 100, 100)));
      setSyncSamplesCount(result.count);

      prevSyncScoreRef.current = result.smoothedScore;
      prevSyncStatusRef.current = confirmedStatus;
      prevSamplesCountRef.current = result.count;
    }
  };

  // Dynamic re-classification if the threshold is manually adjusted during active tracking
  useEffect(() => {
    if (status === 'active' && micStatus === 'active' && faceDetected && syncSamplesCount >= MIN_SYNC_SAMPLES) {
      const newStatus = syncScore >= syncThreshold ? 'LIVE' : 'SPOOF';
      if (newStatus !== syncStatus) {
        setSyncStatus(newStatus);
        prevSyncStatusRef.current = newStatus;
        // Reset confirmation counts on manual threshold change
        liveConfirmCountRef.current = 0;
        spoofConfirmCountRef.current = 0;
      }
    }
  }, [syncThreshold, syncScore, status, micStatus, faceDetected, syncSamplesCount, syncStatus]);


  /**
   * Initializes and configures the FaceMesh model.
   * Caches the instance in faceMeshRef.
   */
  const initFaceMesh = () => {
    if (faceMeshRef.current) return faceMeshRef.current;

    if (!window.FaceMesh) {
      console.error('MediaPipe FaceMesh script has not loaded on window.');
      return null;
    }

    // Instantiate FaceMesh configuration
    const faceMesh = new window.FaceMesh({
      locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/${file}`
    });

    faceMesh.setOptions({
      maxNumFaces: MAX_NUM_FACES, // Uses config constant
      refineLandmarks: false,
      minDetectionConfidence: 0.5,
      minTrackingConfidence: 0.5
    });

    // Register callback for face landmarks result
    faceMesh.onResults((results) => {
      if (!isVerifyingRef.current) {
        return;
      }

      // 1. Calculate FPS
      const now = performance.now();
      const delta = now - lastFrameTimeRef.current;
      lastFrameTimeRef.current = now;
      if (delta > 0) {
        const currentFps = 1000 / delta;
        fpsValuesRef.current.push(currentFps);
        if (fpsValuesRef.current.length > 10) fpsValuesRef.current.shift();
        const avgFps = Math.round(
          fpsValuesRef.current.reduce((a, b) => a + b, 0) / fpsValuesRef.current.length
        );
        setFps(avgFps);
      }

      // 2. Track detection status state
      const hasFace = results.multiFaceLandmarks && results.multiFaceLandmarks.length > 0;
      setFaceDetected(hasFace);

      // 3. Process metrics and update rolling buffer
      if (hasFace) {
        const landmarks = results.multiFaceLandmarks[0];
        const pUpper = landmarks[UPPER_LIP_CENTER];
        const pLower = landmarks[LOWER_LIP_CENTER];
        const pLeft = landmarks[LEFT_LIP_CORNER];
        const pRight = landmarks[RIGHT_LIP_CORNER];

        if (pUpper && pLower && pLeft && pRight) {
          const vDist = calculateDistance(pUpper, pLower);
          const hDist = calculateDistance(pLeft, pRight);
          const ratio = hDist > 0 ? (vDist / hDist) : 0;

          setMetrics({
            verticalDistance: vDist,
            horizontalDistance: hDist,
            lipOpeningRatio: ratio
          });

          // Add to rolling FIFO buffer of 100 entries
          const timestamp = performance.now();
          lipRatioBufferRef.current.push({ timestamp, ratio });
          if (lipRatioBufferRef.current.length > 100) {
            lipRatioBufferRef.current.shift();
          }

          // Capture lip movement data and timestamps during active recording
          if (isRecordingRef.current) {
            localEvidenceRef.current.faceFrames += 1;
            recordingLipMovementRef.current.push(ratio);
            const elapsedMs = timestamp - recordingStartTimeRef.current;
            recordingLipTimestampsRef.current.push(elapsedMs);
          }
        } else {
          setMetrics({
            verticalDistance: 0,
            horizontalDistance: 0,
            lipOpeningRatio: 0
          });
          lipRatioBufferRef.current = [];
        }
      } else {
        setMetrics({
          verticalDistance: 0,
          horizontalDistance: 0,
          lipOpeningRatio: 0
        });
        lipRatioBufferRef.current = [];
      }

      // Day 16: Update synchronization correlation engine
      updateSyncCorrelation(hasFace && lipRatioBufferRef.current.length > 0);


      // 4. Draw landmarks overlay
      const canvas = canvasRef.current;
      if (canvas) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Conditioned on SHOW_LIP_LANDMARKS configuration constant
        if (hasFace && SHOW_LIP_LANDMARKS) {
          const landmarks = results.multiFaceLandmarks[0];
          
          // A. Draw Outer Lip Landmarks in Neon Orange
          ctx.fillStyle = LIP_OUTER_COLOR;
          for (let i = 0; i < OUTER_LIP_INDICES.length; i++) {
            const idx = OUTER_LIP_INDICES[i];
            const landmark = landmarks[idx];
            if (landmark) {
              const x = landmark.x * canvas.width;
              const y = landmark.y * canvas.height;
              ctx.beginPath();
              ctx.arc(x, y, LANDMARK_RADIUS, 0, 2 * Math.PI); // Uses config radius constant
              ctx.fill();
            }
          }

          // B. Draw Inner Lip Landmarks in Neon Pink/Red
          ctx.fillStyle = LIP_INNER_COLOR;
          for (let i = 0; i < INNER_LIP_INDICES.length; i++) {
            const idx = INNER_LIP_INDICES[i];
            const landmark = landmarks[idx];
            if (landmark) {
              const x = landmark.x * canvas.width;
              const y = landmark.y * canvas.height;
              ctx.beginPath();
              ctx.arc(x, y, LANDMARK_RADIUS, 0, 2 * Math.PI); // Uses config radius constant
              ctx.fill();
            }
          }

          // C. Draw Measurement Visualizations (Lines & Key Node Highlights)
          const pUpper = landmarks[UPPER_LIP_CENTER];
          const pLower = landmarks[LOWER_LIP_CENTER];
          const pLeft = landmarks[LEFT_LIP_CORNER];
          const pRight = landmarks[RIGHT_LIP_CORNER];

          if (pUpper && pLower && pLeft && pRight) {
            const xUpper = pUpper.x * canvas.width;
            const yUpper = pUpper.y * canvas.height;
            const xLower = pLower.x * canvas.width;
            const yLower = pLower.y * canvas.height;
            const xLeft = pLeft.x * canvas.width;
            const yLeft = pLeft.y * canvas.height;
            const xRight = pRight.x * canvas.width;
            const yRight = pRight.y * canvas.height;

            // Draw line for Vertical distance (Upper Center to Lower Center) in cyan
            ctx.strokeStyle = 'rgba(0, 255, 204, 0.75)';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(xUpper, yUpper);
            ctx.lineTo(xLower, yLower);
            ctx.stroke();

            // Draw line for Horizontal distance (Left Corner to Right Corner) in yellow
            ctx.strokeStyle = 'rgba(245, 175, 25, 0.75)';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(xLeft, yLeft);
            ctx.lineTo(xRight, yRight);
            ctx.stroke();

            // Helper function to draw key landmarks larger with outer glow ring
            const drawKeyLandmark = (x, y, color) => {
              ctx.fillStyle = color;
              ctx.beginPath();
              ctx.arc(x, y, 4, 0, 2 * Math.PI);
              ctx.fill();
              
              ctx.strokeStyle = '#ffffff';
              ctx.lineWidth = 1;
              ctx.beginPath();
              ctx.arc(x, y, 6, 0, 2 * Math.PI);
              ctx.stroke();
            };

            drawKeyLandmark(xUpper, yUpper, '#00ffcc');
            drawKeyLandmark(xLower, yLower, '#00ffcc');
            drawKeyLandmark(xLeft, yLeft, '#f5af19');
            drawKeyLandmark(xRight, yRight, '#f5af19');
          }
        }
      }
    });

    faceMeshRef.current = faceMesh;
    return faceMesh;
  };

  /**
   * Animation frame loop that continuously captures video frames
   * and sends them to the FaceMesh model.
   */
  const startFrameLoop = (faceMesh) => {
    const video = videoRef.current;
    const canvas = canvasRef.current;

    const processFrame = async () => {
      // Stop the loop if the camera status changes or verification is no longer active
      if (!streamRef.current || statusRef.current !== 'active' || !isVerifyingRef.current) {
        return;
      }

      if (video && video.readyState >= 2) { // HAVE_CURRENT_DATA
        // Dynamically resize canvas to fit video display dimensions
        if (canvas) {
          if (canvas.width !== video.clientWidth || canvas.height !== video.clientHeight) {
            canvas.width = video.clientWidth;
            canvas.height = video.clientHeight;
          }
        }

        try {
          await faceMesh.send({ image: video });
        } catch (err) {
          console.error('FaceMesh frame processing error:', err);
        }
      }

      animationFrameIdRef.current = requestAnimationFrame(processFrame);
    };

    animationFrameIdRef.current = requestAnimationFrame(processFrame);
  };

  /**
   * Day 14: Stop microphone stream tracks, close AudioContext, cancel
   * requestAnimationFrame loops, and reset all metrics/buffers.
   */
  const stopAudio = async () => {
    // 1. Cancel audio animation frame loop
    if (audioAnimationFrameIdRef.current) {
      cancelAnimationFrame(audioAnimationFrameIdRef.current);
      audioAnimationFrameIdRef.current = null;
    }

    // 2. Shut down microphone stream tracks
    if (audioStreamRef.current) {
      const tracks = audioStreamRef.current.getTracks();
      tracks.forEach(track => {
        track.stop();
        console.log(`Stopped audio track: ${track.label}`);
      });
      audioStreamRef.current = null;
    }

    // 3. Close AudioContext
    if (audioContextRef.current) {
      try {
        if (audioContextRef.current.state !== 'closed') {
          await audioContextRef.current.close();
        }
      } catch (err) {
        console.error('Error closing AudioContext:', err);
      }
      audioContextRef.current = null;
    }

    // 4. Reset references and states
    audioAnalyserRef.current = null;
    setAudioEnergy(0);
    audioBufferRef.current = [];
    setMicStatus('off');
    
    // Day 16: Reset sync states on audio stop
    resetSyncState('INACTIVE');

  };

  /**
   * Day 14: Processing loop that continuously reads time-domain data
   * and calculates the normalized RMS audio energy.
   */
  const startAudioLoop = (analyser) => {
    const bufferLength = analyser.fftSize;
    const dataArray = new Float32Array(bufferLength);

    const processAudioFrame = () => {
      // Break loop if microphone stream or context is closed/released or verification is no longer active
      if (!audioStreamRef.current || !audioContextRef.current || audioContextRef.current.state === 'closed' || !isVerifyingRef.current) {
        return;
      }

      analyser.getFloatTimeDomainData(dataArray);

      // Calculate RMS (Root Mean Square)
      let sum = 0;
      for (let i = 0; i < bufferLength; i++) {
        sum += dataArray[i] * dataArray[i];
      }
      const rms = Math.sqrt(sum / bufferLength);

      // Speaking generates RMS values mostly under 0.25. Multiply by 4.0
      // to make it more responsive and visually distinct, then clamp to [0, 1].
      const normalizedEnergy = Math.min(rms * 4.0, 1.0);
      setAudioEnergy(normalizedEnergy);

      // Push to rolling FIFO buffer of 100 entries
      const timestamp = performance.now();
      audioBufferRef.current.push({
        timestamp,
        energy: normalizedEnergy
      });

      if (isRecordingRef.current && normalizedEnergy >= MIN_LOCAL_AUDIO_ENERGY) {
        localEvidenceRef.current.activeAudioFrames += 1;
      }

      if (audioBufferRef.current.length > 100) {
        audioBufferRef.current.shift();
      }

      audioAnimationFrameIdRef.current = requestAnimationFrame(processAudioFrame);
    };

    audioAnimationFrameIdRef.current = requestAnimationFrame(processAudioFrame);
  };

  /**
   * Day 14: Requests microphone access and initializes Web Audio API nodes.
   * If permission is denied, it isolates the failure so webcam remains active.
   */
  const startAudio = async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setMicStatus('unavailable');
      return;
    }

    setMicStatus('requesting');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      audioStreamRef.current = stream;

      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      const audioCtx = new AudioContextClass();
      audioContextRef.current = audioCtx;

      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      audioAnalyserRef.current = analyser;

      setMicStatus('active');

      // Start processing loop
      startAudioLoop(analyser);
    } catch (err) {
      console.error('Microphone initialization failed:', err);
      const errName = err.name || err.toString();
      if (errName === 'NotAllowedError' || errName === 'PermissionDeniedError') {
        setMicStatus('denied');
      } else if (errName === 'NotFoundError' || errName === 'DevicesNotFoundError') {
        setMicStatus('unavailable'); // Device Not Found
      } else {
        setMicStatus('unavailable');
      }
    }
  };

  /**
   * Releases camera tracks, stops the requestAnimationFrame loop,
   * closes the MediaPipe model instance to free WASM heap resources,
   * and resets component states.
   */
  const stopCamera = async () => {
    setIsVerifying(false);
    isVerifyingRef.current = false;
    if (recordingDurationTimeoutRef.current) {
      clearTimeout(recordingDurationTimeoutRef.current);
      recordingDurationTimeoutRef.current = null;
    }

    // Cancel any pending local recording operation.
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    // 1. Terminate animation loop
    if (animationFrameIdRef.current) {
      cancelAnimationFrame(animationFrameIdRef.current);
      animationFrameIdRef.current = null;
    }

    // 2. Shut down media stream tracks
    if (streamRef.current) {
      const tracks = streamRef.current.getTracks();
      tracks.forEach(track => {
        track.stop();
        console.log(`Stopped track: ${track.label}`);
      });
      streamRef.current = null;
    }

    // 3. Clear video element source
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }

    // 4. Clear Canvas overlay
    if (canvasRef.current) {
      const canvas = canvasRef.current;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    // 5. Close and release FaceMesh model instance
    if (faceMeshRef.current) {
      try {
        await faceMeshRef.current.close();
        console.log('MediaPipe FaceMesh resources released.');
      } catch (err) {
        console.error('Error during FaceMesh disposal:', err);
      }
      faceMeshRef.current = null;
    }

    // 6. Reset UI states
    setStatus('off');
    setErrorMsg('');
    setFaceDetected(false);
    setFps(0);
    fpsValuesRef.current = [];
    setMetrics({
      verticalDistance: 0,
      horizontalDistance: 0,
      lipOpeningRatio: 0
    });
    lipRatioBufferRef.current = [];

    // Capture final summary statistics before resetting
    if (sessionSummaryRef.current.totalEvaluations > 0) {
      if (sessionSummaryRef.current.startTime) {
        sessionSummaryRef.current.duration = (performance.now() - sessionSummaryRef.current.startTime) / 1000;
      }
      
      const finalSyncStatus = backendSyncResult ? backendSyncResult.syncStatus : prevSyncStatusRef.current;
      const isLive = (finalSyncStatus === 'LIVE' && phraseVerificationStatus === 'PASS');
      const combinedResult = isLive ? 'LIVE' : 'SPOOF';
      
      setFinalSummary({
        ...sessionSummaryRef.current,
        phraseVerificationStatus: phraseVerificationStatus,
        overallScore: phraseSimilarity,
        characterSimilarity: characterSimilarity,
        wordSimilarity: wordSimilarity,
        whisperConfidence: whisperConfidence,
        processingTime: backendProcessingTime,
        recordingDuration: recordingDuration,
        recognizedText: recognizedText,
        expectedPhrase: activeChallengeRef.current,
        backendSyncResult: backendSyncResult
      });
      
      setFinalResult(combinedResult);
      setShowSummaryCard(true);
    }
    isRecordingRef.current = false;
    recordingLipMovementRef.current = [];
    recordingLipTimestampsRef.current = [];
    setBackendSyncResult(null);

    // Day 16: Reset sync states on camera stop
    resetSyncState('INACTIVE');

    // Day 17 additions: Reset challenge states and refs on camera stop
    setChallengePhrase("");
    setDisplayedChallenge("");
    setChallengeId(null);
    setChallengeStatus("WAITING");
    setChallengeTimeLeft(30);
    setIsRecording(false);
    setRecordingTime(0);
    challengeStartTimeRef.current = null;
    recentChallengeIdsRef.current = [];
    challengeSessionIdRef.current = null;
    clearExpiryTimer();

    // Day 18 additions: Reset history and verification states
    setVerificationHistory([]);
    resetVerificationStates();

    // Day 14: Stop audio processing and reset states
    await stopAudio();

  };

  /**
   * Requests camera permission, loads MediaPipe scripts,
   * instantiates the model, and runs the real-time frame loop.
   */
  const startCamera = async () => {
    // Detect if media devices are supported by current browser
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus('unsupported');
      setErrorMsg('Your browser does not support webcam streaming APIs.');
      return;
    }

    setIsVerifying(true);

    // Hide summary card when starting new session
    setShowSummaryCard(false);
    setFinalSummary(null);

    // Day 18 additions: Abort any active request and reset verification states/history
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    setVerificationHistory([]);
    resetVerificationStates();
    setBackendSyncResult(null);

    // Day 17 additions: Reset challenge and Session ID for a brand-new authentication session
    setChallengePhrase("");
    setDisplayedChallenge("");
    setChallengeId(null);
    setChallengeStatus("WAITING");
    setChallengeTimeLeft(30);
    setIsRecording(false);
    setRecordingTime(0);
    challengeStartTimeRef.current = null;
    recentChallengeIdsRef.current = [];
    challengeSessionIdRef.current = null;
    clearExpiryTimer();

    setStatus('requesting');
    setErrorMsg('');

    try {
      // 1. Dynamic CDN load of MediaPipe library
      await loadMediaPipeScript();

      // 2. Request user media (video only)
      const constraints = {
        video: {
          width: { ideal: 1280 },
          height: { ideal: 720 },
          facingMode: 'user'
        },
        audio: false
      };

      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      streamRef.current = stream;

      // 3. Initialize Face Mesh
      const faceMesh = initFaceMesh();
      if (!faceMesh) {
        throw new Error('Failed to initialize Face Mesh model.');
      }

      setStatus('active');

      // Day 14: Start microphone audio capture and buffer collection
      startAudio();

      // 4. Link stream to HTML video and initialize processing
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        
        // Wait until video has loaded enough data to start the frame loop
        videoRef.current.onloadedmetadata = () => {
          startFrameLoop(faceMesh);
        };
      }
    } catch (error) {
      console.error('Initialization failed:', error);
      handleCameraError(error);
    }
  };

  /**
   * Maps specific media devices and initialization errors to friendly UI statements.
   */
  const handleCameraError = (error) => {
    const errorName = error.name || error.toString();
    
    // Clear state triggers to clean up and restore UI on error
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }

    setIsVerifying(false);

    switch (errorName) {
      case 'NotAllowedError':
      case 'PermissionDeniedError':
        setStatus('denied');
        setErrorMsg('Webcam access was denied. Please update your browser site settings to allow camera permission.');
        break;
      case 'NotFoundError':
      case 'DevicesNotFoundError':
        setStatus('unavailable');
        setErrorMsg('No camera hardware could be found on your device.');
        break;
      case 'NotReadableError':
      case 'TrackStartError':
        setStatus('unavailable');
        setErrorMsg('The camera is already in use by another application or tab.');
        break;
      case 'OverconstrainedError':
      case 'ConstraintNotSatisfiedError':
        setStatus('unavailable');
        setErrorMsg('The requested camera settings are not supported by your hardware.');
        break;
      case 'SecurityError':
        setStatus('unsupported');
        setErrorMsg('Webcam access is restricted in this non-secure context. Please serve via HTTPS or localhost.');
        break;
      default:
        setStatus('unavailable');
        setErrorMsg(error.message || 'Could not initialize face detection. Check your connection.');
        break;
    }
  };

  // Pre-load the script when the component mounts to save startup time
  useEffect(() => {
    loadMediaPipeScript().catch((err) => {
      console.warn('Script pre-load delayed: will load during activation.', err);
    });

    return () => {
      // Day 18: Abort active verification request on unmount
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      
      // Final fallback lifecycle cleanup on unmount
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
      }
      if (faceMeshRef.current) {
        try {
          faceMeshRef.current.close();
        } catch (e) {
          console.error(e);
        }
      }

      // Day 14: Cleanup microphone tracks, AudioContext, loop, and buffers
      if (audioAnimationFrameIdRef.current) {
        cancelAnimationFrame(audioAnimationFrameIdRef.current);
      }
      if (audioStreamRef.current) {
        audioStreamRef.current.getTracks().forEach(track => track.stop());
      }
      if (audioContextRef.current) {
        try {
          audioContextRef.current.close();
        } catch (e) {
          console.error(e);
        }
      }
      
      // Day 17 additions: Clean up challenge expiry timer on unmount
      if (challengeExpiryTimerRef.current) {
        clearTimeout(challengeExpiryTimerRef.current);
      }
      if (recordingTimerRef.current) {
        clearInterval(recordingTimerRef.current);
      }
      if (recordingDurationTimeoutRef.current) {
        clearTimeout(recordingDurationTimeoutRef.current);
      }
    };
  }, []);

  return (
    <div className="camera-box-container">
      {/* Real-time detection status bar (green/red indicators) */}
      {status === 'active' && (
        <div className={`detection-status-bar ${faceDetected ? 'detected' : 'not-detected'}`}>
          <span className="detection-status-dot"></span>
          <span>{faceDetected ? 'Face Detected' : 'No Face Detected'}</span>
        </div>
      )}

      {/* 1. Camera Frame Viewport */}
      <div className={`camera-preview-wrapper ${status === 'active' ? 'active' : ''} ${['denied', 'unavailable', 'unsupported'].includes(status) ? 'error' : ''}`}>
        
        {/* Analyzing Overlay */}
        {phraseVerificationLoading && (
          <div className="camera-placeholder analyzing-container" style={{ zIndex: 10 }}>
            <div className="camera-spinner"></div>
            <div className="camera-placeholder-title">Analyzing...</div>
            <p className="camera-placeholder-text">
              Comparing your voice and lip movements for synchronization. Please wait.
            </p>
          </div>
        )}

        {/* Badges and overlays */}
        {status === 'active' && (
          <>
            <div className="camera-badge-container">
              <div className="camera-badge live">
                <span className="camera-badge-dot"></span>
                LIVE CAMERA
              </div>
              {micStatus === 'active' && (
                <div className="camera-badge live-mic">
                  <span className="mic-badge-dot"></span>
                  LIVE MICROPHONE
                </div>
              )}
            </div>
            <div className="fps-badge">
              FPS: <span className="fps-value">{fps}</span>
            </div>
          </>
        )}

        {status === 'off' && (
          <div className="camera-badge-container">
            <div className="camera-badge off">
              OFFLINE
            </div>
          </div>
        )}

        {/* Video Element */}
        <video
          ref={videoRef}
          className="camera-video"
          autoPlay
          playsInline
          muted
          style={{ display: status === 'active' ? 'block' : 'none' }}
        />

        {/* Canvas Landmark Overlay */}
        {status === 'active' && (
          <canvas
            ref={canvasRef}
            className="camera-canvas"
          />
        )}

        {/* 2. UI State Fallbacks */}
        {status === 'off' && showSummaryCard && finalSummary && (
          <div className="camera-placeholder summary-card-container">
            <div className="summary-card-header">
              <h3>📄 Session Summary</h3>
              <div className={`summary-result-badge status-${finalResult.toLowerCase()}`}>
                Result: {finalResult}
              </div>
            </div>
            
            <div className="summary-stats-grid">
              <div className="summary-stat-item">
                <span className="summary-stat-label">Face-tracking Frames</span>
                <span className="summary-stat-value">{finalSummary.faceFrames ?? 0}</span>
              </div>
              <div className="summary-stat-item">
                <span className="summary-stat-label">Active Microphone Frames</span>
                <span className="summary-stat-value">{finalSummary.activeAudioFrames ?? 0}</span>
              </div>
              <div className="summary-stat-item">
                <span className="summary-stat-label">Lip Movement Variation</span>
                <span className="summary-stat-value">{(finalSummary.lipVariation ?? 0).toFixed(3)}</span>
              </div>
              <div className="summary-stat-item" style={{ gridColumn: 'span 2' }}>
                <span className="summary-stat-label">Local Result Basis</span>
                <span className="summary-stat-value phrase-text-summary">{finalSummary.reason}</span>
              </div>
              <div className="summary-stat-item" style={{ gridColumn: 'span 2' }}>
                <span className="summary-stat-label">Displayed Challenge</span>
                <span className="summary-stat-value phrase-text-summary">"{finalSummary.expectedPhrase}"</span>
              </div>
              <div className="summary-stat-item" style={{ gridColumn: 'span 2' }}>
                <span className="summary-stat-label">Recognized Spoken Phrase</span>
                <span className="summary-stat-value phrase-text-summary">"{finalSummary.spokenPhrase || 'No speech was recognized.'}"</span>
              </div>
              <div className="summary-stat-item" style={{ gridColumn: 'span 2' }}>
                <span className="summary-stat-label">Session Duration</span>
                <span className="summary-stat-value">{finalSummary.duration.toFixed(1)}s</span>
              </div>
              {/* Synchronization detailed stats */}
              {finalSummary.backendSyncResult && (
                <>
                  <div className="summary-stat-item">
                    <span className="summary-stat-label">Sync Score</span>
                    <span className="summary-stat-value">{finalSummary.backendSyncResult.alignedCorrelation.toFixed(3)}</span>
                  </div>
                  <div className="summary-stat-item">
                    <span className="summary-stat-label">Raw Score</span>
                    <span className="summary-stat-value">{finalSummary.backendSyncResult.rawCorrelation.toFixed(3)}</span>
                  </div>
                  <div className="summary-stat-item">
                    <span className="summary-stat-label">Time Offset</span>
                    <span className="summary-stat-value">{finalSummary.backendSyncResult.detectedTimeOffsetMs}ms</span>
                  </div>
                  <div className="summary-stat-item">
                    <span className="summary-stat-label">Duration Diff</span>
                    <span className="summary-stat-value">{finalSummary.backendSyncResult.durationDifferenceMs}ms</span>
                  </div>
                  <div className="summary-stat-item">
                    <span className="summary-stat-label">Audio Duration</span>
                    <span className="summary-stat-value">
                      {(finalSummary.backendSyncResult.audioDurationMs / 1000).toFixed(2)}s
                    </span>
                  </div>
                  <div className="summary-stat-item">
                    <span className="summary-stat-label">Lip Duration</span>
                    <span className="summary-stat-value">
                      {(finalSummary.backendSyncResult.lipDurationMs / 1000).toFixed(2)}s
                    </span>
                  </div>
                  <div className="summary-stat-item">
                    <span className="summary-stat-label">Valid Frames</span>
                    <span className="summary-stat-value">{finalSummary.backendSyncResult.validFrames}</span>
                  </div>
                  <div className="summary-stat-item">
                    <span className="summary-stat-label">Ignored Frames</span>
                    <span className="summary-stat-value">{finalSummary.backendSyncResult.ignoredFrames}</span>
                  </div>
                  <div className="summary-stat-item" style={{ gridColumn: 'span 2' }}>
                    <span className="summary-stat-label">Average Audio Energy</span>
                    <span className="summary-stat-value">{finalSummary.backendSyncResult.averageAudioEnergy.toFixed(4)}</span>
                  </div>
                  <div className="summary-stat-item" style={{ gridColumn: 'span 2' }}>
                    <span className="summary-stat-label">Challenge Phrase</span>
                    <span className="summary-stat-value phrase-text-summary">"{finalSummary.expectedPhrase}"</span>
                  </div>
                </>
              )}
            </div>
            
            <div className="challenge-controls">
              <button
                className="btn-secondary"
                onClick={() => {
                  setShowSummaryCard(false);
                  setFinalSummary(null);
                  setFinalResult('');
                }}
              >
                Close
              </button>
              <button 
                className="btn-primary summary-dismiss-btn"
                onClick={() => {
                  setShowSummaryCard(false);
                  setFinalSummary(null);
                  startCamera();
                }}
              >
                Start New Authentication
              </button>
            </div>
          </div>
        )}

        {status === 'off' && phraseVerificationLoading && (
          <div className="camera-placeholder verifying">
            <div className="camera-spinner"></div>
            <div className="camera-placeholder-title">Processing Verification</div>
            <p className="camera-placeholder-text">
              Analyzing audio patterns and transcribing speech. Please wait...
            </p>
          </div>
        )}

        {status === 'off' && !phraseVerificationLoading && (!showSummaryCard || !finalSummary) && (
          <div className="camera-placeholder">
            <div className="camera-placeholder-icon">📹</div>
            <div className="camera-placeholder-title">Camera Off</div>
            <p className="camera-placeholder-text">
              The camera feed is currently disabled. Click "Start Authentication" below to request access.
            </p>
          </div>
        )}

        {status === 'requesting' && (
          <div className="camera-placeholder">
            <div className="camera-spinner"></div>
            <div className="camera-placeholder-title">Requesting Camera Permission</div>
            <p className="camera-placeholder-text">
              Please click "Allow" on the browser pop-up prompt to activate your camera.
            </p>
          </div>
        )}

        {status === 'denied' && (
          <div className="camera-placeholder camera-error-container">
            <div className="camera-placeholder-icon">🔒</div>
            <div className="camera-placeholder-title">Permission Denied</div>
            <p className="camera-placeholder-text">{errorMsg}</p>
            <div className="camera-instructions">
              💡 Tip: Click the camera icon in your address bar to reset camera permissions, then reload.
            </div>
          </div>
        )}

        {status === 'unavailable' && (
          <div className="camera-placeholder camera-error-container">
            <div className="camera-placeholder-icon">⚠️</div>
            <div className="camera-placeholder-title">Camera Not Available</div>
            <p className="camera-placeholder-text">{errorMsg}</p>
            <div className="camera-instructions">
              💡 Tip: Make sure the camera is connected and not currently used by Zoom, Teams, or another page.
            </div>
          </div>
        )}

        {status === 'unsupported' && (
          <div className="camera-placeholder camera-error-container">
            <div className="camera-placeholder-icon">🚫</div>
            <div className="camera-placeholder-title">Browser Not Supported</div>
            <p className="camera-placeholder-text">{errorMsg}</p>
            <div className="camera-instructions">
              💡 Tip: Open this application in Google Chrome, Mozilla Firefox, or Microsoft Edge.
            </div>
          </div>
        )}
      </div>

      {/* Premium Glassmorphic Challenge Card */}
      {status === 'active' && challengePhrase && (
        <div className="challenge-card glassmorphic">
          <div className="challenge-card-header">
            <span className="challenge-icon">🔐</span>
            <div className="challenge-title-group">
              <h3 className="challenge-card-title">Random Speech Challenge</h3>
            </div>
            <span className={`challenge-status-badge status-${challengeStatus.toLowerCase()}`}>
              {challengeStatus}
            </span>
          </div>

          <div className="challenge-phrase-box">
            <div className="challenge-phrase-label">Speak the phrase clearly:</div>
            <div className={`challenge-phrase-text ${challengeStatus === 'RECORDING' ? 'recording' : ''}`}>
              "{displayedChallenge}"
            </div>
          </div>

          <div className="challenge-timer-section">
            {challengeStatus !== 'RECORDING' && challengeStatus !== 'COMPLETED' ? (
              <>
                <div className="timer-info">
                  <span className="timer-text">Phrase expires in: <strong>{challengeTimeLeft}s</strong></span>
                </div>
                <div className="timer-progress-container">
                  <div 
                    className="timer-progress-bar" 
                    style={{ width: `${(challengeTimeLeft / 30) * 100}%` }}
                  ></div>
                </div>
              </>
            ) : challengeStatus === 'RECORDING' ? (
              <div className="recording-indicator">
                <span className="recording-dot pulse"></span>
                <span>Recording in progress... Keep speaking the phrase</span>
              </div>
            ) : (
              <div className="completed-indicator">
                <span className="completed-icon">✅</span>
                <span>Recording captured locally. Synchronization is the next project phase.</span>
              </div>
            )}
          </div>

          <div className="challenge-controls">
            {challengeStatus === 'RECORDING' ? (
              <button 
                className="btn-stop-record" 
                onClick={stopRecording}
              >
                🔴 Stop Recording
              </button>
            ) : (
              <button 
                className="btn-start-record" 
                disabled={!faceDetected || (phraseVerificationStatus && phraseVerificationStatus !== '')}
                title={phraseVerificationStatus ? "Verification completed" : !faceDetected ? "Position face in camera to record" : "Start Recording"}
                onClick={startRecording}
              >
                🎙️ Start Recording
              </button>
            )}
          </div>
        </div>
      )}

      {/* Premium Glassmorphic Challenge Verification Card */}
      {false && status === 'active' && (phraseVerificationLoading || phraseVerificationStatus || verificationError) && (
        <div className="verification-card glassmorphic">
          <div className="verification-card-header">
            <span className="verification-icon">🗣️</span>
            <div className="verification-title-group">
              <h3 className="verification-card-title">Phrase Verification</h3>
              {backendProcessingTime > 0 && !phraseVerificationLoading && (
                <span className="processing-time-display">
                  Backend time: {backendProcessingTime}ms
                </span>
              )}
            </div>
            {phraseVerificationLoading ? (
              <span className="verification-status-badge status-loading">
                {verificationStage}
              </span>
            ) : (
              <span className={`verification-status-badge status-${phraseVerificationStatus.toLowerCase()}`}>
                {phraseVerificationStatus || 'UNKNOWN'}
              </span>
            )}
          </div>

          {phraseVerificationLoading ? (
            <div className="verification-loading-container">
              <div className="loading-spinner"></div>
              <p className="stage-indicator">
                {verificationStage === 'UPLOADING' && 'Uploading recording...'}
                {verificationStage === 'TRANSCRIBING' && 'Transcribing speech with Whisper...'}
                {verificationStage === 'VERIFYING' && 'Analyzing phrase similarity...'}
              </p>
            </div>
          ) : verificationError ? (
            <div className="verification-error-container">
              <span className="error-icon">⚠️</span>
              <p className="verification-error-msg">{verificationError}</p>
            </div>
          ) : (
            <>
              <div className="verification-grid">
                <div className="verification-item">
                  <span className="verification-label">Expected Phrase</span>
                  <div className="expected-phrase">"{activeChallengeRef.current}"</div>
                </div>
                <div className="verification-item">
                  <span className="verification-label">Recognized Speech</span>
                  <div className="recognized-phrase">
                    {recognizedText ? `"${recognizedText}"` : <span className="text-muted">Silence (No speech detected)</span>}
                  </div>
                </div>
                <div className="verification-item full-width">
                  <span className="verification-label">Overall Score</span>
                  <div className="similarity-container">
                    <div className="similarity-score">{phraseSimilarity.toFixed(1)}%</div>
                    <div className="similarity-bar-container">
                      <div 
                        className={`similarity-bar status-${phraseVerificationStatus.toLowerCase()}`}
                        style={{ width: `${phraseSimilarity}%` }}
                      ></div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Detailed Breakdown */}
              <div className="similarity-details">
                <div className="detail-item">
                  <span className="detail-label">Char Similarity:</span>
                  <span className="detail-value">{characterSimilarity.toFixed(1)}%</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">Word Match:</span>
                  <span className="detail-value">{wordSimilarity.toFixed(1)}%</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">Whisper Confidence:</span>
                  <span className="detail-value">{(whisperConfidence * 100).toFixed(1)}%</span>
                </div>
                {recordingDuration > 0 && (
                  <div className="detail-item">
                    <span className="detail-label">Audio Duration:</span>
                    <span className="detail-value">{(recordingDuration / 1000).toFixed(2)}s</span>
                  </div>
                )}
              </div>
            </>
          )}

          {/* Verification History Collapsible Accordion */}
          {verificationHistory.length > 0 && (
            <div className="history-panel">
              <button 
                className="history-toggle"
                onClick={() => setIsHistoryCollapsed(!isHistoryCollapsed)}
              >
                <span>📊 Verification History ({verificationHistory.length})</span>
                <span className="toggle-icon">{isHistoryCollapsed ? '▼' : '▲'}</span>
              </button>
              
              {!isHistoryCollapsed && (
                <div className="history-list">
                  {verificationHistory.map((item, idx) => (
                    <div className="history-item" key={idx}>
                      <div className="history-item-header">
                        <span className="history-time">{item.timestamp}</span>
                        <span className={`history-status status-${item.status.toLowerCase()}`}>
                          {item.status} ({item.overallScore.toFixed(1)}%)
                        </span>
                      </div>
                      <div className="history-item-body">
                        <div><small>Expected:</small> <span className="history-phrase">"{item.expectedPhrase}"</span></div>
                        <div><small>Recognized:</small> <span className="history-phrase">"{item.recognizedText || 'Silence'}"</span></div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Real-time Lip Movement Metrics Dashboard */}
      {status === 'active' && (
        <div className="metrics-dashboard">
          <div className="metrics-header">
            <h3>👄 Lip Movement Metrics</h3>
            <span className={`metrics-status-badge ${faceDetected ? 'active' : 'inactive'}`}>
              {faceDetected ? 'Tracking Active' : 'Waiting for Face...'}
            </span>
          </div>

          <div className="metrics-grid">
            <div className="metric-card">
              <span className="metric-label">Vertical Distance</span>
              <div className="metric-value-container">
                <span className="metric-value">{metrics.verticalDistance.toFixed(4)}</span>
                <span className="metric-unit">norm</span>
              </div>
              <div className="metric-bar-container">
                <div 
                  className="metric-bar vertical-bar" 
                  style={{ width: `${Math.min(metrics.verticalDistance * 800, 100)}%` }}
                ></div>
              </div>
            </div>

            <div className="metric-card">
              <span className="metric-label">Horizontal Distance</span>
              <div className="metric-value-container">
                <span className="metric-value">{metrics.horizontalDistance.toFixed(4)}</span>
                <span className="metric-unit">norm</span>
              </div>
              <div className="metric-bar-container">
                <div 
                  className="metric-bar horizontal-bar" 
                  style={{ width: `${Math.min(metrics.horizontalDistance * 600, 100)}%` }}
                ></div>
              </div>
            </div>

            <div className="metric-card highlighted-card">
              <span className="metric-label">Lip Opening Ratio</span>
              <div className="metric-value-container">
                <span className="metric-value highlighted-value">{metrics.lipOpeningRatio.toFixed(3)}</span>
                <span className="metric-unit">V/H</span>
              </div>
              <div className="metric-bar-container ratio-bar-container">
                <div 
                  className="metric-bar ratio-bar" 
                  style={{ width: `${Math.min(metrics.lipOpeningRatio * 150, 100)}%` }}
                ></div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Real-time Audio Capture Metrics Dashboard */}
      {status === 'active' && (
        <div className="metrics-dashboard audio-dashboard">
          <div className="metrics-header">
            <h3>🎙️ Audio Capture Metrics</h3>
            <span className={`metrics-status-badge mic-${micStatus}`}>
              {micStatus === 'active' && 'Microphone Active'}
              {micStatus === 'requesting' && 'Requesting Mic...'}
              {micStatus === 'denied' && 'Mic Permission Denied'}
              {micStatus === 'unavailable' && 'Mic Device Not Found'}
              {micStatus === 'off' && 'Microphone Off'}
            </span>
          </div>

          <div className="metrics-grid mic-grid">
            <div className="metric-card">
              <span className="metric-label">Audio Energy</span>
              <div className="metric-value-container">
                <span className="metric-value">{audioEnergy.toFixed(4)}</span>
                <span className="metric-unit">RMS</span>
              </div>
            </div>

            <div className="metric-card">
              <span className="metric-label">Audio Buffer Size</span>
              <div className="metric-value-container">
                <span className="metric-value">{audioBufferRef.current.length}</span>
                <span className="metric-unit">frames</span>
              </div>
            </div>

            <div className="metric-card highlighted-card mic-energy-card">
              <span className="metric-label">Live Volume Meter</span>
              <div className="metric-value-container">
                <span className="metric-value highlighted-value mic-energy-value">{Math.round(audioEnergy * 100)}%</span>
              </div>
              <div className="metric-bar-container mic-energy-bar-container">
                <div 
                  className="metric-bar mic-energy-bar" 
                  style={{ width: `${audioEnergy * 100}%` }}
                ></div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Day 16: Lip-Voice Synchronization Engine Dashboard */}
      {false && status === 'active' && (
        <div className="metrics-dashboard sync-dashboard">
          <div className="metrics-header">
            <h3>🔗 Lip-Voice Synchronization</h3>
            <span className={`sync-status-badge status-${syncStatus.toLowerCase()}`}>
              {syncStatus === 'PENDING' 
                ? `PENDING (${syncSamplesCount}/${MIN_SYNC_SAMPLES})` 
                : syncStatus}
            </span>
          </div>

          <div className="sync-grid">
            <div className="metric-card sync-score-card">
              <span className="metric-label">Smoothed Correlation</span>
              <div className="metric-value-container">
                <span className="metric-value sync-score-value">{syncScore.toFixed(3)}</span>
                <span className="metric-unit">Pearson r</span>
              </div>
              <div className="threshold-indicator">
                Threshold: {syncThreshold.toFixed(2)}
              </div>
            </div>

            <div className="metric-card sync-raw-score-card">
              <span className="metric-label">Raw Correlation</span>
              <div className="metric-value-container">
                <span className="metric-value">{rawSyncScore.toFixed(3)}</span>
                <span className="metric-unit">Pearson r</span>
              </div>
            </div>

            <div className="metric-card sync-confidence-card">
              <span className="metric-label">Synchronization Confidence</span>
              <div className="metric-value-container">
                <span className="metric-value highlighted-value sync-confidence-value">{syncConfidence.toFixed(1)}%</span>
              </div>
              <div className="metric-bar-container sync-confidence-bar-container">
                <div 
                  className={`metric-bar sync-confidence-bar status-${syncStatus.toLowerCase()}`}
                  style={{ width: `${syncConfidence}%` }}
                ></div>
              </div>
            </div>
          </div>

          {/* Threshold Configuration Slider */}
          <div className="sync-settings">
            <div className="settings-header">
              <label htmlFor="threshold-slider" className="settings-label">
                Authentication Threshold: <span className="threshold-val">{syncThreshold.toFixed(2)}</span>
              </label>
            </div>
            <input 
              id="threshold-slider"
              type="range"
              min="0.10"
              max="0.95"
              step="0.05"
              value={syncThreshold}
              onChange={(e) => setSyncThreshold(parseFloat(e.target.value))}
              className="threshold-slider"
            />
          </div>

          {/* Project Diagnostics Panel */}
          <div className="sync-diagnostics">
            <div className="diagnostics-title">📊 Synchronization Diagnostics</div>
            <div className="diagnostics-grid">
              <div className="diagnostic-item">
                <span className="diag-label">Lip Samples:</span>
                <span className="diag-value">{diagnostics.lipSamples}</span>
              </div>
              <div className="diagnostic-item">
                <span className="diag-label">Audio Samples:</span>
                <span className="diag-value">{diagnostics.audioSamples}</span>
              </div>
              <div className="diagnostic-item">
                <span className="diag-label">Aligned Samples:</span>
                <span className="diag-value">{diagnostics.alignedSamples}</span>
              </div>
              <div className="diagnostic-item">
                <span className="diag-label">Sync Window:</span>
                <span className="diag-value">{diagnostics.syncWindow.toFixed(2)}s</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 3. Action Controls */}
      <div className="action-section" style={{ width: '100%' }}>
        {status === 'active' || status === 'requesting' || ['denied', 'unavailable', 'unsupported'].includes(status) ? (
          <button className="btn-danger" onClick={stopCamera}>
            Stop Camera
          </button>
        ) : (
          <button className="btn-primary" onClick={startCamera}>
            Start Authentication
          </button>
        )}
        
        {/* Informative Notes */}
        <div className="info-notes">
          <div className="info-item">
            <span className="info-icon">📹</span>
            <span className="info-text">Requires camera permission for mouth tracking</span>
          </div>
          <div className="info-item">
            <span className="info-icon">🎙️</span>
            <span className="info-text">Requires microphone access for voice sync check</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default CameraPreview;
