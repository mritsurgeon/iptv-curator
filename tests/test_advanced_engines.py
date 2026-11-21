"""Tests for Phase 1-4 advanced engines: state sync, FTS5, EPG-first, UCB1, triage & live_now."""

from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import tempfile
import unittest

from daily_curator_pipeline import CuratorPipeline
from database.database import DatabaseManager
from database.models import ProgrammeRecord, VerificationResultRecord
from database.state_sync import (
    compute_active_outputs_hash,
    prepare_snapshot,
    restore_snapshot,
    verify_sqlite_integrity,
)
from discovery.bandit_allocator import UCB1BanditAllocator
from discovery.base_provider import CandidateChannel
from discovery.epg_inverted_provider import EPGInvertedDiscovery
from discovery.google_dork_provider import (
    GoogleDorkProvider,
    _extract_ddg_links,
    _normalize_repo_raw_url,
    build_google_search_url,
    generate_dynamic_dork_queries,
)
from epg.xmltv_parser import parse_xmltv
from playlist.event_playlist_builder import build_event_playlists
from verification.stream_validator import StreamValidator


class TestAdvancedEngines(unittest.TestCase):
    def test_state_snapshot_compression_and_integrity_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "iptv.db")
            gz_path = os.path.join(tmp, "iptv.db.gz")
            mf_path = os.path.join(tmp, "manifest.json")
            restored_path = os.path.join(tmp, "restored.db")

            db = DatabaseManager(db_path)
            db.ensure_search_query("github_code_search", "rugby m3u8")
            self.assertTrue(verify_sqlite_integrity(db_path))

            manifest = prepare_snapshot(db_path, gz_path, mf_path, active_hash="abc123")
            self.assertEqual(manifest["schema_version"], 5)
            self.assertTrue(restore_snapshot(gz_path, mf_path, restored_path))

            db2 = DatabaseManager(restored_path)
            self.assertEqual(len(db2.get_search_queries("github_code_search")), 1)

    def test_streaming_xmltv_no_alphabetical_truncation_over_5000(self):
        now = datetime.now(timezone.utc)
        s = now.strftime("%Y%m%d%H%M%S +0000")
        e = (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S +0000")
        chunks = ['<?xml version="1.0" encoding="UTF-8"?><tv>']
        chunks.append('<channel id="AAA.News"><display-name>AAA News</display-name></channel>')
        chunks.append('<channel id="Willow.Cricket"><display-name>Willow Cricket</display-name></channel>')
        for i in range(5200):
            chunks.append(f'<programme start="{s}" stop="{e}" channel="AAA.News"><title>News Hour {i}</title></programme>')
        chunks.append(f'<programme start="{s}" stop="{e}" channel="Willow.Cricket"><title>SA20 Live Cricket</title></programme>')
        chunks.append("</tv>")
        raw = "".join(chunks).encode("utf-8")

        parsed = parse_xmltv(raw, programme_filter=lambda p: "cricket" in p["title"].lower())
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["channel_totals"]["AAA.News"], 5200)
        self.assertEqual(len(parsed["programmes"]), 1)
        self.assertEqual(parsed["programmes"][0]["channel_tvg_id"], "Willow.Cricket")

    def test_fts5_epg_inverted_discovery_and_event_playlists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseManager(os.path.join(tmp, "fts.db"))
            now = datetime.now(timezone.utc)
            db.upsert_epg_channels([
                {
                    "epg_channel_id": "ITV4.uk",
                    "epg_source_url": "https://example.com/uk.xml.gz",
                    "display_names": ["ITV4"],
                    "sports_programme_count": 4,
                    "total_programme_count": 20,
                    "sports_duty_cycle": 0.20,
                }
            ])
            db.upsert_programmes([
                ProgrammeRecord(
                    channel_tvg_id="ITV4.uk",
                    title="Six Nations Championship: England vs Wales",
                    start_utc=(now + timedelta(minutes=30)).isoformat(),
                    stop_utc=(now + timedelta(hours=2, minutes=30)).isoformat(),
                    description="Live international rugby",
                    sport="Rugby",
                    competition="Six Nations Championship",
                    teams_or_athletes=["England", "Wales"],
                )
            ])
            hits = db.search_programmes_fts("Six Nations")
            self.assertEqual(len(hits), 1)

            db.upsert_sporting_events([
                {
                    "event_id": "ev_itv4_1",
                    "sport": "Rugby",
                    "competition": "Six Nations Championship",
                    "event_title": "Six Nations Championship: England vs Wales",
                    "start_utc": (now + timedelta(minutes=30)).isoformat(),
                    "stop_utc": (now + timedelta(hours=2, minutes=30)).isoformat(),
                    "channel_id": "itv4_ch",
                    "epg_channel_id": "ITV4.uk",
                    "evidence_type": "confirmed_schedule",
                }
            ])

            inv = EPGInvertedDiscovery(db).discover_targets(min_duty_cycle=0.05)
            self.assertIn("ITV4", inv["discovered_channel_names"])
            self.assertEqual(inv["upcoming_events_48h"], 1)

            channels = [{
                "channel_id": "itv4_ch",
                "name": "ITV4",
                "stream": {"url": "https://mock.test/itv4.m3u8"},
                "epg": {"tvg_id": "ITV4.uk", "tvg_logo": ""},
                "language": {"primary": "English"},
            }]
            ev_counts = build_event_playlists(db, channels, base_dir=tmp, now_dt=now)
            self.assertEqual(ev_counts["live_now.m3u"], 1)
            self.assertEqual(ev_counts["upcoming_events.m3u"], 1)
            with open(os.path.join(tmp, "live_now.m3u"), "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("SAST", content)
                self.assertIn("England vs Wales", content)

    def test_ucb1_bandit_allocator_and_triage_preserves_unknown(self):
        bandit = UCB1BanditAllocator()
        queries = [
            {"query_id": "q1", "query_text": "good", "pulls": 5, "total_reward": 4.5, "origin": "config"},
            {"query_id": "q2", "query_text": "bad", "pulls": 5, "total_reward": 0.1, "origin": "config"},
            {"query_id": "q3", "query_text": "new_surge", "pulls": 0, "total_reward": 0.0, "origin": "event_surge"},
        ]
        selected = bandit.select_queries(queries, budget=2)
        self.assertEqual(selected[0]["query_id"], "q3")
        self.assertEqual(selected[1]["query_id"], "q1")

        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree("config", os.path.join(tmp, "config"))
            tested_urls = []

            def spy_verify(url: str) -> VerificationResultRecord:
                tested_urls.append(url)
                return VerificationResultRecord(
                    url=url, checked_at=datetime.now(timezone.utc).isoformat(),
                    is_accessible=True, requires_auth=False, has_video=True,
                    has_audio=True, playback_ok=True, stream_format="hls",
                    audio_languages=["eng"], latency_ms=150.0,
                )

            pipe = CuratorPipeline(base_dir=tmp, validator=StreamValidator(validator_fn=spy_verify))
            triage = pipe.triage_and_enqueue([
                CandidateChannel(name="Cinema Movies 24/7", stream_url="https://mock.test/movie.m3u8", source_id="iptv_org_sports", group_title="Movies"),
                CandidateChannel(name="Channel 9 FTA", stream_url="https://mock.test/ch9.m3u8", source_id="iptv_org_sports"),
                CandidateChannel(name="SuperSport Rugby", stream_url="https://mock.test/rugby.m3u8", source_id="iptv_org_sports"),
            ])
            self.assertEqual(triage["excluded_pre_triage"], 1)
            self.assertEqual(triage["unknown_candidate"], 1)
            self.assertEqual(triage["high_relevance"], 1)

            # Verify movie URL never reaches ffmpeg gatekeeper
            pipe.process_candidates([], time_budget_sec=30.0)
            self.assertNotIn("https://mock.test/movie.m3u8", tested_urls)
            self.assertIn("https://mock.test/rugby.m3u8", tested_urls)
            self.assertIn("https://mock.test/ch9.m3u8", tested_urls)

    def test_keyless_google_dork_provider_and_stage_scheduler(self):
        with open("config/sports_catalogue.json", "r", encoding="utf-8") as f:
            sc = json.load(f)
        with open("config/broadcaster_catalogue.json", "r", encoding="utf-8") as f:
            bc = json.load(f).get("broadcasters", {})

        dorks = generate_dynamic_dork_queries(sc, bc, max_queries=25)
        self.assertGreaterEqual(len(dorks), 15)
        self.assertTrue(any("Australian Open" in d["query"] or "SA20" in d["query"] for d in dorks))
        self.assertIn("https://www.google.com/search?q=", build_google_search_url(dorks[0]["query"]))

        ddg_html = '<a href="/l/?uddg=https%3A%2F%2Fgithub.com%2Fuser%2Frepo%2Fblob%2Fmain%2Fsports.m3u">link</a>'
        links = _extract_ddg_links(ddg_html)
        self.assertEqual(
            _normalize_repo_raw_url(links[0]),
            "https://raw.githubusercontent.com/user/repo/main/sports.m3u",
        )

        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseManager(os.path.join(tmp, "sched.db"))
            self.assertTrue(db.is_stage_due("epg", 8.0))
            db.record_stage_run("epg", 8.0, "ok", 1.2, {"active": 1})
            self.assertFalse(db.is_stage_due("epg", 8.0))
            self.assertTrue(db.is_stage_due("epg", 8.0, now_dt=datetime.now(timezone.utc) + timedelta(hours=9)))

            maint = db.run_maintenance(retain_history_days=60)
            self.assertEqual(maint["integrity"], "ok")


if __name__ == "__main__":
    unittest.main()
