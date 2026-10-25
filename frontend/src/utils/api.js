/**
 * Sends recorded audio to backend transcription & verification service.
 * Tracks upload progress via callback, and supports AbortController cancellation.
 * 
 * @param {Blob} audioBlob Audio recording blob
 * @param {string} challengePhrase Active phrase
 * @param {string} sessionId Active session ID
 * @param {Function} onProgress Callback containing progress and current stage
 * @param {AbortSignal} abortSignal Abort controller signal for cancellation
 * @returns {Promise<Object>} Backend response JSON
 */
export const uploadAudioForVerification = (audioBlob, challengePhrase, sessionId, onProgress, abortSignal, language = 'en') => {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/transcribe');
    
    if (abortSignal) {
      abortSignal.addEventListener('abort', () => {
        xhr.abort();
        reject(new DOMException('Aborted', 'AbortError'));
      });
      if (abortSignal.aborted) {
        xhr.abort();
        reject(new DOMException('Aborted', 'AbortError'));
        return;
      }
    }
    
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const percent = Math.round((event.loaded / event.total) * 100);
        onProgress({ stage: 'UPLOADING', progress: percent });
      }
    };
    
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const response = JSON.parse(xhr.responseText);
          resolve(response);
        } catch (e) {
          reject(new Error('Failed to parse server response.'));
        }
      } else {
        try {
          const errData = JSON.parse(xhr.responseText);
          const msg = errData.detail || `Server error: status ${xhr.status}`;
          const error = new Error(msg);
          error.status = xhr.status;
          reject(error);
        } catch (e) {
          const error = new Error(`Server returned error status ${xhr.status}`);
          error.status = xhr.status;
          reject(error);
        }
      }
    };
    
    xhr.onerror = () => {
      reject(new Error('Network connection failed or backend is unavailable.'));
    };
    
    xhr.ontimeout = () => {
      reject(new Error('Transcription request timed out.'));
    };
    
    const formData = new FormData();
    formData.append('file', audioBlob, 'recording.webm');
    formData.append('challengePhrase', challengePhrase || '');
    formData.append('sessionId', sessionId || '');
    formData.append('language', language || 'en');
    
    xhr.send(formData);
  });
};

/**
 * Resets session attempts counter on the backend.
 * 
 * @param {string} sessionId Session ID to clear
 * @returns {Promise<Object>} Server response JSON
 */
export const resetSessionAttempts = async (sessionId) => {
  if (!sessionId) return;
  const formData = new FormData();
  formData.append('sessionId', sessionId);
  
  const response = await fetch('/api/transcribe/reset', {
    method: 'POST',
    body: formData
  });
  
  if (!response.ok) {
    throw new Error(`Failed to reset attempts: ${response.statusText}`);
  }
  return response.json();
};

/**
 * Fetches a dynamically generated challenge phrase from the backend.
 * 
 * @param {string} sessionId Active session ID
 * @param {string} language Selected language code ('en' | 'hi' | 'mr')
 * @returns {Promise<Object>} Server response JSON containing the challengePhrase and language
 */
export const fetchNewChallengePhrase = async (sessionId, language = 'en') => {
  if (!sessionId) return;
  const langParam = encodeURIComponent(language || 'en');
  const response = await fetch(`/api/transcribe/challenge/new?sessionId=${encodeURIComponent(sessionId)}&language=${langParam}`);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch challenge phrase: ${response.statusText}`);
  }
  return response.json();
};

/**
 * Sends recorded audio along with lip movement telemetry to backend sync correlation service.
 * Supports AbortController cancellation.
 * 
 * @param {Blob} audioBlob Audio recording blob
 * @param {Array<number>} lipMovement Ratios of lip opening
 * @param {Array<number>} lipTimestamps Timestamps of lip frames relative to recording start
 * @param {string} sessionId Active session ID
 * @param {AbortSignal} abortSignal Abort controller signal for cancellation
 * @param {string} challengePhrase Active challenge phrase
 * @param {string} userId User profile ID
 * @param {string} language Selected language code ('en' | 'hi' | 'mr')
 * @returns {Promise<Object>} Backend response JSON
 */
export const uploadAudioForSync = async (
  audioBlob,
  lipMovement,
  lipTimestamps,
  sessionId,
  abortSignalOrChallenge = '',
  challengePhraseOrUserId = '',
  userIdOrLanguage = '',
  languageOrAttemptId = 'en',
  attemptIdArg = ''
) => {
  let signal = null;
  let challengePhrase = '';
  let userId = '';
  let language = 'en';
  let attemptId = '';

  // Robust argument resolution supporting both 8-param and 9-param signatures:
  // Signature A: (audioBlob, lipMovement, lipTimestamps, sessionId, abortSignalOrNull, challengePhrase, userId, language, attemptId)
  // Signature B: (audioBlob, lipMovement, lipTimestamps, sessionId, challengePhrase, userId, language, attemptId)
  const isSignalOrNull = abortSignalOrChallenge === null || 
                         abortSignalOrChallenge === undefined || 
                         (typeof abortSignalOrChallenge === 'object' && ('aborted' in abortSignalOrChallenge || (typeof AbortSignal !== 'undefined' && abortSignalOrChallenge instanceof AbortSignal)));

  if (isSignalOrNull) {
    signal = (abortSignalOrChallenge && typeof abortSignalOrChallenge === 'object') ? abortSignalOrChallenge : null;
    challengePhrase = challengePhraseOrUserId || '';
    userId = userIdOrLanguage || '';
    language = languageOrAttemptId || 'en';
    attemptId = attemptIdArg || '';
  } else {
    // 5th param is challengePhrase directly
    signal = null;
    challengePhrase = (typeof abortSignalOrChallenge === 'string') ? abortSignalOrChallenge : '';
    userId = challengePhraseOrUserId || '';
    language = userIdOrLanguage || 'en';
    attemptId = languageOrAttemptId || '';
  }

  // Ensure language is a valid normalized code
  const candidateLangs = [language, languageOrAttemptId, userIdOrLanguage].filter(Boolean);
  const matchedLang = candidateLangs.find(l => ['en', 'hi', 'mr'].includes(String(l).toLowerCase()));
  language = matchedLang ? matchedLang.toLowerCase() : (['hi', 'mr'].includes(language) ? language : 'en');

  const formData = new FormData();
  formData.append('file', audioBlob, 'recording.webm');
  formData.append('lipMovement', JSON.stringify(lipMovement));
  formData.append('lipTimestamps', JSON.stringify(lipTimestamps));
  formData.append('sessionId', sessionId || '');
  if (challengePhrase) {
    formData.append('challengePhrase', challengePhrase);
  }
  if (userId) {
    formData.append('userId', userId);
  }
  formData.append('language', language || 'en');
  if (attemptId) {
    formData.append('attemptId', attemptId);
  }

  const response = await fetch('/api/sync', {
    method: 'POST',
    body: formData,
    signal: signal
  });

  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    const msg = errData.error?.message || errData.detail || `Server error: status ${response.status}`;
    const error = new Error(msg);
    error.status = response.status;
    error.code = errData.error?.code;
    throw error;
  }

  return response.json();
};

/**
 * Fetches the current per-user synchronization calibration profile.
 *
 * @param {string} userId User profile identifier
 * @returns {Promise<Object>} Calibration status payload
 */
export const fetchCalibrationStatus = async (userId) => {
  if (!userId) return null;
  const response = await fetch(`/api/calibration/status?userId=${encodeURIComponent(userId)}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch calibration status: ${response.statusText}`);
  }
  return response.json();
};

/**
 * Starts or resets a per-user synchronization calibration profile.
 *
 * @param {string} userId User profile identifier
 * @param {string} sessionId Optional session ID
 * @param {number} requiredSamples Number of required valid samples (default 3)
 * @returns {Promise<Object>} Reset calibration profile payload
 */
export const startNewCalibration = async (userId, sessionId = '', requiredSamples = 3) => {
  const formData = new FormData();
  formData.append('userId', userId || '');
  if (sessionId) {
    formData.append('sessionId', sessionId);
  }
  if (requiredSamples) {
    formData.append('requiredSamples', String(requiredSamples));
  }

  const response = await fetch('/api/calibration/start', {
    method: 'POST',
    body: formData
  });

  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.error?.message || `Failed to start calibration: ${response.statusText}`);
  }
  return response.json();
};

/**
 * Submits a single recorded sample for per-user adaptive threshold calibration.
 */
export const uploadCalibrationSample = async (
  audioBlob,
  lipMovement,
  lipTimestamps,
  sessionId,
  userId,
  challengePhrase = '',
  abortSignal = null,
  requiredSamples = 3,
  language = 'en'
) => {
  const formData = new FormData();
  formData.append('file', audioBlob, 'recording.webm');
  formData.append('lipMovement', JSON.stringify(lipMovement));
  formData.append('lipTimestamps', JSON.stringify(lipTimestamps));
  formData.append('sessionId', sessionId || '');
  formData.append('userId', userId || sessionId || '');
  if (challengePhrase) {
    formData.append('challengePhrase', challengePhrase);
  }
  if (requiredSamples) {
    formData.append('requiredSamples', String(requiredSamples));
  }
  formData.append('language', language || 'en');

  const response = await fetch('/api/calibration/sample', {
    method: 'POST',
    body: formData,
    signal: abortSignal
  });

  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    const msg = errData.error?.message || errData.detail || `Server error: status ${response.status}`;
    const error = new Error(msg);
    error.status = response.status;
    error.code = errData.error?.code;
    throw error;
  }

  return response.json();
};

/**
 * ============================================================================
 * LIVESCAN 2.0 Monitoring and Proctoring APIs
 * ============================================================================
 */

export const startMonitoringSession = async (sessionId, candidateId = '', examId = '', language = 'en') => {
  const formData = new FormData();
  formData.append('sessionId', sessionId);
  if (candidateId) formData.append('candidateId', candidateId);
  if (examId) formData.append('examId', examId);
  formData.append('language', language || 'en');

  const response = await fetch('/api/monitoring/session/start', {
    method: 'POST',
    body: formData
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to start monitoring session.');
  }
  return response.json();
};

export const stopMonitoringSession = async (sessionId, reason = 'User completed monitoring session') => {
  const formData = new FormData();
  formData.append('sessionId', sessionId);
  formData.append('reason', reason);

  const response = await fetch('/api/monitoring/session/stop', {
    method: 'POST',
    body: formData
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to stop monitoring session.');
  }
  return response.json();
};

export const fetchSessionReport = async (sessionId) => {
  if (!sessionId) return null;
  const response = await fetch(`/api/monitoring/session/${encodeURIComponent(sessionId)}/report`);
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to fetch session report.');
  }
  return response.json();
};

export const fetchMonitoringSessions = async (limit = 25) => {
  const response = await fetch(`/api/monitoring/sessions?limit=${limit}`);
  if (!response.ok) {
    throw new Error('Failed to fetch monitoring sessions history.');
  }
  return response.json();
};

export const logMonitoringEvent = async (sessionId, eventType, severity = 'INFO', details = '', metadata = null) => {
  const formData = new FormData();
  formData.append('sessionId', sessionId);
  formData.append('eventType', eventType);
  formData.append('severity', severity);
  formData.append('details', details);
  if (metadata) {
    formData.append('metadata', typeof metadata === 'string' ? metadata : JSON.stringify(metadata));
  }

  const response = await fetch('/api/monitoring/event', {
    method: 'POST',
    body: formData
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to log monitoring event.');
  }
  return response.json();
};

export const sendMonitoringPersonFrame = async (sessionId, imageBlob = null, clientPersonCount = null) => {
  const formData = new FormData();
  formData.append('sessionId', sessionId);
  if (imageBlob) {
    formData.append('file', imageBlob, 'frame.jpg');
  }
  if (clientPersonCount !== null && clientPersonCount !== undefined) {
    formData.append('clientPersonCount', String(clientPersonCount));
  }

  const response = await fetch('/api/monitoring/detect-persons', {
    method: 'POST',
    body: formData
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Person detection analysis failed.');
  }
  return response.json();
};

export const sendMonitoringAudioChunk = async (sessionId, audioBlob, language = 'en', verifyWithWhisper = true, candidateSpeaking = null) => {
  const formData = new FormData();
  formData.append('sessionId', sessionId);
  formData.append('file', audioBlob, 'audio_chunk.webm');
  formData.append('language', language || 'en');
  formData.append('verifyWithWhisper', verifyWithWhisper ? 'true' : 'false');
  if (candidateSpeaking !== null && candidateSpeaking !== undefined) {
    formData.append('candidateSpeaking', candidateSpeaking ? 'true' : 'false');
  }

  const response = await fetch('/api/monitoring/audio-chunk', {
    method: 'POST',
    body: formData
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Audio chunk analysis failed.');
  }
  return response.json();
};

export const fetchSessionStatus = async (sessionId) => {
  if (!sessionId) return null;
  const response = await fetch(`/api/monitoring/session/${encodeURIComponent(sessionId)}/status`);
  if (!response.ok) {
    throw new Error('Failed to fetch session status.');
  }
  return response.json();
};


