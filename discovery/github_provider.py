"""GitHub discovery provider reusing github_file_search helpers."""

import concurrent.futures
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set

from discovery.base_provider import (
    BaseDiscoveryProvider,
    CandidateChannel,
    CandidateEPG,
    DiscoveryResult,
    detect_stream_format,
    extract_repo_provenance,
    http_get_json,
    http_get_text,
)
from playlist_parser import parse_m3u


def parse_m3u_to_candidates(
    content: str,
    source_id: str,
    source_url: str,
    discovery_query: str = "",
) -> tuple[List[CandidateChannel], List[CandidateEPG]]:
    """Parse M3U/M3U8 text into CandidateChannel and embedded CandidateEPG objects with line provenance."""
    channels: List[CandidateChannel] = []
    epgs: List[CandidateEPG] = []
    repo_meta = extract_repo_provenance(source_url)

    lines = content.splitlines()
    url_line_map: Dict[str, int] = {}
    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if idx <= 5 and s.startswith("#EXTM3U"):
            m = re.findall(r'(?:url-tvg|x-tvg-url)="([^"]+)"', s, re.IGNORECASE)
            for match in m:
                for u in match.split(","):
                    cu = u.strip()
                    if cu.startswith("http"):
                        fmt = "xml.gz" if cu.lower().endswith(".gz") else "xml"
                        epgs.append(CandidateEPG(url=cu, source_id=source_id, fmt=fmt))
        elif s and not s.startswith("#") and s not in url_line_map:
            url_line_map[s] = idx

    for ch in parse_m3u(content):
        if not ch.stream_url.startswith(("http://", "https://", "rtmp://", "rtsp://")):
            continue
        line_no = url_line_map.get(ch.stream_url, 0)
        channels.append(
            CandidateChannel(
                name=ch.name,
                stream_url=ch.stream_url,
                source_id=source_id,
                source_url=source_url,
                extinf=f'#EXTINF:-1 tvg-id="{ch.tvg_id}" tvg-name="{ch.tvg_name}" tvg-logo="{ch.tvg_logo}" group-title="{ch.group_title}",{ch.name}',
                tvg_id=ch.tvg_id,
                tvg_name=ch.tvg_name,
                tvg_logo=ch.tvg_logo,
                group_title=ch.group_title,
                stream_format=detect_stream_format(ch.stream_url),
                repo_name=repo_meta["repo_name"],
                repo_url=repo_meta["repo_url"],
                file_path=repo_meta["file_path"],
                record_ref=f"line:{line_no}" if line_no else "",
                discovery_query=discovery_query,
            )
        )
    return channels, epgs


class GitHubProvider(BaseDiscoveryProvider):
    """Discovers M3U/M3U8/XMLTV files from GitHub Code Search & Repo Trees."""

    source_id = "github_code_search"

    def __init__(
        self,
        source_config: Dict[str, Any],
        queries_config: Dict[str, Any],
        token: Optional[str] = None,
    ):
        super().__init__(source_config, queries_config)
        self.token = token or os.environ.get("GIST_PAT") or os.environ.get("GITHUB_TOKEN")

    def discover(self, max_results: int = 30) -> DiscoveryResult:
        queries = self.queries_config.get("platforms", {}).get("github", {}).get("queries", [])
        if not queries:
            queries = ["sports iptv m3u", "premier league m3u8", "xmltv sports epg"]

        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        raw_urls: Set[str] = set()
        epg_urls: List[CandidateEPG] = []

        for q in queries:
            if len(raw_urls) >= max_results:
                break
            try:
                if self.token:
                    url = (
                        f"https://api.github.com/search/code?q="
                        f"{urllib.parse.quote(q)}+extension:m3u+extension:m3u8"
                        f"&sort=indexed&order=desc&per_page=10"
                    )
                    data = http_get_json(url, headers=headers, timeout=8)
                    for item in data.get("items", []):
                        html_url = item.get("html_url", "")
                        raw_url = html_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                        if raw_url:
                            raw_urls.add(raw_url)
                else:
                    repo_url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(q)}&sort=stars&order=desc&per_page=5"
                    data = http_get_json(repo_url, headers=headers, timeout=8)
                    for repo in data.get("items", []):
                        full_name = repo.get("full_name")
                        branch = repo.get("default_branch", "main")
                        if not full_name:
                            continue
                        tree_url = f"https://api.github.com/repos/{full_name}/git/trees/{branch}?recursive=1"
                        try:
                            tree = http_get_json(tree_url, headers=headers, timeout=6)
                            for node in tree.get("tree", []):
                                path = node.get("path", "")
                                if path.lower().endswith((".m3u", ".m3u8")):
                                    raw_urls.add(f"https://raw.githubusercontent.com/{full_name}/{branch}/{path}")
                                elif path.lower().endswith((".xml", ".xml.gz")) and "epg" in path.lower():
                                    epg_urls.append(
                                        CandidateEPG(
                                            url=f"https://raw.githubusercontent.com/{full_name}/{branch}/{path}",
                                            source_id=self.source_id,
                                            fmt="xml.gz" if path.lower().endswith(".gz") else "xml",
                                        )
                                    )
                        except Exception:
                            continue
            except Exception as e:
                if not raw_urls and not epg_urls:
                    return DiscoveryResult(source_id=self.source_id, success=False, error=str(e))

        channels: List[CandidateChannel] = []

        def fetch_one(u: str) -> tuple[List[CandidateChannel], List[CandidateEPG]]:
            try:
                txt = http_get_text(u, timeout=8)
                if "#EXTINF" in txt:
                    return parse_m3u_to_candidates(txt, self.source_id, u)
            except Exception:
                pass
            return [], []

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for chs, epgs in ex.map(fetch_one, list(raw_urls)[:max_results]):
                channels.extend(chs)
                epg_urls.extend(epgs)

        return DiscoveryResult(
            source_id=self.source_id,
            success=True,
            channels=channels,
            epg_urls=epg_urls,
        )

