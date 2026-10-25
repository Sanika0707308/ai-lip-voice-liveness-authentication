"""
Dynamic Random Challenge Phrase Generation Service.
Generates meaningful, everyday, grammatically correct sentences (4-7 words)
WITHOUT any numbers, digits, or numeric codes.

Supported languages:
- English ('en'): Everyday phrases (e.g. "Today is a beautiful day", "I enjoy reading books")
- Hindi ('hi'): Everyday phrases (e.g. "आज का दिन बहुत सुंदर है", "मुझे किताबें पढ़ना बहुत पसंद है")
- Marathi ('mr'): Everyday phrases (e.g. "आजचा दिवस छान आहे", "मला गाणी ऐकायला आवडतात")

Features:
- Randomized natural grammatical templates with rich, pronounceable word pools.
- Thread-safe sliding window duplicate avoidance to prevent repeating recent phrases.
- Backend association with unique attempt IDs and short expiration (TTL).
- Anti-tampering and anti-replay protection.
"""

import random
import threading
from collections import deque
from typing import Tuple, List, Dict, Optional

from backend.config import settings
from backend.services.attempt_tracker import attempt_tracker
from backend.services.verification_service import normalize_language_code

# ==============================================================================
# 1. LEGACY EXPORTS (Maintained for strict backward compatibility)
# ==============================================================================
ADJECTIVES: List[str] = [
    "blue", "green", "red", "yellow", "white", "black", "quick", "lazy",
    "bright", "dark", "warm", "cold", "silent", "loud", "heavy", "light"
]
NOUNS: List[str] = [
    "tiger", "apple", "cloud", "bird", "river", "mountain", "forest", "shadow",
    "sun", "moon", "star", "wind", "rain", "ocean", "tree", "flower"
]
VERBS: List[str] = [
    "runs", "shines", "moves", "flies", "sleeps", "walks", "sings", "dances",
    "grows", "falls", "rises", "flows", "glows", "leaps", "stands", "hides"
]
HI_ADJECTIVES: List[str] = [
    "नीला", "हरा", "लाल", "पीला", "सफेद", "काला", "तेज", "शांत",
    "चमकीला", "गर्म", "ठंडा", "सुंदर", "बड़ा", "छोटा", "नया", "साफ"
]
HI_NOUNS: List[str] = [
    "बाघ", "कमल", "बादल", "पक्षी", "नदी", "पर्वत", "जंगल", "सूरज",
    "चांद", "तारा", "हवा", "बारिश", "सागर", "पेड़", "फूल", "आकाश"
]
HI_VERBS: List[str] = [
    "दौड़ता", "चमकता", "चलता", "उड़ता", "गाता", "नाचता", "बढ़ता", "बहता",
    "खिलता", "दिखता", "आता", "जाता", "बोलता", "खेलता", "रुकता", "हंसता"
]
HI_NUMBERS: List[str] = [
    "शून्य", "एक", "दो", "तीन", "चार", "पांच", "छह", "सात", "आठ", "नौ"
]
MR_ADJECTIVES: List[str] = [
    "निळा", "हिरवा", "लाल", "पिवळा", "पांढरा", "काळा", "वेगवान", "शांत",
    "तेजस्वी", "उबदार", "थंड", "सुंदर", "मोठा", "लहान", "नवीन", "स्वच्छ"
]
MR_NOUNS: List[str] = [
    "वाघ", "कमळ", "ढग", "पक्षी", "नदी", "डोंगर", "जंगल", "सूर्य",
    "चंद्र", "तारा", "वारा", "पाऊस", "सागर", "झाड", "फूल", "आकाश"
]
MR_VERBS: List[str] = [
    "धावतो", "चमकतो", "चालतो", "उडतो", "गातो", "नाचतो", "वाढतो", "वाहतो",
    "फुलतो", "दिसतो", "येतो", "जातो", "बोलतो", "खेळतो", "थांबतो", "हसतो"
]
MR_NUMBERS: List[str] = [
    "शून्य", "एक", "दोन", "तीन", "चार", "पाच", "सहा", "सात", "आठ", "नऊ"
]

# ==============================================================================
# 2. DUPLICATE AVOIDANCE ENGINE
# ==============================================================================
class DuplicateFilter:
    """
    Sliding window cache that remembers recent phrases per language
    to avoid consecutive repetitions across generation requests.
    """
    def __init__(self, capacity: int = 100):
        self._capacity = capacity
        self._history: Dict[str, deque] = {
            "en": deque(maxlen=capacity),
            "hi": deque(maxlen=capacity),
            "mr": deque(maxlen=capacity),
        }
        self._lock = threading.Lock()

    def is_recent(self, language: str, phrase: str) -> bool:
        norm = phrase.strip().lower()
        with self._lock:
            q = self._history.setdefault(language, deque(maxlen=self._capacity))
            return norm in q

    def record(self, language: str, phrase: str) -> None:
        norm = phrase.strip().lower()
        with self._lock:
            q = self._history.setdefault(language, deque(maxlen=self._capacity))
            q.append(norm)

    def clear(self) -> None:
        with self._lock:
            for q in self._history.values():
                q.clear()

duplicate_filter = DuplicateFilter(capacity=100)

# ==============================================================================
# 3. ENGLISH NATURAL EVERYDAY SENTENCE GENERATION (4-7 WORDS, NO NUMBERS)
# ==============================================================================

EN_TIME_SUBJECTS = ["Today", "This morning", "The weather", "The morning", "This afternoon", "The evening", "Our day", "The sunny afternoon"]
EN_QUALIFIERS = ["is a", "is truly a", "is a very", "feels like a", "brings us a", "remains a very"]
EN_ADJ_EVERYDAY = ["beautiful", "wonderful", "pleasant", "peaceful", "delightful", "bright", "sunny", "lovely", "cheerful", "calm", "refreshing", "splendid"]
EN_NOUN_TIME = ["day", "morning", "time", "moment", "season", "experience", "feeling"]

EN_PEOPLE_SUBJECTS = ["I", "We", "Good friends", "Young children", "Many people"]
EN_ENJOY_VERBS = ["enjoy", "love", "like"]
EN_ACTION_OBJECTS = [
    ("reading", "good books"),
    ("reading", "new books"),
    ("listening to", "gentle music"),
    ("listening to", "sweet songs"),
    ("walking in", "green nature"),
    ("walking in", "the quiet park"),
    ("learning", "new skills"),
    ("watching", "the blue sky"),
    ("drinking", "fresh morning tea"),
    ("sharing", "happy moments"),
]

EN_NATURE_SENTENCES = [
    ("Birds sing", "softly in the morning"),
    ("Gentle breeze", "cools the warm afternoon"),
    ("Fresh water", "flows down the river"),
    ("The green trees", "sway in the wind"),
    ("Golden sunlight", "warms the quiet valley"),
    ("Bright stars", "shine in the night"),
    ("Spring flowers", "bloom across the garden"),
    ("Gentle rain", "waters the dry earth"),
    ("The morning sun", "lights up the sky"),
    ("White clouds", "drift across the sky"),
]

EN_WISDOM_SENTENCES = [
    ("Kind words", "bring peace to everyone"),
    ("Good habits", "lead to great success"),
    ("A warm smile", "makes everyone feel happy"),
    ("Daily practice", "builds confidence and strength"),
    ("True friendship", "is a priceless treasure"),
    ("Healthy food", "keeps our body strong"),
    ("Learning new things", "opens many doors"),
    ("Peaceful thoughts", "bring comfort to life"),
]

EN_BASE_CANONICAL = [
    "Today is a beautiful day",
    "I enjoy reading books",
    "The weather is very pleasant today",
    "We love walking in nature",
    "I like listening to gentle music",
    "Birds sing sweetly in the morning",
    "The morning air feels very fresh",
    "Good books inspire our creative minds",
    "Kind words brighten up any day",
    "Gentle water flows down the stream",
]

def generate_english_challenge() -> str:
    """
    Generates an everyday, grammatically correct English sentence (4-7 words)
    without numbers or digits.
    """
    for _ in range(60):
        mode = random.randint(1, 5)
        if mode == 1:
            # "Today is a beautiful day" template (4-6 words)
            s = random.choice(EN_TIME_SUBJECTS)
            q = random.choice(EN_QUALIFIERS)
            a = random.choice(EN_ADJ_EVERYDAY)
            n = random.choice(EN_NOUN_TIME)
            phrase = f"{s} {q} {a} {n}"
        elif mode == 2:
            # "I enjoy reading books" template (4-6 words)
            subj = random.choice(EN_PEOPLE_SUBJECTS)
            verb = random.choice(EN_ENJOY_VERBS)
            action, obj = random.choice(EN_ACTION_OBJECTS)
            phrase = f"{subj} {verb} {action} {obj}"
        elif mode == 3:
            # Nature templates (5-7 words)
            part1, part2 = random.choice(EN_NATURE_SENTENCES)
            phrase = f"{part1} {part2}"
        elif mode == 4:
            # Wisdom / everyday life templates (5-7 words)
            part1, part2 = random.choice(EN_WISDOM_SENTENCES)
            phrase = f"{part1} {part2}"
        else:
            # Canonical everyday sentences
            phrase = random.choice(EN_BASE_CANONICAL)

        tokens = phrase.split()
        if 4 <= len(tokens) <= 7 and not any(ch.isdigit() for ch in phrase):
            if not duplicate_filter.is_recent("en", phrase):
                duplicate_filter.record("en", phrase)
                return phrase

    # Fallback
    fallback = random.choice(EN_BASE_CANONICAL)
    duplicate_filter.record("en", fallback)
    return fallback

# ==============================================================================
# 4. HINDI NATURAL EVERYDAY SENTENCE GENERATION (4-7 WORDS, NO NUMBERS)
# ==============================================================================

HI_TIME_PREFIXES = ["आज का दिन", "आज की सुबह", "आज का मौसम", "सुबह की हवा", "आज की शाम", "यह सुंदर सुबह", "आज का वातावरण"]
HI_TIME_ADJECTIVES = ["बहुत सुंदर", "काफी सुहावना", "अतिशय शांत", "काफी प्यारा", "बहुत सुखद", "काफी अच्छा", "बहुत ताज़ा"]
HI_TIME_ENDINGS = ["है", "लगता है", "दिखता है", "महसूस होता है", "बना हुआ है"]

HI_TIME_PHRASES = [
    "आज का दिन बहुत सुंदर है",
    "आज का दिन काफी अच्छा है",
    "आज का मौसम बहुत सुहावना है",
    "मौसम आज काफी सुहावना और प्यारा है",
    "सुबह की हवा बहुत ताज़ा है",
    "आज की सुबह बहुत शांत है",
    "आज का दिन बहुत शुभ है",
    "शाम का समय बहुत सुखद लगता है",
    "नीला आकाश बहुत साफ दिखता है",
    "आज का सवेरा बहुत खुशनुमा है",
    "बगीचे का वातावरण बहुत शांत है",
    "यह सुबह मन को शांति देती है",
]

HI_ACTIVITY_SUBJECTS = ["मुझे", "हमे", "सभी को", "बच्चों को", "मेरे दोस्तों को", "हम सबको", "विद्यार्थियों को"]
HI_ACTIVITIES = [
    "किताबें पढ़ना",
    "अच्छी पुस्तकें पढ़ना",
    "मधुर संगीत सुनना",
    "सुबह टहलना",
    "प्रकृति में घूमना",
    "नई बातें सीखना",
    "पेड़ पौधे लगाना",
    "सुंदर चित्र बनाना",
    "कहानियां सुनना",
    "सवेरे व्यायाम करना",
    "ज्ञानवर्धक बातें सीखना",
    "साथ मिलकर पढ़ना",
]
HI_ACTIVITY_ENDINGS = ["बहुत पसंद है", "अच्छा लगता है", "काफी पसंद है", "आनंद देता है", "मन को भाता है"]

HI_NATURE_PHRASES = [
    "सुबह सूरज पूरब से निकलता है",
    "नदी का पानी बहुत मीठा है",
    "हरे भरे पेड़ शीतल छाया देते हैं",
    "सुंदर फूल बगीचे में खिलते हैं",
    "चिड़िया सुबह मधुर गीत गाती है",
    "ठंडी हवा धीरे धीरे बहती है",
    "सफेद बादल आकाश में तैरते हैं",
    "बाग में रंगीन तितलियां उड़ती हैं",
    "रात को तारे चमकते रहते हैं",
    "सूरज की किरणें सवेरे चमकती हैं",
    "पहाड़ों पर हरियाली बहुत सुंदर दिखती है",
    "बरसात की बूंदें धरती को महकाती हैं",
]

HI_WISDOM_PHRASES = [
    "सच्चे दोस्त हमेशा साथ देते हैं",
    "मेहनत करने से सफलता मिलती है",
    "हंसते रहने से मन खुश रहता है",
    "ज्ञान बांटने से ज्ञान हमेशा बढ़ता है",
    "मीठी बोली सभी को भाती है",
    "अच्छा व्यवहार जीवन को सुंदर बनाता है",
    "समय का सदुपयोग करना बहुत जरूरी है",
    "सदा सच बोलना अच्छी आदत है",
    "धैर्य रखने से हर काम बनता है",
]

def generate_hindi_challenge() -> str:
    """
    Generates an everyday, grammatically correct Hindi sentence (4-7 words)
    without numbers or digits.
    """
    for _ in range(60):
        mode = random.randint(1, 5)
        if mode == 1:
            p = random.choice(HI_TIME_PREFIXES)
            a = random.choice(HI_TIME_ADJECTIVES)
            e = random.choice(HI_TIME_ENDINGS)
            phrase = f"{p} {a} {e}"
        elif mode == 2:
            phrase = random.choice(HI_TIME_PHRASES)
        elif mode == 3:
            subj = random.choice(HI_ACTIVITY_SUBJECTS)
            act = random.choice(HI_ACTIVITIES)
            ending = random.choice(HI_ACTIVITY_ENDINGS)
            phrase = f"{subj} {act} {ending}"
        elif mode == 4:
            phrase = random.choice(HI_NATURE_PHRASES)
        else:
            phrase = random.choice(HI_WISDOM_PHRASES)

        tokens = phrase.split()
        if 4 <= len(tokens) <= 7 and not any(ch.isdigit() for ch in phrase):
            if not duplicate_filter.is_recent("hi", phrase):
                duplicate_filter.record("hi", phrase)
                return phrase

    fallback = random.choice(HI_TIME_PHRASES)
    duplicate_filter.record("hi", fallback)
    return fallback

# ==============================================================================
# 5. MARATHI NATURAL EVERYDAY SENTENCE GENERATION (4-7 WORDS, NO NUMBERS)
# ==============================================================================

MR_TIME_PREFIXES = ["आजचा दिवस", "आजची सकाळ", "आजचे हवामान", "सकाळचे वातावरण", "आजची संध्याकाळ", "ही सुंदर सकाळ", "बागेतील वातावरण"]
MR_TIME_ADJECTIVES = ["खूप छान", "अतिशय सुंदर", "फार प्रसन्न", "अतिशय शांत", "खूप आनंददायी", "फार आल्हाददायक", "फार सुखद"]
MR_TIME_ENDINGS = ["आहे", "वाटते", "दिसते", "झाले आहे", "वाटत आहे"]

MR_TIME_PHRASES = [
    "आजचा दिवस छान आहे",
    "आजचा दिवस खूप छान आहे",
    "आजचा दिवस खूप सुंदर आहे",
    "सकाळचे वातावरण खूप प्रसन्न आहे",
    "आजचे हवामान अतिशय सुंदर आहे",
    "आजची सकाळ खूप आल्हाददायक आहे",
    "निळे आकाश आज स्वच्छ दिसते",
    "संध्याकाळची वेळ मनाला शांतता देते",
    "आजचा दिवस अतिशय आनंददायी आहे",
    "बागेतील वातावरण खूप प्रसन्न वाटते",
    "आजची सकाळ प्रसन्न आणि सुंदर आहे",
]

MR_ACTIVITY_SUBJECTS = ["मला", "आम्हाला", "लहान मुलांना", "सर्वांनाच", "माझ्या मित्रांना", "आपल्या सर्वांना", "विद्यार्थ्यांना"]
MR_ACTIVITIES = [
    "गाणी ऐकायला",
    "नवी पुस्तके वाचायला",
    "सकाळी फिरायला",
    "नव्या गोष्टी शिकायला",
    "चित्र काढायला",
    "बागेत फिरायला",
    "निसर्गाच्या सानिध्यात राहायला",
    "गोष्टींची पुस्तके वाचायला",
    "सकाळी व्यायाम करायला",
    "मैदानावर खेळायला",
    "चांगल्या सवयी लावायला",
]
MR_ACTIVITY_ENDINGS = ["खूप आवडते", "फार आवडते", "मनापासून आवडते", "आनंद देते", "खूप छान वाटते"]

MR_NATURE_PHRASES = [
    "सकाळी पक्षी मधुर गाणी गातात",
    "नदीचे पाणी संथपणे वाहत आहे",
    "हिरवीगार झाडे शीतल सावली देतात",
    "बागेमध्ये सुंदर फुले फुलली आहेत",
    "सूर्य पूर्वेकडून आकाशात उगवतो",
    "थंड वाऱ्याची झुळूक मनाला आनंद देते",
    "पांढरे ढग आकाशात फिरत आहेत",
    "रात्री आकाशात तारे चमकतात",
    "बागेत रंगीबेरंगी फुलपाखरे उडतात",
    "पहाटेच्या वेळी पक्षांची किलबिल ऐकू येते",
    "डोंगरावरील हिरवळ मनाला आकर्षित करते",
]

MR_WISDOM_PHRASES = [
    "नेहमी हसतमुख आणि आनंदी राहावे",
    "सत्य बोलल्याने मन शांत राहते",
    "चांगले विचार जीवन सुंदर बनवतात",
    "कष्ट केल्याने नक्कीच यश मिळते",
    "गोड बोलण्याने मैत्री अधिक दृढ होते",
    "चांगल्या सवयी शरीराला निरोगी ठेवतात",
    "ज्ञानाची आवड प्रगतीचा मार्ग दाखवते",
    "आपुलकीने वागल्यास सर्वजण आपले होतात",
    "वेळेचे महत्त्व ओळखणे अतिशय गरजेचे आहे",
]

def generate_marathi_challenge() -> str:
    """
    Generates an everyday, grammatically correct Marathi sentence (4-7 words)
    without numbers or digits.
    """
    for _ in range(60):
        mode = random.randint(1, 5)
        if mode == 1:
            p = random.choice(MR_TIME_PREFIXES)
            a = random.choice(MR_TIME_ADJECTIVES)
            e = random.choice(MR_TIME_ENDINGS)
            phrase = f"{p} {a} {e}"
        elif mode == 2:
            phrase = random.choice(MR_TIME_PHRASES)
        elif mode == 3:
            subj = random.choice(MR_ACTIVITY_SUBJECTS)
            act = random.choice(MR_ACTIVITIES)
            ending = random.choice(MR_ACTIVITY_ENDINGS)
            # Grammatical concord
            if "गाणी" in act or "पुस्तके" in act:
                ending = "आवडतात" if subj == "मला" else "खूप आवडतात"
            phrase = f"{subj} {act} {ending}"
        elif mode == 4:
            phrase = random.choice(MR_NATURE_PHRASES)
        else:
            phrase = random.choice(MR_WISDOM_PHRASES)

        tokens = phrase.split()
        if 4 <= len(tokens) <= 7 and not any(ch.isdigit() for ch in phrase):
            if not duplicate_filter.is_recent("mr", phrase):
                duplicate_filter.record("mr", phrase)
                return phrase

    fallback = random.choice(MR_TIME_PHRASES)
    duplicate_filter.record("mr", fallback)
    return fallback

# ==============================================================================
# 6. UNIFIED CHALLENGE GENERATOR & SERVICE
# ==============================================================================

def generate_random_challenge(language: str = "en") -> str:
    """
    Primary interface for challenge phrase generation across all supported languages ('en', 'hi', 'mr').
    Generates meaningful, everyday, grammatically correct sentences of 4-7 words without any numbers.
    """
    lang_code = normalize_language_code(language)
    if lang_code == "hi":
        return generate_hindi_challenge()
    elif lang_code == "mr":
        return generate_marathi_challenge()
    else:
        return generate_english_challenge()


class ChallengeService:
    """
    Orchestrates challenge phrase generation, per-attempt tracking,
    anti-replay expiration management, and session binding.
    """
    def __init__(self):
        pass

    def generate_and_track_challenge(
        self,
        session_id: str,
        language: str = "en",
        ttl_seconds: Optional[int] = None
    ) -> Tuple[str, str, int]:
        """
        Generates a fresh grammatical challenge phrase without numbers, binds it to the
        session with a unique attempt ID, short expiration time (TTL), and active status.
        
        Returns:
            (challenge_phrase, attempt_id, ttl_seconds)
        """
        lang_code = normalize_language_code(language)
        phrase = generate_random_challenge(language=lang_code)
        
        ttl = ttl_seconds or getattr(settings, "CHALLENGE_TIMEOUT_SECONDS", 60)
        attempt_id = attempt_tracker.set_challenge(
            session_id=session_id,
            challenge=phrase,
            language=lang_code,
            ttl_seconds=ttl
        )
        return phrase, attempt_id, ttl


challenge_service = ChallengeService()
