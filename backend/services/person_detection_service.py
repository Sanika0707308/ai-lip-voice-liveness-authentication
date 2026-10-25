import time
import cv2
import numpy as np
from typing import Dict, List, Any, Tuple, Optional
from backend.config import settings

class PersonDetectionService:
    """
    LIVESCAN 2.0 Person Detection Service.
    
    Detects primary candidate and possible additional individuals visible in the camera frame.
    Integrates HOG pedestrian detection and upper-body Haar cascade to ensure full-person 
    and upper-body detection are NOT treated as merely equivalent to frontal face detection.
    
    Includes:
    - Preprocessing for poor lighting (adaptive CLAHE enhancement)
    - Non-maximum suppression (NMS) for multi-detector fusion
    - Temporal debouncing (confirmation window and cooldown deduplication)
    - Auto-clearing with configurable confirmation period
    """

    def __init__(self):
        # Initialize HOG pedestrian detector
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

        # Load upper-body and multiple face cascades for robust multi-angle detection
        upperbody_path = cv2.data.haarcascades + 'haarcascade_upperbody.xml'
        face_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        alt_face_path = cv2.data.haarcascades + 'haarcascade_frontalface_alt2.xml'
        profile_path = cv2.data.haarcascades + 'haarcascade_profileface.xml'
        
        self.upperbody_cascade = cv2.CascadeClassifier(upperbody_path)
        self.face_cascade = cv2.CascadeClassifier(face_path)
        self.alt_face_cascade = cv2.CascadeClassifier(alt_face_path)
        self.profile_cascade = cv2.CascadeClassifier(profile_path)

        # Per-session temporal state tracking
        # session_id -> {
        #   "consecutive_multi_count": int,
        #   "consecutive_single_count": int,
        #   "is_warning_active": bool,
        #   "last_alert_timestamp": float,
        #   "last_detected_count": int,
        #   "first_clear_timestamp": Optional[float]
        # }
        self._session_states: Dict[str, Dict[str, Any]] = {}

    def _get_or_create_state(self, session_id: str) -> Dict[str, Any]:
        if session_id not in self._session_states:
            self._session_states[session_id] = {
                "consecutive_multi_count": 0,
                "consecutive_single_count": 0,
                "is_warning_active": False,
                "last_alert_timestamp": 0.0,
                "last_detected_count": 1,
                "first_clear_timestamp": None
            }
        return self._session_states[session_id]

    def reset_session_state(self, session_id: str):
        """Resets tracking state for a specific session."""
        if session_id in self._session_states:
            del self._session_states[session_id]

    @staticmethod
    def enhance_lighting_if_needed(gray_img: np.ndarray) -> np.ndarray:
        """
        Handles poor lighting conditions (underexposure, harsh shadows, low contrast)
        using Contrast Limited Adaptive Histogram Equalization (CLAHE).
        """
        mean_brightness = np.mean(gray_img)
        if mean_brightness < 60 or mean_brightness > 200:
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
            return clahe.apply(gray_img)
        return gray_img

    @staticmethod
    def _compute_iou(boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> float:
        """Calculates Intersection over Union between two bounding boxes [x, y, w, h]."""
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[0] + boxA[2], boxB[0] + boxB[2])
        yB = min(boxA[1] + boxA[3], boxB[1] + boxB[3])

        interArea = max(0, xB - xA) * max(0, yB - yA)
        boxAArea = boxA[2] * boxA[3]
        boxBArea = boxB[2] * boxB[3]

        unionArea = boxAArea + boxBArea - interArea
        if unionArea <= 0:
            return 0.0
        return interArea / float(unionArea)

    @staticmethod
    def _deduplicate_faces(faces: List[Dict[str, Any]], iou_thresh: float = 0.40) -> List[Dict[str, Any]]:
        """
        Consolidates duplicate bounding box detections that correspond to the exact same physical face.
        Distinct faces with different centers/positions are strictly preserved.
        """
        if not faces:
            return []
        sorted_faces = sorted(faces, key=lambda f: (f["confidence"], f["bbox"][2] * f["bbox"][3]), reverse=True)
        unique: List[Dict[str, Any]] = []
        for f in sorted_faces:
            is_dup = False
            for u in unique:
                iou = PersonDetectionService._compute_iou(f["bbox"], u["bbox"])
                if iou >= iou_thresh:
                    is_dup = True
                    break
                # Center distance check relative to face dimensions
                cx1, cy1 = f["bbox"][0] + f["bbox"][2] / 2.0, f["bbox"][1] + f["bbox"][3] / 2.0
                cx2, cy2 = u["bbox"][0] + u["bbox"][2] / 2.0, u["bbox"][1] + u["bbox"][3] / 2.0
                min_dim = min(f["bbox"][2], u["bbox"][2], f["bbox"][3], u["bbox"][3])
                dist = np.hypot(cx1 - cx2, cy1 - cy2)
                if dist < min_dim * 0.45:
                    is_dup = True
                    break
            if not is_dup:
                unique.append(f)
        return unique

    @staticmethod
    def _cluster_bodies(bodies: List[Dict[str, Any]], iou_thresh: float = 0.35) -> List[Dict[str, Any]]:
        """Deduplicates body detections among themselves."""
        if not bodies:
            return []
        sorted_bodies = sorted(bodies, key=lambda b: b["bbox"][2] * b["bbox"][3], reverse=True)
        unique = []
        for b in sorted_bodies:
            is_dup = False
            for u in unique:
                iou = PersonDetectionService._compute_iou(b["bbox"], u["bbox"])
                if iou >= iou_thresh:
                    is_dup = True
                    break
            if not is_dup:
                unique.append(b)
        return unique

    def detect_persons_in_image(self, image_input: Any) -> Dict[str, Any]:
        """
        Detects primary person and all additional persons in an image.
        Uses multi-detector fusion:
        - Frontal and Alt2 face cascades for high-accuracy direct and angled face detection
        - Profile cascade for side-view faces of secondary individuals
        - Upper-body and HOG pedestrian detectors for body presence
        
        Guarantees that multiple distinct faces are NEVER merged into a single person.
        """
        if isinstance(image_input, bytes):
            nparr = np.frombuffer(image_input, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        elif isinstance(image_input, np.ndarray):
            img = image_input.copy()
        else:
            raise ValueError("Unsupported image input type; expected bytes or np.ndarray")

        if img is None or img.size == 0:
            return {
                "person_count": 0,
                "primary_detected": False,
                "additional_detected": False,
                "detections": [],
                "lighting_condition": "INVALID_IMAGE"
            }

        # Convert to grayscale
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img.copy()

        # Lighting diagnosis
        mean_lum = float(np.mean(gray))
        if mean_lum < 50:
            lighting = "POOR_LOW_LIGHT"
        elif mean_lum > 215:
            lighting = "OVEREXPOSED"
        else:
            lighting = "NORMAL"

        enhanced_gray = self.enhance_lighting_if_needed(gray)

        face_detections: List[Dict[str, Any]] = []

        # 1. Frontal face detection (primary & secondary)
        try:
            boxes = self.face_cascade.detectMultiScale(
                enhanced_gray,
                scaleFactor=1.1,
                minNeighbors=3,
                minSize=(32, 32)
            )
            for (x, y, w, h) in boxes:
                face_detections.append({
                    "bbox": [int(x), int(y), int(w), int(h)],
                    "type": "face",
                    "confidence": 0.90
                })
        except Exception:
            pass

        # 2. Alt2 frontal cascade (higher precision and angle tolerance)
        try:
            boxes = self.alt_face_cascade.detectMultiScale(
                enhanced_gray,
                scaleFactor=1.1,
                minNeighbors=3,
                minSize=(32, 32)
            )
            for (x, y, w, h) in boxes:
                face_detections.append({
                    "bbox": [int(x), int(y), int(w), int(h)],
                    "type": "face",
                    "confidence": 0.92
                })
        except Exception:
            pass

        # 3. Profile face cascade (detects secondary individuals turned sideways)
        try:
            boxes = self.profile_cascade.detectMultiScale(
                enhanced_gray,
                scaleFactor=1.1,
                minNeighbors=3,
                minSize=(32, 32)
            )
            for (x, y, w, h) in boxes:
                face_detections.append({
                    "bbox": [int(x), int(y), int(w), int(h)],
                    "type": "face",
                    "confidence": 0.88
                })

            # Also check flipped for opposite profile orientation
            flipped_gray = cv2.flip(enhanced_gray, 1)
            f_boxes = self.profile_cascade.detectMultiScale(
                flipped_gray,
                scaleFactor=1.1,
                minNeighbors=3,
                minSize=(32, 32)
            )
            img_w = enhanced_gray.shape[1]
            for (fx, fy, fw, fh) in f_boxes:
                orig_x = img_w - (fx + fw)
                face_detections.append({
                    "bbox": [int(orig_x), int(fy), int(fw), int(fh)],
                    "type": "face",
                    "confidence": 0.88
                })
        except Exception:
            pass

        # Deduplicate multiple cascade detections of the same face
        distinct_faces = self._deduplicate_faces(face_detections)

        # Sort faces so primary candidate (largest / closest to center) is Face 1, secondary faces are Face 2, Face 3...
        img_h, img_w = enhanced_gray.shape[:2]
        center_x, center_y = img_w / 2.0, img_h / 2.0

        def _face_priority(f):
            fx, fy, fw, fh = f["bbox"]
            dist_center = np.hypot(fx + fw / 2.0 - center_x, fy + fh / 2.0 - center_y)
            area = fw * fh
            return (area / (1.0 + dist_center * 0.4))

        distinct_faces.sort(key=_face_priority, reverse=True)
        for idx, f in enumerate(distinct_faces):
            f["label"] = f"Face {idx + 1}"
            f["is_primary"] = (idx == 0)

        # 4. Upper-body & HOG detection for body presence
        body_detections: List[Dict[str, Any]] = []
        try:
            upperbody_boxes = self.upperbody_cascade.detectMultiScale(
                enhanced_gray,
                scaleFactor=1.1,
                minNeighbors=3,
                minSize=(70, 70)
            )
            for (x, y, w, h) in upperbody_boxes:
                body_detections.append({
                    "bbox": [int(x), int(y), int(w), int(h)],
                    "type": "upper_body",
                    "confidence": 0.85
                })
        except Exception:
            pass

        try:
            hog_boxes, weights = self.hog.detectMultiScale(
                enhanced_gray, 
                winStride=(8, 8), 
                padding=(16, 16), 
                scale=1.05
            )
            for (x, y, w, h), w_score in zip(hog_boxes, weights):
                if float(w_score) > 0.20:
                    body_detections.append({
                        "bbox": [int(x), int(y), int(w), int(h)],
                        "type": "full_person",
                        "confidence": float(round(min(1.0, 0.5 + float(w_score) * 0.5), 2))
                    })
        except Exception:
            pass

        # Associate bodies with faces: an upper body box containing a face belongs to that face
        unmatched_bodies = []
        for b in body_detections:
            bx, by, bw, bh = b["bbox"]
            matches_face = False
            for f in distinct_faces:
                fx, fy, fw, fh = f["bbox"]
                fcx, fcy = fx + fw / 2.0, fy + fh / 2.0
                if (bx - 20 <= fcx <= bx + bw + 20) and (by - 20 <= fcy <= by + bh * 0.65):
                    matches_face = True
                    break
            if not matches_face:
                unmatched_bodies.append(b)

        unique_bodies = self._cluster_bodies(unmatched_bodies)

        final_detections = list(distinct_faces) + unique_bodies
        person_count = max(len(distinct_faces), len(final_detections))

        return {
            "person_count": person_count,
            "face_count": len(distinct_faces),
            "primary_detected": person_count >= 1,
            "additional_detected": person_count > 1,
            "detections": final_detections,
            "faces": distinct_faces,
            "frame_width": int(img.shape[1]),
            "frame_height": int(img.shape[0]),
            "lighting_condition": lighting
        }

    def process_monitoring_frame(self, session_id: str, raw_person_count: int, detections: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Applies debounce temporal smoothing, confirmation thresholds, and cooldowns
        to avoid repeated alerts for continuous presence and false triggers.
        
        Returns:
            Dict with action triggers:
                - should_alert: bool
                - should_clear: bool
                - is_warning_active: bool
                - alert_type: Optional[str]
                - message: Optional[str]
                - confirmed_person_count: int
        """
        now = time.time()
        state = self._get_or_create_state(session_id)
        
        required_confirmation_frames = settings.MONITORING_PERSON_CONFIRMATION_FRAMES
        cooldown_sec = settings.MONITORING_ALERT_COOLDOWN_SEC
        clear_window_sec = settings.MONITORING_PERSON_CLEAR_SEC

        should_alert = False
        should_clear = False
        alert_type = None
        message = None

        if raw_person_count > 1:
            # Person count increased
            state["consecutive_multi_count"] += 1
            state["consecutive_single_count"] = 0
            state["first_clear_timestamp"] = None

            # Confirm over threshold frames
            if state["consecutive_multi_count"] >= required_confirmation_frames:
                # Check cooldown to prevent duplicate logging for the same continuous event
                time_since_last_alert = now - state["last_alert_timestamp"]
                if not state["is_warning_active"] or time_since_last_alert >= cooldown_sec:
                    should_alert = True
                    state["is_warning_active"] = True
                    state["last_alert_timestamp"] = now
                    alert_type = "ADDITIONAL_PERSON_DETECTED"
                    message = f"Warning: {raw_person_count} people detected in camera frame."
        else:
            # Person count is 0 or 1
            state["consecutive_single_count"] += 1
            state["consecutive_multi_count"] = 0

            # If a warning was previously active, require confirmation period before clearing
            if state["is_warning_active"]:
                if state["first_clear_timestamp"] is None:
                    state["first_clear_timestamp"] = now
                elif (now - state["first_clear_timestamp"]) >= clear_window_sec:
                    # Successfully confirmed clear
                    should_clear = True
                    state["is_warning_active"] = False
                    state["first_clear_timestamp"] = None
                    alert_type = "PERSON_CLEARED"
                    message = "Additional person no longer detected. Frame status normal."

        state["last_detected_count"] = raw_person_count

        return {
            "should_alert": should_alert,
            "should_clear": should_clear,
            "is_warning_active": state["is_warning_active"],
            "alert_type": alert_type,
            "message": message,
            "confirmed_person_count": raw_person_count
        }

person_detection_service = PersonDetectionService()
