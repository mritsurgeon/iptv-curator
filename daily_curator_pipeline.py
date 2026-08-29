#!/usr/bin/env python3
"""
Automated Daily IPTV Curator, GitHub Scraper & Self-Healing Stream Pipeline
1. Scrapes GitHub & candidate pools for sports M3U playlists & match event feeds
2. Deep-filters candidates strictly for ENGLISH SPORTS (purging foreign dubs, non-sports noise, movies, reality TV)
3. Live playback health check via FFprobe (video & audio language inspection) & 3-second live decode
4. Categorizes streams into clean sports genres:
   - 🏉 Rugby & League
   - 🏏 Cricket
   - ⚽ Football & Soccer
   - 🏈 American Football & US Sports
   - 🏎️ Motorsport & Combat Sports
   - 🏆 Main Events & Flagship Sports
   - ⚡ Live Matchday & Event Feeds
5. Builds custom descriptive XMLTV EPG (sports_epg.xml) with match fixture cards
6. Pushes curated playlists & EPG to GitHub Gist & Fire TV
"""

import concurrent.futures
import datetime
import glob
import gzip
import io
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MASTER_M3U_PATH = os.path.join(BASE_DIR, "verified_premier_sports.m3u8")
TEMP_M3U_PATH = os.path.join(BASE_DIR, "temporary_sports.m3u8")
EPG_PATH = os.path.join(BASE_DIR, "sports_epg.xml")
GIST_ID = "d6f9d772966e4ead4aac90331c6a6d9c"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# -----------------------------------------------------------------------------
# NON-SPORTS & NON-ENGLISH EXCLUSION PATTERNS
# -----------------------------------------------------------------------------
NON_SPORTS_KEYWORDS = [
    r"\bmovies?\b", r"\bcinemas?\b", r"\bfilms?\b", r"\bcinestar\b", r"\bnovelas?\b", r"\bseries\b",
    r"\breality\b", r"\bcooking\b", r"\bchef\b", r"\bfood\b", r"\bcocina\b", r"\bkitchen\b",
    r"\btruckers\b", r"\bcrime\b", r"\bdrama\b", r"\bconspiracy\b", r"\bdocumentar\w*\b",
    r"\bnature\b", r"\bkids\b", r"\bcartoons?\b", r"\bdisney\b", r"\bnickelodeon\b", r"\bweather\b",
    r"\bshopping\b", r"\breligio\w*\b", r"\bgospel\b", r"\bmusics?\b", r"\bhit\b", r"\bspiegel\b",
    r"\barirang\b", r"\bsolidaria\b", r"\bgovernment\b", r"\bpublic\s*channel\b", r"\bstorage\s*wars\b",
    r"\bvan\s*damme\b", r"\bcomedia\b", r"\bcomedy\b", r"\bhistoria\b", r"\bhistory\b",
    r"\bspotlight\b", r"\bplayz\b", r"\bpluto\s*tv\b", r"\bnews\b(?!.*sport)", r"\bfox\s*news\b",
    r"\bsky\s*news\b", r"\bbbc\s*news\b", r"\bmarried\b", r"\blifetime\b", r"\byerli\b", r"\baile\b",
    r"\b48\s*hours\b", r"\beverybody\s*hates\s*chris\b", r"\bfox\s*foxi\b", r"\bfix\s*foxi\b",
    r"\bcam\b", r"\bradio\b", r"\bpop[\s-]?up\b", r"\bbang\s*bang\b", r"\bstingray\b", r"\bshows?\b",
    r"\be-?sports?\b", r"\bretake\b", r"\bdstv\b(?!.*supersport)"
]
NON_SPORTS_REGEX = re.compile("|".join(NON_SPORTS_KEYWORDS), re.IGNORECASE)

# Foreign country bouquet prefixes
FOREIGN_PREFIX_REGEX = re.compile(
    r"(?:\b(DE|AL|FR|IT|RO|TR|SE|NO|CZ|PL|EX-YU|AT|HR|RS|BG|DK|FI|NL|GR|IL|KZ|UA|BR|PT|ES|AR|RU|MK|CH|AM|EE|HU)\s*:|[^\w\s]{1,4}\s*\|)",
    re.IGNORECASE
)

# Foreign channel names
NON_ENGLISH_NAMES_REGEX = re.compile(
    r"\b(Tring|Kujtesa|Art\s*Sport|Super\s*Sport\s*Kosova|Digiturk|Tivibu|TRT\s*Spor|Polsat\s*Sport|Okko|QAZ\s*Sport|5Sport|Tigo\s*Sport|DiviSport|Suspilne|Maincast|TyC\s*Sport|Fox\s*Deportes|ESPN\s*Deportes|TNT\s*Novelas|Sinema|Kino|Bigo|Sky\s*Folk|Teleclub|C\s*More|Rai\s*Sport|Icaro|Primocanale|SRF|Al\s*Iraqia|PK\s*Sports|Ekol\s*Sport|Sport\s*[1-5]\s*\(|Dyn\s*Sport|Sport\s*Klub|Arena\s*Sport|Sporty\s*TV|ACI\s*Sport)\b",
    re.IGNORECASE
)

FOREIGN_LANG_CODES = {
    "ger", "deu", "fra", "fre", "alb", "sqi", "ita", "rus", "tur", "spa", "por",
    "ara", "fas", "hin", "urd", "zho", "chi", "tha", "vie", "ind", "heb", "kor",
    "jpn", "pol", "ces", "cze", "slk", "slo", "hun", "ron", "rum", "bul", "ell", "gre"
}


def clean_text_for_analysis(name: str, extinf: str) -> str:
    """Strips URLs, logo URLs, and parameters to avoid false positives on domains like espncdn.com."""
    cleaned_extinf = re.sub(r'tvg-logo="[^"]*"', '', extinf)
    cleaned_extinf = re.sub(r'https?://\S+', '', cleaned_extinf)
    return f"{name} {cleaned_extinf}".strip()


def get_github_token() -> Optional[str]:
    token = os.environ.get("GIST_PAT") or os.environ.get("GIST_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        try:
            token = subprocess.check_output(["gh", "auth", "token"]).decode().strip()
        except Exception:
            token = None
    return token


# ==============================================================================
# 1. GITHUB SCRAPER ENGINE
# ==============================================================================
def scrape_github_sports_playlists(token: Optional[str], limit: int = 15) -> List[str]:
    """Searches GitHub for fresh English sports M3U playlists and returns raw playlist strings."""
    print("🔍 Searching GitHub for fresh English sports playlists & matchday feeds...")
    queries = ["sports m3u", "supersport m3u", "live sports iptv", "cricket rugby m3u8", "premier league m3u8", "sky sports m3u8"]
    headers = {"User-Agent": "IPTV-Curator"}
    if token:
        headers["Authorization"] = f"token {token}"

    raw_playlists = []
    seen_urls = set()

    for q in queries:
        url = f"https://api.github.com/search/code?q={urllib.parse.quote(q)}+extension:m3u+extension:m3u8&sort=indexed&order=desc&per_page=10"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = json.loads(resp.read().decode())
                items = data.get("items", [])
                for it in items:
                    raw_url = it.get("html_url", "").replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                    if raw_url and raw_url not in seen_urls:
                        seen_urls.add(raw_url)
                        try:
                            req_raw = urllib.request.Request(raw_url, headers={"User-Agent": USER_AGENT})
                            with urllib.request.urlopen(req_raw, timeout=5, context=ctx) as r_raw:
                                content = r_raw.read().decode("utf-8", errors="ignore")
                                if "#EXTINF" in content:
                                    raw_playlists.append(content)
                        except Exception:
                            pass
        except Exception as e:
            print(f"  ℹ️ Query note ({q}): {e}")

    print(f"📥 Discovered {len(raw_playlists)} fresh playlist files from GitHub.\n")
    return raw_playlists


# ==============================================================================
# 2. STREAM PARSER & AGGREGATOR
# ==============================================================================
def parse_m3u_text(text: str) -> List[Dict[str, str]]:
    channels = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    cur_ext = None
    for l in lines:
        if l.startswith("#EXTINF"):
            cur_ext = l
        elif cur_ext and not l.startswith("#"):
            name = cur_ext.split(",")[-1].strip() if "," in cur_ext else "Sports Channel"
            channels.append({
                "extinf": cur_ext,
                "name": name,
                "url": l
            })
            cur_ext = None
    return channels


def is_valid_english_sports_candidate(ch: Dict[str, str]) -> bool:
    """Pre-filters candidate streams before expensive network / ffmpeg probing."""
    name = ch["name"]
    extinf = ch["extinf"]
    analysis_text = clean_text_for_analysis(name, extinf)

    # 1. Reject foreign country prefixes and non-English names
    if FOREIGN_PREFIX_REGEX.search(name) or NON_ENGLISH_NAMES_REGEX.search(analysis_text):
        return False

    # 2. Reject non-sports noise
    if NON_SPORTS_REGEX.search(analysis_text):
        # Override only if it explicitly is a tier-1 sports brand
        if not any(k in analysis_text.lower() for k in [
            "supersport", "sky sports", "tnt sports", "premier sports", "willow",
            "espn", "fox sports", "optus sport", "stan sport", "premier league", "championship"
        ]):
            return False

    # 3. Must match valid sports terms or live match format
    sports_keywords = [
        r"\bsport\w*\b", r"\bcricket\b", r"\brugby\b", r"\bfootball\b", r"\bsoccer\b",
        r"\bespn\w*\b", r"\bwillow\b", r"\bsupersport\b", r"\bbein\b",
        r"\bsky\s*sport\w*\b", r"\btnt\s*sport\w*\b", r"\bpremier\s*league\b", r"\bpremier\s*sport\w*\b",
        r"\bf1\b", r"\bformula\s*1\b", r"\bmotogp\b", r"\bmma\b", r"\bufc\b",
        r"\bboxing\b", r"\bfight\b", r"\bnfl\b", r"\bnba\b", r"\bmlb\b", r"\bnhl\b",
        r"\btennis\b", r"\bgolf\b", r"\bstan\s*sport\b", r"\btsn\b", r"\bsportsnet\b",
        r"\bvs\.?\b", r"\bchampionship\b", r"\bleague\s*one\b", r"\bfa\s*cup\b"
    ]
    if not any(re.search(pat, analysis_text, re.IGNORECASE) for pat in sports_keywords):
        return False

    return True


def load_candidate_pool(github_raws: List[str]) -> List[Dict[str, str]]:
    """Gathers permanent candidates, local playlists, and fresh GitHub scrapings."""
    pool = []
    seen_urls = set()

    local_files = [
        MASTER_M3U_PATH,
        TEMP_M3U_PATH,
        os.path.join(BASE_DIR, "verified_rugby.m3u8"),
        os.path.join(BASE_DIR, "verified_cricket.m3u8"),
        os.path.join(BASE_DIR, "verified_football.m3u8"),
        os.path.join(BASE_DIR, "premier_flagship_sports.m3u8"),
        os.path.join(BASE_DIR, "rugby_flagship.m3u8"),
        os.path.join(BASE_DIR, "cricket_flagship.m3u8"),
        os.path.join(BASE_DIR, "football_flagship.m3u8")
    ]
    for lf in local_files:
        if os.path.exists(lf):
            with open(lf, "r", encoding="utf-8", errors="ignore") as f:
                for ch in parse_m3u_text(f.read()):
                    if ch["url"] not in seen_urls and is_valid_english_sports_candidate(ch):
                        seen_urls.add(ch["url"])
                        pool.append(ch)

    for raw in github_raws:
        for ch in parse_m3u_text(raw):
            if ch["url"] not in seen_urls and is_valid_english_sports_candidate(ch):
                seen_urls.add(ch["url"])
                pool.append(ch)

    return pool


# ==============================================================================
# 3. LIVE STREAM HEALTH & AUDIO/VIDEO VALIDATOR
# ==============================================================================
def classify_sport_genre(name: str, extinf: str) -> str:
    """Accurately classifies channel into one of the designated sports categories."""
    text = clean_text_for_analysis(name, extinf).lower()

    # 1. Rugby & League
    if re.search(r'\b(rugby|six nations|top 14|nrl|urc|super rugby|rugbypass|stan sport|premiership rugby)\b', text):
        return "Rugby & League"
    
    # 2. Cricket
    if re.search(r'\b(cricket|willow|t20|ipl|test match|star sports|sony ten|ashes|bbl|pakistan|england vs|india vs|australia vs)\b', text):
        return "Cricket"

    # 3. American Football & US Sports (Check BEFORE generic soccer terms)
    if re.search(r'\b(nfl|american football|broncos|vikings|chiefs|seahawks|packers|cardinals|eagles|bengals|patriots|49ers|bills|ravens|cowboys|saints|steelers|dolphins|nba|basketball|mlb|baseball|nhl|hockey|ncaa|espn\w*|fs1|fs2|fox sports|sec network|acc network|big ten|strike zone)\b', text):
        return "American Football & US Sports"

    # 4. Motorsport & Combat Sports
    if re.search(r'\b(f1|formula 1|formula one|motogp|ufc|mma|boxing|bellator|fightbox|wwe|aew|nurmagomedov|song)\b', text):
        return "Motorsport & Combat Sports"

    # 5. Football & Soccer (Association Football / Premier League / etc.)
    if re.search(r'\b(premier league|championship|league one|league two|fa cup|champions league|europa league|laliga|la liga|serie a|bundesliga|ligue 1|soccer|football|liverpool|nottingham|wolves|stoke|derby|swansea|mk dons|leicester|crystal palace|manchester|chelsea|arsenal|tottenham|real madrid|barcelona|inter|milan|juventus|bayern|dortmund|cbs sports golazo|optus sport|setanta sports)\b', text):
        return "Football & Soccer"

    # 6. Live Matchday & Event Feeds
    if " vs " in text or " v " in text or "event" in text or "ppv" in text:
        return "⚡ Live Matchday & Event Feeds"

    # 7. Main Events & Flagship Multi-Sport
    return "Main Events & Flagship Sports"


def test_stream_health(ch: Dict[str, str]) -> Optional[Dict[str, Any]]:
    url = ch["url"]
    name = ch["name"]
    extinf = ch["extinf"]

    # Pre-check candidate again
    if not is_valid_english_sports_candidate(ch):
        return None

    # 1. HTTP Connectivity & TTFB Latency
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=3.5, context=ctx) as resp:
            if resp.status not in (200, 206, 302):
                return None
            latency_ms = round((time.time() - t0) * 1000, 1)
    except Exception:
        return None

    # 2. FFprobe inspection for Video & Audio Language
    try:
        cmd = ["ffprobe", "-v", "error", "-show_streams", "-of", "json", "-user_agent", USER_AGENT, url]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=4.5)
        if p.returncode != 0 or not p.stdout:
            return None

        probe_data = json.loads(p.stdout)
        has_video = False
        res_str = "720p HD"
        fps = 30
        audio_languages = []

        for s in probe_data.get("streams", []):
            ctype = s.get("codec_type")
            if ctype == "video":
                has_video = True
                w, h = s.get("width", 0), s.get("height", 0)
                fps_s = s.get("r_frame_rate", "0/1")
                try:
                    num, den = fps_s.split("/")
                    fps = round(float(num)/float(den)) if float(den) > 0 else 30
                except Exception:
                    fps = 30
                if h >= 1080 or w >= 1920:
                    res_str = f"1080p ({fps}fps)"
                elif h >= 720 or w >= 1280:
                    res_str = f"720p ({fps}fps)"
            elif ctype == "audio":
                lang = s.get("tags", {}).get("language", "").lower()
                if lang:
                    audio_languages.append(lang)

        if not has_video:
            return None

        # Verify Audio Language: if explicitly tagged as foreign without English, drop stream
        if audio_languages:
            has_english = any(l in ["eng", "en", "qaa", "und"] for l in audio_languages)
            all_foreign = all(l in FOREIGN_LANG_CODES for l in audio_languages)
            if all_foreign and not has_english:
                return None

    except Exception:
        return None

    # 3. Live 3-second ffmpeg packet decode test
    try:
        cmd_decode = ["ffmpeg", "-v", "error", "-user_agent", USER_AGENT, "-t", "3", "-i", url, "-f", "null", "-"]
        p_decode = subprocess.run(cmd_decode, capture_output=True, text=True, timeout=5.5)
        if p_decode.returncode != 0:
            return None
    except Exception:
        return None

    group = classify_sport_genre(name, extinf)

    return {
        "name": name,
        "url": url,
        "extinf": extinf,
        "group": group,
        "resolution": res_str,
        "fps": fps,
        "latency_ms": latency_ms
    }


# ==============================================================================
# 4. PLAYLIST & EPG COMPILER & SYNCER
# ==============================================================================
def compile_and_sync_all(verified_streams: List[Dict[str, Any]], token: Optional[str]):
    custom_epg_url = f"https://gist.githubusercontent.com/mritsurgeon/{GIST_ID}/raw/sports_epg.xml"

    groups: Dict[str, List[Dict[str, Any]]] = {
        "Rugby & League": [],
        "Cricket": [],
        "Football & Soccer": [],
        "American Football & US Sports": [],
        "Motorsport & Combat Sports": [],
        "Main Events & Flagship Sports": [],
        "⚡ Live Matchday & Event Feeds": []
    }

    for s in verified_streams:
        grp = s["group"]
        if grp in groups:
            groups[grp].append(s)
        else:
            groups["Main Events & Flagship Sports"].append(s)

    # 1. Write Master Playlist
    with open(MASTER_M3U_PATH, "w", encoding="utf-8") as f:
        f.write(f'#EXTM3U url-tvg="{custom_epg_url}" x-tvg-url="{custom_epg_url}"\n')
        f.write("#PLAYLIST:Master Flagship & Event Sports (Self-Healing Daily Auto-Curated English Only)\n\n")

        for grp_name, ch_list in groups.items():
            if not ch_list or grp_name == "⚡ Live Matchday & Event Feeds":
                continue
            f.write(f"# =============================================================\n")
            f.write(f"# {grp_name.upper()}\n")
            f.write(f"# =============================================================\n\n")
            for ch in ch_list:
                ext = ch["extinf"]
                ext = re.sub(r'group-title="[^"]*"', f'group-title="{grp_name}"', ext)
                if 'group-title=' not in ext:
                    ext = ext.replace("#EXTINF:-1", f'#EXTINF:-1 group-title="{grp_name}"')
                f.write(f"{ext}\n{ch['url']}\n\n")

        # Also append Live Event Feeds at the bottom of Master
        event_feeds = groups["⚡ Live Matchday & Event Feeds"]
        if event_feeds:
            f.write(f"# =============================================================\n")
            f.write(f"# LIVE MATCHDAY & EVENT FEEDS\n")
            f.write(f"# =============================================================\n\n")
            for ch in event_feeds:
                ext = ch["extinf"]
                ext = re.sub(r'group-title="[^"]*"', 'group-title="⚡ Live Matchday & Event Feeds"', ext)
                f.write(f"{ext}\n{ch['url']}\n\n")

    # 2. Write Dedicated Temporary Event Playlist
    temp_streams = groups["⚡ Live Matchday & Event Feeds"]
    with open(TEMP_M3U_PATH, "w", encoding="utf-8") as f:
        f.write(f'#EXTM3U url-tvg="{custom_epg_url}" x-tvg-url="{custom_epg_url}"\n')
        f.write("#PLAYLIST:Temporary Matchday & Event Streams\n\n")
        for ch in temp_streams:
            f.write(f"{ch['extinf']}\n{ch['url']}\n\n")

    print(f"💾 Saved Master Playlist: {MASTER_M3U_PATH} ({len(verified_streams)} verified English channels)")
    print(f"💾 Saved Event Playlist:  {TEMP_M3U_PATH} ({len(temp_streams)} match event feeds)")

    # 3. Trigger Custom EPG Builder
    print("\n📺 Rebuilding Custom EPG Guide with 8 regional English sources & match cards...")
    subprocess.run(["python3", os.path.join(BASE_DIR, "build_custom_epg.py")])

    # 4. Push to Cloud Gist
    if token:
        try:
            with open(MASTER_M3U_PATH, "r", encoding="utf-8") as f:
                master_m3u_text = f.read()
            with open(TEMP_M3U_PATH, "r", encoding="utf-8") as f:
                temp_m3u_text = f.read()
            with open(EPG_PATH, "r", encoding="utf-8") as f:
                epg_text = f.read()

            payload = {
                "files": {
                    "sports.m3u": {"content": master_m3u_text},
                    "temporary_sports.m3u": {"content": temp_m3u_text},
                    "sports_epg.xml": {"content": epg_text}
                }
            }

            req = urllib.request.Request(
                f"https://api.github.com/gists/{GIST_ID}",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "User-Agent": "IPTV-Daily-Pipeline"
                },
                method="PATCH"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                print("☁️ Successfully pushed updated Master M3U, Temporary M3U, and EPG to Cloud Gist!")
        except Exception as e:
            print(f"⚠️ Gist sync warning: {e}")

    # 5. Push directly to Fire TV if connected via ADB
    try:
        res = subprocess.run(["adb", "get-state"], capture_output=True, text=True, timeout=2)
        if "device" in res.stdout:
            subprocess.run(["adb", "push", MASTER_M3U_PATH, "/sdcard/Download/sports.m3u"], capture_output=True)
            subprocess.run(["adb", "push", TEMP_M3U_PATH, "/sdcard/Download/temporary_sports.m3u"], capture_output=True)
            subprocess.run(["adb", "push", EPG_PATH, "/sdcard/Download/sports_epg.xml"], capture_output=True)
            print("📱 Successfully pushed updated playlists and EPG directly to Fire TV storage via ADB!")
    except Exception:
        pass


# ==============================================================================
# MAIN ORCHESTRATOR
# ==============================================================================
def main():
    print("=" * 85)
    print("🚀 DAILY IPTV CURATOR, EVENT SCRAPER & SELF-HEALING TESTER")
    print("=" * 85)

    token = get_github_token()
    if token:
        print("🔑 Authenticated GitHub token detected.")
    else:
        print("ℹ️ Running in unauthenticated mode.")

    # 1. Scrape GitHub for fresh playlists
    github_raws = scrape_github_sports_playlists(token, limit=15)

    # 2. Gather candidates
    candidates = load_candidate_pool(github_raws)
    print(f"📦 Assembled candidate pool: {len(candidates)} English sports streams to audit.\n")

    # 3. Live playback health check
    print("⚙️ Auditing live playback, decodability & audio language across all candidate streams...")
    verified = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as ex:
        future_map = {ex.submit(test_stream_health, ch): ch for ch in candidates}
        for f in concurrent.futures.as_completed(future_map):
            res = f.result()
            if res:
                verified.append(res)
                print(f"  ✅ [{res['group']}] {res['name']} | Quality: {res['resolution']} | Latency: {res['latency_ms']}ms")

    print(f"\n🏁 Audit Complete: {len(verified)} verified live English streams.\n")

    # 4. Compile, Rebuild EPG & Sync to Cloud
    compile_and_sync_all(verified, token)

    print("\n" + "=" * 85)
    print("✨ DAILY CURATION CYCLE COMPLETED SUCCESSFULLY!")
    print("=" * 85)


if __name__ == "__main__":
    main()
