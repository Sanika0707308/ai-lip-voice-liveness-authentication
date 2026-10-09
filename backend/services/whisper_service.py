import os
import time
import math
from faster_whisper import WhisperModel
from backend.config import settings
from backend.services.verification_service import normalize_language_code, normalize_text

class WhisperService:
    """
    Service integrating Faster Whisper models to transcribe the speech 
    contained within the uploaded audio track and check matching correctness.
    Supports multilingual transcription for English ('en'), Hindi ('hi'), and Marathi ('mr').
    """
    
    _INITIAL_PROMPTS = {
        "hi": "नमस्ते, नीला आकाश एक दो तीन चार पांच छह सात आठ नौ।",
        "mr": "नमस्कार, निळा आकाश एक दोन तीन चार पाच सहा सात आठ नऊ.",
    }

    def __init__(self):
        self.model = None
        self.multilingual_model = None
        
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

    def _get_model_for_language(self, lang_code: str):
        """
        Returns the primary model for English ('en') and the multilingual model
        (e.g. 'small') for Hindi ('hi') and Marathi ('mr') so Devanagari script
        output is accurate, falling back to self.model if needed.
        """
        if self.model is None:
            self.load_model()
        if lang_code in ("hi", "mr"):
            ml_name = getattr(settings, "WHISPER_MULTILINGUAL_MODEL", settings.WHISPER_MODEL)
            if ml_name == settings.WHISPER_MODEL:
                return self.model
            if self.multilingual_model is None:
                try:
                    print(f"Loading Faster Whisper multilingual model '{ml_name}' on CPU...")
                    self.multilingual_model = WhisperModel(ml_name, device="cpu", compute_type="int8")
                    print(f"Faster Whisper multilingual model '{ml_name}' loaded successfully.")
                except Exception as e:
                    print(f"Could not load multilingual model '{ml_name}', falling back to '{settings.WHISPER_MODEL}': {e}")
                    self.multilingual_model = self.model
            return self.multilingual_model
        return self.model
        
    def transcribe_audio(self, audio_filepath: str, language: str = "en") -> dict:
        """
        Transcribes the audio track using the loaded Faster Whisper model
        with the selected language ('en', 'hi', or 'mr').
        
        Inputs:
            audio_filepath (str): Path to the temporary audio file.
            language (str): Language code ('en', 'hi', 'mr'). Defaults to 'en'.
            
        Outputs:
            dict: Structured transcription results dictionary.
        """
        if self.model is None:
            self.load_model()
            
        if not os.path.exists(audio_filepath):
            raise FileNotFoundError(f"Audio file not found: {audio_filepath}")
            
        lang_code = normalize_language_code(language)
        active_model = self._get_model_for_language(lang_code)
        initial_prompt = self._INITIAL_PROMPTS.get(lang_code)
        start_time = time.time()
        
        try:
            # transcribe returns a generator (segments) and transcription info
            # Use vad_filter=True and condition_on_previous_text=False to prevent
            # non-speech/silence hallucination loops across English, Hindi, and Marathi
            try:
                segments, info = active_model.transcribe(
                    audio_filepath,
                    beam_size=5,
                    language=lang_code,
                    initial_prompt=initial_prompt,
                    vad_filter=True,
                    condition_on_previous_text=False
                )
                segments_list = list(segments)
            except Exception:
                segments, info = active_model.transcribe(
                    audio_filepath,
                    beam_size=5,
                    language=lang_code,
                    initial_prompt=initial_prompt,
                    condition_on_previous_text=False
                )
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
                "language": lang_code,
                "detected_language": getattr(info, "language", lang_code),
                "processing_time_ms": processing_time_ms,
                "confidence": confidence,
                "duration": info.duration,
                "speech_start_sec": speech_start_sec,
                "speech_end_sec": speech_end_sec
            }
        except Exception as e:
            print(f"Error during audio transcription: {e}")
            raise e


    def verify_speech_match(self, transcription: str, challenge_phrase: str, language: str = "en") -> bool:
        """
        Compares the transcribed speech with the challenge text using
        language-aware Unicode and number normalization.
        """
        if not transcription or not challenge_phrase:
            return False
        t_clean = normalize_text(transcription, language=language)
        c_clean = normalize_text(challenge_phrase, language=language)
        return bool(t_clean and t_clean == c_clean)

# Global singleton instance
whisper_service = WhisperService()
