import React, { useState, useEffect, useRef } from 'react';
import { generateSessionId } from '../utils/challengePhrases';
import {
  fetchNewChallengePhrase,
  uploadAudioForSync,
  fetchCalibrationStatus,
  startNewCalibration,
  uploadCalibrationSample
} from '../utils/api';

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
const MAX_RECORDING_DURATION_MS = 60000; // Safety stop if the user forgets to stop
const MIN_LOCAL_AUDIO_ENERGY = 0.08;
const MIN_LOCAL_FACE_FRAMES = 20;
const MIN_LOCAL_AUDIO_FRAMES = 30;
const MIN_LOCAL_LIP_VARIATION = 0.02;

// Backend synchronization reference thresholds (matching backend/config.py)
const BACKEND_LIVE_SYNC_THRESHOLD = 0.45;
const BACKEND_MAX_SYNC_OFFSET_MS = 500;
const DEFAULT_CALIBRATION_SAMPLES = 3;

// Supported challenge-response languages
const LANGUAGE_OPTIONS = [
  { code: 'en', label: 'English', englishName: 'English', speechLang: 'en-US' },
  { code: 'hi', label: 'हिन्दी', englishName: 'Hindi', speechLang: 'hi-IN' },
  { code: 'mr', label: 'मराठी', englishName: 'Marathi', speechLang: 'mr-IN' }
];

const getLanguageMeta = (code) =>
  LANGUAGE_OPTIONS.find((item) => item.code === code) || LANGUAGE_OPTIONS[0];

/**
 * Smooths raw lip opening ratios with a 5-point centered moving average and normalizes
 * both lip and audio signals to [0, 1] for live real-time waveform visualization.
 */
const buildLiveNormalizedSeries = (rawPoints, isRecordingWindow = false) => {
  if (!rawPoints || rawPoints.length === 0) return [];
  const t0 = isRecordingWindow ? 0 : rawPoints[0].tMs;
  const n = rawPoints.length;
  const lipVals = rawPoints.map(p => p.lipRaw);
  const audioVals = rawPoints.map(p => p.audioRaw);

  // 5-point centered moving average on lip movement signal (matching SyncService.smooth_signal)
  const smoothedLip = lipVals.map((_, idx) => {
    const start = Math.max(0, idx - 2);
    const end = Math.min(n, idx + 3);
    let sum = 0;
    for (let j = start; j < end; j++) sum += lipVals[j];
    return sum / (end - start);
  });

  const minLip = Math.min(...smoothedLip);
  const maxLip = Math.max(...smoothedLip);
  const lipRange = Math.max(maxLip - minLip, 0.04);

  const minAud = Math.min(...audioVals);
  const maxAud = Math.max(...audioVals);
  const audRange = Math.max(maxAud - minAud, 0.08);

  return rawPoints.map((pt, idx) => ({
    tSec: Math.max(0, (pt.tMs - t0) / 1000),
    lipNorm: Math.min(1, Math.max(0, (smoothedLip[idx] - minLip) / lipRange)),
    audioNorm: Math.min(1, Math.max(0, (audioVals[idx] - minAud) / audRange))
  }));
};

/**
 * Converts backend signalSeries ({ timestampsMs, normalizedLip, normalizedAudio })
 * into graph points [{ tSec, lipNorm, audioNorm }].
 */
const buildBackendSignalSeries = (signalSeries) => {
  if (
    !signalSeries ||
    !Array.isArray(signalSeries.timestampsMs) ||
    !Array.isArray(signalSeries.normalizedLip) ||
    !Array.isArray(signalSeries.normalizedAudio)
  ) {
    return [];
  }
  const len = Math.min(
    signalSeries.timestampsMs.length,
    signalSeries.normalizedLip.length,
    signalSeries.normalizedAudio.length
  );
  const points = [];
  for (let i = 0; i < len; i++) {
    points.push({
      tSec: Math.max(0, Number(signalSeries.timestampsMs[i]) / 1000),
      lipNorm: Math.min(1, Math.max(0, Number(signalSeries.normalizedLip[i]) || 0)),
      audioNorm: Math.min(1, Math.max(0, Number(signalSeries.normalizedAudio[i]) || 0))
    });
  }
  return points;
};
// Configurable constants for MediaPipe Face Mesh custom adjustments
const SHOW_LIP_LANDMARKS = true;
const LIP_OUTER_COLOR = "#0D9488"; // Secondary Accent (Teal)
const LIP_INNER_COLOR = "#2563EB"; // Primary Brand (Blue)
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
  const activeLanguageRef = useRef('en');
  const abortControllerRef = useRef(null);

  // Multilingual Challenge-Response state & ref ('en' | 'hi' | 'mr')
  const [selectedLanguage, setSelectedLanguage] = useState(() => {
    try {
      const savedLang = window.localStorage.getItem('liveness_language');
      if (savedLang && ['en', 'hi', 'mr'].includes(savedLang)) {
        return savedLang;
      }
      return 'en';
    } catch {
      return 'en';
    }
  });
  const selectedLanguageRef = useRef(selectedLanguage);
  useEffect(() => {
    selectedLanguageRef.current = selectedLanguage;
    try {
      window.localStorage.setItem('liveness_language', selectedLanguage);
    } catch {
      // Ignore localStorage write errors
    }
  }, [selectedLanguage]);

  const isRecordingRef = useRef(false);
  const recordingLipMovementRef = useRef([]);
  const recordingLipTimestampsRef = useRef([]);
  const localEvidenceRef = useRef({ faceFrames: 0, activeAudioFrames: 0 });
  const [backendSyncResult, setBackendSyncResult] = useState(null);

  // Real-time lip & audio signal series refs and state for live graph visualization
  const latestAudioEnergyRef = useRef(0);
  const previewSignalSeriesRef = useRef([]);
  const recordingSignalSeriesRef = useRef([]);
  const lastGraphUpdateRef = useRef(0);
  const [liveGraphPoints, setLiveGraphPoints] = useState([]);

  // Per-User Adaptive Synchronization Threshold Calibration state & refs
  const [userId, setUserId] = useState(() => {
    try {
      const saved = window.localStorage.getItem('liveness_user_id');
      if (saved && saved.trim()) return saved.trim();
      const generated = 'user-default';
      window.localStorage.setItem('liveness_user_id', generated);
      return generated;
    } catch {
      return 'user-default';
    }
  });
  const userIdRef = useRef(userId);
  useEffect(() => {
    userIdRef.current = userId;
    try {
      window.localStorage.setItem('liveness_user_id', userId);
    } catch {
      // Ignore localStorage write errors
    }
  }, [userId]);

  const [isCalibrationMode, setIsCalibrationMode] = useState(false);
  const isCalibrationModeRef = useRef(false);
  useEffect(() => {
    isCalibrationModeRef.current = isCalibrationMode;
  }, [isCalibrationMode]);

  const [calibrationProfile, setCalibrationProfile] = useState({
    userId: 'user-default',
    calibrationStatus: 'NOT_STARTED',
    isCalibrated: false,
    requiredSamples: DEFAULT_CALIBRATION_SAMPLES,
    validSamplesCount: 0,
    totalAttempts: 0,
    validScores: [],
    meanScore: null,
    medianScore: null,
    stdDeviation: null,
    adaptiveThreshold: null,
    effectiveThreshold: BACKEND_LIVE_SYNC_THRESHOLD,
    globalThreshold: BACKEND_LIVE_SYNC_THRESHOLD
  });
  const [lastCalibrationOutcome, setLastCalibrationOutcome] = useState(null);

  const refreshCalibrationStatus = async (targetUser = userIdRef.current) => {
    if (!targetUser) return;
    try {
      const data = await fetchCalibrationStatus(targetUser);
      if (data && data.calibrationProfile) {
        setCalibrationProfile(data.calibrationProfile);
      }
    } catch (err) {
      console.warn('Could not fetch calibration status:', err);
    }
  };

  useEffect(() => {
    refreshCalibrationStatus(userId);
  }, [userId]);

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
    recognition.lang = getLanguageMeta(selectedLanguageRef.current).speechLang;
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
   * Day 17: Helper to randomly choose a challenge phrase in the selected language and configure the authentication session.
   */
  const generateChallengePhrase = async (overrideLanguage = null) => {
    if (challengeStatus === "RECORDING" || isRecording) {
      console.warn("Cannot generate new challenge phrase during active recording.");
      return;
    }

    const langToUse = overrideLanguage || selectedLanguageRef.current || 'en';

    // 1. Ensure unique Session ID exists
    ensureSessionId();
    setChallengeStatus("WAITING");
    try {
      const serverChallenge = await fetchNewChallengePhrase(challengeSessionIdRef.current, langToUse);
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

  const handleLanguageChange = async (newLang) => {
    if (isRecording || phraseVerificationLoading) return;
    setSelectedLanguage(newLang);
    selectedLanguageRef.current = newLang;
    if (status === 'active' && micStatus === 'active') {
      await generateChallengePhrase(newLang);
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
    activeLanguageRef.current = selectedLanguageRef.current || 'en';
    
    resetVerificationStates();
    setBackendSyncResult(null);
    recordingLipMovementRef.current = [];
    recordingLipTimestampsRef.current = [];
    recordingSignalSeriesRef.current = [];
    setLiveGraphPoints([]);
    localEvidenceRef.current = { faceFrames: 0, activeAudioFrames: 0 };
    
    const TARGET_RECORDING_SECONDS = 5;
    const chunks = [];
    try {
      const mediaRecorder = new MediaRecorder(audioStreamRef.current, { mimeType: 'audio/webm' });
      mediaRecorderRef.current = mediaRecorder;
      
      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          chunks.push(e.data);
        }
      };
      
      mediaRecorder.onerror = async (event) => {
        console.error("MediaRecorder error:", event.error);
        setVerificationError("Audio recording error occurred.");
        setVerificationStage('ERROR');
        isRecordingRef.current = false;
        setIsRecording(false);
        await stopStreamsImmediately();
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
        setRecordingTime(prev => {
          const nextTime = prev + 1;
          if (nextTime >= TARGET_RECORDING_SECONDS) {
            setTimeout(() => stopRecording(), 0);
          }
          return nextTime;
        });
      }, 1000);
      
      mediaRecorder.start();
      startLocalSpeechRecognition();
      console.log("MediaRecorder started");

      // Auto-stop at 5-second target recording duration
      if (recordingDurationTimeoutRef.current) {
        clearTimeout(recordingDurationTimeoutRef.current);
      }
      recordingDurationTimeoutRef.current = setTimeout(() => {
        stopRecording();
      }, TARGET_RECORDING_SECONDS * 1000);

    } catch (err) {
      console.error("Failed to start MediaRecorder:", err);
      setVerificationError("Failed to start audio recording: " + err.message);
      setVerificationStage('ERROR');
      isRecordingRef.current = false;
      setIsRecording(false);
      stopStreamsImmediately();
    }
  };

  const stopRecording = async () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop();
      } catch (err) {
        console.warn("Error stopping mediaRecorder:", err);
      }
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

    // Release camera stream immediately when recording ends
    await stopStreamsImmediately();
  };

  const stopStreamsImmediately = async () => {
    // 1. Immediately flag processing loops to abort
    setIsVerifying(false);
    isVerifyingRef.current = false;
    statusRef.current = 'off';
    micStatusRef.current = 'off';
    isRecordingRef.current = false;
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

    // 3. Stop and disable all webcam tracks
    if (streamRef.current) {
      try {
        streamRef.current.getTracks().forEach(track => {
          track.enabled = false;
          track.stop();
          console.log(`Stopped video track: ${track.label}`);
        });
      } catch (err) {
        console.warn('Error stopping video stream tracks:', err);
      }
      streamRef.current = null;
    }

    // 4. Stop and disable any tracks attached directly to the HTML video element
    if (videoRef.current) {
      try {
        if (videoRef.current.srcObject) {
          const vStream = videoRef.current.srcObject;
          if (typeof vStream.getTracks === 'function') {
            vStream.getTracks().forEach(track => {
              track.enabled = false;
              track.stop();
            });
          }
        }
      } catch (err) {
        console.warn('Error clearing videoRef srcObject tracks:', err);
      }
      videoRef.current.srcObject = null;
    }

    // 5. Stop and disable all microphone tracks
    if (audioStreamRef.current) {
      try {
        audioStreamRef.current.getTracks().forEach(track => {
          track.enabled = false;
          track.stop();
          console.log(`Stopped audio track: ${track.label}`);
        });
      } catch (err) {
        console.warn('Error stopping audio tracks:', err);
      }
      audioStreamRef.current = null;
    }

    // 6. Close AudioContext
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

    // 7. Reset audio analyzer and energy
    audioAnalyserRef.current = null;
    latestAudioEnergyRef.current = 0;
    previewSignalSeriesRef.current = [];
    setAudioEnergy(0);
    audioBufferRef.current = [];
    setMicStatus('off');

    // 8. Clear Canvas overlay
    if (canvasRef.current) {
      const canvas = canvasRef.current;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
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


  const handleStartNewCalibration = async () => {
    try {
      setLastCalibrationOutcome(null);
      const res = await startNewCalibration(
        userIdRef.current,
        challengeSessionIdRef.current || '',
        DEFAULT_CALIBRATION_SAMPLES
      );
      if (res && res.calibrationProfile) {
        setCalibrationProfile(res.calibrationProfile);
      }
      setIsCalibrationMode(true);
      isCalibrationModeRef.current = true;
      if (status !== 'active') {
        await startCamera();
      } else {
        await generateChallengePhrase();
      }
    } catch (err) {
      console.error('Failed to start calibration:', err);
      setVerificationError(err.message || 'Failed to start calibration.');
    }
  };

  const handleContinueCalibration = async () => {
    setLastCalibrationOutcome(null);
    setIsCalibrationMode(true);
    isCalibrationModeRef.current = true;
    if (status !== 'active') {
      await startCamera();
    } else {
      await generateChallengePhrase();
    }
  };

  const uploadAudioAndVerify = async (audioBlob) => {
    // Immediately release camera and microphone streams!
    await stopStreamsImmediately();

    const boundChallenge = activeChallengeRef.current;
    const boundSessionId = activeSessionIdRef.current;
    const boundLanguage = activeLanguageRef.current || selectedLanguageRef.current || 'en';
    const calibrating = isCalibrationModeRef.current;
    
    if (!boundSessionId) {
      console.warn("Missing bound session ID for verification upload");
      return;
    }
    
    setPhraseVerificationLoading(true);
    setVerificationStage('VERIFYING');
    setVerificationError('');
    
    try {
      const response = calibrating
        ? await uploadCalibrationSample(
            audioBlob,
            recordingLipMovementRef.current,
            recordingLipTimestampsRef.current,
            boundSessionId,
            userIdRef.current,
            boundChallenge,
            abortControllerRef.current ? abortControllerRef.current.signal : null,
            calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES,
            boundLanguage
          )
        : await uploadAudioForSync(
            audioBlob,
            recordingLipMovementRef.current,
            recordingLipTimestampsRef.current,
            boundSessionId,
            abortControllerRef.current ? abortControllerRef.current.signal : null,
            boundChallenge,
            userIdRef.current,
            boundLanguage
          );
      
      if (response.sessionId !== activeSessionIdRef.current) {
        console.warn("Discarding response for stale session ID:", response.sessionId);
        return;
      }
      
      setBackendSyncResult(response);

      if (response.calibrationProfile) {
        setCalibrationProfile(response.calibrationProfile);
      }
      if (calibrating) {
        setLastCalibrationOutcome({
          sampleAccepted: Boolean(response.sampleAccepted),
          sampleRejectionReason: response.sampleRejectionReason || null,
          alignedCorrelation: Number(response.alignedCorrelation || 0),
          currentSampleNumber: Number(response.currentSampleNumber || 0),
          requiredSamples: Number(response.requiredSamples || DEFAULT_CALIBRATION_SAMPLES)
        });
        if (response.isCalibrated || response.calibrationStatus === 'COMPLETE') {
          setIsCalibrationMode(false);
          isCalibrationModeRef.current = false;
        }
      }
      
      const whisper = response.whisperVerification || {};
      const resolvedLang = response.language || whisper.language || boundLanguage;
      const recognized = response.transcribedPhrase || whisper.recognizedText || '';
      const charSim = whisper.characterSimilarityPercentage || 0;
      const wordSim = whisper.wordMatchPercentage || 0;
      const confidence = whisper.whisperConfidence || 0;
      const phraseSim = whisper.overallScore || 0;
      const verifStatus = whisper.verificationStatus || (response.isChallengeMatch ? 'PASS' : 'FAIL');
      const isLive = calibrating ? Boolean(response.sampleAccepted) : (response.livenessResult === 'LIVE');
      const finalRes = isLive ? 'LIVE' : 'SPOOF';
      const rejectionReason = response.rejectionReason || (!isLive ? (whisper.verificationReason || 'Liveness criteria not satisfied.') : null);

      setSyncScore(response.alignedCorrelation || 0);
      setRawSyncScore(response.rawCorrelation || 0);
      setSyncStatus(response.syncStatus || 'SPOOF');
      setSyncConfidence(Math.max(0, Math.min((response.alignedCorrelation || 0) * 100, 100)));
      setSyncSamplesCount(response.validFrames || 0);
      
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
        language: resolvedLang,
        overallScore: phraseSim,
        characterSimilarity: charSim,
        wordSimilarity: wordSim,
        whisperConfidence: confidence,
        status: verifStatus,
        recognizedText: recognized,
        expectedPhrase: boundChallenge
      };
      setVerificationHistory(prev => [newHistoryItem, ...prev].slice(0, 5));

      const backendGraphPoints = buildBackendSignalSeries(response.signalSeries);
      const fallbackGraphPoints = buildLiveNormalizedSeries(recordingSignalSeriesRef.current, true);
      const signalGraphPoints = backendGraphPoints.length > 0 ? backendGraphPoints : fallbackGraphPoints;

      // Construct final summary statistics for summary card display
      setFinalSummary({
        language: resolvedLang,
        averageScore: response.alignedCorrelation || 0,
        maxScore: response.alignedCorrelation || 0,
        minScore: response.alignedCorrelation || 0,
        liveCount: isLive ? 1 : 0,
        spoofCount: isLive ? 0 : 1,
        totalEvaluations: 1,
        duration: (response.audioDurationMs || 0) / 1000,
        phraseVerificationStatus: verifStatus,
        verificationReason: rejectionReason || (isLive ? 'Lip-voice synchronization and speech verification passed.' : 'Verification failed.'),
        rejectionReason: rejectionReason,
        overallScore: phraseSim,
        characterSimilarity: charSim,
        wordSimilarity: wordSim,
        whisperConfidence: confidence,
        processingTime: response.detectedTimeOffsetMs || 0,
        recordingDuration: response.audioDurationMs || 0,
        recognizedText: recognized,
        transcribedPhrase: recognized,
        expectedPhrase: response.challengePhrase || boundChallenge,
        backendSyncResult: response,
        signalGraphPoints: signalGraphPoints,
        isBackendSignalSeries: backendGraphPoints.length > 0,
        wasCalibrationAttempt: calibrating
      });
      
      setFinalResult(finalRes);
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
        errMsg = err.message.includes("Insufficient samples")
          ? `${err.message} Face was not detected for enough frames during recording.`
          : err.message;
      }
      setVerificationError(errMsg);
      setSyncStatus('SPOOF');
      if (calibrating) {
        setLastCalibrationOutcome({
          sampleAccepted: false,
          sampleRejectionReason: errMsg,
          alignedCorrelation: 0,
          currentSampleNumber: calibrationProfile?.validSamplesCount || 0,
          requiredSamples: calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES
        });
      }

      const fallbackGraphPoints = buildLiveNormalizedSeries(recordingSignalSeriesRef.current, true);

      // Construct final summary for error/spoof case
      setFinalSummary({
        language: boundLanguage,
        averageScore: 0,
        maxScore: 0,
        minScore: 0,
        liveCount: 0,
        spoofCount: 1,
        totalEvaluations: 1,
        duration: 0,
        phraseVerificationStatus: 'FAIL',
        verificationReason: errMsg,
        rejectionReason: errMsg,
        overallScore: 0,
        characterSimilarity: 0,
        wordSimilarity: 0,
        whisperConfidence: 0.0,
        processingTime: 0,
        recordingDuration: 0,
        recognizedText: '',
        transcribedPhrase: '',
        expectedPhrase: boundChallenge,
        backendSyncResult: null,
        signalGraphPoints: fallbackGraphPoints,
        isBackendSignalSeries: false,
        wasCalibrationAttempt: calibrating
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
      let currentFrameRatio = 0;
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
          currentFrameRatio = ratio;

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

      // Update live time-series signal buffers for real-time lip vs. audio visualization
      const currentAudioVal = latestAudioEnergyRef.current || 0;
      if (isRecordingRef.current) {
        const recElapsedMs = Math.max(0, now - recordingStartTimeRef.current);
        recordingSignalSeriesRef.current.push({
          tMs: recElapsedMs,
          lipRaw: currentFrameRatio,
          audioRaw: currentAudioVal
        });
      } else {
        previewSignalSeriesRef.current.push({
          tMs: now,
          lipRaw: currentFrameRatio,
          audioRaw: currentAudioVal
        });
        previewSignalSeriesRef.current = previewSignalSeriesRef.current.filter(
          pt => now - pt.tMs <= 5000
        );
      }

      if (now - lastGraphUpdateRef.current >= 80) {
        lastGraphUpdateRef.current = now;
        if (isRecordingRef.current) {
          setLiveGraphPoints(buildLiveNormalizedSeries(recordingSignalSeriesRef.current, true));
        } else {
          setLiveGraphPoints(buildLiveNormalizedSeries(previewSignalSeriesRef.current, false));
        }
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
      latestAudioEnergyRef.current = normalizedEnergy;
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
        language: backendSyncResult?.language || selectedLanguageRef.current || 'en',
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

    // Ensure any previous session streams/tracks are fully released before starting a new authentication
    if (streamRef.current || audioStreamRef.current) {
      await stopStreamsImmediately();
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
      streamRef.current.getTracks().forEach(t => {
        t.enabled = false;
        t.stop();
      });
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach(t => {
        t.enabled = false;
        t.stop();
      });
      audioStreamRef.current = null;
    }

    setIsVerifying(false);
    isVerifyingRef.current = false;

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

  /**
   * Renders a responsive SVG time-series graph comparing normalized Lip Movement (0-1)
   * and normalized Audio Energy (0-1) over time (seconds).
   */
  const renderSignalWaveformGraph = (points = [], maxDurationSec = 5.0, badgeLabel = '') => {
    const svgW = 500;
    const svgH = 148;
    const padLeft = 30;
    const padRight = 14;
    const padTop = 14;
    const padBottom = 24;
    const plotW = svgW - padLeft - padRight;
    const plotH = svgH - padTop - padBottom;

    const maxPointTime = points.length > 0 ? Math.max(...points.map(p => p.tSec)) : 0;
    const effectiveMaxSec = Math.max(maxDurationSec, maxPointTime, 1.0);

    const toX = (tSec) =>
      padLeft + Math.min(1, Math.max(0, tSec / effectiveMaxSec)) * plotW;
    const toY = (normVal) =>
      padTop + (1 - Math.min(1, Math.max(0, normVal))) * plotH;

    const hasLine = points.length >= 2;
    const lipPolyline = hasLine
      ? points.map(p => `${toX(p.tSec).toFixed(1)},${toY(p.lipNorm).toFixed(1)}`).join(' ')
      : '';
    const audioPolyline = hasLine
      ? points.map(p => `${toX(p.tSec).toFixed(1)},${toY(p.audioNorm).toFixed(1)}`).join(' ')
      : '';

    const baselineY = (padTop + plotH).toFixed(1);
    const firstX = hasLine ? toX(points[0].tSec).toFixed(1) : padLeft;
    const lastX = hasLine ? toX(points[points.length - 1].tSec).toFixed(1) : padLeft;

    const lipAreaPoints = hasLine
      ? `${firstX},${baselineY} ${lipPolyline} ${lastX},${baselineY}`
      : '';
    const audioAreaPoints = hasLine
      ? `${firstX},${baselineY} ${audioPolyline} ${lastX},${baselineY}`
      : '';

    const lastPt = hasLine ? points[points.length - 1] : null;
    const timeTicks = [0, 1, 2, 3, 4, 5].map(i =>
      Number(((i / 5) * effectiveMaxSec).toFixed(1))
    );

    return (
      <div className="sync-waveform-card">
        <div className="sync-waveform-header">
          <div className="sync-waveform-title-wrap">
            <span className="sync-waveform-title">📈 Lip Movement vs. Audio Signal (Normalized)</span>
            {badgeLabel && <span className="sync-waveform-mode-badge">{badgeLabel}</span>}
          </div>
          <div className="sync-waveform-legend">
            <span className="legend-item">
              <span className="legend-swatch lip-swatch"></span>
              Lip Signal
            </span>
            <span className="legend-item">
              <span className="legend-swatch audio-swatch"></span>
              Audio Signal
            </span>
          </div>
        </div>

        <div className="sync-waveform-svg-wrap">
          <svg
            viewBox={`0 0 ${svgW} ${svgH}`}
            className="sync-waveform-svg"
            preserveAspectRatio="none"
          >
            <defs>
              <linearGradient id="lipSignalGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#0D9488" stopOpacity="0.22" />
                <stop offset="100%" stopColor="#0D9488" stopOpacity="0.0" />
              </linearGradient>
              <linearGradient id="audioSignalGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#2563EB" stopOpacity="0.18" />
                <stop offset="100%" stopColor="#2563EB" stopOpacity="0.0" />
              </linearGradient>
            </defs>

            {/* Horizontal amplitude grid lines (1.0, 0.5, 0.0) */}
            {[1.0, 0.5, 0.0].map((val) => {
              const y = toY(val);
              return (
                <g key={`y-${val}`}>
                  <line
                    x1={padLeft}
                    y1={y}
                    x2={svgW - padRight}
                    y2={y}
                    stroke="#E2E8F0"
                    strokeDasharray={val === 0.5 ? '3 3' : undefined}
                    strokeWidth="1"
                  />
                  <text
                    x={padLeft - 6}
                    y={y + 3}
                    textAnchor="end"
                    fill="#64748B"
                    fontSize="9"
                    fontWeight="500"
                  >
                    {val.toFixed(1)}
                  </text>
                </g>
              );
            })}

            {/* Vertical time grid lines (0s to 5s) */}
            {timeTicks.map((tVal, idx) => {
              const x = padLeft + (idx / 5) * plotW;
              return (
                <g key={`x-${idx}`}>
                  <line
                    x1={x}
                    y1={padTop}
                    x2={x}
                    y2={padTop + plotH}
                    stroke="#F1F5F9"
                    strokeWidth="1"
                  />
                  <text
                    x={x}
                    y={svgH - 6}
                    textAnchor="middle"
                    fill="#64748B"
                    fontSize="9"
                    fontWeight="500"
                  >
                    {tVal}s
                  </text>
                </g>
              );
            })}

            {hasLine ? (
              <>
                <polygon points={audioAreaPoints} fill="url(#audioSignalGrad)" />
                <polygon points={lipAreaPoints} fill="url(#lipSignalGrad)" />
                <polyline
                  fill="none"
                  stroke="#2563EB"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  points={audioPolyline}
                />
                <polyline
                  fill="none"
                  stroke="#0D9488"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  points={lipPolyline}
                />
                {lastPt && (
                  <>
                    <circle
                      cx={toX(lastPt.tSec)}
                      cy={toY(lastPt.audioNorm)}
                      r="3"
                      fill="#2563EB"
                    />
                    <circle
                      cx={toX(lastPt.tSec)}
                      cy={toY(lastPt.lipNorm)}
                      r="3.2"
                      fill="#0D9488"
                    />
                  </>
                )}
              </>
            ) : (
              <text
                x={padLeft + plotW / 2}
                y={padTop + plotH / 2 + 3}
                textAnchor="middle"
                fill="#64748B"
                fontSize="10.5"
              >
                Waiting for synchronized lip and audio signal frames...
              </text>
            )}
          </svg>
        </div>
      </div>
    );
  };

  return (
    <div className="camera-box-container">
      {/* 0. Multilingual Challenge-Response Selector Bar */}
      <div className="language-selector-bar">
        <div className="language-selector-label-group">
          <span className="language-selector-icon">🌐</span>
          <span className="language-selector-title">Challenge Language:</span>
          <span className="language-selector-active-sub">
            {getLanguageMeta(selectedLanguage).englishName} ({getLanguageMeta(selectedLanguage).label})
          </span>
        </div>
        <div className="language-selector-options" role="radiogroup" aria-label="Select challenge language">
          {LANGUAGE_OPTIONS.map((lang) => {
            const isSelected = selectedLanguage === lang.code;
            return (
              <button
                key={lang.code}
                type="button"
                role="radio"
                aria-checked={isSelected}
                disabled={isRecording || phraseVerificationLoading}
                className={`language-option-btn ${isSelected ? 'active' : ''}`}
                onClick={() => handleLanguageChange(lang.code)}
              >
                <span className="language-native-label">{lang.label}</span>
                {lang.code !== 'en' && (
                  <span className="language-en-sub">{lang.englishName}</span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Main Two-Column Layout Grid */}
      <div className="livescan-main-grid">
        {/* Left Column: Camera Preview, Controls & Real-Time Sensors */}
        <div className="col-camera">
          {/* 1. Camera Card */}
          <div className="camera-card">
            {/* Camera Card Top Header: Status Badges & Hardware Telemetry */}
            <div className="camera-card-header">
              <div className="camera-card-header-left">
                {status === 'active' ? (
                  <div className={`detection-status-bar ${faceDetected ? 'detected' : 'not-detected'}`}>
                    <span className="detection-status-dot"></span>
                    <span>{faceDetected ? 'Face Detected' : 'No Face Detected'}</span>
                  </div>
                ) : (
                  <div className="detection-status-bar offline">
                    <span className="detection-status-dot"></span>
                    <span>Camera Standby</span>
                  </div>
                )}
              </div>

              <div className="camera-card-header-right">
                {status === 'active' && (
                  <>
                    <div className="camera-badge-container-inline">
                      <span className="camera-badge live">
                        <span className="camera-badge-dot"></span>
                        LIVE CAM
                      </span>
                      {micStatus === 'active' && (
                        <span className="camera-badge live-mic">
                          <span className="mic-badge-dot"></span>
                          LIVE MIC
                        </span>
                      )}
                    </div>
                    <div className="fps-badge-inline">
                      FPS: <span className="fps-value">{fps}</span>
                    </div>
                  </>
                )}
                {status === 'off' && (
                  <span className="camera-badge off">OFFLINE</span>
                )}
              </div>
            </div>

            {/* Strict 4:3 Viewport (Zero Height Shift Between States) */}
            <div className={`camera-viewport ${status === 'active' ? 'active' : ''} ${['denied', 'unavailable', 'unsupported'].includes(status) ? 'error' : ''}`}>
              {/* Analyzing Overlay */}
              {phraseVerificationLoading && (
                <div className="camera-placeholder analyzing-container" style={{ zIndex: 10 }}>
                  <div className="camera-spinner"></div>
                  <div className="camera-placeholder-title">Analyzing Signals...</div>
                  <p className="camera-placeholder-text">
                    Comparing your voice and lip movements for synchronization. Please wait.
                  </p>
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

              {/* Landmark Canvas Overlay */}
              {status === 'active' && (
                <canvas
                  ref={canvasRef}
                  className="camera-canvas"
                />
              )}

              {/* Offline Placeholder */}
              {status === 'off' && !phraseVerificationLoading && (
                <div className="camera-placeholder">
                  <div className="camera-placeholder-icon">📹</div>
                  <div className="camera-placeholder-title">Camera Feed Ready</div>
                  <p className="camera-placeholder-text">
                    Select your preferred language above and click "Start Authentication" below to initialize webcam and microphone.
                  </p>
                </div>
              )}

              {/* Requesting Permission */}
              {status === 'requesting' && (
                <div className="camera-placeholder">
                  <div className="camera-spinner"></div>
                  <div className="camera-placeholder-title">Requesting Permission</div>
                  <p className="camera-placeholder-text">
                    Please click "Allow" on the browser prompt to activate your camera and microphone.
                  </p>
                </div>
              )}

              {/* Permission Denied */}
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

              {/* Unavailable */}
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

              {/* Unsupported */}
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

            {/* Camera Actions & Permissions Info */}
            <div className="camera-card-actions">
              {status === 'active' || status === 'requesting' || ['denied', 'unavailable', 'unsupported'].includes(status) ? (
                <button className="btn-danger" onClick={stopCamera}>
                  Stop Camera
                </button>
              ) : (
                <button
                  className="btn-primary"
                  onClick={() => {
                    setIsCalibrationMode(false);
                    isCalibrationModeRef.current = false;
                    startCamera();
                  }}
                >
                  Start Authentication
                </button>
              )}

              <div className="info-notes">
                <div className="info-item">
                  <span className="info-icon">📹</span>
                  <span className="info-text">MediaPipe FaceMesh mouth tracking</span>
                </div>
                <div className="info-item">
                  <span className="info-icon">🎙️</span>
                  <span className="info-text">Real-time voice sync check</span>
                </div>
              </div>
            </div>
          </div>

          {/* Real-Time Lip Movement Metrics (When Active) */}
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

          {/* Real-Time Audio Capture Metrics (When Active) */}
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
        </div>

        {/* Right Column: Verification Panel & Controls */}
        <div className="col-verification">
          {/* CASE A: Verification Result Summary (When Verification Completes) */}
          {showSummaryCard && finalSummary && (
            <div className="summary-card-container">
              <div className="summary-card-header">
                <h3>
                  {finalSummary.wasCalibrationAttempt
                    ? `🎯 Calibration Sample Result (${calibrationProfile?.validSamplesCount || 0} of ${calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES})`
                    : '📄 Liveness Authentication Result'}
                </h3>
                <div className={`summary-result-badge status-${finalResult.toLowerCase()}`}>
                  {finalSummary.wasCalibrationAttempt
                    ? (finalResult === 'LIVE' ? '✅ SAMPLE ACCEPTED' : '❌ SAMPLE REJECTED')
                    : (finalResult === 'LIVE' ? '✅ LIVE' : '❌ SPOOF')}
                </div>
              </div>

              {/* Active Threshold Mode Indicator Banner */}
              {(() => {
                const syncRes = finalSummary.backendSyncResult;
                const isAdaptiveUsed = Boolean(syncRes?.adaptiveCalibrationUsed ?? calibrationProfile?.isCalibrated);
                const effThresh = Number(syncRes?.effectiveThreshold ?? calibrationProfile?.effectiveThreshold ?? BACKEND_LIVE_SYNC_THRESHOLD);
                const globThresh = Number(syncRes?.globalThreshold ?? BACKEND_LIVE_SYNC_THRESHOLD);
                const adapThresh = syncRes?.adaptiveThreshold ?? calibrationProfile?.adaptiveThreshold;
                return (
                  <div className={`threshold-mode-banner ${isAdaptiveUsed ? 'adaptive-active' : 'global-active'}`}>
                    <span className="threshold-mode-pill">
                      {isAdaptiveUsed ? 'Adaptive threshold active' : 'Global threshold active'}
                    </span>
                    <span className="threshold-mode-meta">
                      Global: <strong>{globThresh.toFixed(2)}</strong> &middot;{' '}
                      Adaptive: <strong>{adapThresh != null ? Number(adapThresh).toFixed(2) : 'N/A'}</strong> &middot;{' '}
                      Effective: <strong>{effThresh.toFixed(2)}</strong>
                    </span>
                  </div>
                );
              })()}

              {/* SPOOF Rejection Banner */}
              {finalResult === 'SPOOF' && (
                <div className="rejection-reason-box">
                  <span className="rejection-reason-icon">⚠️</span>
                  <div>
                    <strong>
                      {finalSummary.wasCalibrationAttempt
                        ? 'Calibration Sample Rejected (Not Added to Baseline)'
                        : 'Authentication Rejected (SPOOF)'}
                    </strong>
                    <span>{finalSummary.rejectionReason || finalSummary.verificationReason || 'Liveness criteria not satisfied.'}</span>
                  </div>
                </div>
              )}

              {/* LIVE Confirmation Banner */}
              {finalResult === 'LIVE' && (
                <div className="live-confirmation-box">
                  <span className="live-confirmation-icon">🛡️</span>
                  <div>
                    <strong>
                      {finalSummary.wasCalibrationAttempt
                        ? (calibrationProfile?.isCalibrated
                            ? `Calibration Complete! Effective Threshold: ${Number(calibrationProfile.effectiveThreshold).toFixed(2)}`
                            : `Calibration Sample Accepted (${calibrationProfile?.validSamplesCount || 0} of ${calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES})`)
                        : 'Authentication Approved (LIVE)'}
                    </strong>
                    <span>Spoken challenge phrase matched and physical lip-voice movement is synchronized.</span>
                  </div>
                </div>
              )}

              {/* Post-Verification Lip-Voice Synchronization Details */}
              {(() => {
                const syncRes = finalSummary.backendSyncResult;
                const hasSyncRes = Boolean(syncRes);
                const alignedVal = hasSyncRes ? Number(syncRes.alignedCorrelation || 0) : 0;
                const rawVal = hasSyncRes ? Number(syncRes.rawCorrelation || 0) : 0;
                const offsetVal = hasSyncRes && syncRes.detectedTimeOffsetMs !== undefined ? Number(syncRes.detectedTimeOffsetMs) : null;
                const effectiveThresholdVal = Number(syncRes?.effectiveThreshold ?? calibrationProfile?.effectiveThreshold ?? BACKEND_LIVE_SYNC_THRESHOLD);
                const isSynchronized = Boolean(syncRes?.isSyncValid);
                const isCorrPass = hasSyncRes && alignedVal >= effectiveThresholdVal;
                const isOffsetPass = offsetVal !== null && Math.abs(offsetVal) <= BACKEND_MAX_SYNC_OFFSET_MS;
                const barPercent = Math.max(0, Math.min(alignedVal * 100, 100));
                const resolvedLangCode = finalSummary.language || syncRes?.language || selectedLanguage;
                const langMeta = getLanguageMeta(resolvedLangCode);
                const isChallengePass = finalSummary.phraseVerificationStatus === 'PASS';

                return (
                  <div className="sync-analysis-panel">
                    <div className="sync-Key-metrics-row">
                      <div className="sync-key-card">
                        <span className="sync-key-label">Sync Score</span>
                        <span className={`sync-key-value ${hasSyncRes ? (isCorrPass ? 'text-success' : 'text-danger') : ''}`}>
                          {hasSyncRes ? alignedVal.toFixed(3) : 'N/A'}
                        </span>
                        <span className="sync-key-sub">
                          Effective &ge; {effectiveThresholdVal.toFixed(2)} &middot; Raw: {hasSyncRes ? rawVal.toFixed(3) : 'N/A'}
                        </span>
                      </div>

                      <div className="sync-key-card">
                        <span className="sync-key-label">Time Offset</span>
                        <span className={`sync-key-value ${offsetVal !== null ? (isOffsetPass ? 'text-success' : 'text-danger') : ''}`}>
                          {offsetVal !== null ? `${offsetVal > 0 ? '+' : ''}${offsetVal} ms` : 'N/A'}
                        </span>
                        <span className="sync-key-sub">
                          Max allowed: &plusmn;{BACKEND_MAX_SYNC_OFFSET_MS} ms
                        </span>
                      </div>

                      <div className="sync-key-card">
                        <span className="sync-key-label">Sync Status</span>
                        <span className={`sync-key-value ${isSynchronized ? 'text-success' : 'text-danger'}`}>
                          {isSynchronized ? 'Synchronized' : 'Not Synchronized'}
                        </span>
                        <span className="sync-key-sub">
                          Backend Verdict ({syncRes?.syncStatus || 'SPOOF'})
                        </span>
                      </div>
                    </div>

                    {/* Multilingual Summary */}
                    <div className="sync-multilingual-summary">
                      <div className="sync-ml-row">
                        <span className="sync-ml-label">Language:</span>
                        <span className="sync-ml-value">
                          {langMeta.englishName} ({langMeta.label})
                        </span>
                      </div>
                      <div className="sync-ml-row">
                        <span className="sync-ml-label">Challenge:</span>
                        <span className="sync-ml-value phrase-highlight">
                          {finalSummary.expectedPhrase || '—'}
                        </span>
                      </div>
                      <div className="sync-ml-row">
                        <span className="sync-ml-label">Recognized Speech:</span>
                        <span className={`sync-ml-value ${isChallengePass ? 'text-success' : 'text-danger'}`}>
                          {finalSummary.transcribedPhrase || finalSummary.recognizedText || 'No speech was recognized.'}
                        </span>
                      </div>
                      <div className="sync-ml-row">
                        <span className="sync-ml-label">Challenge Match:</span>
                        <span className={`sync-ml-value ${isChallengePass ? 'text-success' : 'text-danger'}`}>
                          {isChallengePass ? 'Matched' : 'Mismatched'} ({finalSummary.overallScore ? finalSummary.overallScore.toFixed(1) : 0}%)
                        </span>
                      </div>
                      <div className="sync-ml-row">
                        <span className="sync-ml-label">Sync Score:</span>
                        <span className={`sync-ml-value ${hasSyncRes ? (isCorrPass ? 'text-success' : 'text-danger') : ''}`}>
                          {hasSyncRes ? alignedVal.toFixed(3) : '0.000'}
                        </span>
                      </div>
                      <div className="sync-ml-row">
                        <span className="sync-ml-label">Time Offset:</span>
                        <span className={`sync-ml-value ${offsetVal !== null ? (isOffsetPass ? 'text-danger' : 'text-danger') : ''}`}>
                          {offsetVal !== null ? `${offsetVal} ms` : '0 ms'}
                        </span>
                      </div>
                      <div className="sync-ml-row">
                        <span className="sync-ml-label">Liveness Result:</span>
                        <span className={`sync-ml-value ${finalResult === 'LIVE' ? 'text-success' : 'text-danger'}`}>
                          {finalResult}
                        </span>
                      </div>
                    </div>

                    {/* Visual Synchronization Threshold Bar */}
                    <div className="sync-threshold-bar-box">
                      <div className="sync-threshold-bar-labels">
                        <span>Sync Score vs. Effective Threshold ({effectiveThresholdVal.toFixed(2)})</span>
                        <strong>{hasSyncRes ? `${alignedVal.toFixed(3)} / 1.000` : 'N/A'}</strong>
                      </div>
                      <div className="sync-threshold-track">
                        <div
                          className={`sync-threshold-fill ${isCorrPass ? 'pass' : 'fail'}`}
                          style={{ width: `${barPercent}%` }}
                        ></div>
                        <div
                          className="sync-threshold-marker"
                          style={{ left: `${effectiveThresholdVal * 100}%` }}
                          title={`Effective Synchronization Threshold: ${effectiveThresholdVal.toFixed(2)}`}
                        >
                          <span className="sync-threshold-marker-tag">{effectiveThresholdVal.toFixed(2)}</span>
                        </div>
                      </div>
                    </div>

                    {/* Recorded Lip Movement vs Audio Envelope Waveform Graph */}
                    {renderSignalWaveformGraph(
                      finalSummary.signalGraphPoints || [],
                      Math.max((syncRes?.audioDurationMs || 5000) / 1000, 1.0),
                      finalSummary.isBackendSignalSeries ? 'Backend-Aligned Signals' : 'Captured Signals'
                    )}
                  </div>
                );
              })()}

              {/* Detailed Summary Statistics Grid */}
              <div className="summary-stats-grid">
                <div className="summary-stat-item">
                  <span className="summary-stat-label">Language</span>
                  <span className="summary-stat-value">
                    {getLanguageMeta(finalSummary.language || finalSummary.backendSyncResult?.language || selectedLanguage).englishName}{' '}
                    <small style={{ color: 'var(--color-text-muted)' }}>
                      ({getLanguageMeta(finalSummary.language || finalSummary.backendSyncResult?.language || selectedLanguage).label})
                    </small>
                  </span>
                </div>
                <div className="summary-stat-item">
                  <span className="summary-stat-label">Challenge Match</span>
                  <span className={`summary-stat-value ${finalSummary.phraseVerificationStatus === 'PASS' ? 'text-success' : 'text-danger'}`}>
                    {finalSummary.phraseVerificationStatus === 'PASS' ? 'Matched' : 'Mismatched'} ({finalSummary.overallScore ? finalSummary.overallScore.toFixed(1) : 0}%)
                  </span>
                </div>
                <div className="summary-stat-item" style={{ gridColumn: 'span 2' }}>
                  <span className="summary-stat-label">Challenge Phrase</span>
                  <span className="summary-stat-value phrase-text-summary" style={{ maxWidth: '100%', whiteSpace: 'normal', color: '#2563EB' }}>
                    "{finalSummary.expectedPhrase}"
                  </span>
                </div>
                <div className="summary-stat-item" style={{ gridColumn: 'span 2' }}>
                  <span className="summary-stat-label">Recognized Speech (Transcript)</span>
                  <span className="summary-stat-value phrase-text-summary" style={{ maxWidth: '100%', whiteSpace: 'normal', color: finalSummary.phraseVerificationStatus === 'PASS' ? '#16A34A' : '#DC2626' }}>
                    "{finalSummary.transcribedPhrase || finalSummary.recognizedText || 'No speech was recognized.'}"
                  </span>
                </div>
                <div className="summary-stat-item">
                  <span className="summary-stat-label">Liveness Result</span>
                  <span className={`summary-stat-value ${finalResult === 'LIVE' ? 'text-success' : 'text-danger'}`}>
                    {finalResult}
                  </span>
                </div>
                <div className="summary-stat-item">
                  <span className="summary-stat-label">Lip-Voice Sync Status</span>
                  <span className={`summary-stat-value ${(finalSummary.backendSyncResult?.syncStatus === 'LIVE' || finalSummary.backendSyncResult?.isSyncValid) ? 'text-success' : 'text-danger'}`}>
                    {finalSummary.backendSyncResult?.syncStatus || 'SPOOF'}
                  </span>
                </div>
                <div className="summary-stat-item">
                  <span className="summary-stat-label">Aligned Correlation</span>
                  <span className="summary-stat-value">
                    {finalSummary.backendSyncResult ? finalSummary.backendSyncResult.alignedCorrelation.toFixed(3) : finalSummary.averageScore.toFixed(3)}
                    <small style={{ color: 'var(--color-text-muted)', marginLeft: '4px' }}>
                      (min {Number(finalSummary.backendSyncResult?.effectiveThreshold ?? BACKEND_LIVE_SYNC_THRESHOLD).toFixed(2)})
                    </small>
                  </span>
                </div>
                <div className="summary-stat-item">
                  <span className="summary-stat-label">Effective / Global Threshold</span>
                  <span className="summary-stat-value">
                    {Number(finalSummary.backendSyncResult?.effectiveThreshold ?? calibrationProfile?.effectiveThreshold ?? BACKEND_LIVE_SYNC_THRESHOLD).toFixed(2)}
                    <small style={{ color: 'var(--color-text-muted)', marginLeft: '4px' }}>
                      (Global: {Number(finalSummary.backendSyncResult?.globalThreshold ?? BACKEND_LIVE_SYNC_THRESHOLD).toFixed(2)})
                    </small>
                  </span>
                </div>
                <div className="summary-stat-item">
                  <span className="summary-stat-label">Estimated Time Offset</span>
                  <span className="summary-stat-value">
                    {finalSummary.backendSyncResult?.detectedTimeOffsetMs !== undefined ? `${finalSummary.backendSyncResult.detectedTimeOffsetMs}ms` : '0ms'}
                    <small style={{ color: 'var(--color-text-muted)', marginLeft: '4px' }}>(&lt; 500ms)</small>
                  </span>
                </div>
                <div className="summary-stat-item">
                  <span className="summary-stat-label">Audio / Lip Duration</span>
                  <span className="summary-stat-value">
                    {finalSummary.backendSyncResult ? `${(finalSummary.backendSyncResult.audioDurationMs / 1000).toFixed(2)}s / ${(finalSummary.backendSyncResult.lipDurationMs / 1000).toFixed(2)}s` : `${finalSummary.duration.toFixed(1)}s`}
                  </span>
                </div>
                <div className="summary-stat-item">
                  <span className="summary-stat-label">Duration Difference</span>
                  <span className="summary-stat-value">
                    {finalSummary.backendSyncResult?.durationDifferenceMs !== undefined ? `${finalSummary.backendSyncResult.durationDifferenceMs}ms` : '0ms'}
                  </span>
                </div>
                <div className="summary-stat-item">
                  <span className="summary-stat-label">Valid Sync Frames</span>
                  <span className="summary-stat-value">{finalSummary.backendSyncResult?.validFrames ?? 0}</span>
                </div>
              </div>

              {/* Action Buttons inside Summary */}
              <div className="summary-actions">
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
                {!calibrationProfile?.isCalibrated && (isCalibrationMode || finalSummary.wasCalibrationAttempt) && (
                  <button
                    className="btn-primary"
                    onClick={() => {
                      setShowSummaryCard(false);
                      setFinalSummary(null);
                      handleContinueCalibration();
                    }}
                  >
                    Record Sample {Math.min((calibrationProfile?.validSamplesCount || 0) + 1, calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES)} of {calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES}
                  </button>
                )}
                <button 
                  className="btn-primary"
                  onClick={() => {
                    setIsCalibrationMode(false);
                    isCalibrationModeRef.current = false;
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

          {/* CASE B: When Camera is Active (Show Challenge & Sync Monitor) */}
          {status === 'active' && challengePhrase && (
            <div className="challenge-card">
              <div className="challenge-card-header">
                <span className="challenge-icon">{isCalibrationMode ? '🎯' : '🔐'}</span>
                <div className="challenge-title-group">
                  <h3 className="challenge-card-title">
                    {isCalibrationMode
                      ? `Calibration Recording — Sample ${Math.min((calibrationProfile?.validSamplesCount || 0) + 1, calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES)} of ${calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES}`
                      : `Speech Challenge (${getLanguageMeta(selectedLanguage).label})`}
                  </h3>
                </div>
                <span className={`challenge-status-badge status-${challengeStatus.toLowerCase()}`}>
                  {challengeStatus}
                </span>
              </div>

              <div className="challenge-phrase-box">
                <div className="challenge-phrase-label">
                  Speak the {getLanguageMeta(selectedLanguage).englishName} phrase clearly:
                </div>
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
                    <span>Recording in progress... Keep speaking clearly</span>
                  </div>
                ) : (
                  <div className="completed-indicator">
                    <span className="completed-icon">✅</span>
                    <span>Recording captured. Verifying multimodal signals...</span>
                  </div>
                )}
              </div>

              <div className="challenge-controls">
                {challengeStatus === 'RECORDING' ? (
                  <>
                    <div className="recording-duration">
                      Recording: <strong>{recordingTime}s / 5s</strong> (Target: 5 seconds)
                    </div>
                    <div className="timer-progress-container" style={{ width: '100%', marginBottom: '4px' }}>
                      <div 
                        className="timer-progress-bar" 
                        style={{ 
                          width: `${Math.min((recordingTime / 5) * 100, 100)}%`,
                          background: 'linear-gradient(90deg, #ef4444, #f59e0b)'
                        }}
                      ></div>
                    </div>
                    <button
                      className="btn-stop-record"
                      onClick={stopRecording}
                    >
                      Stop Recording &amp; Verify
                    </button>
                  </>
                ) : (
                  <button 
                    className="btn-start-record" 
                    disabled={!faceDetected || (phraseVerificationStatus && phraseVerificationStatus !== '')}
                    title={phraseVerificationStatus ? "Verification completed" : !faceDetected ? "Position face in camera to record" : "Start Recording"}
                    onClick={startRecording}
                  >
                    {isCalibrationMode
                      ? `Record Calibration Sample ${Math.min((calibrationProfile?.validSamplesCount || 0) + 1, calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES)} of ${calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES} (5s)`
                      : 'Start Recording (5s)'}
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Real-time Lip-Voice Synchronization & Signal Waveform Monitor (When Active) */}
          {status === 'active' && (() => {
            const isAdaptiveActive = Boolean(!isCalibrationMode && calibrationProfile?.isCalibrated);
            const activeEffThreshold = isCalibrationMode
              ? BACKEND_LIVE_SYNC_THRESHOLD
              : Number(calibrationProfile?.effectiveThreshold ?? BACKEND_LIVE_SYNC_THRESHOLD);
            return (
              <div className="metrics-dashboard sync-live-monitor">
                <div className="metrics-header">
                  <h3>📈 Lip-Voice Synchronization Monitor</h3>
                  <span className={`metrics-status-badge ${challengeStatus === 'RECORDING' ? 'recording-badge' : 'active'}`}>
                    {isCalibrationMode
                      ? `Calibration Sample ${Math.min((calibrationProfile?.validSamplesCount || 0) + 1, calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES)} of ${calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES}`
                      : (isAdaptiveActive ? 'Adaptive threshold active' : 'Global threshold active')}
                  </span>
                </div>

                <div className="sync-analysis-panel">
                  <div className="sync-Key-metrics-row">
                    <div className="sync-key-card">
                      <span className="sync-key-label">Sync Score</span>
                      <span className="sync-key-value text-muted-live">
                        {phraseVerificationLoading ? 'Computing...' : 'Pending 5s Capture'}
                      </span>
                      <span className="sync-key-sub">
                        Threshold &ge; {activeEffThreshold.toFixed(2)} ({isAdaptiveActive ? 'Adaptive' : 'Global'})
                      </span>
                    </div>

                    <div className="sync-key-card">
                      <span className="sync-key-label">Time Offset</span>
                      <span className="sync-key-value text-muted-live">
                        {phraseVerificationLoading ? 'Aligning...' : 'Pending 5s Capture'}
                      </span>
                      <span className="sync-key-sub">
                        Max allowed: &plusmn;{BACKEND_MAX_SYNC_OFFSET_MS} ms
                      </span>
                    </div>

                    <div className="sync-key-card">
                      <span className="sync-key-label">Sync Status</span>
                      <span className={`sync-key-value ${challengeStatus === 'RECORDING' ? 'text-recording-live' : 'text-muted-live'}`}>
                        {challengeStatus === 'RECORDING' ? 'Sampling Signals...' : 'Awaiting Recording'}
                      </span>
                      <span className="sync-key-sub">
                        Verdict after verification
                      </span>
                    </div>
                  </div>

                  {/* Visual Synchronization Threshold Reference Bar */}
                  <div className="sync-threshold-bar-box">
                    <div className="sync-threshold-bar-labels">
                      <span>
                        {isAdaptiveActive
                          ? `Active Adaptive Threshold (${activeEffThreshold.toFixed(2)} vs Global ${BACKEND_LIVE_SYNC_THRESHOLD.toFixed(2)})`
                          : `Required Synchronization Threshold (${activeEffThreshold.toFixed(2)})`}
                      </span>
                      <strong>Target &ge; {activeEffThreshold.toFixed(2)}</strong>
                    </div>
                    <div className="sync-threshold-track">
                      <div
                        className="sync-threshold-marker"
                        style={{ left: `${activeEffThreshold * 100}%` }}
                        title={`Active Effective Threshold: ${activeEffThreshold.toFixed(2)}`}
                      >
                        <span className="sync-threshold-marker-tag">{activeEffThreshold.toFixed(2)}</span>
                      </div>
                    </div>
                  </div>

                  {/* Live Normalized Lip Movement vs Audio Energy Graph */}
                  {renderSignalWaveformGraph(
                    liveGraphPoints,
                    5.0,
                    challengeStatus === 'RECORDING' ? 'Recording 5s Window (Live)' : 'Rolling 5s Preview (Live)'
                  )}
                </div>
              </div>
            );
          })()}

          {/* CASE C: Verification Guide Card (When Camera is Standby and Summary is Not Open) */}
          {status === 'off' && !showSummaryCard && (
            <div className="verification-guide-card">
              <div className="guide-card-header">
                <span className="guide-icon">🛡️</span>
                <div>
                  <h3 className="guide-title">Multimodal Liveness Verification</h3>
                  <p className="guide-subtitle">Authenticate presence in three steps</p>
                </div>
              </div>
              <div className="guide-steps-list">
                <div className="guide-step-item">
                  <span className="guide-step-num">1</span>
                  <div className="guide-step-content">
                    <strong>Select Challenge Language</strong>
                    <p>Choose English, Hindi, or other supported languages from the bar above.</p>
                  </div>
                </div>
                <div className="guide-step-item">
                  <span className="guide-step-num">2</span>
                  <div className="guide-step-content">
                    <strong>Initialize Authentication</strong>
                    <p>Click "Start Authentication" on the left to activate your camera and microphone.</p>
                  </div>
                </div>
                <div className="guide-step-item">
                  <span className="guide-step-num">3</span>
                  <div className="guide-step-content">
                    <strong>Speak Challenge Phrase</strong>
                    <p>Record for 5 seconds while reading the randomly generated phrase. MediaPipe and Whisper verify lip-voice synchrony.</p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Adaptive Per-User Synchronization Threshold Calibration Card */}
          <div className="calibration-card">
            <div className="calibration-card-header">
              <div className="calibration-title-group">
                <span className="calibration-title">🎯 Adaptive Per-User Calibration</span>
                <span
                  className={`calibration-status-pill ${
                    calibrationProfile?.isCalibrated
                      ? 'calibrated'
                      : calibrationProfile?.calibrationStatus === 'IN_PROGRESS'
                      ? 'in-progress'
                      : 'not-calibrated'
                  }`}
                >
                  {calibrationProfile?.isCalibrated
                    ? `Calibrated (${calibrationProfile.validSamplesCount}/${calibrationProfile.requiredSamples || DEFAULT_CALIBRATION_SAMPLES})`
                    : calibrationProfile?.calibrationStatus === 'IN_PROGRESS'
                    ? `In Progress (${Math.min((calibrationProfile?.validSamplesCount || 0) + 1, calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES)} of ${calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES})`
                    : 'Not Calibrated'}
                </span>
              </div>

              <div className="calibration-active-mode-indicator">
                <span
                  className={`threshold-mode-pill ${
                    calibrationProfile?.isCalibrated ? 'adaptive-active' : 'global-active'
                  }`}
                >
                  {calibrationProfile?.isCalibrated
                    ? '⚡ Adaptive threshold active'
                    : '🌐 Global threshold active'}
                </span>
              </div>
            </div>

            {/* User Profile Row */}
            <div className="calibration-user-row">
              <label className="calibration-user-label" htmlFor="calibration-user-id-input">
                User Profile ID:
              </label>
              <input
                id="calibration-user-id-input"
                type="text"
                className="calibration-user-input"
                value={userId}
                disabled={isRecording || phraseVerificationLoading}
                onChange={(e) => setUserId(e.target.value || 'user-default')}
                placeholder="user-default"
              />
              <span className="calibration-progress-text">
                {calibrationProfile?.isCalibrated
                  ? `Calibration: Complete (${calibrationProfile.validSamplesCount}/${calibrationProfile.requiredSamples || DEFAULT_CALIBRATION_SAMPLES} samples)`
                  : `Progress: ${calibrationProfile?.validSamplesCount || 0} of ${calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES} valid samples`}
              </span>
            </div>

            {/* Threshold Values Grid */}
            <div className="calibration-thresholds-grid">
              <div className="calibration-threshold-box">
                <span className="cal-thresh-label">Global Threshold</span>
                <span className="cal-thresh-val">
                  {Number(calibrationProfile?.globalThreshold ?? BACKEND_LIVE_SYNC_THRESHOLD).toFixed(2)}
                </span>
                <span className="cal-thresh-sub">Minimum safety floor</span>
              </div>
              <div className="calibration-threshold-box">
                <span className="cal-thresh-label">Adaptive Threshold</span>
                <span className="cal-thresh-val highlight-adaptive">
                  {calibrationProfile?.adaptiveThreshold !== null && calibrationProfile?.adaptiveThreshold !== undefined
                    ? Number(calibrationProfile.adaptiveThreshold).toFixed(2)
                    : '—'}
                </span>
                <span className="cal-thresh-sub">
                  {calibrationProfile?.meanScore !== null && calibrationProfile?.meanScore !== undefined
                    ? `μ=${Number(calibrationProfile.meanScore).toFixed(2)}, σ=${Number(calibrationProfile.stdDeviation || 0).toFixed(3)}`
                    : 'Requires 3 samples'}
                </span>
              </div>
              <div className="calibration-threshold-box effective-box">
                <span className="cal-thresh-label">Effective Threshold</span>
                <span className="cal-thresh-val highlight-effective">
                  {Number(calibrationProfile?.effectiveThreshold ?? BACKEND_LIVE_SYNC_THRESHOLD).toFixed(2)}
                </span>
                <span className="cal-thresh-sub">
                  {calibrationProfile?.isCalibrated ? 'Personalized ≥ 0.45' : 'Global fallback (0.45)'}
                </span>
              </div>
            </div>

            {/* Accepted Calibration Sample Scores */}
            <div className="calibration-samples-row">
              <span className="calibration-samples-label">Accepted Samples:</span>
              {Array.isArray(calibrationProfile?.validScores) && calibrationProfile.validScores.length > 0 ? (
                <div className="calibration-samples-list">
                  {calibrationProfile.validScores.map((score, idx) => (
                    <span key={`cal-sample-${idx}`} className="calibration-sample-chip">
                      Sample {idx + 1}: <strong>{Number(score).toFixed(2)}</strong>
                    </span>
                  ))}
                </div>
              ) : (
                <span className="calibration-samples-empty">
                  No calibration samples yet (requires LIVE pass ≥ 0.45 and |offset| ≤ 500 ms).
                </span>
              )}
            </div>

            {/* Last Calibration Sample Feedback */}
            {lastCalibrationOutcome && (
              <div
                className={`calibration-outcome-banner ${
                  lastCalibrationOutcome.sampleAccepted ? 'accepted' : 'rejected'
                }`}
              >
                {lastCalibrationOutcome.sampleAccepted ? (
                  <span>
                    ✅ Sample {lastCalibrationOutcome.currentSampleNumber} of {lastCalibrationOutcome.requiredSamples} accepted (Aligned Sync Score:{' '}
                    <strong>{Number(lastCalibrationOutcome.alignedCorrelation).toFixed(2)}</strong>)
                  </span>
                ) : (
                  <span>
                    ⚠️ Calibration sample rejected — excluded from baseline:{' '}
                    <strong>{lastCalibrationOutcome.sampleRejectionReason}</strong>
                  </span>
                )}
              </div>
            )}

            {/* Calibration Controls */}
            <div className="calibration-actions-row">
              <button
                type="button"
                className="btn-calibration-start"
                disabled={isRecording || phraseVerificationLoading}
                onClick={handleStartNewCalibration}
              >
                {calibrationProfile?.isCalibrated || (calibrationProfile?.validSamplesCount || 0) > 0
                  ? '🔄 Reset & Start New Calibration (3 Samples)'
                  : '🎯 Start Calibration (3 Samples)'}
              </button>

              {!calibrationProfile?.isCalibrated && (calibrationProfile?.validSamplesCount || 0) > 0 && (
                <button
                  type="button"
                  className="btn-calibration-continue"
                  disabled={isRecording || phraseVerificationLoading}
                  onClick={handleContinueCalibration}
                >
                  ▶️ Record Sample {(calibrationProfile?.validSamplesCount || 0) + 1} of{' '}
                  {calibrationProfile?.requiredSamples || DEFAULT_CALIBRATION_SAMPLES}
                </button>
              )}

              {isCalibrationMode && (
                <button
                  type="button"
                  className="btn-calibration-exit"
                  disabled={isRecording || phraseVerificationLoading}
                  onClick={() => {
                    setIsCalibrationMode(false);
                    isCalibrationModeRef.current = false;
                  }}
                >
                  Switch to Standard Verification
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default CameraPreview;
