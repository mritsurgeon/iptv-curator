"""Master sports M3U playlist builder deriving from channel_catalogue.json."""

import os
from typing import Any, Dict, List

from classification.competition_mapper import CompetitionMapper

DEFAULT_EPG_URL = "https://gist.githubusercontent.com/mritsurgeon/d6f9d772966e4ead4aac90331c6a6d9c/raw/sports_epg.xml"


def format_extinf_line(ch: Dict[str, Any], group_title: str) -> str:
    """Format an #EXTINF line populating attributes only when supported by actual data."""
    attrs: List[str] = []
    epg = ch.get("epg", {})
    lang = ch.get("language", {}).get("primary", "")

    if epg.get("tvg_id"):
        attrs.append(f'tvg-id="{epg["tvg_id"]}"')
    if ch.get("name"):
        attrs.append(f'tvg-name="{ch["name"]}"')
    if epg.get("tvg_logo"):
        attrs.append(f'tvg-logo="{epg["tvg_logo"]}"')
    if lang and lang != "Unknown":
        attrs.append(f'tvg-language="{lang}"')
    if ch.get("country"):
        attrs.append(f'tvg-country="{ch["country"]}"')
    if group_title:
        attrs.append(f'group-title="{group_title}"')

    attr_str = " ".join(attrs)
    return f"#EXTINF:-1 {attr_str},{ch['name']}" if attr_str else f"#EXTINF:-1,{ch['name']}"


def write_m3u(
    filepath: str,
    channels: List[Dict[str, Any]],
    group_resolver,
    playlist_title: str = "Sports IPTV",
    epg_url: str = DEFAULT_EPG_URL,
) -> int:
    """Write a deduplicated M3U playlist from canonical channel records."""
    seen_ids = set()
    lines = [
        f'#EXTM3U url-tvg="{epg_url}" x-tvg-url="{epg_url}"\n',
        f"#PLAYLIST:{playlist_title}\n\n",
    ]
    count = 0
    for ch in channels:
        cid = ch["channel_id"]
        url = ch.get("stream", {}).get("url", "")
        if not url or cid in seen_ids:
            continue
        seen_ids.add(cid)
        grp = group_resolver(ch)
        lines.append(f"{format_extinf_line(ch, grp)}\n{url}\n\n")
        count += 1

    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return count


def build_master_playlist(
    channels: List[Dict[str, Any]],
    base_dir: str = ".",
    epg_url: str = DEFAULT_EPG_URL,
    sports_catalogue_path: str = "config/sports_catalogue.json",
) -> Dict[str, int]:
    """Generate master_sports.m3u and legacy verified_premier_sports.m3u8 for Fire TV compatibility."""
    mapper = CompetitionMapper(sports_catalogue_path)

    def resolve_group(ch: Dict[str, Any]) -> str:
        sports = ch.get("sports", [])
        if not sports:
            return "Sports"
        top = sorted(sports, key=lambda s: s.get("confidence", 0.0), reverse=True)[0]
        return mapper.get_playlist_group(top.get("sport", ""), top.get("competitions", []))

    master_path = os.path.join(base_dir, "master_sports.m3u")
    legacy_path = os.path.join(base_dir, "verified_premier_sports.m3u8")

    cnt = write_m3u(
        master_path,
        channels,
        resolve_group,
        playlist_title="Master Sports Catalogue",
        epg_url=epg_url,
    )
    write_m3u(
        legacy_path,
        channels,
        resolve_group,
        playlist_title="Master Sports Catalogue (Fire TV Compatible)",
        epg_url=epg_url,
    )
    return {"master_sports.m3u": cnt, "verified_premier_sports.m3u8": cnt}

