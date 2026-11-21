"""English-language sports M3U playlist builder deriving from channel_catalogue.json."""

import os
from typing import Any, Dict, List

from classification.competition_mapper import CompetitionMapper
from playlist.master_builder import DEFAULT_EPG_URL, write_m3u


def build_english_playlist(
    channels: List[Dict[str, Any]],
    base_dir: str = ".",
    epg_url: str = DEFAULT_EPG_URL,
    sports_catalogue_path: str = "config/sports_catalogue.json",
) -> int:
    """Generate english_sports.m3u containing English and English + Other sports channels."""
    mapper = CompetitionMapper(sports_catalogue_path)
    english_channels = [
        ch for ch in channels
        if ch.get("language", {}).get("primary") in ("English", "English + Other")
    ]

    def resolve_group(ch: Dict[str, Any]) -> str:
        sports = ch.get("sports", [])
        if not sports:
            return "English Sports"
        top = sorted(sports, key=lambda s: s.get("confidence", 0.0), reverse=True)[0]
        return mapper.get_playlist_group(top.get("sport", ""), top.get("competitions", []))

    out_path = os.path.join(base_dir, "english_sports.m3u")
    return write_m3u(
        out_path,
        english_channels,
        resolve_group,
        playlist_title="English Sports Channels",
        epg_url=epg_url,
    )

