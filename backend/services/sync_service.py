import os
import tempfile
import subprocess
import numpy as np
import soundfile as sf
from typing import Dict, Any
from backend.config import settings
from backend.services.lip_service import lip_service

class AudioProcessingError(Exception):
    """Exception raised when audio processing or conversion fails."""
    pass

# Self-healing PATH addition for Gyan.FFmpeg installed via winget on Windows
def _ensure_ffmpeg_in_path():
    ffmpeg_in_path = False
    path_sep = ";" if os.name == "nt" else ":"
    for path in os.environ.get("PATH", "").split(path_sep):
        if not path:
            continue
        for ext in ["", ".exe"]:
            if os.path.isfile(os.path.join(path, f"ffmpeg{ext}")):
                ffmpeg_in_path = True
                break
        if ffmpeg_in_path:
            break
            
    if not ffmpeg_in_path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            winget_packages_base = os.path.join(local_app_data, "Microsoft", "WinGet", "Packages")
            if os.path.exists(winget_packages_base):
                import glob
                candidates = glob.glob(os.path.join(winget_packages_base, "Gyan.FFmpeg.Essentials_*", "ffmpeg-*", "bin"))
                if candidates:
                    os.environ["PATH"] = os.environ.get("PATH", "") + path_sep + candidates[0]

_ensure_ffmpeg_in_path()

class SyncService:
    """
    Multimodal fusion service that correlates visual lip aperture variations 
    with voice energy levels (RMS envelope) over time.
    """
    
    def __init__(self):
        pass

    def smooth_signal(self, values: np.ndarray, window_size: int) -> np.ndarray:
        """
        Applies a centered moving average filter to smooth the signal values.
        """
        if len(values) == 0:
            return values
        smoothed = np.zeros_like(values, dtype=float)
        half = window_size // 2
        n = len(values)
        for i in range(n):
            start = max(0, i - half)
            end = min(n, i + half + 1)
            smoothed[i] = np.mean(values[start:end])
        return smoothed

    def normalize_signal(self, values: np.ndarray) -> np.ndarray:
        """
        Min-Max normalizes signal values to the range [0, 1].
        """
        if len(values) == 0:
            return values
        amin, amax = np.min(values), np.max(values)
        if amax - amin == 0:
            return np.zeros_like(values)
        return (values - amin) / (amax - amin)

    def calculate_audio_energy(self, audio_data: np.ndarray, samplerate: int, lip_timestamps: list) -> np.ndarray:
        """
        Extracts Root-Mean-Square (RMS) envelope values aligned with visual timestamps,
        integrating across dynamic inter-frame intervals so no speech audio is omitted.
        """
        energy_envelope = []
        n_samples = len(audio_data)
        n_frames = len(lip_timestamps)
        if n_samples == 0 or n_frames == 0:
            return np.zeros(n_frames, dtype=float)

        for i, t_ms in enumerate(lip_timestamps):
            if i == 0:
                dt_ms = (lip_timestamps[1] - lip_timestamps[0]) if n_frames > 1 else 33.33
            else:
                dt_ms = max(15.0, float(lip_timestamps[i] - lip_timestamps[i - 1]))
            win_sec = max(0.030, dt_ms / 1000.0)
            half_win = int((win_sec / 2.0) * samplerate)
            center_idx = int((t_ms / 1000.0) * samplerate)
            start_idx = max(0, center_idx - half_win)
            end_idx = min(n_samples, center_idx + half_win)
            
            window = audio_data[start_idx:end_idx]
            if len(window) > 0:
                rms = float(np.sqrt(np.mean(window ** 2)))
            else:
                rms = 0.0
            energy_envelope.append(rms)
            
        return np.array(energy_envelope)

    def cross_correlation_with_lag(
        self,
        x: np.ndarray,
        y: np.ndarray,
        max_lag: int,
        speech_mask: np.ndarray = None,
        avg_dt: float = 33.33,
        max_allowed_offset_ms: float = 500.0,
        effective_threshold: float = 0.45
    ) -> tuple:
        """
        Searches for the optimal cross-correlation alignment over the permissible sync window (<= 500ms),
        evaluating speech-segment correlation, continuous speech window correlation, motion velocity correlation,
        and full-window correlation.
        Returns (rawCorrelation, alignedCorrelation, bestLagIndices).
        """
        n = len(x)
        if n < 2:
            return 0.0, 0.0, 0

        # Determine speech mask on normalized scale if not provided as boolean array
        if speech_mask is None or len(speech_mask) != n:
            valid_mask = y >= 0.15
        elif isinstance(speech_mask, np.ndarray) and speech_mask.dtype == bool:
            valid_mask = speech_mask
        else:
            valid_mask = np.array(speech_mask, dtype=bool)

        def _calc_corr(a: np.ndarray, b: np.ndarray) -> float:
            if len(a) < 4:
                return 0.0
            sa, sb = float(np.std(a)), float(np.std(b))
            if sa > 1e-6 and sb > 1e-6:
                val = float(np.corrcoef(a, b)[0, 1])
                return max(0.0, val) if not np.isnan(val) else 0.0
            return 0.0

        def _eval_alignment(sub_x: np.ndarray, sub_y: np.ndarray, sub_mask: np.ndarray, min_frames: int) -> float:
            if len(sub_x) < min_frames:
                return 0.0
            cf = _calc_corr(sub_x, sub_y)
            cs = 0.0
            if np.sum(sub_mask) >= max(4, min_frames // 2):
                cs = _calc_corr(sub_x[sub_mask], sub_y[sub_mask])
            cw = 0.0
            speech_idx = np.where(sub_mask)[0]
            if len(speech_idx) >= max(4, min_frames // 2):
                ws = max(0, int(speech_idx[0]) - 4)
                we = min(len(sub_x), int(speech_idx[-1]) + 5)
                if we - ws >= 4:
                    cw = _calc_corr(sub_x[ws:we], sub_y[ws:we])
            cm = 0.0
            if len(sub_x) >= 6:
                cm = _calc_corr(np.abs(np.diff(sub_x)), np.abs(np.diff(sub_y)))
            return float(max(cf, cs, cw, cm))

        # Minimum overlapping frames required to ensure statistical significance
        min_overlap = max(settings.MIN_VALID_SYNC_FRAMES // 2, min(12, n // 3))
        max_allowed_lag = int(round(max_allowed_offset_ms / avg_dt)) if avg_dt > 0 else max_lag
        search_lag = min(max_lag, max_allowed_lag)
        search_lag = max(1, search_lag)

        # Calculate raw correlation at lag = 0
        raw_corr = _eval_alignment(x, y, valid_mask, min_overlap)
        best_corr = raw_corr
        best_lag = 0

        # Search optimal alignment within the allowable window
        for lag in range(-search_lag, search_lag + 1):
            if lag == 0:
                continue

            if lag > 0:
                sub_x = x[lag:]
                sub_y = y[:-lag]
                sub_mask = valid_mask[:-lag]
            else:
                abs_lag = abs(lag)
                sub_x = x[:-abs_lag]
                sub_y = y[abs_lag:]
                sub_mask = valid_mask[abs_lag:]

            corr = _eval_alignment(sub_x, sub_y, sub_mask, min_overlap)
            if corr > best_corr:
                best_corr = corr
                best_lag = lag

        return float(raw_corr), float(best_corr), int(best_lag)

    def convert_to_wav(self, audio_filepath: str) -> tuple:
        """
        Converts source audio (WebM, Ogg, MP4, WAV, etc.) into a clean 16kHz mono
        16-bit PCM WAV file via FFmpeg and returns (wav_path, audio_meta).
        Caller is responsible for deleting wav_path.
        """
        temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_wav.close()  # Close so ffmpeg can write to it
        temp_wav_path = temp_wav.name

        cmd = [
            "ffmpeg", "-y", "-i", audio_filepath,
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            temp_wav_path
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30)
            audio_data, samplerate = sf.read(temp_wav_path)
            info = sf.info(temp_wav_path)
            wav_bytes = os.path.getsize(temp_wav_path)
            duration_ms = int(round(info.duration * 1000))
            if audio_data.size > 0:
                overall_rms = float(np.sqrt(np.mean(audio_data ** 2)))
                peak_amp = float(np.max(np.abs(audio_data)))
            else:
                overall_rms = 0.0
                peak_amp = 0.0
            return temp_wav_path, {
                "wavBytes": wav_bytes,
                "sampleRate": int(samplerate),
                "durationMs": duration_ms,
                "overallRms": round(overall_rms, 6),
                "peakAmplitude": round(peak_amp, 6),
            }
        except subprocess.TimeoutExpired as te:
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)
            print(f"FFmpeg conversion timed out: {te}")
            raise AudioProcessingError("Audio conversion timed out.")
        except subprocess.CalledProcessError as cpe:
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)
            stderr_output = cpe.stderr.decode('utf-8', errors='ignore') if cpe.stderr else ""
            print(f"FFmpeg conversion failed: {cpe}. Stderr: {stderr_output}")
            raise AudioProcessingError("Audio conversion failed or file is corrupted.")
        except Exception as e:
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)
            print(f"Error reading converted audio file: {e}")
            raise AudioProcessingError("Failed to read converted audio file properties.")

    def calculate_sync_metrics(
        self,
        lip_movement: list,
        lip_timestamps: list,
        audio_filepath: str,
        correlation_threshold: float = None
    ) -> Dict[str, Any]:
        """
        Runs the full Lip-Voice Synchronization pipeline and computes diagnostic metrics.
        Converts source audio to 16kHz mono WAV when needed.
        """
        global_threshold = float(settings.LIVE_SYNC_THRESHOLD)
        if correlation_threshold is not None:
            effective_threshold = max(float(correlation_threshold), global_threshold)
        else:
            effective_threshold = global_threshold

        owned_temp_wav = None
        try:
            read_wav_path = None
            if audio_filepath.lower().endswith(".wav"):
                try:
                    info = sf.info(audio_filepath)
                    if info.samplerate == 16000 and info.channels == 1:
                        read_wav_path = audio_filepath
                        audio_duration_ms = int(round(info.duration * 1000))
                        audio_data, samplerate = sf.read(read_wav_path)
                except Exception:
                    read_wav_path = None

            if read_wav_path is None:
                owned_temp_wav, wav_meta = self.convert_to_wav(audio_filepath)
                audio_data, samplerate = sf.read(owned_temp_wav)
                audio_duration_ms = wav_meta["durationMs"]

            # 4. Extract aligned audio envelope
            audio_envelope = self.calculate_audio_energy(audio_data, samplerate, lip_timestamps)
            
            # 5. Apply moving average smoothing to lip values and audio envelope
            lip_arr = np.array(lip_movement, dtype=float)
            smoothed_lip = self.smooth_signal(lip_arr, settings.SYNC_SMOOTHING_WINDOW)
            smoothed_audio = self.smooth_signal(audio_envelope, settings.SYNC_SMOOTHING_WINDOW)

            # 6. Normalize visual and audio signals
            norm_lip = self.normalize_signal(smoothed_lip)
            norm_audio = self.normalize_signal(smoothed_audio)

            # 7. Count valid non-silent audio frames and ignored frames
            max_energy = float(np.max(audio_envelope)) if len(audio_envelope) > 0 else 0.0
            avg_energy = float(np.mean(audio_envelope)) if len(audio_envelope) > 0 else 0.0
            min_energy = max(0.015, min(settings.MIN_AUDIO_ENERGY, max_energy * 0.25))
            effective_min_energy = min_energy
            valid_mask = audio_envelope >= min_energy
            valid_frames = int(np.sum(valid_mask))
            ignored_frames = len(lip_movement) - valid_frames

            # Prepare time-aligned normalized signal series for frontend visualization
            t0 = float(lip_timestamps[0]) if len(lip_timestamps) > 0 else 0.0
            rel_timestamps = [round(float(t - t0), 1) for t in lip_timestamps]
            display_audio = norm_audio if valid_frames > 0 else np.clip(audio_envelope, 0.0, 1.0)
            signal_series = {
                "timestampsMs": rel_timestamps,
                "normalizedLip": [round(float(v), 4) for v in norm_lip],
                "normalizedAudio": [round(float(v), 4) for v in display_audio]
            }

            # 8. Validate visual lip movement dynamics via LipService
            is_lip_moving, lip_variance = lip_service.verify_lip_movement(lip_movement)
            if not is_lip_moving:
                return {
                    "audioDurationMs": audio_duration_ms,
                    "rawCorrelation": 0.0,
                    "alignedCorrelation": 0.0,
                    "detectedTimeOffsetMs": 0.0,
                    "validFrames": valid_frames,
                    "ignoredFrames": ignored_frames,
                    "averageAudioEnergy": avg_energy,
                    "maxAudioEnergy": max_energy,
                    "lipVariance": round(lip_variance, 6),
                    "isLipMoving": False,
                    "syncStatus": "SPOOF",
                    "syncReason": (
                        f"Insufficient or static lip movement detected (variance: {lip_variance:.6f} < "
                        f"{settings.MIN_LIP_VARIATION}). Active speech movement is required."
                    ),
                    "isSynchronized": False,
                    "globalThreshold": round(global_threshold, 4),
                    "effectiveThreshold": round(effective_threshold, 4),
                    "maxAllowedOffsetMs": settings.MAX_SYNC_TIME_DIFF_MS,
                    "signalSeries": signal_series
                }

            # 9. Check minimal frame threshold and audio volume (Silence Rejection)
            min_speech_frames = min(settings.MIN_VALID_SYNC_FRAMES, 15)
            if valid_frames < min_speech_frames or max_energy < 0.025 or avg_energy < 0.006:
                return {
                    "audioDurationMs": audio_duration_ms,
                    "rawCorrelation": 0.0,
                    "alignedCorrelation": 0.0,
                    "detectedTimeOffsetMs": 0.0,
                    "validFrames": valid_frames,
                    "ignoredFrames": ignored_frames,
                    "averageAudioEnergy": avg_energy,
                    "maxAudioEnergy": max_energy,
                    "lipVariance": round(lip_variance, 6),
                    "isLipMoving": True,
                    "syncStatus": "SPOOF",
                    "syncReason": "No audible speech detected. Active vocal speech matching the challenge phrase is required.",
                    "isSynchronized": False,
                    "globalThreshold": round(global_threshold, 4),
                    "effectiveThreshold": round(effective_threshold, 4),
                    "maxAllowedOffsetMs": settings.MAX_SYNC_TIME_DIFF_MS,
                    "signalSeries": signal_series
                }

            # Calculate average visual sampling time difference (avg FPS interval)
            deltas = np.diff(lip_timestamps)
            avg_dt = float(np.mean(deltas)) if len(deltas) > 0 else 33.33

            # 10. Cross-correlation with lag (search window bounded to permissible MAX_SYNC_TIME_DIFF_MS = 500ms)
            max_lag_indices = int(round(settings.MAX_SYNC_TIME_DIFF_MS / avg_dt)) if avg_dt > 0 else 15
            max_lag_indices = max(1, max_lag_indices)

            raw_corr, aligned_corr, best_lag = self.cross_correlation_with_lag(
                norm_lip,
                norm_audio,
                max_lag_indices,
                speech_mask=valid_mask,
                avg_dt=avg_dt,
                max_allowed_offset_ms=settings.MAX_SYNC_TIME_DIFF_MS,
                effective_threshold=effective_threshold
            )

            # Map lag to time offset in milliseconds
            time_offset_ms = float(best_lag * avg_dt)
            abs_time_diff_ms = abs(time_offset_ms)

            # 11. Classification status:
            # Rule: If aligned correlation < effective_threshold (which is >= LIVE_SYNC_THRESHOLD = 0.45) -> reject as SPOOF
            # Rule: If visual-audio synchronization difference > MAX_SYNC_TIME_DIFF_MS (500 ms) -> reject as SPOOF
            if aligned_corr < effective_threshold:
                status = "SPOOF"
                sync_reason = (
                    f"Lip-voice cross-correlation ({aligned_corr:.2f}) is below "
                    f"the required threshold ({effective_threshold:.2f})."
                )
                is_sync = False
            elif abs_time_diff_ms > settings.MAX_SYNC_TIME_DIFF_MS:
                status = "SPOOF"
                sync_reason = (
                    f"Visual-audio synchronization difference ({abs_time_diff_ms:.1f}ms) "
                    f"exceeds the maximum allowed threshold ({settings.MAX_SYNC_TIME_DIFF_MS:.0f}ms)."
                )
                is_sync = False
            else:
                status = "LIVE"
                sync_reason = (
                    f"Lip-voice movement synchronized (correlation: {aligned_corr:.2f}, "
                    f"offset: {time_offset_ms:+.1f}ms)."
                )
                is_sync = True

            return {
                "audioDurationMs": audio_duration_ms,
                "rawCorrelation": raw_corr,
                "alignedCorrelation": aligned_corr,
                "detectedTimeOffsetMs": round(time_offset_ms, 2),
                "validFrames": valid_frames,
                "ignoredFrames": ignored_frames,
                "averageAudioEnergy": avg_energy,
                "maxAudioEnergy": max_energy,
                "lipVariance": round(lip_variance, 6),
                "isLipMoving": True,
                "syncStatus": status,
                "syncReason": sync_reason,
                "isSynchronized": is_sync,
                "globalThreshold": round(global_threshold, 4),
                "effectiveThreshold": round(effective_threshold, 4),
                "maxAllowedOffsetMs": settings.MAX_SYNC_TIME_DIFF_MS,
                "signalSeries": signal_series
            }

        finally:
            # Clean up the temporary WAV file created by convert_to_wav
            if owned_temp_wav and os.path.exists(owned_temp_wav):
                try:
                    os.remove(owned_temp_wav)
                except Exception as cleanup_err:
                    print(f"Failed to delete temporary WAV file: {cleanup_err}")

    def verify_sync(self, correlation_score: float) -> bool:
        """
        Backward compatible check.
        """
        return correlation_score >= settings.LIVE_SYNC_THRESHOLD

# Singleton instance
sync_service = SyncService()
