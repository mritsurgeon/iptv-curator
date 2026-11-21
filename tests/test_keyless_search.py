"""Regression tests for Keyless Search Engine Discovery (GoogleDorkProvider)."""

import base64
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from database.database import DatabaseManager
from discovery.google_dork_provider import (
    GoogleDorkProvider,
    _extract_bing_links,
    generate_dynamic_dork_queries,
)


class TestKeylessSearchDiscovery(unittest.TestCase):
    def setUp(self):
        with open("config/discovery_queries.json", encoding="utf-8") as f:
            self.qcfg = json.load(f)
        with open("config/sports_catalogue.json", encoding="utf-8") as f:
            self.scat = json.load(f)
        with open("config/broadcaster_catalogue.json", encoding="utf-8") as f:
            self.bcat = json.load(f).get("broadcasters", {})

    def test_includes_general_channel_discovery_and_index_of_and_catalogues(self):
        qs = generate_dynamic_dork_queries(
            self.scat, self.bcat, queries_config=self.qcfg, max_queries=60
        )
        texts = [q["query"] for q in qs]
        # 1. Includes general_channel_discovery from config/discovery_queries.json
        for gq in self.qcfg["general_channel_discovery"]:
            self.assertIn(gq, texts)
        # 2. Includes intitle:"index of" M3U/M3U8 searches
        self.assertTrue(any('intitle:"index of"' in t.lower() for t in texts))
        # 3. Generated from configured competitions and broadcasters
        self.assertTrue(any("Australian Open" in t or "Wimbledon" in t for t in texts))
        self.assertTrue(any("SuperSport" in t for t in texts))
        # 4. Deduplicated queries
        self.assertEqual(len(texts), len({t.lower() for t in texts}))

    def test_bing_link_extraction_and_deduplication(self):
        b64 = base64.urlsafe_b64encode(b"https://github.com/org/iptv/blob/main/sports.m3u").decode().rstrip("=")
        html = (
            f'<a href="https://www.bing.com/ck/a?u=a1{b64}">r1</a>'
            f'<a href="https://www.bing.com/ck/a?u=a1{b64}">dup</a>'
            '<a href="https://gitlab.com/org/list/-/blob/main/live.m3u8">r2</a>'
        )
        links = _extract_bing_links(html)
        self.assertEqual(len(links), 2)
        self.assertIn("https://github.com/org/iptv/blob/main/sports.m3u", links)
        self.assertIn("https://gitlab.com/org/list/-/blob/main/live.m3u8", links)

    @patch.dict(os.environ, {}, clear=False)
    @patch("discovery.google_dork_provider.http_get_text")
    def test_duckduckgo_keyless_without_searxng_and_circuit_breaker_fallback(self, mock_get):
        os.environ.pop("SEARXNG_URL", None)
        sample_m3u = "#EXTM3U\n#EXTINF:-1,SuperSport Rugby\nhttps://cdn.test/rugby.m3u8\n"

        # Case A: DuckDuckGo returns results without API keys or SearXNG
        def ddg_ok(url, **kwargs):
            if "duckduckgo.com" in url:
                return (
                    '<a href="/l/?uddg=https%3A%2F%2Fgithub.com%2Fu%2Fr%2Fblob%2Fmain%2Fsports.m3u">1</a>'
                    '<a href="/l/?uddg=https%3A%2F%2Fgithub.com%2Fu%2Fr%2Fblob%2Fmain%2Fsports.m3u">dup</a>'
                )
            if "raw.githubusercontent.com" in url:
                return sample_m3u
            return ""

        mock_get.side_effect = ddg_ok
        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseManager(os.path.join(tmp, "k.db"))
            prov = GoogleDorkProvider(
                {"source_id": "google_dork_discovery"},
                self.qcfg,
                db=db,
                sports_catalogue=self.scat,
                broadcaster_catalogue=self.bcat,
                dorks_export_path=os.path.join(tmp, "dorks.txt"),
            )
            res = prov.discover(max_results=2)
            self.assertTrue(res.success)
            self.assertEqual(len(res.channels), 1)
            self.assertEqual(res.channels[0].name, "SuperSport Rugby")
            self.assertEqual(res.channels[0].repo_name, "u/r")

        # Case B: DuckDuckGo returns CAPTCHA -> circuit breaker trips and falls back to Bing HTML
        ddg_calls = {"count": 0}
        b64 = base64.urlsafe_b64encode(b"https://github.com/u2/r2/blob/main/rugby.m3u8").decode().rstrip("=")

        def ddg_captcha_bing_ok(url, **kwargs):
            if "duckduckgo.com" in url:
                ddg_calls["count"] += 1
                return "<html>Please complete CAPTCHA anomaly.js</html>"
            if "bing.com" in url:
                return f'<a href="https://www.bing.com/ck/a?u=a1{b64}">ok</a>'
            if "raw.githubusercontent.com" in url:
                return sample_m3u
            return ""

        mock_get.side_effect = ddg_captcha_bing_ok
        with tempfile.TemporaryDirectory() as tmp:
            prov2 = GoogleDorkProvider(
                {"source_id": "google_dork_discovery"},
                self.qcfg,
                sports_catalogue=self.scat,
                broadcaster_catalogue=self.bcat,
                dorks_export_path=os.path.join(tmp, "dorks2.txt"),
            )
            res2 = prov2.discover(max_results=3)
            self.assertTrue(res2.success)
            # Circuit breaker ensures DuckDuckGo was NOT repeatedly retried after 1st CAPTCHA!
            self.assertEqual(ddg_calls["count"], 1)
            self.assertIn("duckduckgo_html", prov2._blocked_backends)
            self.assertEqual(len(res2.channels), 1)
            self.assertEqual(res2.channels[0].repo_name, "u2/r2")


if __name__ == "__main__":
    unittest.main()

