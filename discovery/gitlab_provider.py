"""GitLab public repositories discovery provider."""

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


class GitLabProvider(BaseDiscoveryProvider):
    """Discovers M3U/M3U8 and XMLTV files from public GitLab projects."""

    source_id = "gitlab_public"

    def discover(self, max_results: int = 30) -> DiscoveryResult:
        queries = self.queries_config.get("platforms", {}).get("gitlab", {}).get(
            "queries", ["iptv sports", "sports m3u8"]
        )
        channels: List[CandidateChannel] = []
        epg_urls: List[CandidateEPG] = []
        seen_files: Set[str] = set()
        had_success = False
        last_err = ""

        for q in queries:
            if len(seen_files) >= max_results:
                break
            search_url = f"https://gitlab.com/api/v4/projects?search={urllib.parse.quote(q)}&per_page=8"
            try:
                projects = http_get_json(search_url, timeout=8)
                had_success = True
                for proj in projects:
                    pid = proj.get("id")
                    web_url = proj.get("web_url", "")
                    branch = proj.get("default_branch", "main")
                    if not pid or not web_url:
                        continue
                    tree_url = f"https://gitlab.com/api/v4/projects/{pid}/repository/tree?recursive=true&per_page=50"
                    try:
                        items = http_get_json(tree_url, timeout=6)
                        for item in items:
                            path = item.get("path", "")
                            raw_url = f"{web_url}/-/raw/{branch}/{path}"
                            if raw_url in seen_files:
                                continue
                            if path.lower().endswith((".m3u", ".m3u8")):
                                seen_files.add(raw_url)
                                txt = http_get_text(raw_url, timeout=6)
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
            except Exception as e:
                last_err = str(e)

        return DiscoveryResult(
            source_id=self.source_id,
            success=had_success,
            channels=channels,
            epg_urls=epg_urls,
            error="" if had_success else last_err,
        )

