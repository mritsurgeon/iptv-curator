"""SourceHut public repositories discovery provider."""

import re
import urllib.parse
from typing import List, Set

from discovery.base_provider import (
    BaseDiscoveryProvider,
    CandidateChannel,
    CandidateEPG,
    DiscoveryResult,
    http_get_text,
)
from discovery.github_provider import parse_m3u_to_candidates


class SourceHutProvider(BaseDiscoveryProvider):
    """Discovers public IPTV playlists and EPG files indexed on SourceHut (git.sr.ht)."""

    source_id = "sourcehut_public"

    def discover(self, max_results: int = 15) -> DiscoveryResult:
        queries = self.queries_config.get("platforms", {}).get("sourcehut", {}).get(
            "queries", ["iptv", "m3u8"]
        )
        channels: List[CandidateChannel] = []
        epg_urls: List[CandidateEPG] = []
        seen_repos: Set[str] = set()
        had_success = False
        last_err = ""

        for q in queries:
            if len(seen_repos) >= max_results:
                break
            url = f"https://git.sr.ht/projects?search={urllib.parse.quote(q)}"
            try:
                html = http_get_text(url, timeout=6)
                had_success = True
                # Extract repo paths like /~user/repo
                for match in re.findall(r'href="(/~[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)"', html):
                    if match in seen_repos:
                        continue
                    seen_repos.add(match)
                    for fname in ("sports.m3u", "sports.m3u8", "playlist.m3u", "playlist.m3u8"):
                        raw_url = f"https://git.sr.ht{match}/blob/main/{fname}"
                        try:
                            txt = http_get_text(raw_url, timeout=4)
                            if "#EXTINF" in txt:
                                chs, epgs = parse_m3u_to_candidates(txt, self.source_id, raw_url)
                                channels.extend(chs)
                                epg_urls.extend(epgs)
                        except Exception:
                            continue
            except Exception as e:
                last_err = str(e)

        return DiscoveryResult(
            source_id=self.source_id,
            success=had_success,
            channels=channels,
            epg_urls=epg_urls,
            error="" if had_success else last_err,
        )

