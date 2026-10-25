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
        Extracts Root-Mean-Square (RMS) envelope scaled values aligned with visual timestamps.
        """
        energy_envelope = []
        # Window size is 30ms representing standard video frame duration
        window_size = int(0.030 * samplerate)
        half_window = window_size // 2
        n_samples = len(audio_data)

        for t_ms in lip_timestamps:
            # Determine center sample index in raw audio for the relative timestamp
            center_idx = int((t_ms / 1000.0) * samplerate)
            start_idx = max(0, center_idx - half_window)
            end_idx = min(n_samples, center_idx + half_window)
            
            window = audio_data[start_idx:end_idx]
            if len(window) > 0:
                rms = np.sqrt(np.mean(window ** 2))
            else:
                rms = 0.0
            
            # Map/Scale audio volume RMS to match the UI Live Volume Meter scaling
            energy = min(rms * 4.0, 1.0)
            energy_envelope.append(energy)
            
        return np.array(energy_envelope)

    def cross_correlation_with_lag(
        self,
        x: np.ndarray,
        y: np.ndarray,
        max_lag: int,
        min_energy: float = 0.02,
        avg_dt: float = 33.33,
        max_allowed_offset_ms: float = 500.0,
        effective_threshold: float = 0.45
    ) -> tuple:
        """
        Searches for the best cross-correlation alignment over the lag indices,
        preventing sample-starved edge artifacts from overriding valid in-window alignments.
        Returns (rawCorrelation, alignedCorrelation, bestLagIndices).
        """
        n = len(x)
        if n < 2:
            return 0.0, 0.0, 0

        valid_mask = y >= min_energy

        # Calculate raw correlation (lag = 0)
        c_full = np.corrcoef(x, y)[0, 1] if n >= 2 else 0.0
        c_full = 0.0 if np.isnan(c_full) else float(c_full)

        c_speech = 0.0
        if np.sum(valid_mask) >= 2:
            cs = np.corrcoef(x[valid_mask], y[valid_mask])[0, 1]
            if not np.isnan(cs):
                c_speech = float(cs)

        raw_corr = float(max(c_full, c_speech))

        best_corr = raw_corr
        best_lag = 0

        best_in_window_corr = raw_corr
        best_in_window_lag = 0

        # Minimum overlapping frames required to avoid spurious small-sample correlations
        min_overlap = max(settings.MIN_VALID_SYNC_FRAMES // 2, min(15, n // 3))
        max_allowed_lag = int(round(max_allowed_offset_ms / avg_dt)) if avg_dt > 0 else max_lag

        # Iterate lag search
        for lag in range(-max_lag, max_lag + 1):
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

            if len(sub_x) >= min_overlap:
                cf = np.corrcoef(sub_x, sub_y)[0, 1]
                cf = 0.0 if np.isnan(cf) else float(cf)

                cs = 0.0
                if np.sum(sub_mask) >= max(2, min_overlap // 2):
                    cs_val = np.corrcoef(sub_x[sub_mask], sub_y[sub_mask])[0, 1]
                    if not np.isnan(cs_val):
                        cs = float(cs_val)

                corr = max(cf, cs)

                # Track best alignment within the permissible +/-500ms sync window
                if abs(lag) <= max_allowed_lag:
                    if corr > best_in_window_corr:
                        best_in_window_corr = corr
                        best_in_window_lag = lag

                # Track overall best alignment (including out-of-window for detecting true lag spoofs)
                if corr > best_corr:
                    best_corr = corr
                    best_lag = lag

        # If an in-window alignment meets or exceeds threshold and is not decisively beaten
        # by a significantly stronger out-of-window peak (e.g. a true lag spoof with offset > 500ms),
        # prioritize the in-window alignment to avoid edge noise artifacts.
        if best_in_window_corr >= effective_threshold and (best_corr - best_in_window_corr < 0.10):
            return float(raw_corr), float(best_in_window_corr), best_in_window_lag
        else:
            return float(raw_corr), float(best_corr), best_lag

    def calculate_sync_metrics(
        self,
        lip_movement: list,
        lip_timestamps: list,
        audio_filepath: str,
        correlation_threshold: float = None
    ) -> Dict[str, Any]:
        """
        Runs the full Lip-Voice Synchronization pipeline and computes diagnostic metrics.
        Converts the source audio to WAV first.
        """
        global_threshold = float(settings.LIVE_SYNC_THRESHOLD)
        if correlation_threshold is not None:
            effective_threshold = max(float(correlation_threshold), global_threshold)
        else:
            effective_threshold = global_threshold

        temp_wav = None
        try:
            # 1. Create a thread-safe temporary file for WAV output
            temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            temp_wav.close()  # Close so ffmpeg can write to it
            temp_wav_path = temp_wav.name

            # 2. Convert source audio to 16kHz mono WAV using ffmpeg with 30s timeout
            cmd = [
                "ffmpeg", "-y", "-i", audio_filepath,
                "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                temp_wav_path
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30)
            except subprocess.TimeoutExpired as te:
                print(f"FFmpeg conversion timed out: {te}")
                raise AudioProcessingError("Audio conversion timed out.")
            except subprocess.CalledProcessError as cpe:
                stderr_output = cpe.stderr.decode('utf-8', errors='ignore') if cpe.stderr else ""
                print(f"FFmpeg conversion failed: {cpe}. Stderr: {stderr_output}")
                raise AudioProcessingError("Audio conversion failed or file is corrupted.")

            # 3. Read WAV file (16kHz mono format)
            try:
                audio_data, samplerate = sf.read(temp_wav_path)
                info = sf.info(temp_wav_path)
                audio_duration_ms = int(round(info.duration * 1000))
            except Exception as e:
                print(f"Error reading converted audio file: {e}")
                raise AudioProcessingError("Failed to read converted audio file properties.")

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
            min_energy = settings.MIN_AUDIO_ENERGY
            valid_mask = audio_envelope >= min_energy
            valid_frames = int(np.sum(valid_mask))
            ignored_frames = len(lip_movement) - valid_frames
            avg_energy = float(np.mean(audio_envelope)) if len(audio_envelope) > 0 else 0.0

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

            # 9. Check minimal frame threshold
            if valid_frames < settings.MIN_VALID_SYNC_FRAMES:
                return {
                    "audioDurationMs": audio_duration_ms,
                    "rawCorrelation": 0.0,
                    "alignedCorrelation": 0.0,
                    "detectedTimeOffsetMs": 0.0,
                    "validFrames": valid_frames,
                    "ignoredFrames": ignored_frames,
                    "averageAudioEnergy": avg_energy,
                    "lipVariance": round(lip_variance, 6),
                    "isLipMoving": True,
                    "syncStatus": "SPOOF",
                    "syncReason": f"Insufficient speech audio frames ({valid_frames}/{settings.MIN_VALID_SYNC_FRAMES} required).",
                    "isSynchronized": False,
                    "globalThreshold": round(global_threshold, 4),
                    "effectiveThreshold": round(effective_threshold, 4),
                    "maxAllowedOffsetMs": settings.MAX_SYNC_TIME_DIFF_MS,
                    "signalSeries": signal_series
                }

            # Calculate average visual sampling time difference (avg FPS interval)
            deltas = np.diff(lip_timestamps)
            avg_dt = float(np.mean(deltas)) if len(deltas) > 0 else 33.33

            # 10. Cross-correlation with lag (search window up to 2x MAX_SYNC_TIME_DIFF_MS so offsets > 500ms can be detected)
            search_lag_ms = max(settings.SYNC_MAX_LAG_MS * 2, int(settings.MAX_SYNC_TIME_DIFF_MS * 2))
            max_lag_indices = int(round(search_lag_ms / avg_dt))
            max_lag_indices = max(1, max_lag_indices)

            raw_corr, aligned_corr, best_lag = self.cross_correlation_with_lag(
                norm_lip,
                norm_audio,
                max_lag_indices,
                min_energy,
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
            # Clean up the temporary WAV file
            if temp_wav and os.path.exists(temp_wav.name):
                try:
                    os.remove(temp_wav.name)
                except Exception as cleanup_err:
                    print(f"Failed to delete temporary WAV file: {cleanup_err}")

    def verify_sync(self, correlation_score: float) -> bool:
        """
        Backward compatible check.
        """
        return correlation_score >= settings.LIVE_SYNC_THRESHOLD

# Singleton instance
sync_service = SyncService()
