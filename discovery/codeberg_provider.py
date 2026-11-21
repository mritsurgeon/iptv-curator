"""Codeberg public repositories discovery provider."""

import urllib.error
import urllib.parse
from typing import Any, Dict, List, Set

from discovery.base_provider import (
    BaseDiscoveryProvider,
    CandidateChannel,
    CandidateEPG,
    DiscoveryResult,
    http_get_json,
    http_get_text,
)
from discovery.github_provider import parse_m3u_to_candidates


class CodebergProvider(BaseDiscoveryProvider):
    """Discovers M3U/M3U8 and XMLTV files from Codeberg (Forgejo/Gitea API)."""

    source_id = "codeberg_public"

    def discover(self, max_results: int = 20) -> DiscoveryResult:
        queries = self.queries_config.get("platforms", {}).get("codeberg", {}).get(
            "queries", ["iptv sports", "sports m3u"]
        )
        channels: List[CandidateChannel] = []
        epg_urls: List[CandidateEPG] = []
        seen_files: Set[str] = set()
        had_success = False
        last_err = ""

        for q in queries:
            if len(seen_files) >= max_results:
                break
            search_url = f"https://codeberg.org/api/v1/repos/search?q={urllib.parse.quote(q)}&limit=5"
            try:
                data = http_get_json(search_url, timeout=6)
                had_success = True
                for rp in data.get("data", []):
                    owner = rp.get("owner", {}).get("login")
                    name = rp.get("name")
                    branch = rp.get("default_branch", "main")
                    if not owner or not name:
                        continue
                    tree_url = f"https://codeberg.org/api/v1/repos/{owner}/{name}/git/trees/{branch}?recursive=1"
                    try:
                        tree = http_get_json(tree_url, timeout=6)
                        for node in tree.get("tree", []):
                            path = node.get("path", "")
                            raw_url = f"https://codeberg.org/{owner}/{name}/raw/branch/{branch}/{path}"
                            if raw_url in seen_files:
                                continue
                            if path.lower().endswith((".m3u", ".m3u8")):
                                seen_files.add(raw_url)
                                txt = http_get_text(raw_url, timeout=5)
                                if "#EXTINF" in txt:
                                    chs, epgs = parse_m3u_to_candidates(txt, self.source_id, raw_url)
                                    channels.extend(chs)
                                    epg_urls.extend(epgs)
                            elif path.lower().endswith((".xml", ".xml.gz")) and "epg" in path.lower():
                                seen_files.add(raw_url)
                                epg_urls.append(
                                    CandidateEPG(
                                        url=raw_url,
                                        source_id=self.source_id,
                                        fmt="xml.gz" if path.lower().endswith(".gz") else "xml",
                                    )
                                )
                    except Exception:
                        continue
            except urllib.error.HTTPError as e:
                last_err = str(e)
                if e.code == 429:
                    # Respect Codeberg rate limit immediately without hammering remaining queries
                    break
            except Exception as e:
                last_err = str(e)

        return DiscoveryResult(
            source_id=self.source_id,
            success=had_success,
            channels=channels,
            epg_urls=epg_urls,
            error="" if had_success else last_err,
        )

