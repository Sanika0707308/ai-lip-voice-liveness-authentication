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
export const uploadAudioForSync = async (audioBlob, lipMovement, lipTimestamps, sessionId, abortSignal, challengePhrase = '', userId = '', language = 'en') => {
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

  const response = await fetch('/api/sync', {
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

