"""EPG discovery provider for XMLTV, .xml.gz, and JSON guide directories (incl. iptv-org guides.json)."""

from typing import List, Set

from discovery.base_provider import (
    BaseDiscoveryProvider,
    CandidateEPG,
    DiscoveryResult,
    http_get_json,
)


class EPGDiscoveryProvider(BaseDiscoveryProvider):
    """Discovers public XMLTV and EPG guide sources."""

    source_id = "epg_discovery"

    def discover(self, max_results: int = 100) -> DiscoveryResult:
        base_url = self.source_config.get("base_url", "")
        method = self.source_config.get("discovery_method", "xmltv_gz")
        epg_urls: List[CandidateEPG] = []

        if not base_url:
            return DiscoveryResult(source_id=self.source_id, success=False, error="Missing base_url")

        try:
            if method == "json_catalogue":
                data = http_get_json(base_url, timeout=12)
                seen_urls: Set[str] = set()
                if isinstance(data, list):
                    for item in data:
                        if len(epg_urls) >= max_results:
                            break
                        # Handle direct XMLTV URL entries OR iptv-org guides.json (site/channel schema)
                        u = item.get("url") or ""
                        if not u and item.get("site"):
                            site = str(item["site"]).strip()
                            if site.startswith(("http://", "https://")):
                                u = site
                        if u.startswith(("http://", "https://")) and u not in seen_urls:
                            seen_urls.add(u)
                            fmt = "xml.gz" if u.lower().endswith(".gz") else "xml"
                            epg_urls.append(
                                CandidateEPG(
                                    url=u,
                                    source_id=self.source_id,
                                    fmt=fmt,
                                    source_name=item.get("site", "") or self.source_config.get("name", ""),
                                )
                            )
            else:
                fmt = "xml.gz" if base_url.lower().endswith(".gz") else "xml"
                epg_urls.append(
                    CandidateEPG(
                        url=base_url,
                        source_id=self.source_id,
                        fmt=fmt,
                        source_name=self.source_config.get("name", ""),
                    )
                )
            return DiscoveryResult(source_id=self.source_id, success=True, epg_urls=epg_urls)
        except Exception as e:
            return DiscoveryResult(source_id=self.source_id, success=False, error=str(e))
