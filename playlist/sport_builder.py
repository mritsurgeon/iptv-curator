"""Sport-specific M3U playlist builder deriving from channel_catalogue.json."""

import os
from typing import Any, Dict, List

from classification.competition_mapper import CompetitionMapper
from playlist.master_builder import DEFAULT_EPG_URL, write_m3u

SPORT_OUTPUT_MAP = {
    "Football": ("football.m3u", "verified_football.m3u8", "Football"),
    "Rugby": ("rugby.m3u", "verified_rugby.m3u8", "Rugby"),
    "Cricket": ("cricket.m3u", "verified_cricket.m3u8", "Cricket"),
    "Tennis": ("tennis.m3u", None, "Tennis"),
    "Combat Sports": ("combat_sports.m3u", "verified_combat.m3u8", "Combat Sports"),
    "Motorsport": ("verified_motorsport.m3u8", None, "Motorsport"),
}


def build_sport_playlists(
    channels: List[Dict[str, Any]],
    base_dir: str = ".",
    epg_url: str = DEFAULT_EPG_URL,
    sports_catalogue_path: str = "config/sports_catalogue.json",
) -> Dict[str, int]:
    """
    Build sport-specific playlists. Multi-sport channels appear in each relevant
    sport playlist without duplicating entries within a single playlist.
    """
    mapper = CompetitionMapper(sports_catalogue_path)
    counts: Dict[str, int] = {}

    for sport_name, (primary_file, legacy_file, title) in SPORT_OUTPUT_MAP.items():
        matching = [
            ch for ch in channels
            if any(s.get("sport") == sport_name for s in ch.get("sports", []))
        ]

        def resolve_grp(ch: Dict[str, Any], sname: str = sport_name) -> str:
            for s in ch.get("sports", []):
                if s.get("sport") == sname:
                    return mapper.get_playlist_group(sname, s.get("competitions", []))
            return sname

        out_path = os.path.join(base_dir, primary_file)
        cnt = write_m3u(
            out_path,
            matching,
            resolve_grp,
            playlist_title=title,
            epg_url=epg_url,
        )
        counts[primary_file] = cnt

        if legacy_file and legacy_file != primary_file:
            leg_path = os.path.join(base_dir, legacy_file)
            write_m3u(
                leg_path,
                matching,
                resolve_grp,
                playlist_title=title,
                epg_url=epg_url,
            )

    return counts

