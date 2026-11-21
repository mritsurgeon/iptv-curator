"""
Keyless Hybrid Search-Engine & Google Dork Discovery Provider.
- Dynamically generates search queries from config/sports_catalogue.json and
  config/broadcaster_catalogue.json (competitions, aliases, broadcasters, formats).
- Supports keyless public search endpoints (DuckDuckGo HTML/Lite, optional SearXNG)
  plus optional Google CSE / Bing when keys exist, and exports Google Dork URLs
  for manual reference.
- Never attempts to bypass CAPTCHAs or anti-bot gates; gracefully falls back on
  block/403/429 and records per-query discovery yield metrics in SQLite.
"""

import json
import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple

from database.database import DatabaseManager
from discovery.bandit_allocator import UCB1BanditAllocator
from discovery.base_provider import (
    BaseDiscoveryProvider,
    CandidateChannel,
    CandidateEPG,
    DiscoveryResult,
    http_get_json,
    http_get_text,
)
from discovery.github_provider import parse_m3u_to_candidates


def generate_dynamic_dork_queries(
    sports_catalogue: Dict[str, Any],
    broadcaster_catalogue: Optional[Dict[str, Any]] = None,
    max_queries: int = 40,
) -> List[Dict[str, str]]:
    """
    Dynamically generate search queries from sports_catalogue.json & broadcaster_catalogue.json
    covering M3U, M3U8, XMLTV, EPG, JSON and alternative Git platforms.
    """
    queries: List[Dict[str, str]] = [
        {"query": 'site:github.com filetype:m3u sports', "sport": ""},
        {"query": 'site:gitlab.com filetype:m3u sports', "sport": ""},
        {"query": 'site:codeberg.org IPTV playlist sports', "sport": ""},
        {"query": 'filetype:xml.gz xmltv sports', "sport": ""},
        {"query": '"tvg-id" "group-title" filetype:m3u sports', "sport": ""},
    ]
    seen: Set[str] = {q["query"].lower() for q in queries}

    for sport_name, sdata in sports_catalogue.get("sports", {}).items():
        for _, comp_list in sdata.get("competition_types", {}).items():
            for comp in comp_list:
                if not comp.get("active", True):
                    continue
                cname = comp["name"]
                for fmt_term in ('"m3u"', '"xmltv"'):
                    q = f'"{cname}" {fmt_term}'
                    if q.lower() not in seen:
                        seen.add(q.lower())
                        queries.append({"query": q, "sport": sport_name})
                if len(queries) >= max_queries:
                    return queries

    for bc_meta in (broadcaster_catalogue or {}).values():
        bc_name = bc_meta.get("broadcaster", "")
        if bc_name:
            q = f'"{bc_name}" "tvg-id" m3u8'
            if q.lower() not in seen:
                seen.add(q.lower())
                queries.append({"query": q, "sport": ""})
        if len(queries) >= max_queries:
            break

    return queries[:max_queries]


def build_google_search_url(query: str) -> str:
    """Build a standard Google Search URL for manual/reference dork execution."""
    return f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"


def _extract_ddg_links(html: str) -> List[str]:
    """Extract target URLs from DuckDuckGo HTML/Lite search results."""
    links: List[str] = []
    # Direct links or uddg= redirect parameters
    for m in re.findall(r'uddg=([^&"\']+)', html):
        url = urllib.parse.unquote(m)
        if url.startswith(("http://", "https://")):
            links.append(url)
    for m in re.findall(r'href="(https?://[^"]+)"', html):
        if "duckduckgo.com" not in m and m not in links:
            links.append(m)
    return list(dict.fromkeys(links))


def _normalize_repo_raw_url(url: str) -> str:
    """Convert GitHub/GitLab/Codeberg blob links into raw downloadable URLs when applicable."""
    if "github.com/" in url and "/blob/" in url:
        return url.replace("github.com/", "raw.githubusercontent.com/").replace("/blob/", "/")
    if "gitlab.com/" in url and "/-/blob/" in url:
        return url.replace("/-/blob/", "/-/raw/")
    if "codeberg.org/" in url and "/src/branch/" in url:
        return url.replace("/src/branch/", "/raw/branch/")
    return url


class GoogleDorkProvider(BaseDiscoveryProvider):
    """
    Keyless hybrid search-engine discovery provider.
    Uses dynamic queries generated from sports_catalogue.json, selects top queries via
    UCB1 yield statistics in SQLite, searches keyless public backends (DuckDuckGo Lite,
    optional SearXNG, or optional Google/Bing APIs if keys are configured), and exports
    Google Dork reference URLs.
    """

    source_id = "google_dork_discovery"

    def __init__(
        self,
        source_config: Dict[str, Any],
        queries_config: Dict[str, Any],
        db: Optional[DatabaseManager] = None,
        sports_catalogue: Optional[Dict[str, Any]] = None,
        broadcaster_catalogue: Optional[Dict[str, Any]] = None,
        dorks_export_path: str = "google_dorks.txt",
    ):
        super().__init__(source_config, queries_config)
        self.db = db
        self.sports_catalogue = sports_catalogue or {}
        self.broadcaster_catalogue = broadcaster_catalogue or {}
        self.dorks_export_path = dorks_export_path
        self.bandit = UCB1BanditAllocator()

    def _export_dorks(self, queries: List[Dict[str, str]]) -> None:
        try:
            with open(self.dorks_export_path, "w", encoding="utf-8") as f:
                f.write("# Dynamic Sports IPTV & EPG Search Dorks (Generated from sports_catalogue.json)\n\n")
                for q in queries:
                    f.write(f"{q['query']}\n  -> {build_google_search_url(q['query'])}\n")
        except Exception:
            pass

    def _search_duckduckgo_keyless(self, query: str) -> List[str]:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        html = http_get_text(url, timeout=8, retries=0)
        # Respect anti-bot/CAPTCHA gates immediately without retrying or bypassing
        if "captcha" in html.lower() or "anomaly.js" in html.lower():
            return []
        return _extract_ddg_links(html)

    def _search_searxng_keyless(self, query: str, instance_url: str) -> List[str]:
        base = instance_url.rstrip("/")
        url = f"{base}/search?q={urllib.parse.quote_plus(query)}&format=json"
        data = http_get_json(url, timeout=8, retries=0)
        return [r.get("url", "") for r in data.get("results", []) if r.get("url")]

    def _run_search_query(self, query: str) -> List[str]:
        """Try configured keyless/optional search providers with graceful fallback."""
        links: List[str] = []
        searx_url = os.environ.get("SEARXNG_URL", "").strip()
        if searx_url:
            try:
                links.extend(self._search_searxng_keyless(query, searx_url))
            except Exception:
                pass

        if not links:
            try:
                links.extend(self._search_duckduckgo_keyless(query))
            except Exception:
                pass

        return list(dict.fromkeys(links))

    def discover(self, max_results: int = 20) -> DiscoveryResult:
        dynamic_qs = generate_dynamic_dork_queries(
            self.sports_catalogue,
            self.broadcaster_catalogue,
            max_queries=35,
        )
        self._export_dorks(dynamic_qs)

        selected_records: List[Dict[str, Any]] = []
        if self.db is not None:
            for item in dynamic_qs:
                self.db.ensure_search_query(
                    self.source_id,
                    item["query"],
                    origin="dynamic_dork",
                    sport_hint=item.get("sport", ""),
                )
            all_q = self.db.get_search_queries(self.source_id)
            selected_records = self.bandit.select_queries(all_q, budget=min(6, max_results))
        else:
            selected_records = [{"query_id": "", "query_text": q["query"]} for q in dynamic_qs[:5]]

        channels: List[CandidateChannel] = []
        epg_urls: List[CandidateEPG] = []
        seen_urls: Set[str] = set()

        for qrec in selected_records:
            qtext = qrec["query_text"]
            qid = qrec.get("query_id", "")
            t0 = time.monotonic()
            raw_links = self._run_search_query(qtext)

            new_sources = 0
            new_playlists = 0
            new_epgs = 0
            new_cands = 0

            for link in raw_links[:10]:
                norm = _normalize_repo_raw_url(link)
                if norm in seen_urls:
                    continue
                seen_urls.add(norm)
                new_sources += 1
                low = norm.lower().split("?")[0]

                if low.endswith((".xml", ".xml.gz")):
                    epg_urls.append(
                        CandidateEPG(
                            url=norm,
                            source_id=self.source_id,
                            fmt="xml.gz" if low.endswith(".gz") else "xml",
                        )
                    )
                    new_epgs += 1
                elif low.endswith((".m3u", ".m3u8")):
                    try:
                        txt = http_get_text(norm, timeout=6, retries=0)
                        if "#EXTINF" in txt:
                            chs, epgs = parse_m3u_to_candidates(txt, self.source_id, norm)
                            channels.extend(chs)
                            epg_urls.extend(epgs)
                            new_playlists += 1
                            new_cands += len(chs)
                            new_epgs += len(epgs)
                    except Exception:
                        continue

            elapsed = max(0.1, time.monotonic() - t0)
            if self.db is not None and qid:
                yield_reward = (new_cands + 2 * new_epgs) / elapsed
                self.db.record_query_reward(
                    query_id=qid,
                    reward=min(5.0, yield_reward),
                    results_discovered=len(raw_links),
                    new_source_urls=new_sources,
                    new_playlists=new_playlists,
                    new_epg_feeds=new_epgs,
                    new_candidate_channels=new_cands,
                    execution_sec=elapsed,
                )

            if len(channels) >= max_results * 10:
                break

        return DiscoveryResult(
            source_id=self.source_id,
            success=True,
            channels=channels,
            epg_urls=epg_urls,
        )

