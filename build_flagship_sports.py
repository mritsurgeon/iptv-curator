#!/usr/bin/env python3
"""
Premier Flagship Sports Playlist Builder
Filters all IPTV sources exclusively for Tier-1, Main Event, and Flagship Sports channels.
High focus on:
  1. Rugby (SuperSport Rugby, Sky Sports Action/Arena, TNT Sports, Premier Sports, Top 14, NRL)
  2. Football / Soccer (Premier League, Champions League, LaLiga, Serie A, beIN Sports, Sky Sports Football, TNT Sports, DAZN, Optus Sport)
  3. Cricket (Sky Sports Cricket, Willow TV, Star Sports, Sony Ten/Six, PTV Sports, Astro Cricket)
  4. Flagship Main Events (Sky Sports Main Event, ESPN 1/2, FS1/FS2, Eurosport, SuperSport Grandstand, TSN, Sportsnet)

Eliminates lower-tier leagues, local amateur feeds, non-sport noise, and duplicates.
"""

import glob
import os
import re
from typing import Dict, List, Set, Tuple

EPG_URL = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"

# -------------------------------------------------------------
# DEFINITIONS OF TIER 1 / MAIN EVENT PATTERNS
# -------------------------------------------------------------

# 1. RUGBY & LEAGUE
RUGBY_PATTERNS = [
    r"\brugby\b",
    r"\bsix\s*nations\b",
    r"\bpremiership\s*rugby\b",
    r"\bsuper\s*rugby\b",
    r"\bnrl\b",
    r"\btop\s*14\b",
    r"\bpro14\b",
    r"\burc\b",
    r"\bstan\s*sport\b",
    r"\bsky\s*sports?\s*(action|arena)\b",
    r"\bsupersport\s*(rugby|grandstand)\b",
    r"\bcanal\+\s*sport\b"
]

# 2. FOOTBALL / SOCCER (TOP TIER)
FOOTBALL_PATTERNS = [
    r"\bpremier\s*league\b",
    r"\blaliga\b",
    r"\bserie\s*a\b",
    r"\bbundesliga\b",
    r"\bligue\s*1\b",
    r"\bchampions\s*league\b",
    r"\beuropa\s*league\b",
    r"\buefa\b",
    r"\bsky\s*sports?\s*(football|premier|bundesliga|calcio)\b",
    r"\bsupersport\s*(football|premier|laliga|variety|maximo)\b",
    r"\btnt\s*sports?\s*[1-4]\b",
    r"\bbt\s*sports?\s*[1-4]\b",
    r"\bbein\s*sports?\s*([1-9]|1[0-6]|english|premium|global|hd|xtra)\b",
    r"\boptus\s*sport\b",
    r"\barena\s*sport\s*([1-6]|premium)\b",
    r"\bdazn\s*(1|2|laliga|football)\b",
    r"\bmovistar\s*(laliga|deportes|champions)\b",
    r"\bcanal\+\s*(foot|sport)\b",
    r"\bnbc\s*sports?\b",
    r"\bcbs\s*sports?\b",
    r"\bsetanta\s*sports?\b",
    r"\bpremier\s*sports?\s*[1-2]?\b"
]

# 3. CRICKET
CRICKET_PATTERNS = [
    r"\bcricket\b",
    r"\bwillow\b",
    r"\bwillow\s*xtra\b",
    r"\bsky\s*sports?\s*cricket\b",
    r"\bsupersport\s*cricket\b",
    r"\bstar\s*sports?\s*(1|2|select|hindi|hd)\b",
    r"\bsony\s*(ten|six|sports?)\b",
    r"\bptv\s*sports?\b",
    r"\bten\s*sports?\b",
    r"\bastro\s*cricket\b",
    r"\bosn\s*(sports\s*)?cricket\b",
    r"\bt-?20\b",
    r"\bipl\b"
]

# 4. GENERAL FLAGSHIP / MAIN EVENT CHANNELS
FLAGSHIP_PATTERNS = [
    r"\bsky\s*sports?\s*main\s*event\b",
    r"\bsky\s*sports?\s*f1\b",
    r"\bsky\s*sports?\s*golf\b",
    r"\bsky\s*sports?\s*tennis\b",
    r"\bsupersport\s*(grandstand|action|golf|tennis|motorsport)\b",
    r"\bespn\s*(1|2|u|hd|news|deportes)?\b",
    r"\bfox\s*sports?\s*(1|2|hd|racing|plus)?\b",
    r"\beurosport\s*(1|2|hd)\b",
    r"\btsn\s*[1-5]\b",
    r"\bsportsnet\s*(east|west|ontario|pacific|one|360|world)?\b",
    r"\bmotogp\b",
    r"\bformula\s*1\b",
    r"\bufc\b",
    r"\bmma\b"
]

# STRICT EXCLUSIONS (Remove non-sports, low tier / amateur feeds, movies)
EXCLUSION_PATTERNS = [
    r"\baflam\b", r"\bmovie\b", r"\bcinema\b", r"\bseries\b", r"\bnovela\b",
    r"\bfishing\b", r"\bhunting\b", r"\bdarts\b", r"\bbilliards\b", r"\bpool\b",
    r"\bpoker\b", r"\bchess\b", r"\bhigh\s*school\b", r"\bmiddle\s*school\b",
    r"\btest\s*stream\b", r"\bradio\b", r"\bcam\b", r"\bwebcam\b", r"\bloop\b"
]

# STRICT NON-ENGLISH LANGUAGE AND COUNTRY CODE PATTERNS
NON_ENGLISH_PATTERNS = [
    r"\((AR|FR|DE|ES|IT|AL|RU|TR|GR|NL|PT|PL|CZ|SK|HU|RO|BG|IL|IR|TH|VN|ID|CN|KR|JP|UR|HI|TA|TE|ML|KN|BN|PA|GU|AF|SR|HR|SI|SE|NO|DK|FI|LT|LV|EE|UA|KZ)\)",
    r"\b(ARABIC|FRANCAIS|FRENCH|ESPANOL|SPANISH|DEUTSCH|GERMAN|ITALIAN|ITALIANO|ALBANIAN|RUSSIAN|TURKISH|GREEK|DUTCH|PORTUGUESE|POLISH|HINDI|TAMIL|TELUGU|KANNADA|MALAYALAM|BENGALI|URDU|MARATHI|HEBREW|FARSI|TAGALOG|CHINESE|JAPANESE|KOREAN|VIETNAMESE|THAI|INDONESIAN|SWAHILI|SERBIAN|CROATIAN|BULGARIAN|ROMANIAN|HUNGARIAN|CZECH|SLOVAK)\b",
    r"\b(CANAL\+|MOVISTAR|ARENA\s*SPORT|SUPER\s*SPORT.*\(AL\)|ZONA\s*DAZN|RMC\s*SPORT|TELECLUB|MATCH\s*TV|POLSAT|ELEVEN\s*SPORTS?\s*(PL|BE|PT)|SPORT\s*TV\s*[1-6]|EUROSPORT\s*(FR|DE|ES|IT|NL|PL|RU))\b"
]

RUGBY_REGEX = re.compile("|".join(RUGBY_PATTERNS), re.IGNORECASE)
FOOTBALL_REGEX = re.compile("|".join(FOOTBALL_PATTERNS), re.IGNORECASE)
CRICKET_REGEX = re.compile("|".join(CRICKET_PATTERNS), re.IGNORECASE)
FLAGSHIP_REGEX = re.compile("|".join(FLAGSHIP_PATTERNS), re.IGNORECASE)
EXCLUSION_REGEX = re.compile("|".join(EXCLUSION_PATTERNS), re.IGNORECASE)
NON_ENG_REGEX = re.compile("|".join(NON_ENGLISH_PATTERNS), re.IGNORECASE)


def is_english_channel(extinf: str, name: str) -> bool:
    """Returns True if the channel is an English-language stream."""
    text = f"{name} {extinf}"
    if NON_ENG_REGEX.search(text):
        # Override if explicitly designated English or UK/US/CA/AU/NZ/ZA feed
        if re.search(r"\b(ENGLISH|ENG|UK|US|CA|AU|NZ|ZA|IE)\b", name, re.IGNORECASE):
            return True
        return False
    return True


def classify_channel(extinf: str, name: str) -> Tuple[bool, str]:
    """
    Classifies channel into (is_valid_flagship, category_name).
    Returns (True, 'Rugby'|'Football'|'Cricket'|'Main Event') or (False, '')
    """
    # 1. Enforce English Only
    if not is_english_channel(extinf, name):
        return False, ""

    # 2. Exclude non-sports/amateur
    if EXCLUSION_REGEX.search(name) and not any(k in name.lower() for k in ["sport", "espn", "rugby", "cricket", "football", "premier"]):
        return False, ""

    text = f"{name} {extinf}"

    # 3. Cricket
    if CRICKET_REGEX.search(text):
        return True, "Cricket"

    # 4. Rugby
    if RUGBY_REGEX.search(text):
        return True, "Rugby & League"

    # 5. Football / Soccer
    if FOOTBALL_REGEX.search(text):
        return True, "Football & Soccer"

    # 6. Other Tier-1 Flagship / Main Events
    if FLAGSHIP_REGEX.search(text):
        return True, "Main Events & Flagship Sports"

    return False, ""


def parse_m3u_file(filepath: str) -> List[Tuple[str, str, str, str]]:
    """Extracts flagship channels from an M3U file."""
    channels = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = [l.strip() for l in f if l.strip()]
    except Exception:
        return []

    current_extinf = None
    for line in lines:
        if line.startswith("#EXTINF"):
            current_extinf = line
        elif current_extinf and not line.startswith("#"):
            url = line
            name = current_extinf.split(",")[-1].strip() if "," in current_extinf else "Unknown"
            
            is_flagship, category = classify_channel(current_extinf, name)
            if is_flagship:
                # Update/inject group-title to classified category
                clean_extinf = re.sub(r'group-title="[^"]*"', '', current_extinf)
                clean_extinf = clean_extinf.replace("#EXTINF:-1", f'#EXTINF:-1 group-title="{category}"')
                if 'group-title=' not in clean_extinf:
                    clean_extinf = re.sub(r'(#EXTINF:\s*-?\d+)', rf'\1 group-title="{category}"', clean_extinf)
                channels.append((clean_extinf, name, url, category))
            current_extinf = None

    return channels


def build_premier_playlists():
    print("🏆 Building Premier Flagship Sports Playlists (Rugby, Football, Cricket, Main Events)...\n")
    
    scan_sources = [
        "/Users/ian/code/IPTV/downloaded_playlists",
        "/Users/ian/code/IPTV/all_sports_master_curated.m3u8",
        "/Users/ian/code/IPTV/curated_sports_playlist.m3u",
        "/Users/ian/code/IPTV/downloaded_playlist.m3u"
    ]

    all_files = []
    for s in scan_sources:
        if os.path.isdir(s):
            all_files.extend(glob.glob(os.path.join(s, "*.m3u*")))
        elif os.path.isfile(s):
            all_files.append(s)

    all_channels: List[Tuple[str, str, str, str]] = []
    for f in all_files:
        if "premier_" in os.path.basename(f) or "flagship" in os.path.basename(f):
            continue
        all_channels.extend(parse_m3u_file(f))

    # Deduplication by Stream URL
    seen_urls: Set[str] = set()
    deduped: List[Tuple[str, str, str, str]] = []
    
    for extinf, name, url, category in all_channels:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append((extinf, name, url, category))

    # Breakdown by category
    categories: Dict[str, List[Tuple[str, str, str, str]]] = {
        "Rugby & League": [],
        "Football & Soccer": [],
        "Cricket": [],
        "Main Events & Flagship Sports": []
    }

    for item in deduped:
        cat = item[3]
        if cat in categories:
            categories[cat].append(item)

    print("📊 Flagship Channels breakdown:")
    print(f"  🏉 Rugby & League:               {len(categories['Rugby & League'])} channels")
    print(f"  ⚽ Football & Soccer:            {len(categories['Football & Soccer'])} channels")
    print(f"  🏏 Cricket:                      {len(categories['Cricket'])} channels")
    print(f"  🌟 Main Events & Flagship Sports: {len(categories['Main Events & Flagship Sports'])} channels")
    print(f"  ──────────────────────────────────────────")
    print(f"  🔥 TOTAL PREMIER FLAGSHIP CHANNELS: {len(deduped)} channels\n")

    # 1. Master Premier Flagship Playlist
    master_file = "premier_flagship_sports.m3u8"
    with open(master_file, "w", encoding="utf-8") as f:
        f.write(f'#EXTM3U url-tvg="{EPG_URL}" x-tvg-url="{EPG_URL}"\n')
        f.write('#PLAYLIST:Premier Flagship Sports (Rugby, Football, Cricket, Main Events)\n\n')
        for cat_name, ch_list in categories.items():
            ch_list.sort(key=lambda x: x[1].upper())
            for extinf, name, url, _ in ch_list:
                f.write(f"{extinf}\n{url}\n\n")

    # 2. Individual Dedicated Playlists for Rugby, Football, and Cricket
    dedicated_files = {
        "rugby_flagship.m3u8": ("Rugby & League Flagship", categories["Rugby & League"]),
        "football_flagship.m3u8": ("Football & Soccer Flagship", categories["Football & Soccer"]),
        "cricket_flagship.m3u8": ("Cricket Flagship", categories["Cricket"])
    }

    for filename, (title, ch_list) in dedicated_files.items():
        ch_list.sort(key=lambda x: x[1].upper())
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f'#EXTM3U url-tvg="{EPG_URL}" x-tvg-url="{EPG_URL}"\n')
            f.write(f'#PLAYLIST:{title}\n\n')
            for extinf, name, url, _ in ch_list:
                f.write(f"{extinf}\n{url}\n\n")

    print(f"💾 Generated Master Playlist:  [premier_flagship_sports.m3u8] ({len(deduped)} channels)")
    print(f"🏉 Generated Rugby Playlist:   [rugby_flagship.m3u8] ({len(categories['Rugby & League'])} channels)")
    print(f"⚽ Generated Football Playlist:[football_flagship.m3u8] ({len(categories['Football & Soccer'])} channels)")
    print(f"🏏 Generated Cricket Playlist: [cricket_flagship.m3u8] ({len(categories['Cricket'])} channels)")
    print("\n🎉 Done! All premier flagship playlists are ready.")


if __name__ == "__main__":
    build_premier_playlists()
