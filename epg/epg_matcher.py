"""Multi-signal EPG channel matcher backed by config/broadcaster_catalogue.json."""

import json
import os
from typing import Any, Dict, List, Optional

from playlist_parser import normalize_name


def load_broadcaster_catalogue(path: str = "config/broadcaster_catalogue.json") -> Dict[str, Dict[str, Any]]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("broadcasters", {})
    return {}


class EPGMatcher:
    """
    Matches channels to EPG identifiers using:
      1. Exact tvg-id match against discovered XMLTV channels
      2. Config-driven broadcaster catalogue (config/broadcaster_catalogue.json)
      3. Normalized display-name & alias comparison
    """

    def __init__(
        self,
        epg_channels_index: Dict[str, Dict[str, Any]],
        broadcaster_catalogue_path: str = "config/broadcaster_catalogue.json",
    ):
        self.index = epg_channels_index
        self.broadcasters = load_broadcaster_catalogue(broadcaster_catalogue_path)
        self.norm_map: Dict[str, str] = {}
        for eid, meta in epg_channels_index.items():
            parts = eid.split(".")
            base = "".join(parts[:-1]) if len(parts) > 1 else eid
            self.norm_map[normalize_name(eid)] = eid
            self.norm_map[normalize_name(base)] = eid
            for dn in meta.get("display_names", []):
                ndn = normalize_name(dn)
                if ndn:
                    self.norm_map.setdefault(ndn, eid)

    def match(
        self,
        name: str,
        tvg_id: str = "",
        aliases: Optional[List[str]] = None,
        broadcaster: str = "",
    ) -> Dict[str, Any]:
        aliases = aliases or []

        # 1. Exact tvg-id match
        if tvg_id and tvg_id in self.index:
            meta = self.index[tvg_id]
            conf = 0.65 if meta.get("is_stale") else 0.98
            return {
                "tvg_id": tvg_id,
                "epg_source": meta.get("epg_source", ""),
                "tvg_logo": meta.get("icon", ""),
                "status": "matched",
                "confidence": conf,
                "match_method": "exact_id",
            }

        # 2. Config-driven canonical broadcaster mapping (longest key match first)
        low_name = name.lower()
        for key in sorted(self.broadcasters.keys(), key=len, reverse=True):
            if key in low_name:
                cmeta = self.broadcasters[key]
                cid = cmeta["tvg_id"]
                in_live_epg = cid in self.index
                is_stale = self.index.get(cid, {}).get("is_stale", False) if in_live_epg else False
                conf = 0.90 if (in_live_epg and not is_stale) else (0.55 if is_stale else 0.75)
                return {
                    "tvg_id": cid,
                    "epg_source": self.index.get(cid, {}).get("epg_source", ""),
                    "tvg_logo": cmeta.get("logo", "") or self.index.get(cid, {}).get("icon", ""),
                    "status": "matched",
                    "confidence": conf,
                    "match_method": "canonical_broadcaster",
                }

        # 3. Normalized name / alias match
        for candidate in [name] + aliases:
            nc = normalize_name(candidate)
            if nc and nc in self.norm_map:
                eid = self.norm_map[nc]
                meta = self.index.get(eid, {})
                conf = 0.50 if meta.get("is_stale") else 0.80
                if broadcaster and broadcaster.lower() in " ".join(meta.get("display_names", [])).lower():
                    conf = min(0.95, conf + 0.08)
                return {
                    "tvg_id": eid,
                    "epg_source": meta.get("epg_source", ""),
                    "tvg_logo": meta.get("icon", ""),
                    "status": "matched",
                    "confidence": round(conf, 2),
                    "match_method": "normalized_name",
                }

        return {
            "tvg_id": tvg_id,
            "epg_source": "",
            "tvg_logo": "",
            "status": "unmatched",
            "confidence": 0.0,
            "match_method": "none",
        }
