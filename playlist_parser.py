#!/usr/bin/env python3
"""
M3U Playlist Parser & EPG Mapper
Parses M3U/M3U8 playlists and matches channels against EPGShare01 database.
"""

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

@dataclass
class Channel:
    id: str = ""
    name: str = ""
    tvg_id: str = ""
    tvg_name: str = ""
    tvg_logo: str = ""
    group_title: str = ""
    stream_url: str = ""
    mapped_epg_id: str = ""
    match_confidence: str = "none"  # "exact_id", "exact_doc", "normalized_name", "manual"

def normalize_name(name: str) -> str:
    """Normalizes channel name for fuzzy matching (lowercase, stripped punctuation)."""
    # Remove common tags like HD, FHD, 4K, UK:, US:, ZA:, etc.
    s = re.sub(r"\b(FHD|HD|SD|4K|HEVC|RAW|1080p|720p|50fps|H265)\b", "", name, flags=re.I)
    s = re.sub(r"^[A-Z]{2,3}\s*:\s*", "", s)  # strip prefix like 'ZA: ' or 'UK: '
    s = re.sub(r"[^a-zA-Z0-9]", "", s)
    return s.lower()

def parse_m3u(content: str) -> List[Channel]:
    """Parses an M3U playlist string into Channel objects."""
    channels = []
    lines = content.splitlines()
    current_channel: Optional[Channel] = None
    
    # Regex to capture EXTINF attributes
    extinf_pattern = re.compile(r'#EXTINF:(?P<duration>-?\d+)\s*(?P<attributes>.*),(?P<name>.*)$')
    attr_pattern = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')

    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if line.startswith("#EXTINF"):
            match = extinf_pattern.match(line)
            if match:
                raw_attrs = match.group("attributes")
                raw_name = match.group("name").strip()
                attrs = dict(attr_pattern.findall(raw_attrs))
                
                current_channel = Channel(
                    name=raw_name,
                    tvg_id=attrs.get("tvg-id", ""),
                    tvg_name=attrs.get("tvg-name", raw_name),
                    tvg_logo=attrs.get("tvg-logo", ""),
                    group_title=attrs.get("group-title", "General")
                )
            else:
                current_channel = Channel(name="Unknown Channel")
        elif not line.startswith("#") and current_channel is not None:
            current_channel.stream_url = line
            channels.append(current_channel)
            current_channel = None

    return channels

def map_channels_to_epg(channels: List[Channel], epg_channel_ids: List[str]) -> Tuple[List[Channel], Dict[str, int]]:
    """
    Maps M3U channels against a list of valid EPGShare01 channel IDs.
    Priority:
    1. Exact tvg-id match
    2. Normalized channel name match against EPG ID
    """
    epg_set = set(epg_channel_ids)
    
    # Pre-index normalized EPG IDs: 'bbcone' -> 'BBC.One.uk'
    norm_epg_map = {}
    for eid in epg_channel_ids:
        # e.g., 'BBC.Brit.HD.za' -> 'bbcbrithdza' and 'bbcbrithd' and 'bbcbrit'
        parts = eid.split(".")
        base_name = "".join(parts[:-1]) if len(parts) > 1 else eid
        norm_epg_map[normalize_name(eid)] = eid
        norm_epg_map[normalize_name(base_name)] = eid

    stats = {"exact_id": 0, "normalized_name": 0, "unmapped": 0}

    for ch in channels:
        # 1. Exact tvg-id match
        if ch.tvg_id and ch.tvg_id in epg_set:
            ch.mapped_epg_id = ch.tvg_id
            ch.match_confidence = "exact_id"
            stats["exact_id"] += 1
            continue
        
        # 2. Normalized matching
        norm_ch = normalize_name(ch.tvg_name or ch.name)
        if norm_ch in norm_epg_map:
            ch.mapped_epg_id = norm_epg_map[norm_ch]
            ch.match_confidence = "normalized_name"
            stats["normalized_name"] += 1
            continue
        
        # Unmapped
        ch.mapped_epg_id = ""
        ch.match_confidence = "none"
        stats["unmapped"] += 1

    return channels, stats

def export_mapped_m3u(channels: List[Channel], epg_url: str = "") -> str:
    """Exports channels back into a clean M3U playlist format with tvg-id mappings."""
    header = f'#EXTM3U url-tvg="{epg_url}"\n' if epg_url else '#EXTM3U\n'
    out = [header]
    for ch in channels:
        effective_tvg_id = ch.mapped_epg_id or ch.tvg_id
        line = f'#EXTINF:-1 tvg-id="{effective_tvg_id}" tvg-name="{ch.tvg_name}" tvg-logo="{ch.tvg_logo}" group-title="{ch.group_title}",{ch.name}\n{ch.stream_url}\n'
        out.append(line)
    return "".join(out)
