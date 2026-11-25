import React, { useState, useEffect, useRef } from 'react';
import {
  startMonitoringSession,
  stopMonitoringSession,
  logMonitoringEvent,
  sendMonitoringPersonFrame,
  sendMonitoringAudioChunk,
  fetchSessionReport,
  initInterviewSession,
  submitInterviewAnswer,
  fetchInterviewResults,
  transcribeAnswerAudio
} from '../utils/api';
import SessionReportModal from './SessionReportModal';

const MAX_INTEGRITY_WARNINGS = 5;
const DEFAULT_QUESTION_TIME_LIMIT = 120; // 2 minutes per question

export default function InterviewDashboard({
  candidateData,
  initialStream,
  onInterviewEnd
}) {
  const {
    candidateName = 'Candidate',
    candidateEmail = 'candidate@example.com',
    interviewRole = 'Software Specialist',
    roleId = 'python',
    customRole = '',
    skills = '',
    language = 'en',
    sessionId
  } = candidateData;

  // Stream & video refs
  const videoRef = useRef(null);
  const streamRef = useRef(initialStream || null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);

  // Background monitoring interval & recorder refs
  const sessionStartTimeRef = useRef(null);
  const timerIntervalRef = useRef(null);
  const questionTimerIntervalRef = useRef(null);
  const audioMeterIntervalRef = useRef(null);
  const personCheckIntervalRef = useRef(null);
  const monitoringAudioRecorderRef = useRef(null);
  const monitoringAudioIntervalRef = useRef(null);
  const monitoringAudioSliceTimeoutRef = useRef(null);
  const isAudioMonitoringActiveRef = useRef(false);
  const isSendingFrameRef = useRef(false);
  const isSendingAudioRef = useRef(false);

  // Answer Voice Recorder refs
  const answerRecorderRef = useRef(null);
  const answerAudioChunksRef = useRef([]);

  // Hardware controls
  const [isMicMuted, setIsMicMuted] = useState(false);
  const [isVideoOff, setIsVideoOff] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [audioLevel, setAudioLevel] = useState(0);
  const audioLevelRef = useRef(0);

  // Integrity monitoring states
  const [personCount, setPersonCount] = useState(1);
  const [faceStatus, setFaceStatus] = useState('NORMAL');
  const [warningCount, setWarningCount] = useState(0);
  const [warningsList, setWarningsList] = useState([]);
  const [isSessionExpired, setIsSessionExpired] = useState(false);
  const [speechStatusText, setSpeechStatusText] = useState('Clear (No background voice)');
  const [bannerAlert, setBannerAlert] = useState(null);

  // Face Detection Bounding Boxes Overlay Refs
  const overlayCanvasRef = useRef(null);
  const facesListRef = useRef([]);
  const frameDimRef = useRef({ width: 640, height: 480 });

  // AI Interview Questions & Progression States
  const [isLoadingQuestions, setIsLoadingQuestions] = useState(true);
  const [questions, setQuestions] = useState([]);
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [currentQuestion, setCurrentQuestion] = useState(null);
  const [questionSecondsLeft, setQuestionSecondsLeft] = useState(DEFAULT_QUESTION_TIME_LIMIT);
  const [isAISpeaking, setIsAISpeaking] = useState(false);

  // Candidate Answer Inputs
  const [isRecordingVoiceAnswer, setIsRecordingVoiceAnswer] = useState(false);
  const isRecordingVoiceAnswerRef = useRef(false);
  const [voiceSecondsRecorded, setVoiceSecondsRecorded] = useState(0);
  const voiceTimerRef = useRef(null);
  const [recordedAudioBlob, setRecordedAudioBlob] = useState(null);
  const [typedAnswer, setTypedAnswer] = useState('');
  const [isSubmittingAnswer, setIsSubmittingAnswer] = useState(false);
  const [lastEvaluation, setLastEvaluation] = useState(null);
  const [showFeedbackModal, setShowFeedbackModal] = useState(false);

  // Final session audit report modal
  const [reportData, setReportData] = useState(null);
  const [isReportOpen, setIsReportOpen] = useState(false);
  const [isEnding, setIsEnding] = useState(false);
  const [isInterviewCompleted, setIsInterviewCompleted] = useState(false);

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

  // -------------------------------------------------------------------------
  // Text-To-Speech (TTS): Reads questions aloud
  // -------------------------------------------------------------------------
  const speakQuestion = (text, lang = 'en') => {
    if (!text || !window.speechSynthesis) return;
    try {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      if (lang === 'hi') utterance.lang = 'hi-IN';
      else if (lang === 'mr') utterance.lang = 'mr-IN';
      else utterance.lang = 'en-US';
      utterance.rate = 0.95;
      utterance.pitch = 1.0;
      utterance.onstart = () => setIsAISpeaking(true);
      utterance.onend = () => setIsAISpeaking(false);
      utterance.onerror = () => setIsAISpeaking(false);
      window.speechSynthesis.speak(utterance);
    } catch (e) {
      setIsAISpeaking(false);
    }
  };

  const stopSpeaking = () => {
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
      setIsAISpeaking(false);
    }
  };

  // -------------------------------------------------------------------------
  // Face Detection Bounding Boxes Overlay
  // Rule: When exactly 1 face is present, show clean camera preview without boxes.
  // When multiple faces detected, draw clean rectangular boxes around each face!
  // -------------------------------------------------------------------------
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
    // SPEC RULE: When exactly 1 face (or 0) is present, do NOT draw any box!
    if (!faces || faces.length <= 1) {
      return;
    }

    // MULTIPLE FACES DETECTED: Draw clean rectangular boxes around each face
    const videoNativeW = video.videoWidth || frameDimRef.current.width || 640;
    const videoNativeH = video.videoHeight || frameDimRef.current.height || 480;
    const frameW = frameDimRef.current.width || 640;
    const frameH = frameDimRef.current.height || 480;

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

      // Mirror X coordinate since video has CSS transform: scaleX(-1)
      const mirroredX = width - (unmirroredX + boxW);
      const mirroredY = unmirroredY;

      const isPrimary = index === 0;
      const strokeColor = isPrimary ? '#3B82F6' : '#EF4444';
      const badgeBg = isPrimary ? '#2563EB' : '#DC2626';
      const labelText = isPrimary ? 'Candidate' : `Alert: Additional Person (Face ${index + 1})`;

      ctx.save();
      const radius = 6;
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(mirroredX, mirroredY, boxW, boxH, radius);
      } else {
        ctx.rect(mirroredX, mirroredY, boxW, boxH);
      }
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = strokeColor;
      ctx.shadowColor = isPrimary ? 'rgba(59, 130, 246, 0.4)' : 'rgba(239, 68, 68, 0.6)';
      ctx.shadowBlur = 8;
      ctx.stroke();

      ctx.fillStyle = isPrimary ? 'rgba(59, 130, 246, 0.06)' : 'rgba(239, 68, 68, 0.10)';
      ctx.fill();

      // Label Pill Tag
      ctx.shadowBlur = 0;
      ctx.font = 'bold 12px "Plus Jakarta Sans", -apple-system, sans-serif';
      const textMetrics = ctx.measureText(labelText);
      const padX = 10;
      const tagH = 22;
      const tagW = textMetrics.width + padX * 2;
      const tagX = Math.max(4, Math.min(width - tagW - 4, mirroredX));
      const tagY = mirroredY > 26 ? mirroredY - 26 : mirroredY + 4;

      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(tagX, tagY, tagW, tagH, 4);
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

  // -------------------------------------------------------------------------
  // Record Unified Integrity Warning
  // -------------------------------------------------------------------------
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
      }, 5500);

      // If MAX_INTEGRITY_WARNINGS (5) reached, terminate interview immediately!
      if (newCount >= MAX_INTEGRITY_WARNINGS) {
        handleExpireSession(`Exceeded maximum allowed integrity warnings (${MAX_INTEGRITY_WARNINGS} of ${MAX_INTEGRITY_WARNINGS}).`);
      }
      return newCount;
    });
  };

  // -------------------------------------------------------------------------
  // Session Expiry & Termination Handler
  // -------------------------------------------------------------------------
  const handleExpireSession = async (reason) => {
    setIsSessionExpired(true);
    stopSpeaking();
    cleanupMonitoring();

    try {
      const res = await stopMonitoringSession(sessionId, reason);
      if (res && res.report) {
        setReportData(res.report);
      }
    } catch (err) {
      console.warn('Failed to stop session gracefully on expiry:', err);
    }
  };

  // -------------------------------------------------------------------------
  // Cleanup all timers and media
  // -------------------------------------------------------------------------
  const cleanupMonitoring = () => {
    if (timerIntervalRef.current) {
      clearInterval(timerIntervalRef.current);
      timerIntervalRef.current = null;
    }
    if (questionTimerIntervalRef.current) {
      clearInterval(questionTimerIntervalRef.current);
      questionTimerIntervalRef.current = null;
    }
    if (voiceTimerRef.current) {
      clearInterval(voiceTimerRef.current);
      voiceTimerRef.current = null;
    }
    if (audioMeterIntervalRef.current) {
      clearInterval(audioMeterIntervalRef.current);
      audioMeterIntervalRef.current = null;
    }
    if (personCheckIntervalRef.current) {
      clearInterval(personCheckIntervalRef.current);
      personCheckIntervalRef.current = null;
    }
    if (monitoringAudioIntervalRef.current) {
      clearTimeout(monitoringAudioIntervalRef.current);
      monitoringAudioIntervalRef.current = null;
    }
    if (monitoringAudioSliceTimeoutRef.current) {
      clearTimeout(monitoringAudioSliceTimeoutRef.current);
      monitoringAudioSliceTimeoutRef.current = null;
    }
    isAudioMonitoringActiveRef.current = false;

    if (monitoringAudioRecorderRef.current && monitoringAudioRecorderRef.current.state === 'recording') {
      try { monitoringAudioRecorderRef.current.stop(); } catch {}
    }
    if (answerRecorderRef.current && answerRecorderRef.current.state === 'recording') {
      try { answerRecorderRef.current.stop(); } catch {}
    }

    facesListRef.current = [];
    if (overlayCanvasRef.current) {
      const ctx = overlayCanvasRef.current.getContext('2d');
      if (ctx) ctx.clearRect(0, 0, overlayCanvasRef.current.width, overlayCanvasRef.current.height);
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => {
        try { track.stop(); } catch {}
      });
    }

    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      try { audioContextRef.current.close(); } catch {}
    }
  };

  // -------------------------------------------------------------------------
  // Initialization: Media, Monitoring, & Fetch AI Questions
  // -------------------------------------------------------------------------
  useEffect(() => {
    let isCancelled = false;

    const initInterview = async () => {
      // 1. Ensure camera stream is active and alive
      let stream = streamRef.current;
      const isStreamAlive = stream && 
        stream.active && 
        stream.getVideoTracks().length > 0 && 
        stream.getVideoTracks().some(t => t.readyState === 'live');

      if (!isStreamAlive) {
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
        videoRef.current.play().catch((e) => console.warn('Camera video play warning:', e));
      }

      // Initialize monitoring session on the backend
      startMonitoringSession(sessionId, candidateEmail, roleId, language)
        .catch((mErr) => console.warn('Background monitoring session init notice:', mErr));

      // Audio volume analyzer for speaking activity
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

      // 2. Fetch curated / dynamic interview questions matching candidate's selected count
      setIsLoadingQuestions(true);
      const targetQuestionCount = candidateData.questionCount || 5;
      try {
        const initRes = await initInterviewSession(
          sessionId,
          roleId,
          candidateData.experienceLevel || 'fresher',
          language,
          candidateName,
          candidateEmail,
          skills,
          customRole,
          targetQuestionCount
        );

        if (initRes && initRes.questions && initRes.questions.length > 0) {
          const loadedQuestions = initRes.questions.slice(0, targetQuestionCount);
          setQuestions(loadedQuestions);
          setCurrentQuestionIndex(0);
          const firstQ = loadedQuestions[0];
          setCurrentQuestion(firstQ);
          setQuestionSecondsLeft(firstQ.timeLimit || DEFAULT_QUESTION_TIME_LIMIT);

          // Automatically read first question aloud via TTS
          setTimeout(() => {
            speakQuestion(firstQ.question, language);
          }, 800);
        } else {
          throw new Error('Empty questions list returned from server.');
        }
      } catch (qErr) {
        console.warn('Using client question fallback for interview:', qErr);
        const fullFallbackPool = [
          {
            id: 'fb-01',
            category: 'Core Technical Competency',
            question: `Can you explain the core foundational concepts and architectural principles you rely on most when developing in ${interviewRole}?`,
            timeLimit: 120
          },
          {
            id: 'fb-02',
            category: 'Project Architecture & Challenges',
            question: `Describe a challenging technical project you worked on recently in ${interviewRole}. What was your design approach and how did you resolve bottlenecks?`,
            timeLimit: 120
          },
          {
            id: 'fb-03',
            category: 'System Reliability & Quality Assurance',
            question: `What is your methodology for debugging complex errors, testing code reliability, and ensuring quality in production systems?`,
            timeLimit: 120
          },
          {
            id: 'fb-04',
            category: 'Performance Optimization & Scaling',
            question: `How do you identify performance bottlenecks and optimize memory, latency, and resource consumption in ${interviewRole}?`,
            timeLimit: 120
          },
          {
            id: 'fb-05',
            category: 'Security & API Design',
            question: `What secure coding practices, input validation strategies, and API communication standards do you implement in ${interviewRole}?`,
            timeLimit: 120
          },
          {
            id: 'fb-06',
            category: 'Concurrency & State Management',
            question: `How do you handle concurrent operations, asynchronous workflows, and state synchronization in ${interviewRole}?`,
            timeLimit: 120
          },
          {
            id: 'fb-07',
            category: 'Testing & Continuous Integration',
            question: `What is your automated testing strategy (unit, integration, regression) and how do you integrate automated testing in your CI/CD pipeline?`,
            timeLimit: 120
          },
          {
            id: 'fb-08',
            category: 'Data Persistence & Caching',
            question: `How do you design database schemas, index queries, and leverage caching layers to maximize throughput?`,
            timeLimit: 120
          },
          {
            id: 'fb-09',
            category: 'Incident Response & Production Monitoring',
            question: `Walk us through a critical production incident or failure you diagnosed. How did you restore availability and implement preventive safeguards?`,
            timeLimit: 120
          },
          {
            id: 'fb-10',
            category: 'Technical Leadership & Collaboration',
            question: `How do you conduct code reviews, mentor peers, and balance rapid feature delivery with architectural maintainability?`,
            timeLimit: 120
          }
        ];
        const slicedFallback = fullFallbackPool.slice(0, targetQuestionCount);
        setQuestions(slicedFallback);
        setCurrentQuestionIndex(0);
        setCurrentQuestion(slicedFallback[0]);
        setQuestionSecondsLeft(120);
        setTimeout(() => {
          speakQuestion(slicedFallback[0].question, language);
        }, 800);
      } finally {
        setIsLoadingQuestions(false);
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

      // 4. Attach resize listener for bounding box canvas
      window.addEventListener('resize', drawFaceBoundingBoxes);

      // 5. Start background Person/Face Detection sampling
      startPersonDetectionLoop();

      // 6. Start background Audio/VAD sampling
      startAudioMonitoringLoop();
    };

    initInterview();

    return () => {
      isCancelled = true;
      stopSpeaking();
      window.removeEventListener('resize', drawFaceBoundingBoxes);
      cleanupMonitoring();
    };
  }, []);

  // -------------------------------------------------------------------------
  // Per-Question Countdown Timer
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (isSessionExpired || isEnding || isInterviewCompleted || !currentQuestion) return;

    if (questionTimerIntervalRef.current) clearInterval(questionTimerIntervalRef.current);

    questionTimerIntervalRef.current = setInterval(() => {
      setQuestionSecondsLeft((prev) => {
        if (prev <= 1) {
          clearInterval(questionTimerIntervalRef.current);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => {
      if (questionTimerIntervalRef.current) clearInterval(questionTimerIntervalRef.current);
    };
  }, [currentQuestionIndex, isSessionExpired, isEnding, isInterviewCompleted]);

  // -------------------------------------------------------------------------
  // Browser Tab-Switch & Window Blur Detection
  // Unified Warning Count increment with 4s cooldown
  // -------------------------------------------------------------------------
  useEffect(() => {
    let tabSwitchCooldown = false;

    const handleTabSwitch = async (eventDetail) => {
      if (isSessionExpired || isEnding || isInterviewCompleted || tabSwitchCooldown) return;
      tabSwitchCooldown = true;
      setTimeout(() => { tabSwitchCooldown = false; }, 4000);

      try {
        const res = await logMonitoringEvent(
          sessionId,
          'TAB_SWITCH_DETECTED',
          'WARNING',
          `Candidate navigated away from the interview window (${eventDetail}).`
        );
        recordWarning({
          type: 'TAB_SWITCH_DETECTED',
          title: 'Browser Tab Switch Detected',
          message: 'Navigating away from the interview tab is an integrity violation. Please remain focused on this window.',
          serverCount: res?.warningCount
        });
        if (res?.isSessionExpired) {
          handleExpireSession(`Terminated: Maximum allowed integrity warnings (${MAX_INTEGRITY_WARNINGS} of ${MAX_INTEGRITY_WARNINGS}) exceeded.`);
        }
      } catch (e) {
        recordWarning({
          type: 'TAB_SWITCH_DETECTED',
          title: 'Browser Tab Switch Detected',
          message: 'Navigating away from the interview tab is an integrity violation. Please remain focused on this window.'
        });
      }
    };

    const onVisibilityChange = () => {
      if (document.hidden) {
        handleTabSwitch('Tab Hidden');
      }
    };

    const onWindowBlur = () => {
      handleTabSwitch('Window Lost Focus');
    };

    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('blur', onWindowBlur);

    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('blur', onWindowBlur);
    };
  }, [sessionId, isSessionExpired, isEnding, isInterviewCompleted]);

  // -------------------------------------------------------------------------
  // Periodic Person & Face Detection Sampling (800ms)
  // -------------------------------------------------------------------------
  const startPersonDetectionLoop = () => {
    if (personCheckIntervalRef.current) clearInterval(personCheckIntervalRef.current);

    personCheckIntervalRef.current = setInterval(async () => {
      if (isSessionExpired || isEnding || isInterviewCompleted) return;
      if (!videoRef.current || videoRef.current.readyState < 2 || isSendingFrameRef.current) return;

      try {
        const video = videoRef.current;
        const canvas = document.createElement('canvas');
        canvas.width = Math.min(640, video.videoWidth || 640);
        canvas.height = Math.min(480, video.videoHeight || 480);
        const ctx = canvas.getContext('2d');
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

        canvas.toBlob(async (blob) => {
          if (!blob || isSessionExpired || isEnding || isInterviewCompleted || isSendingFrameRef.current) return;
          isSendingFrameRef.current = true;
          try {
            const res = await sendMonitoringPersonFrame(sessionId, blob, null);
            if (res.success) {
              const detectedFaceCount = res.faceCount !== undefined ? res.faceCount : (res.personCount || 1);
              setPersonCount(detectedFaceCount);
              setFaceStatus(res.faceStatus || 'NORMAL');

              // Update face bounding boxes and redraw overlay
              facesListRef.current = res.faces || [];
              frameDimRef.current = {
                width: res.frameWidth || canvas.width,
                height: res.frameHeight || canvas.height
              };
              drawFaceBoundingBoxes();

              if (res.shouldAlert) {
                recordWarning({
                  type: res.alertType || 'ADDITIONAL_PERSON_DETECTED',
                  title: res.alertType === 'FACE_ABSENT' ? 'Face Absent / Not Visible' : 'Additional Person Detected',
                  message: res.alertMessage || `Integrity flag: ${detectedFaceCount} individuals in camera view.`,
                  serverCount: res.warningCount
                });
              }

              if (res.isSessionExpired) {
                handleExpireSession(`Terminated by proctoring: maximum warnings (${MAX_INTEGRITY_WARNINGS}) exceeded.`);
              }
            }
          } catch (e) {
          } finally {
            isSendingFrameRef.current = false;
          }
        }, 'image/jpeg', 0.70);
      } catch (err) {
        isSendingFrameRef.current = false;
      }
    }, 1000);
  };

  // -------------------------------------------------------------------------
  // Periodic Background Audio / VAD Sampling
  // -------------------------------------------------------------------------
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
      if (!isAudioMonitoringActiveRef.current || isSessionExpired || isEnding || isInterviewCompleted || isRecordingVoiceAnswerRef.current) {
        if (isAudioMonitoringActiveRef.current && !isSessionExpired && !isEnding && !isInterviewCompleted) {
          monitoringAudioSliceTimeoutRef.current = setTimeout(recordNextAudioSlice, 500);
        }
        return;
      }

      try {
        const recorder = new MediaRecorder(audioStream, options);
        monitoringAudioRecorderRef.current = recorder;
        const chunks = [];

        recorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) chunks.push(e.data);
        };

        recorder.onstop = async () => {
          if (chunks.length > 0 && !isMicMuted && !isSessionExpired && !isEnding && !isInterviewCompleted && !isSendingAudioRef.current) {
            const audioBlob = new Blob(chunks, { type: mimeType || 'audio/webm' });
            if (audioBlob.size > 1500) {
              isSendingAudioRef.current = true;
              try {
                // If candidate is actively recording an answer or audio volume is elevated, inform backend
                const candidateSpeakingNow = isRecordingVoiceAnswer || audioLevelRef.current > 18;
                const res = await sendMonitoringAudioChunk(sessionId, audioBlob, language, false, candidateSpeakingNow);
                if (res.success) {
                  if (res.is_speech_detected) {
                    if (res.classification === 'DUAL_SPEAKERS') {
                      setSpeechStatusText('Dual Simultaneous Voices Detected');
                    } else if (res.classification === 'CANDIDATE_ANSWERING') {
                      setSpeechStatusText('Candidate Speaking (Normal)');
                    } else if (res.transcription_snippet) {
                      setSpeechStatusText(`Possible Background Voice: "${res.transcription_snippet}"`);
                    } else {
                      setSpeechStatusText('Acoustic Vocal Energy Detected');
                    }
                  } else {
                    setSpeechStatusText('Acoustic Environment Normal');
                  }

                  if (res.should_alert) {
                    recordWarning({
                      type: 'POSSIBLE_BACKGROUND_SPEECH',
                      title: 'Possible Background Voice Detected',
                      message: res.transcription_snippet
                        ? `Acoustic utterance flagged: "${res.transcription_snippet}"`
                        : (res.alert_message || 'Suspicious additional speech detected in environment.'),
                      serverCount: res.warningCount
                    });
                  }

                  if (res.isSessionExpired) {
                    handleExpireSession(`Terminated by proctoring: maximum warnings (${MAX_INTEGRITY_WARNINGS}) exceeded.`);
                  }
                }
              } catch (e) {
              } finally {
                isSendingAudioRef.current = false;
              }
            }
          }

          if (isAudioMonitoringActiveRef.current && !isSessionExpired && !isEnding && !isInterviewCompleted) {
            monitoringAudioSliceTimeoutRef.current = setTimeout(recordNextAudioSlice, 400);
          }
        };

        recorder.start();
        monitoringAudioIntervalRef.current = setTimeout(() => {
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

  // -------------------------------------------------------------------------
  // Voice Answer Recording Controls
  // -------------------------------------------------------------------------
  const handleStartVoiceRecording = () => {
    if (!streamRef.current || isRecordingVoiceAnswer || isSessionExpired || isEnding) return;
    const audioTrack = streamRef.current.getAudioTracks()[0];
    if (!audioTrack) {
      alert('Microphone is not available for voice recording.');
      return;
    }

    stopSpeaking(); // stop any TTS prompt
    answerAudioChunksRef.current = [];
    setRecordedAudioBlob(null);

    try {
      const audioStream = new MediaStream([audioTrack]);
      const recorder = new MediaRecorder(audioStream);
      answerRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          answerAudioChunksRef.current.push(e.data);
        }
      };

      recorder.onstop = async () => {
        const blob = new Blob(answerAudioChunksRef.current, { type: 'audio/webm' });
        setRecordedAudioBlob(blob);
        if (blob.size > 1200) {
          try {
            const res = await transcribeAnswerAudio(blob, language);
            if (res && res.success && res.text) {
              setTypedAnswer((prev) => (prev ? `${prev} ${res.text}` : res.text).trim());
            }
          } catch (e) {
            console.warn('Voice answer transcription notice:', e);
          }
        }
      };

      recorder.start(100);
      setIsRecordingVoiceAnswer(true);
      isRecordingVoiceAnswerRef.current = true;
      setVoiceSecondsRecorded(0);

      if (voiceTimerRef.current) clearInterval(voiceTimerRef.current);
      voiceTimerRef.current = setInterval(() => {
        setVoiceSecondsRecorded((s) => s + 1);
      }, 1000);
    } catch (e) {
      console.error('Failed to start answer recorder:', e);
    }
  };

  const handleStopVoiceRecording = () => {
    if (!isRecordingVoiceAnswer) return;
    if (voiceTimerRef.current) {
      clearInterval(voiceTimerRef.current);
      voiceTimerRef.current = null;
    }
    setIsRecordingVoiceAnswer(false);
    isRecordingVoiceAnswerRef.current = false;
    if (answerRecorderRef.current && answerRecorderRef.current.state === 'recording') {
      try { answerRecorderRef.current.stop(); } catch {}
    }
  };

  // -------------------------------------------------------------------------
  // Submit Candidate Answer (Voice and/or Typed Text)
  // -------------------------------------------------------------------------
  const handleSubmitAnswer = async () => {
    if (isSubmittingAnswer || isSessionExpired || isEnding) return;

    // Check if voice or text was provided
    const hasVoice = recordedAudioBlob && recordedAudioBlob.size > 500;
    const hasText = typedAnswer.trim().length > 0;

    if (!hasVoice && !hasText) {
      alert('Please provide your answer either by recording voice audio or typing in the text box.');
      return;
    }

    setIsSubmittingAnswer(true);
    stopSpeaking();

    try {
      const res = await submitInterviewAnswer(
        sessionId,
        currentQuestionIndex,
        currentQuestion?.id || `q-${currentQuestionIndex}`,
        hasVoice ? recordedAudioBlob : null,
        typedAnswer.trim(),
        language
      );

      if (res && res.success) {
        setLastEvaluation(res.evaluation);
        setShowFeedbackModal(true);

        // If interview complete, prepare final completion state
        if (res.isCompleted) {
          setIsInterviewCompleted(true);
        }
      }
    } catch (err) {
      alert(err.message || 'Failed to submit answer. Please try again.');
    } finally {
      setIsSubmittingAnswer(false);
    }
  };

  // -------------------------------------------------------------------------
  // Advance to Next Question
  // -------------------------------------------------------------------------
  const handleProceedToNext = () => {
    setShowFeedbackModal(false);
    setLastEvaluation(null);
    setRecordedAudioBlob(null);
    setTypedAnswer('');
    setVoiceSecondsRecorded(0);

    const nextIndex = currentQuestionIndex + 1;
    if (nextIndex < questions.length) {
      setCurrentQuestionIndex(nextIndex);
      const nextQ = questions[nextIndex];
      setCurrentQuestion(nextQ);
      setQuestionSecondsLeft(nextQ.timeLimit || DEFAULT_QUESTION_TIME_LIMIT);

      // Speak next question
      setTimeout(() => {
        speakQuestion(nextQ.question, language);
      }, 500);
    } else {
      // All questions completed! Open comprehensive results report
      handleViewFinalResults();
    }
  };

  // -------------------------------------------------------------------------
  // Fetch Full Interview Results & Open Report Modal
  // -------------------------------------------------------------------------
  const handleViewFinalResults = async () => {
    setIsEnding(true);
    stopSpeaking();
    cleanupMonitoring();

    try {
      const resultsRes = await fetchInterviewResults(sessionId);
      if (resultsRes && resultsRes.report) {
        setReportData(resultsRes.report);
      } else {
        const fallback = await fetchSessionReport(sessionId);
        setReportData(fallback?.report || null);
      }
      setIsReportOpen(true);
    } catch (err) {
      console.warn('Failed to compile results report:', err);
      const fallback = await fetchSessionReport(sessionId);
      setReportData(fallback?.report || null);
      setIsReportOpen(true);
    } finally {
      setIsEnding(false);
    }
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

  const progressPercent = questions.length > 0
    ? Math.round(((currentQuestionIndex + (isInterviewCompleted ? 1 : 0)) / questions.length) * 100)
    : 0;

  return (
    <div className="interview-dashboard-wrap">
      {/* Top Header & Telemetry Bar */}
      <header className="dashboard-header-bar">
        <div className="header-brand-info">
          <div className="brand-pill">
            <span className="brand-dot-pulse"></span>
            <span className="brand-role-title">{interviewRole}</span>
          </div>
          <span className="header-divider">/</span>
          <span className="candidate-name-header">{candidateName}</span>
          {skills && (
            <span className="header-skills-chip" title={`Focus Skills: ${skills}`}>
              🎯 {skills.split(',')[0]}
            </span>
          )}
        </div>

        <div className="header-telemetry-controls">
          <div className="timer-badge-chip" title="Total Session Elapsed Time">
            <span className="timer-icon">⏱️</span>
            <span className="timer-value">{formatTimer(elapsedSeconds)}</span>
          </div>

          <div className="session-status-chip">
            <span className={`status-indicator-dot ${isSessionExpired ? 'dot-terminated' : isInterviewCompleted ? 'dot-completed' : 'dot-live'}`}></span>
            <span className="status-indicator-text">
              {isSessionExpired ? 'Session Expired' : isInterviewCompleted ? 'Interview Completed' : 'Interview In Progress'}
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

      {/* Main Grid: 2-Column Responsive Layout */}
      <div className="dashboard-main-grid">
        {/* Left Column: Interactive AI Interview Stage */}
        <section className="interview-question-stage">
          {/* Progress Tracker Bar */}
          <div className="question-progress-card">
            <div className="progress-meta-row">
              <span className="progress-step-text">
                Question {currentQuestionIndex + 1} of {questions.length || 5}
              </span>
              <span className="progress-percent-text">{progressPercent}% Completed</span>
            </div>
            <div className="progress-track-bg">
              <div
                className="progress-track-fill"
                style={{ width: `${Math.min(100, Math.max(5, progressPercent))}%` }}
              ></div>
            </div>
          </div>

          {isLoadingQuestions ? (
            <div className="loading-questions-card">
              <div className="card-spinner"></div>
              <h3>Formulating Role Questions with AI...</h3>
              <p>Analyzing technical track for {interviewRole} ({candidateData.experienceLevel || 'fresher'}).</p>
            </div>
          ) : currentQuestion ? (
            <div className="active-question-card">
              {/* Question Header */}
              <div className="question-card-header">
                <div className="q-category-pill">
                  <span className="category-icon">💡</span>
                  <span>{currentQuestion.category || 'Technical Evaluation'}</span>
                </div>

                <div className="q-header-actions">
                  {/* Countdown Timer */}
                  <div className={`countdown-chip ${questionSecondsLeft <= 20 ? 'chip-urgent' : ''}`}>
                    <span className="clock-icon">⏳</span>
                    <span>{formatTimer(questionSecondsLeft)}</span>
                  </div>

                  {/* Text-To-Speech Controls */}
                  {isAISpeaking ? (
                    <button
                      type="button"
                      className="btn-tts-action btn-tts-speaking"
                      onClick={stopSpeaking}
                      title="Pause speaking"
                    >
                      <span className="tts-pulse-waves">🔊</span>
                      <span>Stop Speech</span>
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn-tts-action"
                      onClick={() => speakQuestion(currentQuestion.question, language)}
                      title="Read question aloud using AI speech"
                    >
                      <span>🔊 Listen Aloud</span>
                    </button>
                  )}
                </div>
              </div>

              {/* The Actual Question Text */}
              <div className="question-prompt-box">
                <h2 className="question-text">{currentQuestion.question}</h2>
              </div>

              {/* Dual Answering Controls: Voice and Typed Text */}
              <div className="answering-controls-wrap">
                {/* Voice Input Section */}
                <div className="voice-input-card">
                  <div className="voice-card-header">
                    <span className="voice-title">🎙️ Spoken Voice Answer</span>
                    {isRecordingVoiceAnswer && (
                      <span className="recording-live-tag">
                        <span className="rec-dot-pulse"></span>
                        Recording: {formatTimer(voiceSecondsRecorded)}
                      </span>
                    )}
                  </div>

                  <div className="voice-buttons-row">
                    {!isRecordingVoiceAnswer ? (
                      <button
                        type="button"
                        className="btn-voice-record"
                        onClick={handleStartVoiceRecording}
                        disabled={isSubmittingAnswer || isSessionExpired}
                      >
                        <span className="btn-icon">🎙️</span>
                        <span>{recordedAudioBlob ? 'Re-record Voice Answer' : 'Start Voice Recording'}</span>
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn-voice-stop"
                        onClick={handleStopVoiceRecording}
                      >
                        <span className="btn-icon">⏹</span>
                        <span>Stop Voice Recording</span>
                      </button>
                    )}

                    {recordedAudioBlob && !isRecordingVoiceAnswer && (
                      <span className="voice-ready-badge">
                        ✓ Audio captured ({formatTimer(voiceSecondsRecorded)})
                      </span>
                    )}
                  </div>
                </div>

                {/* Typed Text Input Section */}
                <div className="typed-input-card">
                  <div className="typed-header">
                    <label htmlFor="typedAnswerBox" className="typed-label">
                      ✍️ Or Type Your Answer (or refine transcribed voice):
                    </label>
                    <span className="word-count-badge">
                      {typedAnswer.trim() ? `${typedAnswer.trim().split(/\s+/).length} words` : '0 words'}
                    </span>
                  </div>

                  <textarea
                    id="typedAnswerBox"
                    className="typed-textarea"
                    rows={4}
                    placeholder="Type your structured explanation, code logic, or architectural reasoning here..."
                    value={typedAnswer}
                    onChange={(e) => setTypedAnswer(e.target.value)}
                    disabled={isSubmittingAnswer || isSessionExpired}
                  />
                </div>

                {/* Submit Answer Action */}
                <div className="answer-submit-row">
                  <button
                    type="button"
                    className="btn-submit-answer"
                    onClick={handleSubmitAnswer}
                    disabled={
                      isSubmittingAnswer ||
                      isSessionExpired ||
                      (!recordedAudioBlob && !typedAnswer.trim())
                    }
                  >
                    {isSubmittingAnswer ? (
                      <span className="btn-spinner-content">
                        <span className="spinner-dot"></span>
                        <span>Evaluating Technical Accuracy...</span>
                      </span>
                    ) : (
                      <span>Submit Answer & View Evaluation &rarr;</span>
                    )}
                  </button>
                </div>
              </div>

              {/* Immediate Evaluation Feedback Card */}
              {showFeedbackModal && lastEvaluation && (
                <div className="answer-feedback-overlay-card">
                  <div className="feedback-card-header">
                    <div className="feedback-score-pill">
                      <span className="score-num">{lastEvaluation.score}</span>
                      <span className="score-max">/ 10</span>
                      <span className="score-pct">({lastEvaluation.percentage}%)</span>
                    </div>
                    <span className="feedback-title">Instant Rubric Assessment</span>
                  </div>

                  {lastEvaluation.strengths && lastEvaluation.strengths.length > 0 && (
                    <div className="feedback-section strengths-box">
                      <span className="box-title">✅ Demonstrated Strengths:</span>
                      <ul>
                        {lastEvaluation.strengths.map((str, idx) => (
                          <li key={idx}>{str}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {lastEvaluation.missingConcepts && lastEvaluation.missingConcepts.length > 0 && (
                    <div className="feedback-section concepts-box">
                      <span className="box-title">💡 Concepts to Include:</span>
                      <div className="concept-tags-wrap">
                        {lastEvaluation.missingConcepts.map((c, idx) => (
                          <span key={idx} className="concept-tag">{c}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {lastEvaluation.improvementSuggestions && lastEvaluation.improvementSuggestions.length > 0 && (
                    <div className="feedback-section improvements-box">
                      <span className="box-title">📈 Improvement Recommendations:</span>
                      <ul>
                        {lastEvaluation.improvementSuggestions.map((sug, idx) => (
                          <li key={idx}>{sug}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <div className="feedback-next-action-row">
                    <button
                      type="button"
                      className="btn-next-question"
                      onClick={handleProceedToNext}
                    >
                      {currentQuestionIndex + 1 < questions.length ? (
                        <span>Proceed to Next Question &rarr;</span>
                      ) : (
                        <span>Finish Interview & Generate Structured Report &rarr;</span>
                      )}
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="all-completed-card">
              <span className="completed-icon">🎉</span>
              <h2>All Questions Answered!</h2>
              <p>You have successfully answered all technical interview questions.</p>
              <button
                type="button"
                className="btn-primary-large"
                onClick={handleViewFinalResults}
              >
                Generate Final Structured Interview Report &rarr;
              </button>
            </div>
          )}
        </section>

        {/* Right Column: Live Camera, Face Detection & Proctoring Telemetry */}
        <aside className="dashboard-monitoring-sidebar">
          {/* Camera Viewport Card */}
          <div className="video-viewport-card">
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              onLoadedMetadata={(e) => e.target.play().catch(() => {})}
              className={`dashboard-video-element ${isVideoOff ? 'video-hidden' : ''}`}
            />

            {/* Overlaid Canvas: Rectangular bounding box ONLY when multiple faces detected */}
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

            {/* In-Video Status Overlays */}
            <div className="video-overlay-top-left">
              <span className="candidate-video-badge">{candidateName}</span>
              {audioLevel > 15 && !isMicMuted && (
                <span className="audio-active-badge">🎙️ Speaking</span>
              )}
            </div>

            <div className="video-overlay-top-right">
              {personCount === 1 ? (
                <span className="person-count-badge badge-ok">1 Face (Verified)</span>
              ) : personCount > 1 ? (
                <span className="person-count-badge badge-alert">
                  ⚠️ {personCount} Faces Detected (Alert)
                </span>
              ) : (
                <span className="person-count-badge badge-warning">
                  ⚠️ Face Not Visible
                </span>
              )}
            </div>

            {/* Face Absence Alert Banner if 0 faces */}
            {personCount === 0 && !isVideoOff && (
              <div className="face-absence-warning-overlay">
                <span>⚠️ Face not visible. Please look directly into the camera.</span>
              </div>
            )}

            {/* Camera / Mic Toolbar */}
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
                onClick={handleViewFinalResults}
                disabled={isEnding || isSessionExpired}
                title="Finish and submit interview session"
              >
                <span className="control-icon">⏹</span>
                <span className="control-label">{isEnding ? 'Compiling...' : 'Finish Interview'}</span>
              </button>
            </div>
          </div>

          {/* Monitoring Integrity Summary */}
          <div className="sidebar-card status-metrics-card">
            <h3 className="sidebar-card-title">Real-Time Telemetry</h3>
            <div className="integrity-status-rows">
              <div className="status-row">
                <span className="status-row-icon">👤</span>
                <div className="status-row-content">
                  <span className="status-row-label">Visual Surveillance</span>
                  <span className={`status-row-value ${personCount > 1 ? 'text-warn' : personCount === 0 ? 'text-danger' : 'text-ok'}`}>
                    {personCount === 1 ? 'Single candidate verified' : personCount > 1 ? `Multiple faces detected (${personCount})` : 'Face not visible'}
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

              <div className="status-row">
                <span className="status-row-icon">🖥️</span>
                <div className="status-row-content">
                  <span className="status-row-label">Browser Window</span>
                  <span className="status-row-value text-ok">Active & In Focus</span>
                </div>
              </div>
            </div>
          </div>

          {/* Unified Warnings History Panel */}
          <div className="sidebar-card alerts-panel-card">
            <div className="alerts-card-header">
              <h3 className="sidebar-card-title">Integrity Warnings</h3>
              <span className={`warning-counter-tag ${warningCount >= MAX_INTEGRITY_WARNINGS ? 'tag-danger' : warningCount > 0 ? 'tag-warning' : 'tag-neutral'}`}>
                {warningCount} / {MAX_INTEGRITY_WARNINGS} Warnings
              </span>
            </div>

            {isSessionExpired && (
              <div className="session-terminated-card" role="alert">
                <span className="terminated-icon">🚫</span>
                <h4 className="terminated-title">Interview Terminated</h4>
                <p className="terminated-sub">
                  The interview was terminated because the maximum allowed integrity warnings ({MAX_INTEGRITY_WARNINGS}) was exceeded.
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
                  <p>No integrity warnings.</p>
                  <span className="sub-hint">Session compliance is optimal.</span>
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

      {/* Comprehensive Final Session Report Modal */}
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
