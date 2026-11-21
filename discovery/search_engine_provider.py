"""Optional search-engine discovery provider (Google Custom Search / Bing Web Search)."""

import os
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


class SearchEngineProvider(BaseDiscoveryProvider):
    """Discovers public M3U/XMLTV resources using configurable search queries."""

    source_id = "search_engine_dorks"

    def _collect_queries(self) -> List[str]:
        queries: List[str] = []
        queries.extend(self.queries_config.get("general_channel_discovery", []))
        queries.extend(self.queries_config.get("epg_discovery", []))
        for sport_queries in self.queries_config.get("by_sport", {}).values():
            queries.extend(sport_queries)
        return list(dict.fromkeys(queries))

    def _search_google(self, query: str, api_key: str, cse_id: str) -> List[str]:
        params = {"q": query, "key": api_key, "cx": cse_id, "num": 10}
        url = f"https://www.googleapis.com/customsearch/v1?{urllib.parse.urlencode(params)}"
        data = http_get_json(url, timeout=8)
        return [it.get("link", "") for it in data.get("items", []) if it.get("link")]

    def _search_bing(self, query: str, bing_key: str) -> List[str]:
        url = f"https://api.bing.microsoft.com/v7.0/search?q={urllib.parse.quote(query)}&count=10"
        data = http_get_json(url, headers={"Ocp-Apim-Subscription-Key": bing_key}, timeout=8)
        return [it.get("url", "") for it in data.get("webPages", {}).get("value", []) if it.get("url")]

    def discover(self, max_results: int = 20) -> DiscoveryResult:
        google_key = os.environ.get("GOOGLE_API_KEY")
        google_cse = os.environ.get("GOOGLE_CSE_ID")
        bing_key = os.environ.get("BING_SEARCH_API_KEY")

        queries = self._collect_queries()
        if not ((google_key and google_cse) or bing_key):
            # Gracefully succeed with 0 items when no search API credentials are configured
            return DiscoveryResult(source_id=self.source_id, success=True, channels=[], epg_urls=[])

        discovered_links: Set[str] = set()
        for q in queries[:10]:
            if len(discovered_links) >= max_results:
                break
            try:
                if google_key and google_cse:
                    discovered_links.update(self._search_google(q, google_key, google_cse))
                if bing_key:
                    discovered_links.update(self._search_bing(q, bing_key))
            except Exception:
                continue

        channels: List[CandidateChannel] = []
        epg_urls: List[CandidateEPG] = []
        for link in list(discovered_links)[:max_results]:
            low = link.lower().split("?")[0]
            if low.endswith((".xml", ".xml.gz")):
                epg_urls.append(
                    CandidateEPG(
                        url=link,
                        source_id=self.source_id,
                        fmt="xml.gz" if low.endswith(".gz") else "xml",
                    )
                )
            else:
                try:
                    txt = http_get_text(link, timeout=6)
                    if "#EXTINF" in txt:
                        chs, epgs = parse_m3u_to_candidates(txt, self.source_id, link)
                        channels.extend(chs)
                        epg_urls.extend(epgs)
                except Exception:
                    continue

        return DiscoveryResult(
            source_id=self.source_id,
            success=True,
            channels=channels,
            epg_urls=epg_urls,
        )

