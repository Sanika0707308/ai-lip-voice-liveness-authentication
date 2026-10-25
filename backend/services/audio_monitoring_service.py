import os
import time
import tempfile
import numpy as np
import soundfile as sf
from typing import Dict, Any, Optional, Tuple
from backend.config import settings
from backend.services.whisper_service import whisper_service
from backend.services.sync_service import _ensure_ffmpeg_in_path
import subprocess

_ensure_ffmpeg_in_path()

class AudioMonitoringService:
    """
    LIVESCAN 2.0 Audio Monitoring & Background Speech Detection Service.
    
    Analyzes short audio windows (1.0 - 3.0s) captured during active monitoring sessions.
    Distinguishes potential vocal activity from silence, stationary fan noise, and 
    impulsive mechanical sounds (typing, mouse clicks) using:
    - Root-Mean-Square (RMS) Energy thresholding
    - Zero-Crossing Rate (ZCR)
    - Speech-band (300Hz - 3400Hz) Spectral Energy Concentration
    - Crest Factor & Transient Analysis (to filter keyboard/mouse clicks)
    - Lightweight Faster-Whisper ASR confirmation for high-confidence speech segments
    """

    def __init__(self):
        # Per-session cooldown tracking: session_id -> float timestamp of last alert
        self._last_alert_times: Dict[str, float] = {}

    def reset_session(self, session_id: str):
        """Clears cooldown tracking for a session."""
        if session_id in self._last_alert_times:
            del self._last_alert_times[session_id]

    def _convert_audio_to_wav_pcm(self, input_filepath: str) -> Optional[str]:
        """
        Converts uploaded webm/ogg/mp4 audio chunks into 16kHz mono WAV PCM.
        """
        temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_wav.close()
        
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", input_filepath,
            "-ac", "1",
            "-ar", "16000",
            "-vn",
            temp_wav.name
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=5)
            return temp_wav.name
        except Exception:
            if os.path.exists(temp_wav.name):
                try:
                    os.unlink(temp_wav.name)
                except OSError:
                    pass
            return None

    @staticmethod
    def estimate_pitch_tracks(samples: np.ndarray, sample_rate: int = 16000) -> Tuple[float, bool, list]:
        """
        Estimates fundamental frequency (F0 in Hz) and detects multiple concurrent
        speakers via autocorrelation peak analysis in the human pitch band (75 Hz - 420 Hz).
        """
        if len(samples) < int(sample_rate * 0.2):
            return 0.0, False, []

        # Analyze a representative 1.0s center window
        window_len = min(len(samples), int(sample_rate * 1.0))
        start_idx = (len(samples) - window_len) // 2
        frame = samples[start_idx : start_idx + window_len].astype(np.float32)
        frame -= np.mean(frame)
        if np.max(np.abs(frame)) < 1e-4:
            return 0.0, False, []

        # Autocorrelation via FFT
        n = len(frame)
        n_fft = 2 ** int(np.ceil(np.log2(2 * n - 1)))
        fx = np.fft.rfft(frame, n=n_fft)
        r = np.fft.irfft(fx * np.conj(fx), n=n_fft)[:n]
        if r[0] <= 1e-9:
            return 0.0, False, []
        r_norm = r / r[0]

        # Lags corresponding to 75 Hz - 420 Hz
        min_lag = max(1, int(sample_rate / 420))
        max_lag = min(len(r_norm) - 1, int(sample_rate / 75))
        if max_lag <= min_lag:
            return 0.0, False, []

        search_region = r_norm[min_lag:max_lag]
        peaks = []
        for i in range(1, len(search_region) - 1):
            if search_region[i] > search_region[i - 1] and search_region[i] > search_region[i + 1] and search_region[i] > 0.25:
                lag = min_lag + i
                f0 = sample_rate / lag
                peaks.append((f0, float(search_region[i])))

        peaks.sort(key=lambda p: p[1], reverse=True)
        if not peaks:
            return 0.0, False, []

        primary_f0 = round(peaks[0][0], 1)
        dual_speakers = False
        secondary_f0s = []

        # Check for secondary distinct pitch peaks
        for f0, strength in peaks[1:]:
            ratio = f0 / primary_f0 if primary_f0 > 0 else 1.0
            # If not an octave harmonic (within 12% of 2.0, 0.5, or 1.0) and has notable strength
            is_octave = (0.88 <= ratio <= 1.12) or (1.85 <= ratio <= 2.15) or (0.45 <= ratio <= 0.55)
            if not is_octave and strength >= 0.30:
                dual_speakers = True
                secondary_f0s.append(round(f0, 1))

        return primary_f0, dual_speakers, secondary_f0s

    def analyze_audio_features(self, samples: np.ndarray, sample_rate: int = 16000) -> Dict[str, Any]:
        """
        Extracts acoustic features from normalized 1D audio samples.
        """
        if len(samples) == 0:
            return {
                "classification": "SILENCE",
                "is_speech": False,
                "confidence": 0.0,
                "rms": 0.0,
                "zcr": 0.0,
                "speech_band_ratio": 0.0,
                "primary_f0": 0.0,
                "dual_speakers": False,
                "reason": "Empty audio buffer"
            }

        # 1. RMS Energy
        rms = float(np.sqrt(np.mean(samples ** 2)))
        peak = float(np.max(np.abs(samples)))
        crest_factor = (peak / rms) if rms > 1e-6 else 1.0

        # Silence check
        energy_thresh = settings.VAD_ENERGY_THRESHOLD
        if rms < energy_thresh:
            return {
                "classification": "SILENCE",
                "is_speech": False,
                "confidence": 0.0,
                "rms": round(rms, 5),
                "zcr": 0.0,
                "speech_band_ratio": 0.0,
                "crest_factor": round(crest_factor, 2),
                "primary_f0": 0.0,
                "dual_speakers": False,
                "reason": f"Signal energy ({rms:.4f}) is below ambient silence threshold ({energy_thresh:.4f})."
            }

        # 2. Zero Crossing Rate (ZCR)
        zero_crossings = np.sum(np.abs(np.diff(samples > 0)))
        zcr = float(zero_crossings) / float(len(samples))

        # 3. FFT Spectral Band Energy (300 Hz - 3400 Hz human speech band)
        fft_vals = np.abs(np.fft.rfft(samples))
        freqs = np.fft.rfftfreq(len(samples), 1.0 / sample_rate)
        total_energy = float(np.sum(fft_vals ** 2))

        if total_energy > 1e-9:
            speech_mask = (freqs >= 300) & (freqs <= 3400)
            low_noise_mask = (freqs < 250)
            speech_energy = float(np.sum(fft_vals[speech_mask] ** 2))
            low_energy = float(np.sum(fft_vals[low_noise_mask] ** 2))
            speech_band_ratio = speech_energy / total_energy
            low_noise_ratio = low_energy / total_energy
        else:
            speech_band_ratio = 0.0
            low_noise_ratio = 0.0

        # 4. Impulsive noise filtering (typing clicks, sharp bumps)
        is_impulsive = crest_factor > 6.0 and zcr > 0.20

        # 5. Continuous fan/AC filtering (stationary low-frequency hum)
        is_fan_hum = low_noise_ratio > 0.65 and speech_band_ratio < 0.25

        if is_impulsive:
            return {
                "classification": "MECHANICAL_NOISE",
                "is_speech": False,
                "confidence": 0.15,
                "rms": round(rms, 5),
                "zcr": round(zcr, 4),
                "speech_band_ratio": round(speech_band_ratio, 4),
                "crest_factor": round(crest_factor, 2),
                "primary_f0": 0.0,
                "dual_speakers": False,
                "reason": "Acoustic signature resembles mechanical impulse (e.g. keyboard typing or mouse click)."
            }

        if is_fan_hum:
            return {
                "classification": "BACKGROUND_HUM",
                "is_speech": False,
                "confidence": 0.10,
                "rms": round(rms, 5),
                "zcr": round(zcr, 4),
                "speech_band_ratio": round(speech_band_ratio, 4),
                "crest_factor": round(crest_factor, 2),
                "primary_f0": 0.0,
                "dual_speakers": False,
                "reason": "Low-frequency stationary background hum (e.g. fan or air conditioning)."
            }

        # 6. Fundamental frequency & multi-speaker analysis
        primary_f0, dual_speakers, secondary_f0s = self.estimate_pitch_tracks(samples, sample_rate)

        # 7. Speech heuristic score
        zcr_speech_fit = 1.0 - min(1.0, abs(zcr - 0.09) / 0.09)
        speech_prob = 0.5 * speech_band_ratio + 0.3 * zcr_speech_fit + 0.2 * min(1.0, rms / 0.08)
        speech_prob = float(min(1.0, max(0.0, speech_prob)))

        is_speech = speech_prob >= settings.VAD_CONFIDENCE_THRESHOLD and speech_band_ratio > 0.30

        return {
            "classification": "POSSIBLE_SPEECH" if is_speech else "BACKGROUND_NOISE",
            "is_speech": is_speech,
            "confidence": round(speech_prob, 3),
            "rms": round(rms, 5),
            "zcr": round(zcr, 4),
            "speech_band_ratio": round(speech_band_ratio, 4),
            "crest_factor": round(crest_factor, 2),
            "primary_f0": primary_f0,
            "dual_speakers": dual_speakers,
            "secondary_f0s": secondary_f0s,
            "reason": (
                "Dual vocal pitch tracks detected in acoustic window." if (is_speech and dual_speakers)
                else "Vocal tract formant frequencies and harmonic energy detected in human speech band." if is_speech
                else "Acoustic energy does not match speech characteristics."
            )
        }

    def process_audio_chunk(
        self, 
        audio_file_path: str, 
        session_id: str, 
        language: str = "en",
        verify_with_whisper: bool = True,
        candidate_is_speaking: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        Analyzes an audio file or chunk for background speech.
        
        Args:
            audio_file_path: Path to temporary audio chunk (WAV/WEBM/OGG).
            session_id: Active monitoring session identifier.
            language: Expected language ("en", "hi", "mr").
            verify_with_whisper: If True and acoustic features indicate speech,
                                passes segment to Whisper to confirm word presence.
            candidate_is_speaking: Optional boolean flag indicating if candidate's
                                   microphone/lip volume indicates candidate answering.
        """
        wav_path = None
        now = time.time()
        cooldown_sec = settings.VAD_SPEECH_COOLDOWN_SEC

        try:
            # If not a standard WAV, convert using ffmpeg
            if not audio_file_path.lower().endswith(".wav"):
                wav_path = self._convert_audio_to_wav_pcm(audio_file_path)
                target_path = wav_path or audio_file_path
            else:
                target_path = audio_file_path

            # Load audio samples via soundfile
            data, sr = sf.read(target_path, dtype='float32')
            if len(data.shape) > 1:
                data = np.mean(data, axis=1)

            feature_result = self.analyze_audio_features(data, sample_rate=sr)
            is_speech = feature_result["is_speech"]
            confidence = feature_result["confidence"]
            dual_speakers = feature_result.get("dual_speakers", False)
            transcription = ""

            # If acoustic features indicate speech and Whisper verification is requested,
            # confirm with Whisper
            if is_speech and verify_with_whisper and len(data) >= sr * 0.5:
                try:
                    whisper_res = whisper_service.transcribe_audio(target_path, language=language)
                    transcription = (whisper_res.get("text") or whisper_res.get("transcription") or "").strip()
                    whisper_conf = whisper_res.get("confidence", 0.0)
                    
                    if transcription and len(transcription) > 1:
                        # Genuine words/speech detected
                        confidence = round(max(confidence, float(whisper_conf)), 3)
                        is_speech = True
                    else:
                        # Whisper found no actual speech tokens -> adjust down to avoid false alarms
                        confidence = round(confidence * 0.75, 3)
                        if confidence < settings.VAD_CONFIDENCE_THRESHOLD:
                            is_speech = False
                            feature_result["classification"] = "BACKGROUND_NOISE"
                except Exception:
                    # Non-blocking fallback: retain acoustic feature evaluation
                    pass

            # Classification & candidate-answering discrimination
            classification = feature_result["classification"]
            alert_reason = feature_result["reason"]

            if is_speech:
                if dual_speakers:
                    classification = "DUAL_SPEAKERS"
                    alert_reason = "Multiple distinct vocal pitch tracks detected in acoustic window (two concurrent speakers)."
                elif candidate_is_speaking is True:
                    # Candidate is answering their interview questions normally
                    classification = "CANDIDATE_ANSWERING"
                    alert_reason = "Candidate vocal response matching normal single-speaker acoustic pattern."
                elif candidate_is_speaking is False:
                    # Candidate mouth is stationary but speech was captured in the room
                    classification = "BACKGROUND_SPEECH"
                    alert_reason = "Vocal activity detected in acoustic environment while candidate is not speaking."

            # Check cooldown deduplication
            last_alert = self._last_alert_times.get(session_id, 0.0)
            in_cooldown = (now - last_alert) < cooldown_sec

            # Alert criteria:
            # - If dual speakers detected: always trigger alert (two people speaking)
            # - If background speech (candidate is not speaking): trigger alert
            # - If candidate is answering alone: do NOT trigger alert
            # - If candidate_is_speaking is None (test / unflagged): trigger alert if speech detected
            if is_speech:
                if dual_speakers:
                    should_alert = not in_cooldown
                elif candidate_is_speaking is True:
                    should_alert = False  # Suppress false alert for candidate's own answers
                else:
                    should_alert = not in_cooldown
            else:
                should_alert = False

            if should_alert:
                self._last_alert_times[session_id] = now

            iso_timestamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))
            time_display = time.strftime('%H:%M:%S', time.localtime(now))

            return {
                "success": True,
                "sessionId": session_id,
                "timestamp": iso_timestamp,
                "timeDisplay": time_display,
                "is_speech_detected": is_speech,
                "should_alert": should_alert,
                "in_cooldown": in_cooldown,
                "confidence": confidence,
                "classification": classification,
                "dual_speakers_detected": dual_speakers,
                "acoustic_metrics": {
                    "rms": feature_result["rms"],
                    "zcr": feature_result["zcr"],
                    "speech_band_ratio": feature_result["speech_band_ratio"],
                    "crest_factor": feature_result.get("crest_factor", 1.0),
                    "primary_f0": feature_result.get("primary_f0", 0.0)
                },
                "transcription_snippet": transcription if is_speech else "",
                "alert_message": "Possible Background Speech Detected" if should_alert else None,
                "reason": alert_reason,
                "disclaimer": (
                    "Possible background speech detected. This metric flags vocal activity in the acoustic "
                    "environment for interview review and does not assert identity or prove interview compromise."
                )
            }

        except Exception as err:
            return {
                "success": False,
                "sessionId": session_id,
                "is_speech_detected": False,
                "should_alert": False,
                "confidence": 0.0,
                "classification": "PROCESSING_ERROR",
                "error": str(err)
            }
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

audio_monitoring_service = AudioMonitoringService()
