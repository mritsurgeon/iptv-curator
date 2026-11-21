"""Language detection using audio stream metadata, playlist tags, EPG & broadcaster signals."""

import re
from typing import Any, Dict, List, Optional, Tuple

ENGLISH_ISO = {"eng", "en", "en-us", "en-gb", "en-za", "en-au", "en-ca", "en-ie", "en-nz"}

NON_ENGLISH_ISO = {
    "spa": "Spanish", "es": "Spanish",
    "fra": "French", "fre": "French", "fr": "French",
    "deu": "German", "ger": "German", "de": "German",
    "ita": "Italian", "it": "Italian",
    "por": "Portuguese", "pt": "Portuguese",
    "rus": "Russian", "ru": "Russian",
    "ara": "Arabic", "ar": "Arabic",
    "tur": "Turkish", "tr": "Turkish",
    "hin": "Hindi", "hi": "Hindi",
    "zho": "Chinese", "chi": "Chinese", "zh": "Chinese",
    "jpn": "Japanese", "ja": "Japanese",
    "kor": "Korean", "ko": "Korean",
    "pol": "Polish", "pl": "Polish",
    "nld": "Dutch", "dut": "Dutch", "nl": "Dutch",
    "ron": "Romanian", "rum": "Romanian", "ro": "Romanian",
    "bul": "Bulgarian", "bg": "Bulgarian",
    "hun": "Hungarian", "hu": "Hungarian",
    "ces": "Czech", "cze": "Czech", "cs": "Czech",
    "ukr": "Ukrainian", "uk": "Ukrainian",
    "tha": "Thai", "th": "Thai",
    "vie": "Vietnamese", "vi": "Vietnamese",
    "ind": "Indonesian", "id": "Indonesian",
}

EXPLICIT_NON_ENGLISH_TAGS = re.compile(
    r"\b(espa[nñ]ol|spanish|fran[cç]ais|french|deutsch|german|italiano|italian|portugu[eê]s|"
    r"russian|arabic|hindi|tamil|telugu|turkish|deportes|esportes)\b|"
    r"\((?:ES|FR|DE|IT|PT|RU|AR|TR|HI|PL|RO|BG|HU|CZ)\)",
    re.IGNORECASE,
)

EXPLICIT_ENGLISH_TAGS = re.compile(
    r"\b(english|eng)\b|\((?:EN|ENG|UK|US|AU|NZ|ZA|CA|IE)\)",
    re.IGNORECASE,
)

NON_LATIN_SCRIPT = re.compile(
    r"[\u4e00-\u9fff\u0400-\u04ff\u0600-\u06ff\u0e00-\u0e7f\u0900-\u097f\uac00-\ud7af\u3040-\u30ff]"
)

KNOWN_ENGLISH_BROADCASTERS = {
    "supersport", "sky sports", "tnt sports", "premier sports", "willow",
    "bbc sport", "itv sport", "stan sport", "optus sport", "cbs sports",
    "nbc sports", "fox sports", "espn", "tsn", "sportsnet", "sabc sport",
    "tennis channel", "rugbypass", "icc.tv", "fifa+", "red bull tv",
}


class LanguageDetector:
    """
    Classifies channel broadcast language into:
      - 'English'
      - 'English + Other'
      - 'Other Language'
      - 'Unknown'
    without assuming English solely from English words in the channel name.
    """

    def detect(
        self,
        name: str,
        extinf: str = "",
        audio_languages: Optional[List[str]] = None,
        language_hint: str = "",
        broadcaster: str = "",
        epg_descriptions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        audio_langs = [l.lower().strip() for l in (audio_languages or []) if l and l.lower() != "und"]
        detected_english = False
        other_langs: List[str] = []

        # 1. Audio stream metadata (strongest signal)
        for code in audio_langs:
            if code in ENGLISH_ISO:
                detected_english = True
            elif code in NON_ENGLISH_ISO:
                lang_name = NON_ENGLISH_ISO[code]
                if lang_name not in other_langs:
                    other_langs.append(lang_name)

        # 2. Playlist tvg-language / explicit hint
        tvg_lang_m = re.search(r'tvg-language="([^"]*)"', extinf, re.IGNORECASE)
        hint_text = " ".join(filter(None, [language_hint, tvg_lang_m.group(1) if tvg_lang_m else ""]))
        if hint_text:
            for part in re.split(r"[,;/|]+", hint_text):
                p = part.strip().lower()
                if p in ENGLISH_ISO or p == "english":
                    detected_english = True
                elif p in NON_ENGLISH_ISO:
                    ln = NON_ENGLISH_ISO[p]
                    if ln not in other_langs:
                        other_langs.append(ln)
                elif p and p not in ("und", "unknown"):
                    cap = p.capitalize()
                    if cap not in other_langs:
                        other_langs.append(cap)

        # 3. Non-Latin scripts or explicit language tags in metadata/name
        combined = f"{name} {extinf}"
        if NON_LATIN_SCRIPT.search(combined):
            if "Non-Latin" not in other_langs:
                other_langs.append("Non-Latin")
        if EXPLICIT_NON_ENGLISH_TAGS.search(combined):
            m = EXPLICIT_NON_ENGLISH_TAGS.search(combined)
            tag = m.group(0).strip("()") if m else "Other"
            if tag not in other_langs:
                other_langs.append(tag)
        if EXPLICIT_ENGLISH_TAGS.search(combined):
            detected_english = True

        # 4. Known English-language broadcaster metadata
        bc_low = (broadcaster or name).lower()
        if any(kb in bc_low for kb in KNOWN_ENGLISH_BROADCASTERS) and not other_langs:
            detected_english = True

        # 5. EPG programme descriptions
        if epg_descriptions:
            sample = " ".join(epg_descriptions[:5])
            if NON_LATIN_SCRIPT.search(sample):
                if "Non-Latin" not in other_langs:
                    other_langs.append("Non-Latin")
            elif re.search(r"\b(the|and|with|live|match|championship|highlights)\b", sample, re.IGNORECASE):
                detected_english = True

        # Determine final classification
        if detected_english and other_langs:
            primary = "English + Other"
            confidence = 0.75
        elif detected_english:
            primary = "English"
            confidence = 1.0 if audio_langs else 0.85
        elif other_langs:
            primary = "Other Language"
            confidence = 0.1
        else:
            primary = "Unknown"
            confidence = 0.35

        return {
            "primary": primary,
            "secondary": other_langs,
            "audio_tracks": audio_langs,
            "confidence": confidence,
        }

