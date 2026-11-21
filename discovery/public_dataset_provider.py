"""Public dataset & community IPTV catalogue provider (complete M3U and JSON ingestion)."""

import json
from typing import List

from discovery.base_provider import (
    BaseDiscoveryProvider,
    CandidateChannel,
    CandidateEPG,
    DiscoveryResult,
    detect_stream_format,
    extract_repo_provenance,
    http_get_conditional,
)
from discovery.github_provider import parse_m3u_to_candidates


class PublicDatasetProvider(BaseDiscoveryProvider):
    """
    Fetches complete public M3U/M3U8 playlists or JSON stream directories without
    silent alphabetical truncation. Uses dataset_retrieval_limit=0 for full ingestion,
    leaving downstream prioritization and verification budgets to the pipeline.
    """

    source_id = "public_dataset"

    def discover(self, max_results: int = 0) -> DiscoveryResult:
        """
        Retrieve public dataset.
        max_results <= 0 means retrieve the complete dataset (no truncation).
        """
        base_url = self.source_config.get("base_url", "")
        method = self.source_config.get("discovery_method", "direct_m3u")
        if not base_url:
            return DiscoveryResult(source_id=self.source_id, success=False, error="Missing base_url")

        channels: List[CandidateChannel] = []
        epg_urls: List[CandidateEPG] = []
        try:
            resp = http_get_conditional(base_url, timeout=15, retries=2)
            raw_text = resp["data"].decode("utf-8", errors="ignore")

            if method == "json_catalogue":
                data = json.loads(raw_text)
                repo_meta = extract_repo_provenance(base_url)
                if isinstance(data, list):
                    subset = data if max_results <= 0 else data[:max_results]
                    for idx, item in enumerate(subset):
                        stream_url = item.get("url") or item.get("stream_url") or ""
                        if not stream_url.startswith(("http://", "https://")):
                            continue
                        ch_name = item.get("title") or item.get("channel") or item.get("name") or "Unknown"
                        channels.append(
                            CandidateChannel(
                                name=str(ch_name),
                                stream_url=stream_url,
                                source_id=self.source_id,
                                source_url=base_url,
                                tvg_id=item.get("channel", "") or "",
                                stream_format=detect_stream_format(stream_url),
                                repo_name=repo_meta["repo_name"],
                                repo_url=repo_meta["repo_url"],
                                file_path=repo_meta["file_path"],
                                record_ref=f"json:{idx}",
                                metadata={"raw_dataset_item": item},
                            )
                        )
            else:
                if "#EXTINF" in raw_text:
                    channels, epg_urls = parse_m3u_to_candidates(raw_text, self.source_id, base_url)
                    if max_results > 0:
                        channels = channels[:max_results]

            return DiscoveryResult(
                source_id=self.source_id,
                success=True,
                channels=channels,
                epg_urls=epg_urls,
            )
        except Exception as e:
            return DiscoveryResult(source_id=self.source_id, success=False, error=str(e))
