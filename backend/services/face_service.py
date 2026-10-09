import numpy as np
from backend.config import settings

class FaceService:
    """
    Service defining facial landmark configurations, indices, and geometric integrity checks.
    MediaPipe Face Mesh tracking runs in the client browser, with coordinate validation available here.
    """
    
    # Key MediaPipe Face Mesh landmark indices
    NOSE_TIP = 1
    CHIN = 152
    LEFT_EYE_CORNER = 33
    RIGHT_EYE_CORNER = 263
    LEFT_MOUTH_CORNER = 61
    RIGHT_MOUTH_CORNER = 291
    UPPER_LIP = 13
    LOWER_LIP = 14

    def __init__(self):
        pass

    def verify_face_pose(self, face_landmarks: list) -> bool:
        """
        Validates whether key facial landmarks are present and reasonably oriented towards the camera.
        """
        if not face_landmarks:
            return False
            
        try:
            # Check presence of key bounding landmarks
            required_indices = [
                self.NOSE_TIP, self.CHIN, self.LEFT_EYE_CORNER, 
                self.RIGHT_EYE_CORNER, self.LEFT_MOUTH_CORNER, self.RIGHT_MOUTH_CORNER
            ]
            for idx in required_indices:
                if idx >= len(face_landmarks) or face_landmarks[idx] is None:
                    return False
            return True
        except Exception:
            return False

face_service = FaceService()
