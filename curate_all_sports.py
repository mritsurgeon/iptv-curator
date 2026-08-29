#!/usr/bin/env python3
"""
Sports Channel Extractor & Master Curated Playlist Builder
Scans all downloaded playlists (.m3u, .m3u8) across the workspace,
filters exclusively for sports channels, deduplicates streams,
and compiles a clean, organized master sports playlist with EPG support.
"""

import glob
import os
import re
import ssl
import sys
import urllib.request
from typing import Dict, List, Set, Tuple

# Sports identification keywords (channel names, group titles, genres)
SPORTS_KEYWORDS = [
    r"\bsport\b", r"\bsports\b", r"\bespn\b", r"\bfifa\b", r"\bnba\b", r"\bnfl\b",
    r"\bnhl\b", r"\bmlb\b", r"\bufc\b", r"\bmma\b", r"\bwwe\b", r"\bf1\b",
    r"\bformula\s*1\b", r"\bmotogp\b", r"\btour\s*de\s*france\b", r"\btennis\b",
    r"\bgolf\b", r"\bcricket\b", r"\brugby\b", r"\bfootball\b", r"\bsoccer\b",
    r"\bbadminton\b", r"\bvball\b", r"\bvolleyball\b", r"\bhockey\b", r"\bboxing\b",
    r"\bextreme\s*sports\b", r"\bred\s*bull\s*tv\b", r"\beurosport\b", r"\bsky\s*sports\b",
    r"\bbein\s*sports?\b", r"\bdazn\b", r"\bsupersport\b", r"\btsn\b", r"\bsportsnet\b",
    r"\bwillow\b", r"\btnt\s*sports\b", r"\bfox\s*sports\b", r"\bcbs\s*sports\b",
    r"\barena\s*sport\b", r"\bbt\s*sport\b", r"\bcanal\+\s*sport\b", r"\bmovistar\s*deportes\b",
    r"\bdeporte\b", r"\bdeportes\b", r"\besporte\b", r"\besportes\b", r"\bsportv\b",
    r"\bsetanta\b", r"\boptus\s*sport\b", r"\bstan\s*sport\b", r"\bastro\s*super\s*sport\b",
    r"\bracing\b", r"\bcombat\b", r"\bfight\b"
]

EXCLUDE_KEYWORDS = [r"\bmovie\b", r"\bmovies\b", r"\bcinema\b", r"\bseries\b", r"\bfilm\b", r"\bnovela\b"]

SPORTS_REGEX = re.compile("|".join(SPORTS_KEYWORDS), re.IGNORECASE)
EXCLUDE_REGEX = re.compile("|".join(EXCLUDE_KEYWORDS), re.IGNORECASE)

EPG_URL = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"
OUTPUT_MASTER_PLAYLIST = "all_sports_master_curated.m3u8"


def is_sports_channel(extinf_line: str, channel_name: str) -> bool:
    """Determines if a channel entry belongs to sports based on metadata or title."""
    # If explicitly contains movies/cinema and no sport tag, exclude
    combined_text = f"{channel_name} {extinf_line}"
    if EXCLUDE_REGEX.search(channel_name) and not any(k in channel_name.lower() for k in ["sport", "espn", "ufc", "nba", "nfl"]):
        return False

    # Check group-title attribute if present
    group_match = re.search(r'group-title="([^"]*)"', extinf_line, re.IGNORECASE)
    if group_match:
        group = group_match.group(1).lower()
        if any(k in group for k in ["sport", "deporte", "esporte", "fifa", "espn", "football", "nba", "nfl"]):
            return True
            
    # Check channel title & extinf text
    return bool(SPORTS_REGEX.search(combined_text))


def parse_playlist_file(filepath: str) -> List[Tuple[str, str, str, str]]:
    """
    Parses an M3U/M3U8 file and extracts sports channels.
    Returns list of (extinf_line, channel_name, stream_url, source_file)
    """
    channels = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = [line.strip() for line in f if line.strip()]
    except Exception as e:
        print(f"  ❌ Failed to read {filepath}: {e}")
        return []

    current_extinf = None
    source_name = os.path.basename(filepath)

    for line in lines:
        if line.startswith("#EXTINF"):
            current_extinf = line
        elif current_extinf and not line.startswith("#"):
            stream_url = line
            
            # Extract channel name from end of #EXTINF line
            name = current_extinf.split(",")[-1].strip() if "," in current_extinf else "Unknown Channel"
            
            if is_sports_channel(current_extinf, name):
                channels.append((current_extinf, name, stream_url, source_name))
            current_extinf = None

    return channels


def extract_and_curate_sports(input_dirs_and_files: List[str]) -> None:
    """Scans all playlists, filters sports, deduplicates, and creates master sports playlist."""
    print("🔍 Scanning playlists across workspace for sports channels...\n" + "=" * 70)
    
    all_files = []
    for item in input_dirs_and_files:
        if os.path.isdir(item):
            all_files.extend(glob.glob(os.path.join(item, "*.m3u*")))
        elif os.path.isfile(item):
            all_files.append(item)

    print(f"📦 Found {len(all_files)} playlist files to analyze.")
    
    raw_sports_channels = []
    file_stats = {}

    for fpath in all_files:
        if os.path.abspath(fpath) == os.path.abspath(OUTPUT_MASTER_PLAYLIST):
            continue
        found = parse_playlist_file(fpath)
        if found:
            fname = os.path.basename(fpath)
            file_stats[fname] = len(found)
            raw_sports_channels.extend(found)

    print("\n📊 Sports channels found per playlist:")
    for fname, count in sorted(file_stats.items(), key=lambda x: x[1], reverse=True)[:15]:
        print(f"  • {fname}: {count} sports streams")
    if len(file_stats) > 15:
        print(f"  ... and {len(file_stats) - 15} more files")

    print(f"\nTotal raw sports stream entries extracted: {len(raw_sports_channels)}")

    # Deduplication by Stream URL and Normalized Channel Name
    print("\n🧹 Deduplicating channels...")
    unique_channels = []
    seen_urls: Set[str] = set()
    
    for extinf, name, url, source in raw_sports_channels:
        clean_url = url.strip()
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)
        
        # Ensure proper group-title is set to "Sports" if missing or generic
        if 'group-title="' not in extinf:
            extinf = extinf.replace("#EXTINF:-1", '#EXTINF:-1 group-title="Sports"')
            if 'group-title=' not in extinf:
                extinf = re.sub(r'(#EXTINF:\s*-?\d+)', r'\1 group-title="Sports"', extinf)

        unique_channels.append((extinf, name, clean_url, source))

    print(f"✨ Total Unique Sports Channels after deduplication: {len(unique_channels)}")

    # Sort channels alphabetically by channel name
    unique_channels.sort(key=lambda x: x[1].upper())

    # Write Master Curated Playlist
    header = (
        f'#EXTM3U url-tvg="{EPG_URL}" x-tvg-url="{EPG_URL}"\n'
        f'#PLAYLIST:Master Curated Sports Live IPTV\n\n'
    )

    with open(OUTPUT_MASTER_PLAYLIST, "w", encoding="utf-8") as f:
        f.write(header)
        for extinf, name, url, source in unique_channels:
            f.write(f"{extinf}\n{url}\n\n")

    # Export individual filtered playlists
    filtered_dir = "downloaded_sports_only"
    os.makedirs(filtered_dir, exist_ok=True)
    for fpath in all_files:
        found = parse_playlist_file(fpath)
        if found:
            fname = os.path.basename(fpath)
            clean_name = fname.replace(".m3u8", "_sports.m3u8").replace(".m3u", "_sports.m3u")
            out_file = os.path.join(filtered_dir, clean_name)
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(f'#EXTM3U url-tvg="{EPG_URL}" x-tvg-url="{EPG_URL}"\n\n')
                for extinf, name, url, _ in found:
                    f.write(f"{extinf}\n{url}\n\n")

    print(f"📁 Saved individual filtered playlists to: {filtered_dir}/")
    print(f"\n💾 Saved master curated sports playlist to: {OUTPUT_MASTER_PLAYLIST}")
    print(f"🎉 Successfully built master playlist containing {len(unique_channels)} pure sports channels!")


if __name__ == "__main__":
    scan_targets = [
        "/Users/ian/code/IPTV/downloaded_playlists",
        "/Users/ian/code/IPTV/curated_sports_playlist.m3u",
        "/Users/ian/code/IPTV/apsattv_firetv.m3u",
        "/Users/ian/code/IPTV/downloaded_playlist.m3u"
    ]
    extract_and_curate_sports(scan_targets)
