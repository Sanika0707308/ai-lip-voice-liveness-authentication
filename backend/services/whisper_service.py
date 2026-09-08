import os
import time
import math
from faster_whisper import WhisperModel
from backend.config import settings

class WhisperService:
    """
    Service integrating Faster Whisper models to transcribe the speech 
    contained within the uploaded audio track and check matching correctness.
    """
    
    def __init__(self):
        self.model = None
        
    def load_model(self):
        """
        Loads the configured Faster Whisper model into memory on startup.
        Ensures model is loaded only once.
        """
        if self.model is None:
            model_name = settings.WHISPER_MODEL
            print(f"Loading Faster Whisper model '{model_name}' on CPU...")
            try:
                # Force CPU execution with int8 quantization for compatibility and performance
                self.model = WhisperModel(model_name, device="cpu", compute_type="int8")
                print(f"Faster Whisper model '{model_name}' loaded successfully.")
            except Exception as e:
                print(f"Failed to load Faster Whisper model '{model_name}': {e}")
                raise e
        
    def transcribe_audio(self, audio_filepath: str) -> dict:
        """
        Transcribes the audio track using the loaded Faster Whisper model.
        
        Inputs:
            audio_filepath (str): Path to the temporary audio file.
            
        Outputs:
            dict: Structured transcription results dictionary.
        """
        if self.model is None:
            self.load_model()
            
        if not os.path.exists(audio_filepath):
            raise FileNotFoundError(f"Audio file not found: {audio_filepath}")
            
        start_time = time.time()
        
        try:
            # transcribe returns a generator (segments) and transcription info
            segments, info = self.model.transcribe(audio_filepath, beam_size=5, language="en")
            # Transcription happens lazily, force execution by listing segments
            segments_list = list(segments)
            
            # Combine segments into single string
            text = " ".join([segment.text for segment in segments_list]).strip()
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            # Calculate segment log probabilities to confidence score
            logprobs = [seg.avg_logprob for seg in segments_list if hasattr(seg, "avg_logprob")]
            confidence = sum([math.exp(lp) for lp in logprobs]) / len(logprobs) if logprobs else 0.0
            confidence = max(0.0, min(1.0, confidence))
            
            speech_start_sec = segments_list[0].start if segments_list else None
            speech_end_sec = segments_list[-1].end if segments_list else None
            
            return {
                "success": True,
                "text": text,
                "language": info.language,
                "processing_time_ms": processing_time_ms,
                "confidence": confidence,
                "duration": info.duration,
                "speech_start_sec": speech_start_sec,
                "speech_end_sec": speech_end_sec
            }
        except Exception as e:
            print(f"Error during audio transcription: {e}")
            raise e


    def verify_speech_match(self, transcription: str, challenge_phrase: str) -> bool:
        """
        Compares the transcribed speech with the challenge text, ensuring 
        similarity (simple word overlap or lowercase equality).
        """
        if not transcription or not challenge_phrase:
            return False
        # Strip punctuation, convert to lowercase and strip whitespace
        t_clean = "".join([c.lower() for c in transcription if c.isalnum() or c.isspace()]).strip()
        c_clean = "".join([c.lower() for c in challenge_phrase if c.isalnum() or c.isspace()]).strip()
        return t_clean == c_clean

# Global singleton instance
whisper_service = WhisperService()
