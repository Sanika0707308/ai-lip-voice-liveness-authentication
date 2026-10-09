"""
Adaptive Per-User Synchronization Threshold Calibration Service.

================================================================================
MATHEMATICAL FORMULA & STATISTICAL RATIONALE
================================================================================
Given N valid genuine calibration samples S = {s_1, s_2, ..., s_N} where:
  - N >= CALIBRATION_REQUIRED_SAMPLES (default: 3, configurable 3..5)
  - Each sample s_i is a valid `alignedCorrelation` score in [0.45, 1.0] that passed:
      1. Challenge phrase verification (spoken phrase matched expected challenge)
      2. Audio/lip recording duration consistency (|audioDurationMs - lipDurationMs| <= 500 ms)
      3. Valid speech frame count (validFrames >= 30)
      4. Visual-audio time offset rule (|detectedTimeOffsetMs| <= 500 ms)
      5. Global minimum correlation floor (s_i >= 0.45)

Step 1 — Baseline Statistics:
  - Mean score:
      mu = (1 / N) * sum_{i=1..N}(s_i)
  - Median score:
      median = median(s_1, ..., s_N)
  - Population standard deviation (score variation across calibration samples):
      sigma = sqrt( (1 / N) * sum_{i=1..N}((s_i - mu)^2) )

Step 2 — Raw Adaptive Threshold Derivation:
  - Tolerance allowance:
      margin = max(k * sigma, delta)
    where:
      k     = CALIBRATION_STD_MULTIPLIER (default: 1.0)
      delta = CALIBRATION_DEFAULT_MARGIN (default: 0.08)
  - Raw calculated threshold:
      T_raw = mu - max(k * sigma, delta)

  Rationale:
  - If a user exhibits natural variance across calibration attempts (sigma > 0.08),
    subtracting 1.0 * sigma adapts the threshold to their actual speaking variability.
  - If a user happens to produce nearly identical scores during calibration (sigma < 0.08),
    the minimum margin delta = 0.08 prevents an overly tight threshold (e.g., mu - 0.005)
    that would cause false rejections during normal future authentications.

Step 3 — Safety Bounds & Global Minimum Protection:
  - Global safety minimum:
      T_min = LIVE_SYNC_THRESHOLD = 0.45
  - Upper cap:
      T_max = CALIBRATION_MAX_THRESHOLD = 0.85
  - Clamped adaptive threshold:
      T_adaptive = min(max(T_raw, T_min), T_max)

  Safety Guarantee:
  - T_adaptive is NEVER lower than the configured global threshold (0.45).
    For example, if T_raw = 0.38, T_adaptive is clamped to 0.45.
  - The +/-500 ms visual-audio time offset rule (MAX_SYNC_TIME_DIFF_MS = 500 ms)
    remains completely independent and unchanged.
================================================================================
"""

import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import numpy as np

from backend.config import settings
from backend.services.mongodb_service import mongodb_service


class CalibrationService:
    """
    Manages per-user synchronization calibration profiles, sample validation,
    statistical baseline computation, and effective threshold resolution.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def compute_adaptive_threshold(valid_scores: List[float]) -> Dict[str, Optional[float]]:
        """
        Computes statistical baseline metrics and the clamped adaptive threshold
        from a list of valid calibration correlation scores.

        Formula:
            mu         = mean(valid_scores)
            median     = median(valid_scores)
            sigma      = std(valid_scores)  (ddof=0)
            margin     = max(CALIBRATION_STD_MULTIPLIER * sigma, CALIBRATION_DEFAULT_MARGIN)
            T_raw      = mu - margin
            T_adaptive = min(max(T_raw, LIVE_SYNC_THRESHOLD), CALIBRATION_MAX_THRESHOLD)
        """
        global_min = float(settings.LIVE_SYNC_THRESHOLD)
        max_cap = float(settings.CALIBRATION_MAX_THRESHOLD)
        k = float(settings.CALIBRATION_STD_MULTIPLIER)
        delta = float(settings.CALIBRATION_DEFAULT_MARGIN)

        if not valid_scores:
            return {
                "meanScore": None,
                "medianScore": None,
                "stdDeviation": None,
                "rawCalculatedThreshold": None,
                "adaptiveThreshold": None,
                "effectiveThreshold": round(global_min, 4),
                "minimumSafetyThreshold": round(global_min, 4),
            }

        arr = np.array(valid_scores, dtype=float)
        mean_val = float(np.mean(arr))
        median_val = float(np.median(arr))
        std_val = float(np.std(arr, ddof=0))

        margin = max(k * std_val, delta)
        raw_threshold = mean_val - margin
        clamped_threshold = min(max(raw_threshold, global_min), max_cap)

        return {
            "meanScore": round(mean_val, 4),
            "medianScore": round(median_val, 4),
            "stdDeviation": round(std_val, 4),
            "rawCalculatedThreshold": round(raw_threshold, 4),
            "adaptiveThreshold": round(clamped_threshold, 4),
            "effectiveThreshold": round(clamped_threshold, 4),
            "minimumSafetyThreshold": round(global_min, 4),
        }

    def _build_default_profile(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        required_samples: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Creates an empty uncalibrated profile structure for a user/session."""
        req_samples = int(required_samples or settings.CALIBRATION_REQUIRED_SAMPLES)
        req_samples = max(
            int(settings.CALIBRATION_MIN_SAMPLES),
            min(req_samples, int(settings.CALIBRATION_MAX_SAMPLES)),
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        global_min = round(float(settings.LIVE_SYNC_THRESHOLD), 4)

        return {
            "userId": user_id,
            "sessionId": session_id or user_id,
            "createdAt": now_iso,
            "updatedAt": now_iso,
            "calibratedAt": None,
            "totalAttempts": 0,
            "requiredSamples": req_samples,
            "validSamplesCount": 0,
            "validScores": [],
            "meanScore": None,
            "medianScore": None,
            "stdDeviation": None,
            "rawCalculatedThreshold": None,
            "adaptiveThreshold": None,
            "effectiveThreshold": global_min,
            "globalThreshold": global_min,
            "minimumSafetyThreshold": global_min,
            "maxAllowedTimeOffsetMs": float(settings.MAX_SYNC_TIME_DIFF_MS),
            "calibrationStatus": "NOT_STARTED",
            "isCalibrated": False,
        }

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves the stored calibration profile for `user_id` from cache or MongoDB/fallback.
        Returns None if the user has no saved profile.
        """
        if not user_id:
            return None

        with self._lock:
            if user_id in self._cache:
                return dict(self._cache[user_id])

        stored = mongodb_service.get_calibration_profile(user_id)
        if stored:
            # Ensure safety floor on loaded profile
            global_min = round(float(settings.LIVE_SYNC_THRESHOLD), 4)
            stored["globalThreshold"] = global_min
            stored["minimumSafetyThreshold"] = global_min
            if stored.get("isCalibrated") and stored.get("adaptiveThreshold") is not None:
                safe_adaptive = round(max(float(stored["adaptiveThreshold"]), global_min), 4)
                stored["adaptiveThreshold"] = safe_adaptive
                stored["effectiveThreshold"] = safe_adaptive
            else:
                stored["effectiveThreshold"] = global_min

            with self._lock:
                self._cache[user_id] = dict(stored)
            return dict(stored)

        return None

    def get_or_default_profile(self, user_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns the existing profile for `user_id` if available, or an unpersisted
        default `NOT_STARTED` profile summary.
        """
        existing = self.get_profile(user_id)
        if existing:
            return existing
        return self._build_default_profile(user_id=user_id, session_id=session_id)

    def start_new_calibration(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        required_samples: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Resets any previous calibration profile for `user_id` and initializes a fresh
        calibration state (`IN_PROGRESS` with 0 samples).
        """
        if not user_id or not user_id.strip():
            raise ValueError("userId is required to start calibration.")

        user_id = user_id.strip()
        mongodb_service.delete_calibration_profile(user_id)

        profile = self._build_default_profile(
            user_id=user_id,
            session_id=session_id or user_id,
            required_samples=required_samples,
        )
        profile["calibrationStatus"] = "IN_PROGRESS"

        record_id = mongodb_service.save_calibration_profile(profile)
        profile["dbRecordId"] = record_id

        with self._lock:
            self._cache[user_id] = dict(profile)

        return dict(profile)

    def record_calibration_sample(
        self,
        user_id: str,
        session_id: str,
        aligned_correlation: float,
        detected_time_offset_ms: float,
        is_challenge_match: bool,
        is_sync_valid: bool,
        duration_mismatch: bool,
        rejection_reason: Optional[str] = None,
        required_samples: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Evaluates a single calibration attempt and updates the user's calibration profile.

        A sample is ACCEPTED only if all of the following hold:
          1. `is_challenge_match` is True
          2. `not duration_mismatch` (|audioDuration - lipDuration| <= 500 ms)
          3. `is_sync_valid` is True
          4. `abs(detected_time_offset_ms) <= settings.MAX_SYNC_TIME_DIFF_MS` (500 ms)
          5. `aligned_correlation >= settings.LIVE_SYNC_THRESHOLD` (0.45)

        Invalid or failed attempts increment `totalAttempts` but are NEVER added
        to `validScores`.
        """
        user_id = (user_id or session_id or "").strip()
        if not user_id:
            raise ValueError("userId or sessionId is required for calibration.")

        existing = self.get_profile(user_id)
        if existing is None:
            profile = self._build_default_profile(
                user_id=user_id,
                session_id=session_id,
                required_samples=required_samples,
            )
        else:
            profile = dict(existing)
            if required_samples is not None:
                req = max(
                    int(settings.CALIBRATION_MIN_SAMPLES),
                    min(int(required_samples), int(settings.CALIBRATION_MAX_SAMPLES)),
                )
                profile["requiredSamples"] = req

        now_iso = datetime.now(timezone.utc).isoformat()
        profile["sessionId"] = session_id or profile.get("sessionId") or user_id
        profile["updatedAt"] = now_iso
        profile["totalAttempts"] = int(profile.get("totalAttempts", 0)) + 1

        global_min = float(settings.LIVE_SYNC_THRESHOLD)
        max_offset = float(settings.MAX_SYNC_TIME_DIFF_MS)

        offset_ok = abs(float(detected_time_offset_ms)) <= max_offset
        corr_ok = float(aligned_correlation) >= global_min

        sample_accepted = bool(
            is_challenge_match
            and not duration_mismatch
            and is_sync_valid
            and offset_ok
            and corr_ok
        )

        sample_rejection_reason = None
        if not sample_accepted:
            if rejection_reason:
                sample_rejection_reason = rejection_reason
            elif duration_mismatch:
                sample_rejection_reason = "Recording duration mismatch exceeds 500 ms limit."
            elif not is_challenge_match:
                sample_rejection_reason = "Spoken challenge phrase did not match the expected phrase."
            elif not offset_ok:
                sample_rejection_reason = (
                    f"Visual-audio time offset ({detected_time_offset_ms:+.1f}ms) exceeds "
                    f"the maximum allowed +/-{max_offset:.0f}ms rule."
                )
            elif not corr_ok:
                sample_rejection_reason = (
                    f"Sample correlation ({aligned_correlation:.3f}) is below the minimum "
                    f"global threshold ({global_min:.2f})."
                )
            else:
                sample_rejection_reason = "Sample did not satisfy synchronization validity criteria."

        valid_scores = list(profile.get("validScores", []))
        req_count = int(profile.get("requiredSamples", settings.CALIBRATION_REQUIRED_SAMPLES))

        if sample_accepted:
            valid_scores.append(round(float(aligned_correlation), 4))

        profile["validScores"] = valid_scores
        profile["validSamplesCount"] = len(valid_scores)

        # Recompute statistics over valid samples collected so far
        stats = self.compute_adaptive_threshold(valid_scores)
        profile["meanScore"] = stats["meanScore"]
        profile["medianScore"] = stats["medianScore"]
        profile["stdDeviation"] = stats["stdDeviation"]
        profile["rawCalculatedThreshold"] = stats["rawCalculatedThreshold"]
        profile["minimumSafetyThreshold"] = stats["minimumSafetyThreshold"]
        profile["globalThreshold"] = round(global_min, 4)

        if len(valid_scores) >= req_count:
            profile["calibrationStatus"] = "COMPLETE"
            profile["isCalibrated"] = True
            profile["calibratedAt"] = profile.get("calibratedAt") or now_iso
            profile["adaptiveThreshold"] = stats["adaptiveThreshold"]
            profile["effectiveThreshold"] = stats["effectiveThreshold"]
        else:
            profile["calibrationStatus"] = "IN_PROGRESS"
            profile["isCalibrated"] = False
            profile["calibratedAt"] = None
            profile["adaptiveThreshold"] = None
            profile["effectiveThreshold"] = round(global_min, 4)

        record_id = mongodb_service.save_calibration_profile(profile)
        profile["dbRecordId"] = record_id

        with self._lock:
            self._cache[user_id] = dict(profile)

        return {
            "sampleAccepted": sample_accepted,
            "sampleRejectionReason": sample_rejection_reason,
            "currentSampleNumber": len(valid_scores),
            "requiredSamples": req_count,
            "remainingSamples": max(0, req_count - len(valid_scores)),
            "calibrationProfile": dict(profile),
        }

    def resolve_threshold_for_verification(
        self,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Determines the correlation threshold to apply during normal authentication.

        Rules:
          1. Look up calibration profile by `user_id` first, then `session_id`.
          2. If a profile exists, `calibrationStatus == "COMPLETE"`, and
             `validSamplesCount >= requiredSamples`, use `max(adaptiveThreshold, 0.45)`.
          3. Otherwise, fall back to `LIVE_SYNC_THRESHOLD = 0.45`.
        """
        global_min = round(float(settings.LIVE_SYNC_THRESHOLD), 4)

        profile = None
        if user_id and user_id.strip():
            profile = self.get_profile(user_id.strip())
        if profile is None and session_id and session_id.strip():
            profile = self.get_profile(session_id.strip())

        if profile is not None:
            status_val = profile.get("calibrationStatus", "NOT_STARTED")
            req_samples = int(profile.get("requiredSamples", settings.CALIBRATION_REQUIRED_SAMPLES))
            valid_count = int(profile.get("validSamplesCount", 0))
            adaptive_val = profile.get("adaptiveThreshold")

            if (
                status_val == "COMPLETE"
                and valid_count >= req_samples
                and adaptive_val is not None
            ):
                effective_val = round(max(float(adaptive_val), global_min), 4)
                return {
                    "globalThreshold": global_min,
                    "adaptiveThreshold": round(float(adaptive_val), 4),
                    "effectiveThreshold": effective_val,
                    "adaptiveCalibrationUsed": True,
                    "calibrationStatus": "COMPLETE",
                    "calibrationProfile": profile,
                }

            return {
                "globalThreshold": global_min,
                "adaptiveThreshold": None,
                "effectiveThreshold": global_min,
                "adaptiveCalibrationUsed": False,
                "calibrationStatus": status_val,
                "calibrationProfile": profile,
            }

        return {
            "globalThreshold": global_min,
            "adaptiveThreshold": None,
            "effectiveThreshold": global_min,
            "adaptiveCalibrationUsed": False,
            "calibrationStatus": "NOT_STARTED",
            "calibrationProfile": None,
        }


# Singleton instance
calibration_service = CalibrationService()
