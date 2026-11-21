"""Regression tests for Source Provenance, Pipeline Traceability & Source Performance."""

from datetime import datetime, timezone
import os
import shutil
import tempfile
import unittest

from catalogue.channel_catalogue import build_canonical_record
from daily_curator_pipeline import CuratorPipeline
from database.database import DatabaseManager
from database.models import ChannelRecord, StreamEndpointRecord, VerificationResultRecord
from discovery.base_provider import CandidateChannel, extract_repo_provenance
from discovery.github_provider import parse_m3u_to_candidates
from reporting.provenance_report import build_provenance_report, inspect_channel_provenance
from verification.stream_validator import StreamValidator


class TestProvenanceAndTraceability(unittest.TestCase):
    def test_repo_provenance_and_m3u_line_extraction(self):
        gh = extract_repo_provenance("https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8")
        self.assertEqual(gh["repo_name"], "Free-TV/IPTV")
        self.assertEqual(gh["repo_url"], "https://github.com/Free-TV/IPTV")
        self.assertEqual(gh["file_path"], "playlist.m3u8")

        gl = extract_repo_provenance("https://gitlab.com/team/sports-iptv/-/raw/main/rugby.m3u")
        self.assertEqual(gl["repo_name"], "team/sports-iptv")
        self.assertEqual(gl["file_path"], "rugby.m3u")

        # Non-repo feed returns empty strings (never fabricates)
        non_repo = extract_repo_provenance("https://iptv-org.github.io/iptv/categories/sports.m3u")
        self.assertEqual(non_repo["repo_name"], "")

        m3u = "#EXTM3U\n#EXTINF:-1,SuperSport Rugby\nhttps://cdn.test/rugby.m3u8\n"
        chs, _ = parse_m3u_to_candidates(
            m3u,
            source_id="github_code_search",
            source_url="https://raw.githubusercontent.com/user/repo/main/sports.m3u",
            discovery_query="rugby m3u8",
        )
        self.assertEqual(chs[0].repo_name, "user/repo")
        self.assertEqual(chs[0].record_ref, "line:3")
        self.assertEqual(chs[0].discovery_query, "rugby m3u8")

    def test_multiple_repos_same_channel_preserves_every_origin_and_epg_separation(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree("config", os.path.join(tmp, "config"))
            validator = StreamValidator(
                validator_fn=lambda u: VerificationResultRecord(
                    url=u, checked_at=datetime.now(timezone.utc).isoformat(),
                    is_accessible=True, requires_auth=False, has_video=True,
                    has_audio=True, playback_ok=True, stream_format="hls",
                    audio_languages=["eng"], latency_ms=180.0,
                )
            )
            pipe = CuratorPipeline(base_dir=tmp, validator=validator)

            # Repo A discovers primary endpoint; Repo B independently supplies the SAME URL + a 2nd endpoint
            c1 = CandidateChannel(
                name="SuperSport Rugby",
                stream_url="https://cdn.test/ss_rugby_1.m3u8",
                source_id="github_code_search",
                source_url="https://raw.githubusercontent.com/orgA/sports/main/live.m3u8",
                repo_name="orgA/sports",
                repo_url="https://github.com/orgA/sports",
                file_path="live.m3u8",
                record_ref="line:12",
                tvg_id="RUGBY.za",
            )
            c2_dup = CandidateChannel(
                name="SuperSport Rugby",
                stream_url="https://cdn.test/ss_rugby_1.m3u8",
                source_id="gitlab_public",
                source_url="https://gitlab.com/orgB/rugby/-/raw/main/all.m3u",
                repo_name="orgB/rugby",
                repo_url="https://gitlab.com/orgB/rugby",
                file_path="all.m3u",
                record_ref="line:5",
                tvg_id="RUGBY.za",
            )
            c3_second_ep = CandidateChannel(
                name="SuperSport Rugby",
                stream_url="https://cdn.test/ss_rugby_2.m3u8",
                source_id="github_code_search",
                source_url="https://raw.githubusercontent.com/orgA/sports/main/backup.m3u8",
                repo_name="orgA/sports",
                repo_url="https://github.com/orgA/sports",
                file_path="backup.m3u8",
                record_ref="line:4",
                tvg_id="RUGBY.za",
            )

            epg_idx = {
                "RUGBY.za": {
                    "display_names": ["SuperSport Rugby"],
                    "epg_source": "https://epgshare01.online/epgshare01/epg_ripper_ZA1.xml.gz",
                    "icon": "",
                    "is_stale": False,
                }
            }
            pipe.process_candidates([c1, c2_dup, c3_second_ep], epg_channels_index=epg_idx)

            # Verify original provenance is preserved and NOT overwritten by rediscovery
            origins = pipe.db.get_endpoint_provenance("https://cdn.test/ss_rugby_1.m3u8")
            self.assertEqual(len(origins), 2)
            self.assertTrue(origins[0]["is_original"])
            self.assertEqual(origins[0]["repo_name"], "orgA/sports")
            self.assertFalse(origins[1]["is_original"])
            self.assertEqual(origins[1]["repo_name"], "orgB/rugby")

            # Verify EPG provenance is strictly separate from stream provenance
            inspected = inspect_channel_provenance(pipe.db, "SuperSport Rugby")
            self.assertIsNotNone(inspected)
            self.assertEqual(len(inspected["stream_endpoints"]), 2)
            self.assertEqual(
                inspected["epg_provenance"]["epg_feed_url"],
                "https://epgshare01.online/epgshare01/epg_ripper_ZA1.xml.gz",
            )
            self.assertNotEqual(
                inspected["epg_provenance"]["epg_feed_url"],
                inspected["stream_endpoints"][0]["origins"][0]["playlist_url"],
            )

            # Verify canonical catalogue & multi-origin report
            cat = pipe.catalogue_mgr.rebuild_catalogue()
            prov_rep = build_provenance_report(pipe.db, cat["channels"])
            self.assertEqual(len(prov_rep["multi_origin_channels"]), 1)

    def test_rejected_candidate_retains_origin_and_missing_provenance_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree("config", os.path.join(tmp, "config"))
            validator = StreamValidator(
                validator_fn=lambda u: VerificationResultRecord(
                    url=u, checked_at=datetime.now(timezone.utc).isoformat(),
                    is_accessible=False, requires_auth=False, has_video=False,
                    has_audio=False, playback_ok=False, stream_format="hls",
                    failure_reason="http_error_404",
                )
            )
            pipe = CuratorPipeline(base_dir=tmp, validator=validator)

            # 1. Pre-triage rejected candidate retains origin & trace
            bad_movie = CandidateChannel(
                name="Cinema Movies 24/7",
                stream_url="https://cdn.test/movies.m3u8",
                source_id="github_code_search",
                source_url="https://raw.githubusercontent.com/spam/repo/main/list.m3u",
                repo_name="spam/repo",
                group_title="Movies",
            )
            # 2. Stream-verification rejected candidate retains origin & trace
            dead_rugby = CandidateChannel(
                name="SuperSport Rugby",
                stream_url="https://cdn.test/dead_rugby.m3u8",
                source_id="github_code_search",
                source_url="https://raw.githubusercontent.com/dead/repo/main/rugby.m3u",
                repo_name="dead/repo",
            )
            pipe.process_candidates([bad_movie, dead_rugby])

            t_movie = pipe.db.get_candidate_trace("https://cdn.test/movies.m3u8")
            self.assertEqual(t_movie["rejection_stage"], "pre_triage")
            self.assertEqual(t_movie["repo_name"], "spam/repo")

            t_dead = pipe.db.get_candidate_trace("https://cdn.test/dead_rugby.m3u8")
            self.assertEqual(t_dead["rejection_stage"], "stream_verification")
            self.assertEqual(t_dead["rejection_reason"], "http_error_404")

            # 3. Missing provenance is represented as 'unknown', never fabricated
            rec = build_canonical_record({
                "channel_id": "ch_no_prov",
                "name": "Mystery Sports",
                "endpoints": [{"url": "https://cdn.test/x.m3u8", "status": "active", "provenance": []}],
            })
            self.assertEqual(rec["source"]["source_id"], "unknown")
            self.assertEqual(rec["source"]["repo_name"], "unknown")

            # 4. Source stats aggregate independently by repository
            stats = pipe.db.get_source_performance_stats()
            repos = {r["name"]: r for r in stats["by_repository"]}
            self.assertIn("spam/repo", repos)
            self.assertIn("dead/repo", repos)
            self.assertEqual(repos["spam/repo"]["rejection_rate"], 1.0)
            self.assertEqual(repos["dead/repo"]["verification_success_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()

