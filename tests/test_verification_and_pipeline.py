"""Integration tests for stream verification gatekeeper, health monitor, DB & outputs."""

from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from daily_curator_pipeline import CuratorPipeline
from database.models import VerificationResultRecord
from discovery.base_provider import CandidateChannel
from verification.stream_validator import StreamValidator, validate_stream_opening


class TestVerificationAndPipeline(unittest.TestCase):
    def _setup_workspace(self, tmpdir: str) -> str:
        cfg_dst = os.path.join(tmpdir, "config")
        shutil.copytree("config", cfg_dst)
        return tmpdir

    @patch("verification.stream_validator.subprocess.run")
    @patch("verification.stream_validator.urllib.request.urlopen")
    def test_existing_gatekeeper_success_and_auth_failure(self, mock_urlopen, mock_run):
        # Mock HTTP 200
        resp_mock = MagicMock()
        resp_mock.status = 200
        resp_mock.__enter__.return_value = resp_mock
        mock_urlopen.return_value = resp_mock

        # Mock ffprobe then ffmpeg
        ffprobe_out = json.dumps({
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "r_frame_rate": "50/1"},
                {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}},
            ]
        })
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=ffprobe_out),
            MagicMock(returncode=0, stdout=""),
        ]

        res = validate_stream_opening("https://mock.test/live.m3u8")
        self.assertTrue(res.is_accessible)
        self.assertFalse(res.requires_auth)
        self.assertTrue(res.has_video)
        self.assertTrue(res.has_audio)
        self.assertTrue(res.playback_ok)
        self.assertEqual(res.resolution, "1080p (50fps)")
        self.assertEqual(res.audio_languages, ["eng"])

        # Mock HTTP 403 auth required
        resp_403 = MagicMock()
        resp_403.status = 403
        resp_403.__enter__.return_value = resp_403
        mock_urlopen.return_value = resp_403
        res_auth = validate_stream_opening("https://mock.test/paid.m3u8")
        self.assertFalse(res_auth.is_accessible)
        self.assertTrue(res_auth.requires_auth)

    def test_end_to_end_pipeline_and_health_restoration(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._setup_workspace(tmp)

            state = {"live_ok": True}

            def mock_verify(url: str) -> VerificationResultRecord:
                now = datetime.now(timezone.utc).isoformat()
                if "dead" in url or (not state["live_ok"] and "rugby" in url):
                    return VerificationResultRecord(
                        url=url, checked_at=now, is_accessible=False, requires_auth=False,
                        has_video=False, has_audio=False, playback_ok=False,
                        stream_format="hls", failure_reason="timeout",
                    )
                return VerificationResultRecord(
                    url=url, checked_at=now, is_accessible=True, requires_auth=False,
                    has_video=True, has_audio=True, playback_ok=True,
                    stream_format="hls", resolution="1080p (50fps)",
                    video_codec="h264", audio_codec="aac", audio_languages=["eng"],
                    latency_ms=220.0, fps=50,
                )

            validator = StreamValidator(validator_fn=mock_verify)
            pipe = CuratorPipeline(base_dir=ws, validator=validator)

            candidates = [
                CandidateChannel(
                    name="SuperSport Rugby",
                    stream_url="https://mock.test/rugby.m3u8",
                    source_id="iptv_org_sports",
                    tvg_id="RUGBY.za",
                    broadcaster="SuperSport",
                ),
                CandidateChannel(
                    name="Tennis Channel",
                    stream_url="https://mock.test/tennis.m3u8",
                    source_id="iptv_org_sports",
                    broadcaster="Tennis Channel",
                ),
                CandidateChannel(
                    name="Dead Channel",
                    stream_url="https://mock.test/dead.m3u8",
                    source_id="iptv_org_sports",
                ),
                CandidateChannel(
                    name="Movie Channel",
                    stream_url="https://mock.test/movies.m3u8",
                    source_id="iptv_org_sports",
                    group_title="Movies",
                ),
            ]

            proc = pipe.process_candidates(candidates)
            self.assertEqual(proc["verified"], 2)
            self.assertEqual(proc["failed"], 1)
            self.assertEqual(proc["excluded_non_sports"], 1)

            build_res = pipe.build_outputs()
            self.assertEqual(build_res["primary_channels"], 2)

            # Verify required outputs exist and derive from channel_catalogue.json
            for fname in (
                "channel_catalogue.json",
                "channel_health_report.json",
                "source_registry.json",
                "master_sports.m3u",
                "english_sports.m3u",
                "rugby.m3u",
                "tennis.m3u",
                "football.m3u",
                "cricket.m3u",
                "combat_sports.m3u",
                "sports_epg.xml",
                "daily_report.md",
                "verified_premier_sports.m3u8",
            ):
                self.assertTrue(os.path.exists(os.path.join(ws, fname)), f"Missing {fname}")

            with open(os.path.join(ws, "tennis.m3u"), "r", encoding="utf-8") as f:
                self.assertIn("Tennis Channel", f.read())

            # Simulate temporary outage -> forced health check marks inactive and removes from active playlists
            state["live_ok"] = False
            h1 = pipe.health_monitor.run_health_check(only_due=False)
            self.assertEqual(len(h1["deactivated"]), 1)
            pipe.build_outputs(health_check_stats=h1)
            with open(os.path.join(ws, "rugby.m3u"), "r", encoding="utf-8") as f:
                self.assertNotIn("SuperSport Rugby", f.read())

            # Simulate recovery -> forced health check auto-restores channel
            state["live_ok"] = True
            h2 = pipe.health_monitor.run_health_check(only_due=False)
            self.assertEqual(len(h2["restored"]), 1)
            pipe.build_outputs(health_check_stats=h2)
            with open(os.path.join(ws, "rugby.m3u"), "r", encoding="utf-8") as f:
                self.assertIn("SuperSport Rugby", f.read())


if __name__ == "__main__":
    unittest.main()

