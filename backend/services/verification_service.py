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
        "zero": "zero", "one": "one", "won": "one", "two": "two", "to": "two", "too": "two",
        "three": "three", "four": "four", "for": "four", "fore": "four",
        "five": "five", "six": "six", "seven": "seven", "eight": "eight", "ate": "eight", "nine": "nine",
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

    # 0. Strip zero-width joiners/spaces and soft hyphens
    for zw in ("\u200b", "\u200c", "\u200d", "\ufeff", "\u00ad"):
        text = text.replace(zw, "")

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

FILLER_WORDS = {
    "uh", "um", "ah", "er", "hmm", "well", "okay", "ok", "so", "yeah", "yes",
    "like", "right", "sure", "please", "thank", "thanks", "you",
    "अरे", "अच्छा", "हाँ", "हा", "हूँ", "हुं", "ओके",
    "बरं", "होय", "हो", "नक्की"
}

def strip_boundary_fillers(text: str, reference_text: str = "") -> str:
    """
    Strips leading and trailing conversational filler tokens from transcribed text
    if those tokens are not part of the reference challenge phrase.
    """
    ref_tokens = set(reference_text.split()) if reference_text else set()
    words = text.split()
    if not words:
        return ""
    start = 0
    while start < len(words) and words[start] in FILLER_WORDS and words[start] not in ref_tokens:
        start += 1
    end = len(words)
    while end > start and words[end - 1] in FILLER_WORDS and words[end - 1] not in ref_tokens:
        end -= 1
    return " ".join(words[start:end])

def _devanagari_fuzzy_canonical(w: str) -> str:
    """
    Phonetic canonicalization for Devanagari words to resolve minor
    ASR vowel-length alternations (short/long i, short/long u, e/ai, o/au, nukta, visarga).
    """
    w = w.replace("\u0940", "\u093f")  # ी -> ि
    w = w.replace("\u0942", "\u0941")  # ू -> ु
    w = w.replace("\u0948", "\u0947")  # ै -> े
    w = w.replace("\u094c", "\u094b")  # ौ -> ो
    w = w.replace("\u0937", "\u0936")  # ष -> श
    w = w.replace("\u0903", "")        # remove visarga ः
    w = w.replace("\u093d", "")        # remove avagraha ऽ
    if len(w) > 2 and w.endswith("\u0902"):  # trailing anusvara on plural/inflection
        w = w[:-1]
    return w

def calculate_fuzzy_word_match(expected_words: list, recognized_words: list) -> float:
    """
    Calculates token-level fuzzy match ratio where minor morphological variations
    and Devanagari vowel-length transcription alternations are granted match credit.
    """
    if not expected_words and not recognized_words:
        return 1.0
    if not expected_words or not recognized_words:
        return 0.0

    matched_credits = 0.0
    used_indices = set()
    for ew in expected_words:
        best_sim = 0.0
        best_idx = -1
        ew_canon = _devanagari_fuzzy_canonical(ew)
        for idx, rw in enumerate(recognized_words):
            if idx in used_indices:
                continue
            if ew == rw or (ew_canon and ew_canon == _devanagari_fuzzy_canonical(rw)):
                best_sim = 1.0
                best_idx = idx
                break
            # Standard sequence matcher ratio
            sim_std = SequenceMatcher(None, ew, rw).ratio()
            # Devanagari canonical sequence matcher ratio
            sim_canon = SequenceMatcher(None, ew_canon, _devanagari_fuzzy_canonical(rw)).ratio()
            sim = max(sim_std, sim_canon)
            if sim >= 0.75 and sim > best_sim:
                best_sim = sim
                best_idx = idx
        if best_idx != -1 and best_sim >= 0.75:
            used_indices.add(best_idx)
            matched_credits += 1.0 if best_sim >= 0.90 else 0.85

    return (2.0 * matched_credits) / (len(expected_words) + len(recognized_words))

def calculate_character_similarity(expected: str, recognized: str) -> float:
    """
    Calculates character-level similarity percentage (0-100) using difflib.SequenceMatcher,
    augmented with best-window matching if recognized string has leading/trailing context.
    """
    if not expected and not recognized:
        return 100.0
    if not expected or not recognized:
        return 0.0

    global_ratio = SequenceMatcher(None, expected, recognized).ratio()
    canon_exp = _devanagari_fuzzy_canonical(expected)
    canon_rec = _devanagari_fuzzy_canonical(recognized)
    canon_ratio = SequenceMatcher(None, canon_exp, canon_rec).ratio() if canon_exp else 0.0
    best_ratio = max(global_ratio, canon_ratio)

    # If recognized text is longer than expected, evaluate sliding windows
    len_exp = len(expected)
    len_rec = len(recognized)
    if len_rec > len_exp:
        if expected in recognized:
            return 100.0
        margin = int(len_exp * 0.3) + 5
        min_win = max(1, len_exp - margin)
        max_win = min(len_rec, len_exp + margin)
        step = max(1, len_exp // 10)
        for w_len in range(min_win, max_win + 1, step):
            for start in range(0, len_rec - w_len + 1, step):
                sub = recognized[start:start + w_len]
                ratio = SequenceMatcher(None, expected, sub).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio

    return float(best_ratio * 100.0)

def calculate_word_similarity(expected: str, recognized: str) -> float:
    """
    Calculates word-level similarity percentage (0-100) using SequenceMatcher over token lists,
    augmented with sliding-window alignment and fuzzy token morphology matching.
    """
    words_expected = expected.split()
    words_recognized = recognized.split()
    if not words_expected and not words_recognized:
        return 100.0
    if not words_expected or not words_recognized:
        return 0.0

    # 1. Base SequenceMatcher ratio
    base_ratio = SequenceMatcher(None, words_expected, words_recognized).ratio()

    # 2. Fuzzy word match ratio over full text
    fuzzy_ratio = calculate_fuzzy_word_match(words_expected, words_recognized)
    best_ratio = max(base_ratio, fuzzy_ratio)

    # 3. If recognized text has extra words, check sliding windows
    n_exp = len(words_expected)
    n_rec = len(words_recognized)
    if n_rec > n_exp:
        min_win = max(1, n_exp - 1)
        max_win = min(n_rec, n_exp + 2)
        for w_len in range(min_win, max_win + 1):
            for start in range(n_rec - w_len + 1):
                window = words_recognized[start:start + w_len]
                w_base = SequenceMatcher(None, words_expected, window).ratio()
                w_fuz = calculate_fuzzy_word_match(words_expected, window)
                win_score = max(w_base, w_fuz)
                if win_score > best_ratio:
                    best_ratio = win_score

    return float(best_ratio * 100.0)

def calculate_weighted_score(char_score: float, word_score: float, confidence: float) -> float:
    """
    Computes overall weighted score (0-100) based on weights in settings:
    - Character Similarity (40%)
    - Word Similarity (40%)
    - Whisper Confidence (20%)
    
    If text match is high (char and word scores >= 75%), Whisper's acoustic confidence
    alone will not drag a correct recitation below the verification threshold.
    """
    confidence_percentage = confidence * 100
    
    w_char = settings.WEIGHT_CHARACTER_SIMILARITY
    w_word = settings.WEIGHT_WORD_SIMILARITY
    w_conf = settings.WEIGHT_WHISPER_CONFIDENCE
    
    standard_score = (char_score * w_char) + (word_score * w_word) + (confidence_percentage * w_conf)
    
    # Textual similarity score
    text_score = (char_score * 0.5) + (word_score * 0.5)
    if text_score >= 60.0:
        blended_score = (text_score * 0.90) + (confidence_percentage * 0.10)
        return float(max(standard_score, blended_score))
    
    return float(standard_score)

def get_adaptive_threshold(expected_phrase: str) -> float:
    """
    Returns configurable threshold from settings (default: 70%).
    """
    return getattr(settings, "CHALLENGE_MATCH_THRESHOLD", 70.0)

def evaluate_verification(score: float, expected_text: str, recognized_text: str, threshold: float, raw_recognized: str = "") -> tuple[str, str]:
    """
    Evaluates whether the challenge verification passes or fails.
    - If recognized_text has no speech/meaningful content: FAIL immediately.
    - If score >= threshold: PASS.
    - Else: FAIL.
    """
    cleaned = (recognized_text or "").strip().lower()
    raw_lower = (raw_recognized or recognized_text or "").strip().lower()

    # Whisper silence hallucination patterns
    hallucination_keywords = ["blank_audio", "thank you", "watching", "subtitles", "inaudible", "blank audio"]
    is_hallucination = (
        not cleaned or
        any(h in raw_lower or h in cleaned for h in hallucination_keywords)
    )
    if is_hallucination and not any(k in (expected_text or "").lower() for k in ["thank you", "watching", "subtitles"]):
        return "FAIL", "No audible speech detected. Please speak the challenge phrase clearly."
    
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
        start_verify_time = datetime.now(timezone.utc)
        
        # 1. Normalization (language-aware)
        norm_expected = normalize_text(expected_phrase, language=lang_code)
        norm_recognized = normalize_text(recognized_text, language=lang_code)
        cleaned_recognized = strip_boundary_fillers(norm_recognized, norm_expected)
        
        # 2. Similarity Calculations
        char_sim = calculate_character_similarity(norm_expected, norm_recognized)
        word_sim = calculate_word_similarity(norm_expected, norm_recognized)
        if cleaned_recognized and cleaned_recognized != norm_recognized:
            clean_char_sim = calculate_character_similarity(norm_expected, cleaned_recognized)
            clean_word_sim = calculate_word_similarity(norm_expected, cleaned_recognized)
            char_sim = max(char_sim, clean_char_sim)
            word_sim = max(word_sim, clean_word_sim)
        
        # 3. Weighted Scoring
        overall_score = calculate_weighted_score(char_sim, word_sim, whisper_confidence)
        
        # 4. Adaptive Thresholding
        threshold = get_adaptive_threshold(expected_phrase)
        
        # 5. Result classification
        status, reason = evaluate_verification(overall_score, norm_expected, norm_recognized, threshold, raw_recognized=recognized_text)
        if "No audible speech detected" in reason:
            overall_score = 0.0
            char_sim = 0.0
            word_sim = 0.0
        
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
