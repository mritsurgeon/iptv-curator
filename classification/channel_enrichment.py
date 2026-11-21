"""Channel metadata enrichment (broadcaster, aliases, optional country metadata)."""

import re
from typing import Dict, List, Tuple

KNOWN_BROADCASTERS = [
    ("SuperSport", r"\bsupersport\b"),
    ("Sky Sports", r"\bsky\s*sports?\b"),
    ("TNT Sports", r"\btnt\s*sports?\b"),
    ("Premier Sports", r"\bpremier\s*sports?\b"),
    ("Willow Cricket", r"\bwillow\b"),
    ("ESPN", r"\bespn\b"),
    ("Fox Sports", r"\bfox\s*sports?\b|\bfs[12]\b"),
    ("beIN Sports", r"\bbein\s*sports?\b"),
    ("Stan Sport", r"\bstan\s*sport\b"),
    ("Optus Sport", r"\boptus\s*sport\b"),
    ("CBS Sports", r"\bcbs\s*sports?\b"),
    ("NBC Sports", r"\bnbc\s*sports?\b"),
    ("TSN", r"\btsn\b"),
    ("Sportsnet", r"\bsportsnet\b"),
    ("SABC Sport", r"\bsabc\s*sport\b"),
    ("Tennis Channel", r"\btennis\s*channel\b"),
    ("BBC Sport", r"\bbbc\s*sport\b"),
    ("ITV Sport", r"\bitv\s*(?:sport|4)\b"),
    ("DAZN", r"\bdazn\b"),
    ("Eurosport", r"\beurosport\b"),
]


def extract_broadcaster(name: str, existing: str = "") -> str:
    if existing:
        return existing
    for label, pat in KNOWN_BROADCASTERS:
        if re.search(pat, name, re.IGNORECASE):
            return label
    return ""


def clean_channel_aliases(name: str, tvg_name: str = "") -> Tuple[str, List[str]]:
    """Strip quality/prefix noise to produce a clean canonical display name and aliases."""
    aliases: List[str] = []
    if tvg_name and tvg_name.strip() and tvg_name.strip() != name.strip():
        aliases.append(tvg_name.strip())
    clean = re.sub(r"^[A-Z]{2,3}\s*:\s*", "", name).strip()
    clean = re.sub(r"\s*\[(?:HD|FHD|4K|SD|Geo-blocked|Not 24/7)\]\s*", "", clean, flags=re.IGNORECASE).strip()
    if clean and clean != name.strip():
        aliases.append(name.strip())
    return clean or name.strip(), list(dict.fromkeys(aliases))


def enrich_channel_metadata(channel_dict: Dict) -> Dict:
    """Enrich broadcaster and aliases while keeping country optional and non-scoring."""
    name = channel_dict.get("name", "Unknown")
    tvg_name = channel_dict.get("tvg_name", "")
    clean_name, aliases = clean_channel_aliases(name, tvg_name)
    broadcaster = extract_broadcaster(clean_name, channel_dict.get("broadcaster", ""))
    channel_dict["name"] = clean_name
    existing_aliases = channel_dict.get("aliases", [])
    channel_dict["aliases"] = list(dict.fromkeys(existing_aliases + aliases))
    channel_dict["broadcaster"] = broadcaster
    return channel_dict

