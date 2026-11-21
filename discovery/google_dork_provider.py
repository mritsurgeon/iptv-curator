"""
Keyless Hybrid Search-Engine & Google Dork Discovery Provider.
- Integrates config/discovery_queries.json (including intitle:"index of" M3U/M3U8 dorks)
  with dynamically generated queries from config/sports_catalogue.json and
  config/broadcaster_catalogue.json (no duplicate query definitions).
- Supports keyless public search backends (optional SEARXNG_URL, DuckDuckGo HTML,
  Bing HTML fallback) without requiring paid API keys.
- Enforces a per-run circuit breaker on CAPTCHA / throttling / HTTP errors so blocked
  providers are never repeatedly retried and never halt the wider pipeline.
"""

import base64
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

BOT_GATE_MARKERS = (
    "captcha",
    "anomaly.js",
    "verify you are human",
    "robot check",
    "unusual traffic",
)


def generate_dynamic_dork_queries(
    sports_catalogue: Dict[str, Any],
    broadcaster_catalogue: Optional[Dict[str, Any]] = None,
    queries_config: Optional[Dict[str, Any]] = None,
    max_queries: int = 60,
) -> List[Dict[str, str]]:
    """
    Build deduplicated discovery queries from:
      1. config/discovery_queries.json (general_channel_discovery incl. intitle:"index of",
         epg_discovery, by_sport)
      2. config/sports_catalogue.json (sports, competitions, aliases)
      3. config/broadcaster_catalogue.json (broadcasters, channel names, TVG IDs)
    """
    queries: List[Dict[str, str]] = []
    seen: Set[str] = set()

    def _add(qtext: str, sport: str = "", origin: str = "dynamic_dork") -> None:
        cleaned = qtext.strip()
        if not cleaned or cleaned.lower() in seen or len(queries) >= max_queries:
            return
        seen.add(cleaned.lower())
        queries.append({"query": cleaned, "sport": sport, "origin": origin})

    # 1. Integrate configured queries (single source of truth: config/discovery_queries.json)
    qcfg = queries_config or {}
    for q in qcfg.get("general_channel_discovery", []):
        _add(q, origin="config_general")
    for q in qcfg.get("epg_discovery", []):
        _add(q, origin="config_epg")
    for sp, sp_qs in qcfg.get("by_sport", {}).items():
        for q in sp_qs:
            _add(q, sport=sp, origin="config_sport")

    # Core cross-platform & public directory index templates
    for base_q in (
        'site:github.com filetype:m3u sports',
        'site:gitlab.com filetype:m3u sports',
        'site:codeberg.org IPTV playlist sports',
        'filetype:xml.gz xmltv sports',
        '"tvg-id" "group-title" filetype:m3u sports',
        'intitle:"index of" ("sports.m3u8" OR "sports.m3u")',
    ):
        _add(base_q, origin="dynamic_dork")

    # 2. Expand dynamically from sports_catalogue.json (competitions & aliases)
    for sport_name, sdata in sports_catalogue.get("sports", {}).items():
        _add(f'intitle:"index of" m3u8 "{sport_name}"', sport=sport_name)
        for _, comp_list in sdata.get("competition_types", {}).items():
            for comp in comp_list:
                if not comp.get("active", True):
                    continue
                cname = comp["name"]
                _add(f'"{cname}" ("m3u" OR "m3u8")', sport=sport_name)
                _add(f'"{cname}" "xmltv"', sport=sport_name)
                if len(queries) >= max_queries:
                    return queries

    # 3. Expand dynamically from broadcaster_catalogue.json (channels, broadcasters, TVG IDs)
    for ch_key, bc_meta in (broadcaster_catalogue or {}).items():
        bc_name = bc_meta.get("broadcaster", "")
        tvg_name = bc_meta.get("tvg_name", "") or ch_key
        if bc_name:
            _add(f'"{bc_name}" "tvg-id" m3u8')
        if tvg_name and tvg_name.lower() != bc_name.lower():
            _add(f'"{tvg_name}" filetype:m3u8')
        if len(queries) >= max_queries:
            break

    return queries[:max_queries]


def build_google_search_url(query: str) -> str:
    """Build a standard Google Search URL for manual/reference dork execution."""
    return f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"


def _is_bot_gate(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in BOT_GATE_MARKERS)


def _extract_ddg_links(html: str) -> List[str]:
    """Extract deduplicated target URLs from DuckDuckGo HTML/Lite search results."""
    links: List[str] = []
    for m in re.findall(r'uddg=([^&"\']+)', html):
        url = urllib.parse.unquote(m)
        if url.startswith(("http://", "https://")):
            links.append(url)
    for m in re.findall(r'href="(https?://[^"]+)"', html):
        if "duckduckgo.com" not in m:
            links.append(m)
    return list(dict.fromkeys(links))


def _extract_bing_links(html: str) -> List[str]:
    """Extract deduplicated organic URLs from Bing HTML results (direct + u=a1 encoded)."""
    links: List[str] = []
    for m in re.findall(r'href="(https?://[^"]+)"', html):
        if not any(d in m for d in ("bing.com", "microsoft.com", "live.com", "w3.org")):
            links.append(m)
    for token in re.findall(r"u=a1([A-Za-z0-9_-]+)", html):
        pad = token + "=" * (-len(token) % 4)
        try:
            decoded = base64.urlsafe_b64decode(pad).decode("utf-8", errors="ignore")
            if decoded.startswith(("http://", "https://")) and "bing.com" not in decoded:
                links.append(decoded)
        except Exception:
            continue
    return list(dict.fromkeys(links))


NON_EPG_XML_NAMES = (
    "sitemap.xml",
    "rss.xml",
    "feed.xml",
    "atom.xml",
    "opensearch.xml",
    "manifest.xml",
    "pom.xml",
    "package.xml",
    "web.xml",
    "browserconfig.xml",
    "crossdomain.xml",
)

SKIP_HTML_EXTENSIONS = (
    ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".ico", ".mp4", ".mkv", ".avi", ".mp3", ".css", ".js",
    ".woff", ".woff2", ".ttf", ".exe", ".dmg",
)


def _normalize_repo_raw_url(url: str) -> str:
    """Convert GitHub/GitLab/Codeberg/Pastebin links into raw downloadable URLs when applicable."""
    if "github.com/" in url and "/blob/" in url:
        return url.replace("github.com/", "raw.githubusercontent.com/").replace("/blob/", "/")
    if "gitlab.com/" in url and "/-/blob/" in url:
        return url.replace("/-/blob/", "/-/raw/")
    if "codeberg.org/" in url and "/src/branch/" in url:
        return url.replace("/src/branch/", "/raw/branch/")
    m_pb = re.match(r"^https?://(?:www\.)?pastebin\.com/([A-Za-z0-9]{6,12})/?$", url)
    if m_pb:
        return f"https://pastebin.com/raw/{m_pb.group(1)}"
    return url


def _is_candidate_epg_url(url: str) -> bool:
    low = url.lower().split("?")[0].split("#")[0]
    if not low.endswith((".xml", ".xml.gz")):
        return False
    fname = low.rsplit("/", 1)[-1]
    if fname in NON_EPG_XML_NAMES:
        return False
    return True


def _extract_playlist_and_epg_links_from_html(
    html: str,
    base_url: str,
    max_links: int = 12,
) -> List[str]:
    """
    1-hop HTML link extractor for search results (directory indexes, GitHub/GitLab
    repo pages, blogs, paste pages). Resolves relative hrefs and extracts embedded
    .m3u/.m3u8/.xml/.xml.gz URLs, normalizing repo blob links to raw URLs.
    """
    found: List[str] = []
    seen: Set[str] = set()

    def _add_candidate(raw_href: str) -> None:
        if len(found) >= max_links:
            return
        href = raw_href.strip().replace("&amp;", "&")
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            return
        full = urllib.parse.urljoin(base_url, href)
        if not full.startswith(("http://", "https://")):
            return
        norm = _normalize_repo_raw_url(full)
        low = norm.lower().split("?")[0].split("#")[0]
        if low.endswith((".m3u", ".m3u8")) or _is_candidate_epg_url(norm):
            if norm not in seen:
                seen.add(norm)
                found.append(norm)

    for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        _add_candidate(href)
        if len(found) >= max_links:
            return found

    for raw_url in re.findall(
        r'https?://[^\s"\'<>()]+?\.(?:m3u8?|xml(?:\.gz)?)(?:\?[^\s"\'<>()]*)?',
        html,
        flags=re.IGNORECASE,
    ):
        _add_candidate(raw_url)
        if len(found) >= max_links:
            return found

    return found


class GoogleDorkProvider(BaseDiscoveryProvider):
    """
    Keyless hybrid search-engine discovery provider.
    Uses configured + dynamic queries, selects top queries via UCB1 yield statistics,
    queries keyless backends with a per-run circuit breaker on blocks/CAPTCHAs,
    and exports Google Search dork URLs for manual reference.
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
        self._blocked_backends: Set[str] = set()

    def _export_dorks(self, queries: List[Dict[str, str]]) -> None:
        try:
            with open(self.dorks_export_path, "w", encoding="utf-8") as f:
                f.write("# Dynamic Sports IPTV & EPG Search Dorks (Generated from config + catalogues)\n\n")
                for q in queries:
                    f.write(f"{q['query']}\n  -> {build_google_search_url(q['query'])}\n")
        except Exception:
            pass

    def _search_searxng_keyless(self, query: str, instance_url: str) -> List[str]:
        base = instance_url.rstrip("/")
        url = f"{base}/search?q={urllib.parse.quote_plus(query)}&format=json"
        data = http_get_json(url, timeout=8, retries=0)
        return [r.get("url", "") for r in data.get("results", []) if r.get("url")]

    def _search_duckduckgo_keyless(self, query: str) -> List[str]:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        html = http_get_text(url, timeout=8, retries=0)
        if _is_bot_gate(html):
            self._blocked_backends.add("duckduckgo_html")
            return []
        return _extract_ddg_links(html)

    def _search_bing_keyless(self, query: str) -> List[str]:
        url = f"https://www.bing.com/search?q={urllib.parse.quote_plus(query)}"
        html = http_get_text(url, timeout=8, retries=0)
        if _is_bot_gate(html):
            self._blocked_backends.add("bing_html")
            return []
        return _extract_bing_links(html)

    def _run_search_query(self, query: str) -> Tuple[List[str], str]:
        """
        Query available keyless backends in priority order, skipping any backend
        that tripped the per-run circuit breaker. Returns (deduplicated_links, backend_used).
        """
        links: List[str] = []
        used = "none"

        searx_url = os.environ.get("SEARXNG_URL", "").strip()
        if searx_url and "searxng" not in self._blocked_backends:
            try:
                res = self._search_searxng_keyless(query, searx_url)
                if res:
                    links.extend(res)
                    used = "searxng"
            except Exception:
                self._blocked_backends.add("searxng")

        if "duckduckgo_html" not in self._blocked_backends:
            try:
                res = self._search_duckduckgo_keyless(query)
                if res:
                    links.extend(res)
                    used = "duckduckgo_html" if used == "none" else f"{used}+duckduckgo_html"
            except Exception:
                self._blocked_backends.add("duckduckgo_html")

        if not links and "bing_html" not in self._blocked_backends:
            try:
                res = self._search_bing_keyless(query)
                if res:
                    links.extend(res)
                    used = "bing_html"
            except Exception:
                self._blocked_backends.add("bing_html")

        return list(dict.fromkeys(links)), used

    def discover(self, max_results: int = 20) -> DiscoveryResult:
        dynamic_qs = generate_dynamic_dork_queries(
            self.sports_catalogue,
            self.broadcaster_catalogue,
            queries_config=self.queries_config,
            max_queries=60,
        )
        self._export_dorks(dynamic_qs)

        selected_records: List[Dict[str, Any]] = []
        if self.db is not None:
            for item in dynamic_qs:
                self.db.ensure_search_query(
                    self.source_id,
                    item["query"],
                    origin=item.get("origin", "dynamic_dork"),
                    sport_hint=item.get("sport", ""),
                )
            all_q = self.db.get_search_queries(self.source_id)
            selected_records = self.bandit.select_queries(all_q, budget=min(8, max_results))
        else:
            selected_records = [{"query_id": "", "query_text": q["query"]} for q in dynamic_qs[:6]]

        channels: List[CandidateChannel] = []
        epg_urls: List[CandidateEPG] = []
        seen_urls: Set[str] = set()

        for qrec in selected_records:
            qtext = qrec["query_text"]
            qid = qrec.get("query_id", "")
            t0 = time.monotonic()
            raw_links, _backend = self._run_search_query(qtext)

            new_sources = 0
            new_playlists = 0
            new_epgs = 0
            new_cands = 0

            def _ingest_direct_url(target_url: str) -> Tuple[int, int, int]:
                """Ingest a direct .m3u/.m3u8/.xml/.xml.gz URL. Returns (playlists, epgs, cands)."""
                t_low = target_url.lower().split("?")[0].split("#")[0]
                if _is_candidate_epg_url(target_url):
                    epg_urls.append(
                        CandidateEPG(
                            url=target_url,
                            source_id=self.source_id,
                            fmt="xml.gz" if t_low.endswith(".gz") else "xml",
                        )
                    )
                    return (0, 1, 0)
                if t_low.endswith((".m3u", ".m3u8")):
                    try:
                        txt = http_get_text(target_url, timeout=6, retries=0)
                        if "#EXTINF" in txt:
                            chs, epgs = parse_m3u_to_candidates(
                                txt, self.source_id, target_url, discovery_query=qtext
                            )
                            channels.extend(chs)
                            epg_urls.extend(epgs)
                            return (1, len(epgs), len(chs))
                    except Exception:
                        return (0, 0, 0)
                return (0, 0, 0)

            html_hops_used = 0
            max_html_hops_per_query = 5

            for link in raw_links[:10]:
                norm = _normalize_repo_raw_url(link)
                if norm in seen_urls:
                    continue
                seen_urls.add(norm)
                new_sources += 1
                low = norm.lower().split("?")[0].split("#")[0]

                if low.endswith((".m3u", ".m3u8")) or _is_candidate_epg_url(norm):
                    p_cnt, e_cnt, c_cnt = _ingest_direct_url(norm)
                    new_playlists += p_cnt
                    new_epgs += e_cnt
                    new_cands += c_cnt
                elif html_hops_used < max_html_hops_per_query and not low.endswith(SKIP_HTML_EXTENSIONS):
                    # 1-hop HTML crawl for directory indexes ("index of"), repo pages, and paste pages
                    html_hops_used += 1
                    try:
                        page_txt = http_get_text(norm, timeout=5, retries=0)
                    except Exception:
                        continue
                    if not page_txt or _is_bot_gate(page_txt):
                        continue
                    if "#EXTINF" in page_txt:
                        chs, epgs = parse_m3u_to_candidates(
                            page_txt, self.source_id, norm, discovery_query=qtext
                        )
                        channels.extend(chs)
                        epg_urls.extend(epgs)
                        new_playlists += 1
                        new_cands += len(chs)
                        new_epgs += len(epgs)
                        continue
                    hop_links = _extract_playlist_and_epg_links_from_html(page_txt, norm, max_links=6)
                    for hop_url in hop_links:
                        if hop_url in seen_urls:
                            continue
                        seen_urls.add(hop_url)
                        new_sources += 1
                        p_cnt, e_cnt, c_cnt = _ingest_direct_url(hop_url)
                        new_playlists += p_cnt
                        new_epgs += e_cnt
                        new_cands += c_cnt

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
