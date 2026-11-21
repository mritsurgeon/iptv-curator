"""Tests for discovery providers, URL normalization, format detection & source registry."""

import os
import tempfile
import unittest
from unittest.mock import patch

from database.database import DatabaseManager
from discovery.base_provider import detect_stream_format, normalize_stream_url
from discovery.discovery_manager import DiscoveryManager
from discovery.github_provider import parse_m3u_to_candidates


SAMPLE_M3U = """#EXTM3U url-tvg="https://example.com/epg.xml.gz"
#EXTINF:-1 tvg-id="RUGBY.za" tvg-name="SuperSport Rugby" group-title="Rugby",SuperSport Rugby
https://example.com/live/rugby.m3u8/
#EXTINF:-1 tvg-id="TENNIS.1" group-title="Tennis",Tennis Channel HD
http://example.com/tennis/stream.ts
"""


class TestDiscoveryAndRegistry(unittest.TestCase):
    def test_url_normalization_and_format_detection(self):
        self.assertEqual(
            normalize_stream_url("  HTTPS://Example.COM/live/stream.m3u8/  "),
            "https://example.com/live/stream.m3u8",
        )
        self.assertEqual(detect_stream_format("https://cdn.test/live/ch1.m3u8?token=abc"), "hls")
        self.assertEqual(detect_stream_format("http://cdn.test/ch1.ts"), "mpegts")
        self.assertEqual(detect_stream_format("https://cdn.test/manifest.mpd"), "dash")

    def test_parse_m3u_to_candidates(self):
        chs, epgs = parse_m3u_to_candidates(SAMPLE_M3U, "github_code_search", "https://example.com/list.m3u")
        self.assertEqual(len(chs), 2)
        self.assertEqual(len(epgs), 1)
        self.assertEqual(epgs[0].fmt, "xml.gz")
        self.assertEqual(chs[0].tvg_id, "RUGBY.za")
        self.assertEqual(chs[1].stream_format, "mpegts")

    def test_source_registry_and_reliability_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseManager(os.path.join(tmp, "test.db"))
            mgr = DiscoveryManager(
                db,
                source_registry_path="config/source_registry.json",
                queries_path="config/discovery_queries.json",
                sports_catalogue_path="config/sports_catalogue.json",
            )
            sources = db.get_all_sources()
            self.assertGreaterEqual(len(sources), 8)
            initial = next(s["reliability_score"] for s in sources if s["source_id"] == "gitlab_public")

            after_fail = db.record_source_scan("gitlab_public", success=False)
            self.assertLess(after_fail, initial)

            after_good = db.record_source_scan(
                "gitlab_public", success=True, channels_found=10, channels_working=10, channels_failed=0
            )
            self.assertGreater(after_good, after_fail)


if __name__ == "__main__":
    unittest.main()

