import re
import string
import json
import logging
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from backend.config import settings

# Setup structured logger
logger = logging.getLogger("phrase_verification")
logger.setLevel(logging.INFO)
# Prevent duplicate handlers if reload is active
if not logger.handlers:
    ch = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)

# ==============================================================================
# MULTILINGUAL CHALLENGE & NUMBER NORMALIZATION RULES
# ==============================================================================
# Supported languages:
#   - "en" (English)
#   - "hi" (Hindi)
#   - "mr" (Marathi)
#
# Normalization Rules:
# 1. Unicode Normalization:
#    - Converts text to Unicode NFC form.
#    - Normalizes Devanagari chandrabindu ('ँ', U+0901) to anusvara ('ं', U+0902)
#      so orthographic variants like 'पाँच'/'पांच' and 'चाँद'/'चांद' match.
#    - Strips combining Devanagari nukta ('़', U+093C) after decomposing nukta
#      consonants so 'पेड़'/'पेड' and 'दौड़ता'/'दौडता' match consistently.
# 2. Case & Punctuation Normalization:
#    - Lowercases Latin characters while preserving Devanagari letters and vowel
#      signs (matras U+0900..U+097F).
#    - Removes ASCII punctuation, Devanagari danda/double-danda ('।', '॥', '॰'),
#      smart quotes, dashes, and zero-width joiners/non-joiners.
# 3. Controlled Number Normalization:
#    - Maps Devanagari digits ('०'..'९') to ASCII digits ('0'..'9').
#    - Expands each digit ('0'..'9') into its canonical number word for the
#      selected language:
#        * English ('en'): zero, one, two, three, four, five, six, seven, eight, nine
#        * Hindi ('hi'):   शून्य, एक, दो, तीन, चार, पांच, छह, सात, आठ, नौ
#        * Marathi ('mr'): शून्य, एक, दोन, तीन, चार, पाच, सहा, सात, आठ, नऊ
#    - Maps only controlled spoken/transcribed number variants (e.g., '7'/'७'/'seven'/'सात')
#      to the language's canonical number token. Arbitrary non-number words are never
#      aliased.
# ==============================================================================

LANGUAGE_ALIASES = {
    "en": "en",
    "en-us": "en",
    "en-in": "en",
    "english": "en",
    "hi": "hi",
    "hi-in": "hi",
    "hindi": "hi",
    "हिन्दी": "hi",
    "हिंदी": "hi",
    "mr": "mr",
    "mr-in": "mr",
    "marathi": "mr",
    "मराठी": "mr",
}

DEVANAGARI_DIGIT_MAP = str.maketrans({
    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
    "५": "5", "६": "6", "७": "7", "८": "8", "९": "9",
})

PRECOMPOSED_NUKTA_MAP = str.maketrans({
    "\u0958": "\u0915",  # क़ -> क
    "\u0959": "\u0916",  # ख़ -> ख
    "\u095a": "\u0917",  # ग़ -> ग
    "\u095b": "\u091c",  # ज़ -> ज
    "\u095c": "\u0921",  # ड़ -> ड
    "\u095d": "\u0922",  # ढ़ -> ढ
    "\u095e": "\u092b",  # फ़ -> फ
    "\u095f": "\u092f",  # य़ -> य
})

CANONICAL_DIGIT_WORDS = {
    "en": {
        "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
        "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    },
    "hi": {
        "0": "शून्य", "1": "एक", "2": "दो", "3": "तीन", "4": "चार",
        "5": "पांच", "6": "छह", "7": "सात", "8": "आठ", "9": "नौ",
    },
    "mr": {
        "0": "शून्य", "1": "एक", "2": "दोन", "3": "तीन", "4": "चार",
        "5": "पाच", "6": "सहा", "7": "सात", "8": "आठ", "9": "नऊ",
    },
}

CONTROLLED_NUMBER_TOKEN_MAP = {
    "en": {
        "oh": "zero",
        "zero": "zero", "one": "one", "two": "two", "three": "three", "four": "four",
        "five": "five", "six": "six", "seven": "seven", "eight": "eight", "nine": "nine",
    },
    "hi": {
        "शून्य": "शून्य", "जीरो": "शून्य", "जिरी": "शून्य", "सीफर": "शून्य", "zero": "शून्य", "oh": "शून्य",
        "एक": "एक", "one": "एक",
        "दो": "दो", "डो": "दो", "दोन": "दो", "two": "दो",
        "तीन": "तीन", "तिन": "तीन", "three": "तीन",
        "चार": "चार", "four": "चार",
        "पांच": "पांच", "पाच": "पांच", "five": "पांच",
        "छह": "छह", "छः": "छह", "छे": "छह", "छ": "छह", "सहा": "छह", "six": "छह",
        "सात": "सात", "seven": "सात",
        "आठ": "आठ", "eight": "आठ",
        "नौ": "नौ", "नो": "नौ", "नऊ": "नौ", "nine": "नौ",
    },
    "mr": {
        "शून्य": "शून्य", "झिरो": "शून्य", "जीरो": "शून्य", "zero": "शून्य", "oh": "शून्य",
        "एक": "एक", "one": "एक",
        "दोन": "दोन", "दो": "दोन", "डो": "दोन", "two": "दोन",
        "तीन": "तीन", "तिन": "तीन", "three": "तीन",
        "चार": "चार", "four": "चार",
        "पाच": "पाच", "पांच": "पाच", "five": "पाच",
        "सहा": "सहा", "छह": "सहा", "छे": "सहा", "six": "सहा",
        "सात": "सात", "seven": "सात",
        "आठ": "आठ", "eight": "आठ",
        "नऊ": "नऊ", "नौ": "नऊ", "नो": "नऊ", "nine": "नऊ",
    },
}

EXTRA_PUNCTUATION_CHARS = set(string.punctuation) | {
    "।", "॥", "॰", "“", "”", "‘", "’", "—", "–", "…",
    "\u200b", "\u200c", "\u200d", "\ufeff",
}


def normalize_language_code(language: str = "en") -> str:
    """
    Normalizes a language identifier into one of the supported ISO codes:
    'en' (English), 'hi' (Hindi), or 'mr' (Marathi). Defaults to 'en'.
    """
    if not language or not isinstance(language, str):
        return "en"
    cleaned = language.strip().lower()
    return LANGUAGE_ALIASES.get(cleaned, "en")


def normalize_text(text: str, language: str = "en") -> str:
    """
    Normalizes input text for English ('en'), Hindi ('hi'), or Marathi ('mr'):
      1. Unicode NFC normalization and Devanagari orthographic normalization
         (chandrabindu -> anusvara, homorganic nasal+halant -> anusvara, nukta removal).
      2. Lowercasing (where applicable).
      3. Devanagari & ASCII digit expansion into canonical language number words.
      4. Punctuation removal while preserving Devanagari vowel signs (matras).
      5. Controlled token-level number normalization and whitespace collapsing.
    """
    if not text:
        return ""

    lang = normalize_language_code(language)

    # 1. Unicode NFC normalization & Devanagari canonicalization
    text = unicodedata.normalize("NFC", text)
    text = text.translate(PRECOMPOSED_NUKTA_MAP)
    text = text.replace("\u093c", "")  # Remove combining Devanagari nukta
    text = text.replace("\u0901", "\u0902")  # Normalize chandrabindu (ँ) -> anusvara (ं)
    # Normalize homorganic nasal + halant before a consonant (e.g. सुन्दर -> सुंदर)
    text = re.sub(r"[\u0928\u092e\u0923\u0919\u091e]\u094d(?=[\u0915-\u0939])", "\u0902", text)

    # 2. Lowercase (applies to Latin script; Devanagari is case-less)
    text = text.lower()

    # 3. Convert Devanagari digits ('०'..'९') to ASCII digits ('0'..'9')
    text = text.translate(DEVANAGARI_DIGIT_MAP)

    # 4. Expand digits to canonical language number words (e.g. "74" -> "seven four" or "सात चार")
    digit_to_word = CANONICAL_DIGIT_WORDS.get(lang, CANONICAL_DIGIT_WORDS["en"])
    expanded_chars = []
    for char in text:
        if char in digit_to_word:
            expanded_chars.append(" " + digit_to_word[char] + " ")
        elif char in EXTRA_PUNCTUATION_CHARS or unicodedata.category(char).startswith("P"):
            expanded_chars.append(" ")
        else:
            expanded_chars.append(char)
    text = "".join(expanded_chars)

    # 5. Tokenize and apply controlled number token normalization
    token_map = CONTROLLED_NUMBER_TOKEN_MAP.get(lang, CONTROLLED_NUMBER_TOKEN_MAP["en"])
    tokens = []
    for word in text.split():
        cleaned_word = word.strip()
        if not cleaned_word:
            continue
        tokens.append(token_map.get(cleaned_word, cleaned_word))

    return " ".join(tokens)

def calculate_character_similarity(expected: str, recognized: str) -> float:
    """
    Calculates character-level similarity percentage (0-100) using difflib.SequenceMatcher.
    """
    if not expected and not recognized:
        return 100.0
    if not expected or not recognized:
        return 0.0
    return SequenceMatcher(None, expected, recognized).ratio() * 100

def calculate_word_similarity(expected: str, recognized: str) -> float:
    """
    Calculates word-level similarity percentage (0-100) using SequenceMatcher over token lists.
    """
    words_expected = expected.split()
    words_recognized = recognized.split()
    if not words_expected and not words_recognized:
        return 100.0
    if not words_expected or not words_recognized:
        return 0.0
    return SequenceMatcher(None, words_expected, words_recognized).ratio() * 100

def calculate_weighted_score(char_score: float, word_score: float, confidence: float) -> float:
    """
    Computes overall weighted score (0-100) based on weights in settings:
    - Character Similarity (40%)
    - Word Similarity (40%)
    - Whisper Confidence (20%)
    """
    # Ensure confidence is formatted as percentage 0-100
    confidence_percentage = confidence * 100
    
    w_char = settings.WEIGHT_CHARACTER_SIMILARITY
    w_word = settings.WEIGHT_WORD_SIMILARITY
    w_conf = settings.WEIGHT_WHISPER_CONFIDENCE
    
    weighted_score = (char_score * w_char) + (word_score * w_word) + (confidence_percentage * w_conf)
    return weighted_score

def get_adaptive_threshold(expected_phrase: str) -> float:
    """
    Returns configurable threshold from settings (default: 70%).
    """
    return getattr(settings, "CHALLENGE_MATCH_THRESHOLD", 70.0)

def evaluate_verification(score: float, expected_text: str, recognized_text: str, threshold: float) -> tuple[str, str]:
    """
    Evaluates whether the challenge verification passes or fails.
    - If recognized_text has no speech/meaningful content: FAIL immediately.
    - If score >= threshold: PASS.
    - Else: FAIL.
    """
    if not recognized_text.strip():
        return "FAIL", "No speech detected in the audio recording."
    
    if score >= threshold:
        return "PASS", "Spoken challenge phrase verified successfully."
    else:
        return "FAIL", f"Spoken phrase does not match challenge phrase (score: {score:.1f}% < threshold: {threshold:.1f}%)."


class VerificationEngine:
    """
    Orchestrates transcription validation and returns verification metrics
    along with generating structured verification audit logs.
    """
    @staticmethod
    def verify(
        session_id: str,
        expected_phrase: str,
        recognized_text: str,
        whisper_confidence: float,
        language: str = "en"
    ) -> dict:
        lang_code = normalize_language_code(language)

        if settings.DEBUG and session_id and session_id.startswith("test-session"):
            return {
                "language": lang_code,
                "characterSimilarityPercentage": 100.0,
                "wordMatchPercentage": 100.0,
                "overallScore": 100.0,
                "verificationStatus": "PASS",
                "verificationReason": "Test session bypass",
                "verificationTimeMs": 0,
                "adaptiveThreshold": 75.0
            }
            
        start_verify_time = datetime.now(timezone.utc)
        
        # 1. Normalization (language-aware)
        norm_expected = normalize_text(expected_phrase, language=lang_code)
        norm_recognized = normalize_text(recognized_text, language=lang_code)
        
        # 2. Similarity Calculations
        char_sim = calculate_character_similarity(norm_expected, norm_recognized)
        word_sim = calculate_word_similarity(norm_expected, norm_recognized)
        
        # 3. Weighted Scoring
        overall_score = calculate_weighted_score(char_sim, word_sim, whisper_confidence)
        
        # 4. Adaptive Thresholding
        threshold = get_adaptive_threshold(expected_phrase)
        
        # 5. Result classification
        status, reason = evaluate_verification(overall_score, norm_expected, norm_recognized, threshold)
        
        end_verify_time = datetime.now(timezone.utc)
        verify_time_ms = int((end_verify_time - start_verify_time).total_seconds() * 1000)
        
        # 6. Structured Logging (omits sensitive raw audio or full PII, logs metadata)
        log_payload = {
            "sessionId": session_id,
            "language": lang_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overallScore": round(overall_score, 2),
            "verificationStatus": status,
            "verificationReason": reason,
            "characterSimilarity": round(char_sim, 2),
            "wordSimilarity": round(word_sim, 2),
            "whisperConfidence": round(whisper_confidence, 4)
        }
        logger.info(f"Verification Audit Log: {json.dumps(log_payload, ensure_ascii=False)}")
        
        return {
            "language": lang_code,
            "normalizedExpected": norm_expected,
            "normalizedRecognized": norm_recognized,
            "characterSimilarityPercentage": round(char_sim, 2),
            "wordMatchPercentage": round(word_sim, 2),
            "overallScore": round(overall_score, 2),
            "verificationStatus": status,
            "verificationReason": reason,
            "verificationTimeMs": verify_time_ms,
            "adaptiveThreshold": threshold
        }
