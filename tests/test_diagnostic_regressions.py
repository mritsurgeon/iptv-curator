"""Regression tests derived from the live Springboks vs Ireland diagnostic."""

from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from classification.sports_classifier import SportsClassifier
from daily_curator_pipeline import CuratorPipeline
from database.database import DatabaseManager
from database.models import ProgrammeRecord, VerificationResultRecord
from discovery.base_provider import CandidateChannel
from discovery.epg_inverted_provider import EPGInvertedDiscovery
from discovery.public_dataset_provider import PublicDatasetProvider
from epg.epg_fetcher import is_radio_only_channel
from epg.epg_matcher import EPGMatcher
from playlist.event_playlist_builder import build_event_playlists
from verification.stream_validator import StreamValidator


class TestDiagnosticRegressions(unittest.TestCase):
    def setUp(self):
        self.sc = SportsClassifier("config/sports_catalogue.json")

    @patch("discovery.public_dataset_provider.http_get_conditional")
    def test_public_dataset_not_truncated_beyond_30(self, mock_get):
        lines = ["#EXTM3U\n"]
        for i in range(450):
            lines.append(f'#EXTINF:-1 group-title="General",Generic Channel {i}\nhttps://example.com/ch{i}.m3u8\n')
        lines.append('#EXTINF:-1 tvg-id="ITV1.HD.uk" group-title="UK",ITV1 HD\nhttps://example.com/itv1.m3u8\n')
        lines.append('#EXTINF:-1 tvg-id="Virgin.Media.One.HD.ie" group-title="IE",Virgin Media One HD\nhttps://example.com/vm1.m3u8\n')
        mock_get.return_value = {"not_modified": False, "data": "".join(lines).encode("utf-8"), "etag": "", "last_modified": "", "sha256": "abc"}

        prov = PublicDatasetProvider(
            {"source_id": "free_tv_iptv", "base_url": "https://example.com/playlist.m3u8", "discovery_method": "direct_m3u"},
            {},
        )
        res = prov.discover(max_results=0)
        self.assertTrue(res.success)
        self.assertEqual(len(res.channels), 452)
        names = {c.name for c in res.channels}
        self.assertIn("ITV1 HD", names)
        self.assertIn("Virgin Media One HD", names)

    def test_supersport_rugby_vs_grandstand_classification(self):
        rugby_res = self.sc.classify(name="SuperSport Rugby", broadcaster="SuperSport")
        rugby_sports = {s["sport"]: s["competitions"] for s in rugby_res["sports"]}
        self.assertIn("Rugby", rugby_sports)
        self.assertIn("Nations Championship", rugby_sports["Rugby"])
        self.assertIn("United Rugby Championship", rugby_sports["Rugby"])
        self.assertNotIn("Football", rugby_sports)
        self.assertNotIn("Combat Sports", rugby_sports)

        # Multi-sport flagship retains legitimate multi-sport associations
        gs_res = self.sc.classify(name="SuperSport Grandstand", broadcaster="SuperSport")
        gs_sports = {s["sport"] for s in gs_res["sports"]}
        self.assertIn("Rugby", gs_sports)
        self.assertIn("Football", gs_sports)

    def test_nations_championship_aliases_context_and_fta_broadcasters(self):
        for phrase in (
            "Ireland v South Africa: Nations Championship",
            "Autumn Nations Series: Ireland v South Africa",
            "Quilter Nations Series: Ireland v South Africa",
            "Test Rugby: South Africa vs Ireland",
        ):
            r = self.sc.classify(name=phrase)
            self.assertTrue(r["is_sports"], f"Failed to recognise: {phrase}")
            self.assertIn("Nations Championship", r["sports"][0]["competitions"])

        # Bare country names without rugby context must NOT trigger false positives
        self.assertFalse(self.sc.classify(name="South Africa News")["is_sports"])
        self.assertFalse(self.sc.classify(name="Ireland Travel Guide")["is_sports"])

        matcher = EPGMatcher({}, "config/broadcaster_catalogue.json")
        self.assertEqual(matcher.match("ITV1 HD")["tvg_id"], "ITV1.HD.uk")
        self.assertEqual(matcher.match("Virgin Media One HD")["tvg_id"], "Virgin.Media.One.HD.ie")
        self.assertEqual(matcher.match("STV HD")["tvg_id"], "STV.HD.uk")

    def test_radio_channels_excluded_from_tv_sports(self):
        self.assertTrue(is_radio_only_channel("BBC.R.Ulster.uk", ["BBC Radio Ulster"]))
        self.assertTrue(is_radio_only_channel("RTÉ.Radio.1.uk", ["RTÉ Radio 1"]))
        self.assertFalse(is_radio_only_channel("ITV1.HD.uk", ["ITV1 HD"]))
        self.assertFalse(is_radio_only_channel("RUGBY.za", ["SuperSport Rugby"]))

    def test_epg_targets_missing_channel_investigation_and_no_unverified_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseManager(os.path.join(tmp, "diag_reg.db"))
            now = datetime.now(timezone.utc)
            db.upsert_epg_channels([
                {
                    "epg_channel_id": "ITV1.HD.uk",
                    "epg_source_url": "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz",
                    "display_names": ["ITV1 HD"],
                    "sports_programme_count": 2,
                    "total_programme_count": 40,
                    "sports_duty_cycle": 0.05,
                }
            ])
            db.upsert_programmes([
                ProgrammeRecord(
                    channel_tvg_id="ITV1.HD.uk",
                    title="Ireland v South Africa: Nations Championship",
                    start_utc=(now + timedelta(minutes=20)).isoformat(),
                    stop_utc=(now + timedelta(hours=2)).isoformat(),
                    description="Live coverage from Aviva Stadium",
                    sport="Rugby",
                    competition="Nations Championship",
                    teams_or_athletes=["Ireland", "South Africa"],
                )
            ])
            db.upsert_sporting_events([
                {
                    "event_id": "ev_itv1_sa_ire",
                    "sport": "Rugby",
                    "competition": "Nations Championship",
                    "event_title": "Ireland v South Africa: Nations Championship",
                    "start_utc": (now + timedelta(minutes=20)).isoformat(),
                    "stop_utc": (now + timedelta(hours=2)).isoformat(),
                    "channel_id": "",
                    "epg_channel_id": "ITV1.HD.uk",
                    "evidence_type": "confirmed_schedule",
                }
            ])

            inv = EPGInvertedDiscovery(db)
            targets = inv.discover_targets(min_duty_cycle=0.01)
            self.assertEqual(len(targets["targets"]), 1)
            t = targets["targets"][0]
            self.assertEqual(t["tvg_id"], "ITV1.HD.uk")
            self.assertEqual(t["competition"], "Nations Championship")
            self.assertFalse(t["has_verified_stream"])
            self.assertTrue(any("ITV1 HD" in s for s in t["search_terms"]))

            # Record missing-channel investigation when no stream endpoint exists
            recorded = inv.record_missing_channel_investigations(
                discovered_candidates=[],
                providers_searched=["free_tv_iptv", "iptv_org_sports"],
            )
            self.assertEqual(recorded, 1)
            missing = db.get_missing_channel_investigations(only_pending=True)
            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0]["epg_channel_id"], "ITV1.HD.uk")
            self.assertIn("no_candidate_endpoint_found", missing[0]["rejection_reasons"])

            # EPG confirmation alone MUST NOT put an unverified channel into live_now.m3u
            ev_counts = build_event_playlists(db, channels=[], base_dir=tmp, now_dt=now)
            self.assertEqual(ev_counts["live_now.m3u"], 0)

    def test_candidate_preservation_when_verification_budget_exhausted(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree("config", os.path.join(tmp, "config"))
            validator = StreamValidator(
                validator_fn=lambda u: VerificationResultRecord(
                    url=u, checked_at=datetime.now(timezone.utc).isoformat(),
                    is_accessible=True, requires_auth=False, has_video=True,
                    has_audio=True, playback_ok=True, stream_format="hls",
                )
            )
            pipe = CuratorPipeline(base_dir=tmp, validator=validator)
            stats = pipe.process_candidates(
                [
                    CandidateChannel(name="SuperSport Rugby", stream_url="https://mock.test/r1.m3u8", source_id="iptv_org_sports"),
                    CandidateChannel(name="ITV1 HD", stream_url="https://mock.test/itv1.m3u8", source_id="free_tv_iptv"),
                ],
                time_budget_sec=0.0,
            )
            self.assertEqual(stats["tested"], 0)
            self.assertEqual(stats["deferred_by_budget"], 2)
            # Both candidates remain safely preserved in candidate_queue for the next run
            rem_hi = pipe.db.fetch_queued_candidates("high_relevance", limit=10)
            rem_unk = pipe.db.fetch_queued_candidates("unknown_candidate", limit=10)
            self.assertEqual(len(rem_hi) + len(rem_unk), 2)


if __name__ == "__main__":
    unittest.main()

