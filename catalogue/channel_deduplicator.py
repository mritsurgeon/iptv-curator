"""Stable channel identifier & cross-source deduplication."""

import hashlib
import re
from typing import Any, Dict, List

from playlist_parser import normalize_name


def make_stable_channel_id(name: str, tvg_id: str = "") -> str:
    """
    Generate a deterministic, stable channel identifier from normalized name or tvg_id
    so channels discovered across different repositories map to the same canonical record.
    """
    base = normalize_name(name) or normalize_name(tvg_id) or "unknown"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:32]
    return f"{slug}_{digest}" if slug else f"ch_{digest}"


class ChannelDeduplicator:
    """Groups multiple stream endpoints from different sources under one canonical channel."""

    def __init__(self, existing_channels: List[Dict[str, Any]] = None):
        self.by_norm: Dict[str, str] = {}
        for ch in existing_channels or []:
            cid = ch["channel_id"]
            for candidate_name in [ch.get("name", "")] + ch.get("aliases", []):
                n = normalize_name(candidate_name)
                if n:
                    self.by_norm[n] = cid

    def resolve_channel_id(self, name: str, tvg_id: str = "", aliases: List[str] = None) -> str:
        for candidate in [name] + (aliases or []):
            n = normalize_name(candidate)
            if n and n in self.by_norm:
                return self.by_norm[n]
        cid = make_stable_channel_id(name, tvg_id)
        n_main = normalize_name(name)
        if n_main:
            self.by_norm[n_main] = cid
        return cid

