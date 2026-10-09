import math
import numpy as np
from backend.config import settings

class LipService:
    """
    Service responsible for calculating mouth aperture changes, computing
    Lip Vertical Distance (LVD) and Lip Horizontal Distance (LHD) to monitor speech dynamics.
    """
    
    def __init__(self):
        # MediaPipe Face Mesh landmark indexes for lips
        # 13: Inner upper lip center, 14: Inner lower lip center
        # 61: Inner/outer left corner, 291: Inner/outer right corner
        self.upper_lip_idx = 13   
        self.lower_lip_idx = 14   
        self.left_corner_idx = 61 
        self.right_corner_idx = 291 

    @staticmethod
    def euclidean_distance(p1, p2) -> float:
        """
        Calculates 2D/3D Euclidean distance between two landmark coordinate dictionaries or objects.
        """
        if p1 is None or p2 is None:
            return 0.0
        
        x1 = getattr(p1, 'x', p1.get('x', 0) if isinstance(p1, dict) else p1[0])
        y1 = getattr(p1, 'y', p1.get('y', 0) if isinstance(p1, dict) else p1[1])
        z1 = getattr(p1, 'z', p1.get('z', 0) if isinstance(p1, dict) else (p1[2] if len(p1) > 2 else 0))
        
        x2 = getattr(p2, 'x', p2.get('x', 0) if isinstance(p2, dict) else p2[0])
        y2 = getattr(p2, 'y', p2.get('y', 0) if isinstance(p2, dict) else p2[1])
        z2 = getattr(p2, 'z', p2.get('z', 0) if isinstance(p2, dict) else (p2[2] if len(p2) > 2 else 0))
        
        return math.sqrt((x1 - x2)**2 + (y1 - y2)**2 + (z1 - z2)**2)

    def calculate_lip_aperture(self, face_landmarks_history: list) -> list:
        """
        Processes facial landmarks history across frames to extract lip aperture ratio values.
        LVD = EuclideanDistance(upper_lip, lower_lip)
        LHD = EuclideanDistance(left_corner, right_corner)
        Lip Aperture Ratio = LVD / LHD
        """
        ratios = []
        for landmarks in face_landmarks_history:
            if not landmarks:
                ratios.append(0.0)
                continue
            
            try:
                p_upper = landmarks[self.upper_lip_idx]
                p_lower = landmarks[self.lower_lip_idx]
                p_left = landmarks[self.left_corner_idx]
                p_right = landmarks[self.right_corner_idx]

                v_dist = self.euclidean_distance(p_upper, p_lower)
                h_dist = self.euclidean_distance(p_left, p_right)

                ratio = (v_dist / h_dist) if h_dist > 1e-6 else 0.0
                ratios.append(float(ratio))
            except (IndexError, KeyError, TypeError):
                ratios.append(0.0)
                
        return ratios

    def verify_lip_movement(self, aperture_history: list) -> tuple[bool, float]:
        """
        Analyzes the variation in lip opening to confirm active physical movement, 
        detecting static photo attacks.
        Returns (is_active, variance).
        """
        if not aperture_history or len(aperture_history) < 2:
            return False, 0.0
            
        try:
            arr = np.array(aperture_history, dtype=float)
            if not np.all(np.isfinite(arr)):
                return False, 0.0
            variance = float(np.var(arr))
            if np.isnan(variance) or np.isinf(variance):
                return False, 0.0
            is_moving = bool(variance >= settings.MIN_LIP_VARIATION)
            return is_moving, variance
        except (ValueError, TypeError):
            return False, 0.0

lip_service = LipService()
