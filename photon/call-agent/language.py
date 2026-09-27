"""Which language is the caller speaking, and what should we answer in?

Detection is by Unicode script, not by a model or an API call: Telugu,
Tamil and Devanagari occupy disjoint code-point ranges, so a caller
speaking Telugu produces Telugu characters and there is nothing to infer.
Zero latency, no key, no network, and it works identically whichever STT
vendor is in front of it — which matters because the whole point of this
module is to sit between two swappable stacks (Deepgram and Sarvam).

Its one real limitation: it needs STT to emit NATIVE SCRIPT. Sarvam's
saaras/saarika do. If an STT romanises Telugu as Latin text ("meeru ela
unnaru"), every heuristic here sees Latin and says English — so
`AGENT_REPLY_LANGUAGE` exists to pin the language explicitly when the
transcript can't be trusted to carry it.
"""
from __future__ import annotations

import unicodedata

# Sarvam's BCP-47 codes (see SarvamTTSLanguages) mapped to the Unicode
# block that identifies them. Devanagari is shared by Hindi and Marathi;
# it resolves to Hindi, which is the safe default for a support call and
# the language actually asked for here.
_SCRIPTS: list[tuple[str, str, range]] = [
    ("te-IN", "Telugu", range(0x0C00, 0x0C80)),
    ("ta-IN", "Tamil", range(0x0B80, 0x0C00)),
    ("hi-IN", "Hindi", range(0x0900, 0x0980)),      # Devanagari
    ("bn-IN", "Bengali", range(0x0980, 0x0A00)),
    ("pa-IN", "Punjabi", range(0x0A00, 0x0A80)),    # Gurmukhi
    ("gu-IN", "Gujarati", range(0x0A80, 0x0B00)),
    ("od-IN", "Odia", range(0x0B00, 0x0B80)),
    ("kn-IN", "Kannada", range(0x0C80, 0x0D00)),
    ("ml-IN", "Malayalam", range(0x0D00, 0x0D80)),
]

DEFAULT_LANGUAGE = "en-IN"
_NAMES = {code: name for code, name, _ in _SCRIPTS} | {"en-IN": "English"}

# Below this share of letters, a stray Indic character (a name, an emoji-
# like glyph, one mis-transcribed word) shouldn't flip the whole reply's
# language. Code-mixed speech is the norm on Indian support calls — "sir
# webhook fail అవుతోంది" is Telugu with English nouns, and should be
# answered in Telugu, so the bar is a plurality of letters, not a majority.
_MIN_SCRIPT_SHARE = 0.20


def detect_language(text: str, default: str = DEFAULT_LANGUAGE) -> str:
    """Best-effort BCP-47 code for the language `text` is written in."""
    if not text:
        return default

    counts: dict[str, int] = {}
    letters = 0
    for char in text:
        if not unicodedata.category(char).startswith("L"):
            continue  # skip digits, spaces, punctuation — they carry no script
        letters += 1
        for code, _name, block in _SCRIPTS:
            if ord(char) in block:
                counts[code] = counts.get(code, 0) + 1
                break

    if not letters or not counts:
        return default

    code, count = max(counts.items(), key=lambda kv: kv[1])
    return code if count / letters >= _MIN_SCRIPT_SHARE else default


def language_name(code: str) -> str:
    """Human-readable name, for telling the compose LLM what to write in."""
    return _NAMES.get(code, "English")


# Spoken instantly on a greeting, with no LLM in the loop — so it has to
# be pre-written per language rather than generated.
GREETINGS = {
    "en-IN": "Hi — I'm here and listening. Ask me anything whenever you're ready.",
    "hi-IN": "नमस्ते — मैं यहाँ हूँ और सुन रहा हूँ। जो भी पूछना हो, पूछिए।",
    "te-IN": "నమస్కారం — నేను ఇక్కడే ఉన్నాను, వింటున్నాను. ఏదైనా అడగండి.",
    "ta-IN": "வணக்கம் — நான் இங்கே இருக்கிறேன், கேட்டுக்கொண்டிருக்கிறேன். எதுவும் கேளுங்கள்.",
}


def greeting_for(code: str) -> str:
    return GREETINGS.get(code, GREETINGS["en-IN"])


# Spoken the instant we decide to attach a screen frame — BEFORE the vision
# call starts — so the caller hears something while a visual turn does its
# extra work (~1.3s of vision on top of a normal turn).
#
# Pre-written per language for the same reason GREETINGS are: there is no
# LLM in this path, and there must not be — an acknowledgement that waited
# on a model would defeat its own purpose.
#
# Deliberately SHORT. This plays before the answer, so every word here is
# added to the answer's own delay. A long, friendly line would make the turn
# genuinely slower while trying to make it feel faster.
#
# These make no factual claim whatsoever, so the "no uncited claim" rule is
# untouched — there is nothing in them to cite, exactly as with GREETINGS.
ACKNOWLEDGEMENTS = {
    "en-IN": [
        "Okay, let me look.",
        "Sure, checking your screen.",
        "Got it, one moment.",
        "Right, I can see it — one sec.",
        "Let me take a look at that.",
        "Mmm, looking at your screen now.",
        "Okay, give me a second with this.",
        "Ah, let me see what's on there.",
    ],
    "hi-IN": [
        "ठीक है, देखता हूँ।",
        "हाँ, आपकी स्क्रीन देख रहा हूँ।",
        "समझ गया, एक पल।",
        "अच्छा, ज़रा देखता हूँ।",
        "स्क्रीन देख रहा हूँ, एक सेकंड।",
        "हाँ, दिख रहा है — एक पल।",
        "रुकिए, स्क्रीन पर देखता हूँ।",
        "ठीक है, एक सेकंड दीजिए।",
    ],
    "te-IN": [
        "సరే, చూస్తాను.",
        "అలాగే, మీ స్క్రీన్ చూస్తున్నాను.",
        "అర్థమైంది, ఒక్క నిమిషం.",
        "సరే, ఒక్క సెకను చూస్తాను.",
        "మీ స్క్రీన్ చూస్తున్నాను, ఒక్క క్షణం.",
        "అవును, కనిపిస్తోంది — ఒక్క నిమిషం.",
        "ఆగండి, స్క్రీన్ చూస్తున్నాను.",
        "అలాగే, ఒక్క సెకను ఇవ్వండి.",
    ],
    "ta-IN": [
        "சரி, பார்க்கிறேன்.",
        "ஆம், உங்கள் திரையைப் பார்க்கிறேன்.",
        "புரிந்தது, ஒரு நிமிடம்.",
        "சரி, ஒரு நொடி பார்க்கிறேன்.",
        "உங்கள் திரையைப் பார்க்கிறேன், ஒரு கணம்.",
        "ஆமா, தெரிகிறது — ஒரு நிமிடம்.",
        "இருங்கள், திரையைப் பார்க்கிறேன்.",
        "சரி, ஒரு நொடி கொடுங்கள்.",
    ],
}


# The same idea as ACKNOWLEDGEMENTS, but for an ordinary lookup rather than a
# screen-share turn — and they cannot be shared. "Sure, checking your screen"
# on a turn where nobody is sharing anything is a small lie about what the
# agent is doing, which is exactly the class of thing the grounding rules
# exist to prevent. These say only that a lookup is happening, which is true
# by construction: this is spoken WHILE the brain-api call is in flight.
LOOKUP_FILLERS = {
    "en-IN": [
        "Hmm, checking — one moment.",
        "Let me check that, please wait.",
        "Mmm, one sec while I look.",
        "Right, let me pull that up.",
        "Give me a second, I'm looking.",
        "Ah, let me dig into that.",
        "One moment, just checking.",
        "Let me see what I can find.",
        "Hold on, looking that up now.",
        "Checking on that — bear with me.",
    ],
    "hi-IN": [
        "हम्म, देख रहा हूँ — एक पल।",
        "ज़रा देखता हूँ, एक सेकंड रुकिए।",
        "एक पल, देख रहा हूँ।",
        "अच्छा, ज़रा चेक करता हूँ।",
        "एक सेकंड दीजिए, देख रहा हूँ।",
        "रुकिए, अभी देखकर बताता हूँ।",
        "थोड़ा रुकिए, ढूँढ रहा हूँ।",
        "देखता हूँ क्या मिलता है।",
        "बस एक पल, चेक कर रहा हूँ।",
        "हाँ, अभी देखता हूँ।",
    ],
    "te-IN": [
        "చూస్తున్నాను — ఒక్క నిమిషం.",
        "కొంచెం ఆగండి, చూసి చెప్తాను.",
        "ఒక్క సెకను, చూస్తున్నాను.",
        "సరే, ఇప్పుడే చూస్తాను.",
        "ఒక్క నిమిషం ఇవ్వండి, వెతుకుతున్నాను.",
        "ఆగండి, ఇప్పుడే చెక్ చేస్తాను.",
        "కొంచెం సమయం ఇవ్వండి, చూస్తున్నాను.",
        "ఏం దొరుకుతుందో చూస్తాను.",
        "ఒక్క క్షణం, చెక్ చేస్తున్నాను.",
        "అలాగే, ఇప్పుడే చూస్తాను.",
    ],
    "ta-IN": [
        "பார்க்கிறேன் — ஒரு நிமிடம்.",
        "கொஞ்சம் இருங்கள், பார்த்துச் சொல்கிறேன்.",
        "ஒரு நொடி, பார்க்கிறேன்.",
        "சரி, இப்போதே பார்க்கிறேன்.",
        "ஒரு நிமிடம் கொடுங்கள், தேடுகிறேன்.",
        "இருங்கள், இப்போது சரிபார்க்கிறேன்.",
        "கொஞ்சம் நேரம் கொடுங்கள், பார்க்கிறேன்.",
        "என்ன கிடைக்கிறது என்று பார்க்கிறேன்.",
        "ஒரு கணம், சரிபார்த்துக் கொண்டிருக்கிறேன்.",
        "ஆமா, இப்போதே பார்க்கிறேன்.",
    ],
}


def lookup_filler_for(code: str, index: int = 0) -> str:
    """What to say when an ordinary turn is taking long enough to feel dead."""
    variants = LOOKUP_FILLERS.get(code) or LOOKUP_FILLERS["en-IN"]
    return variants[index % len(variants)]


def acknowledgement_for(code: str, index: int = 0) -> str:
    """Rotate through the variants so repeated visual turns do not sound canned.

    The index is the caller's turn counter, not randomness: a fixed cycle is
    reproducible in tests, and back-to-back turns are guaranteed to differ,
    which random choice would not be.
    """
    variants = ACKNOWLEDGEMENTS.get(code) or ACKNOWLEDGEMENTS["en-IN"]
    return variants[index % len(variants)]
