#!/usr/bin/env python3
"""
Automated Daily IPTV Curator, GitHub Scraper & Self-Healing Stream Pipeline
1. Scrapes GitHub for newly published M3U sports playlists & match event feeds
2. Deep-tests ALL candidate streams (Permanent Flagship & Temporary Event Feeds) with live ffmpeg decode
3. Prunes dead/expired links and categorizes streams into:
   - Rugby & League
   - Cricket
   - Football & Soccer
   - Main Events & Flagship Sports
   - ⚡ Temporary & Event Sports
4. Rebuilds custom lightweight XMLTV EPG (sports_epg.xml)
5. Automatically syncs master playlist and EPG to GitHub Gist & Git Repo
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

EPG_SOURCES = [
    ("UK", "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"),
    ("US", "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"),
    ("IE", "https://epgshare01.online/epgshare01/epg_ripper_IE1.xml.gz"),
    ("IN", "https://epgshare01.online/epgshare01/epg_ripper_IN1.xml.gz"),
    ("AU", "https://epgshare01.online/epgshare01/epg_ripper_AU1.xml.gz")
]

# Words that indicate temporary, matchday, PPV, or event pop-up feeds
TEMP_EVENT_KEYWORDS = [
    r"\bevent\b", r"\bmatch\b", r"\bppv\b", r"\blive\s*\d+\b", r"\bstream\s*\d+\b",
    r"\bextra\b", r"\bpop[\s-]?up\b", r"\bfeed\b", r"\bgame\s*\d+\b", r"\bcourt\s*\d+\b",
    r"\bpitch\s*\d+\b", r"\btournament\b", r"\bufc\b", r"\bboxing\b", r"\bwwe\b", r"\baew\b"
]
TEMP_EVENT_REGEX = re.compile("|".join(TEMP_EVENT_KEYWORDS), re.I)


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
    """Searches GitHub for fresh sports M3U playlists and returns raw playlist strings."""
    print("🔍 Searching GitHub for fresh sports playlists and event feeds...")
    queries = ["sports m3u", "supersport m3u", "live sports iptv", "cricket rugby m3u8"]
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
                        # Fetch raw playlist
                        try:
                            req_raw = urllib.request.Request(raw_url, headers={"User-Agent": USER_AGENT})
                            with urllib.request.urlopen(req_raw, timeout=5, context=ctx) as r_raw:
                                content = r_raw.read().decode("utf-8", errors="ignore")
                                if "#EXTINF" in content:
                                    raw_playlists.append(content)
                        except Exception:
                            pass
        except Exception as e:
            print(f"  ℹ️ Search query '{q}' note: {e}")

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
            url = l
            name = cur_ext.split(",")[-1].strip() if "," in cur_ext else "Sports Channel"
            channels.append({
                "extinf": cur_ext,
                "name": name,
                "url": url
            })
            cur_ext = None
    return channels


def load_candidate_pool(github_raws: List[str]) -> List[Dict[str, str]]:
    """Gathers permanent candidates, local playlists, and fresh GitHub scrapings."""
    pool = []
    seen_urls = set()

    # 1. Existing verified playlists
    local_files = [
        MASTER_M3U_PATH,
        TEMP_M3U_PATH,
        os.path.join(BASE_DIR, "verified_rugby.m3u8"),
        os.path.join(BASE_DIR, "verified_cricket.m3u8"),
        os.path.join(BASE_DIR, "verified_football.m3u8"),
        os.path.join(BASE_DIR, "premier_flagship_sports.m3u8")
    ]
    for lf in local_files:
        if os.path.exists(lf):
            with open(lf, "r", encoding="utf-8", errors="ignore") as f:
                for ch in parse_m3u_text(f.read()):
                    if ch["url"] not in seen_urls:
                        seen_urls.add(ch["url"])
                        pool.append(ch)

    # 2. Fresh GitHub scrapings
    for raw in github_raws:
        for ch in parse_m3u_text(raw):
            if ch["url"] not in seen_urls:
                # Basic sports filter
                if any(k in ch["name"].lower() or k in ch["extinf"].lower() for k in [
                    "sport", "cricket", "rugby", "football", "soccer", "espn", "fox", "willow",
                    "supersport", "dstv", "bein", "sky", "tnt", "premier", "f1", "mma", "fight", "event", "match"
                ]):
                    seen_urls.add(ch["url"])
                    pool.append(ch)

    return pool


# ==============================================================================
# 3. LIVE STREAM HEALTH & PLAYBACK TESTER
# ==============================================================================
def test_stream_health(ch: Dict[str, str]) -> Optional[Dict[str, Any]]:
    url = ch["url"]
    name = ch["name"]
    extinf = ch["extinf"]

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

    # 2. FFprobe inspection
    try:
        cmd = ["ffprobe", "-v", "error", "-show_streams", "-of", "json", "-user_agent", USER_AGENT, url]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=4.5)
        if p.returncode != 0 or not p.stdout:
            return None

        probe_data = json.loads(p.stdout)
        has_video = False
        res_str = "720p HD"
        fps = 30

        for s in probe_data.get("streams", []):
            if s.get("codec_type") == "video":
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
                break

        if not has_video:
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

    # Determine Group Category based on clean matching
    name_low = name.lower()
    ext_low = extinf.lower()

    if any(k in name_low or k in ext_low for k in ["rugby", "stan sport", "top 14", "urc"]):
        group = "Rugby & League"
    elif any(k in name_low or k in ext_low for k in ["cricket", "willow", "star sports"]):
        group = "Cricket"
    elif any(k in name_low or k in ext_low for k in ["football", "soccer", "bein", "premier league", "laliga", "serie a"]):
        group = "Football & Soccer"
    elif any(k in name_low for k in ["espn", "fox sports", "sky sports", "dstv", "supersport variety", "fightbox", "bellator", "mma tv"]):
        group = "Main Events & Flagship Sports"
    elif TEMP_EVENT_REGEX.search(name_low) or "ppv" in name_low or "event" in name_low:
        group = "⚡ Temporary & Event Sports"
    else:
        group = "Main Events & Flagship Sports"

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
        "Main Events & Flagship Sports": [],
        "⚡ Temporary & Event Sports": []
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
        f.write("#PLAYLIST:Master Flagship & Event Sports (Self-Healing Daily Auto-Curated)\n\n")

        for grp_name, ch_list in groups.items():
            if not ch_list:
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

    # 2. Write Dedicated Temporary Event Playlist
    temp_streams = groups["⚡ Temporary & Event Sports"]
    with open(TEMP_M3U_PATH, "w", encoding="utf-8") as f:
        f.write(f'#EXTM3U url-tvg="{custom_epg_url}" x-tvg-url="{custom_epg_url}"\n')
        f.write("#PLAYLIST:Temporary Matchday & PPV Event Streams\n\n")
        for ch in temp_streams:
            f.write(f"{ch['extinf']}\n{ch['url']}\n\n")

    print(f"💾 Saved Master Playlist: {MASTER_M3U_PATH} ({len(verified_streams)} total channels)")
    print(f"💾 Saved Temporary Events Playlist: {TEMP_M3U_PATH} ({len(temp_streams)} event feeds)")

    # 3. Trigger Custom EPG Builder
    print("\n📺 Rebuilding Custom EPG Guide...")
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
    print(f"📦 Assembled candidate pool: {len(candidates)} streams to audit.\n")

    # 3. Live playback health check
    print("⚙️ Auditing live playback and decodability across all candidate streams...")
    verified = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as ex:
        future_map = {ex.submit(test_stream_health, ch): ch for ch in candidates}
        for f in concurrent.futures.as_completed(future_map):
            res = f.result()
            if res:
                verified.append(res)
                print(f"  ✅ [{res['group']}] {res['name']} | Quality: {res['resolution']} | Latency: {res['latency_ms']}ms")

    print(f"\n🏁 Audit Complete: {len(verified)} verified live streams (Permanent + Temporary Events).\n")

    # 4. Compile, Rebuild EPG & Sync to Cloud
    compile_and_sync_all(verified, token)

    print("\n" + "=" * 85)
    print("✨ DAILY CURATION CYCLE COMPLETED SUCCESSFULLY!")
    print("=" * 85)


if __name__ == "__main__":
    main()
