#!/usr/bin/env python3
"""
Curated Sports Playlist Builder & EPG Integrator
Downloads legal sports streams from iptv-org, verifies stream reachability concurrently,
and embeds EPGShare01 XMLTV guide data.
"""

import concurrent.futures
import re
import ssl
import sys
import urllib.request
from typing import List, Tuple

# Official legal free-to-air sports category from iptv-org
SOURCE_PLAYLIST_URL = "https://iptv-org.github.io/iptv/categories/sports.m3u"
EPG_GUIDE_URL = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"
OUTPUT_PLAYLIST = "curated_sports_playlist.m3u"

# SSL context for stream probing
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def download_playlist(url: str) -> str:
    """Downloads the base M3U playlist."""
    print(f"📡 Fetching sports playlist from: {url}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as response:
        content = response.read().decode("utf-8", errors="ignore")
        print(f"✅ Successfully downloaded source playlist ({len(content.splitlines())} lines)")
        return content

def parse_streams(content: str) -> List[Tuple[str, str, str]]:
    """
    Parses M3U into (extinf, channel_name, stream_url).
    """
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    channels = []
    current_extinf = None

    for line in lines:
        if line.startswith("#EXTINF"):
            current_extinf = line
        elif current_extinf and not line.startswith("#"):
            stream_url = line
            name = current_extinf.split(",")[-1].strip() if "," in current_extinf else "Unknown Channel"
            channels.append((current_extinf, name, stream_url))
            current_extinf = None

    return channels

def check_single_stream(item: Tuple[str, str, str]) -> Tuple[bool, Tuple[str, str, str]]:
    """Tests if a stream is live and reachable via HTTP GET/HEAD."""
    extinf, name, url = item
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=4, context=ctx) as resp:
            if resp.status in (200, 206, 302):
                return True, item
    except Exception:
        pass
    return False, item

def verify_and_build_playlist(channels: List[Tuple[str, str, str]], max_workers: int = 25) -> int:
    """Verifies streams in parallel and saves active ones to curated_sports_playlist.m3u."""
    print(f"\n🔍 Verifying {len(channels)} sports streams (using {max_workers} concurrent threads)...")
    
    active_channels = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(check_single_stream, ch) for ch in channels]
        completed = 0
        for f in concurrent.futures.as_completed(futures):
            is_active, item = f.result()
            completed += 1
            if is_active:
                active_channels.append(item)
                print(f"  [{completed}/{len(channels)}] ✅ ACTIVE: {item[1]}")
            else:
                if completed % 20 == 0:
                    print(f"  [{completed}/{len(channels)}] ... probing in progress")

    print(f"\n✨ Probing complete! Found {len(active_channels)} verified live streams out of {len(channels)} candidates.")

    header = (
        f'#EXTM3U url-tvg="{EPG_GUIDE_URL}" x-tvg-url="{EPG_GUIDE_URL}"\n\n'
    )

    with open(OUTPUT_PLAYLIST, "w", encoding="utf-8") as f:
        f.write(header)
        for extinf, name, url in active_channels:
            f.write(f"{extinf}\n{url}\n\n")

    print(f"💾 Saved verified playlist to: {OUTPUT_PLAYLIST}")
    return len(active_channels)

if __name__ == "__main__":
    raw_m3u = download_playlist(SOURCE_PLAYLIST_URL)
    parsed = parse_streams(raw_m3u)
    count = verify_and_build_playlist(parsed, max_workers=30)
    print(f"\n🎉 Done! {count} channels configured with EPGShare01.")
