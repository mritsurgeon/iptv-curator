"""Official broadcaster & sports federation discovery provider."""

import re
from typing import List

from discovery.base_provider import (
    BaseDiscoveryProvider,
    CandidateChannel,
    DiscoveryResult,
    detect_stream_format,
    http_get_text,
)


class OfficialBroadcasterProvider(BaseDiscoveryProvider):
    """Discovers public FAST/FTA/federation streams from configured broadcaster pages."""

    source_id = "official_broadcasters_catalogue"

    def discover(self, max_results: int = 30) -> DiscoveryResult:
        broadcasters = self.queries_config.get("official_broadcasters", [])
        channels: List[CandidateChannel] = []
        had_success = False
        last_err = ""

        m3u8_re = re.compile(r'https?://[^\s"\'<>]+\.m3u8(?:\?[^\s"\'<>]*)?', re.IGNORECASE)

        for bc in broadcasters[:max_results]:
            name = bc.get("name", "Broadcaster")
            url = bc.get("url", "")
            sport = bc.get("sport", "")
            lang = bc.get("language", "English")
            if not url:
                continue
            try:
                html = http_get_text(url, timeout=6, retries=1)
                had_success = True
                for stream_url in dict.fromkeys(m3u8_re.findall(html)):
                    channels.append(
                        CandidateChannel(
                            name=name,
                            stream_url=stream_url,
                            source_id=self.source_id,
                            source_url=url,
                            broadcaster=name,
                            language_hint=lang,
                            group_title=sport or "Sports",
                            stream_format=detect_stream_format(stream_url),
                            metadata={"broadcaster_type": bc.get("type", "FTA")},
                        )
                    )
            except Exception as e:
                last_err = str(e)
                continue

        return DiscoveryResult(
            source_id=self.source_id,
            success=had_success or not broadcasters,
            channels=channels,
            error="" if had_success else last_err,
        )

