"""Tests for language detection, multi-sport classification, competition mapping & scoring."""

import unittest

from catalogue.channel_catalogue import select_primary_channels
from classification.channel_scorer import ChannelScorer
from classification.competition_mapper import CompetitionMapper
from classification.language_detector import LanguageDetector
from classification.sports_classifier import SportsClassifier


class TestClassificationAndScoring(unittest.TestCase):
    def setUp(self):
        self.lang = LanguageDetector()
        self.sports = SportsClassifier("config/sports_catalogue.json")
        self.mapper = CompetitionMapper("config/sports_catalogue.json")
        self.scorer = ChannelScorer("config/scoring_weights.json")

    def test_language_classification_variants(self):
        r_en = self.lang.detect("SuperSport Rugby", audio_languages=["eng"])
        self.assertEqual(r_en["primary"], "English")

        r_multi = self.lang.detect("Eurosport", audio_languages=["eng", "fra"])
        self.assertEqual(r_multi["primary"], "English + Other")
        self.assertIn("French", r_multi["secondary"])

        r_other = self.lang.detect("ESPN Deportes (ES)", audio_languages=["spa"])
        self.assertEqual(r_other["primary"], "Other Language")

        r_unk = self.lang.detect("Live Feed 101", audio_languages=[])
        self.assertEqual(r_unk["primary"], "Unknown")

    def test_multi_sport_and_tennis_classification(self):
        res_tennis = self.sports.classify("Tennis Channel Wimbledon ATP Tour")
        self.assertTrue(res_tennis["is_sports"])
        sports_names = [s["sport"] for s in res_tennis["sports"]]
        self.assertIn("Tennis", sports_names)
        comps = res_tennis["sports"][0]["competitions"]
        self.assertIn("Wimbledon", comps)
        self.assertIn("ATP Tour", comps)

        res_multi = self.sports.classify(
            "SuperSport Action",
            epg_programmes=[
                {"title": "UFC 310 Live", "description": "MMA", "competition": "UFC"},
                {"title": "SA20 Cricket Highlights", "description": "T20", "competition": "SA20"},
            ],
        )
        multi_names = {s["sport"] for s in res_multi["sports"]}
        self.assertIn("Combat Sports", multi_names)
        self.assertIn("Cricket", multi_names)

        res_non = self.sports.classify("Cinema Movies 24/7", group_title="Movies")
        self.assertFalse(res_non["is_sports"])

    def test_competition_hierarchy_grouping(self):
        grp = self.mapper.get_playlist_group("Rugby", ["United Rugby Championship"])
        self.assertEqual(grp, "Rugby - Club and provincial competitions")

    def test_channel_scoring_tiers(self):
        prem = self.scorer.score(
            sports_relevance=1.0,
            primary_language="English",
            playback_ok=True,
            success_count=10,
            failure_count=0,
            latency_ms=250.0,
            has_tvg_id=True,
            has_logo=True,
            has_broadcaster=True,
            has_resolution=True,
            epg_matched=True,
            epg_confidence=0.95,
            source_reliability=0.95,
        )
        self.assertGreaterEqual(prem["total"], 90.0)
        self.assertEqual(prem["tier"], "premium")
        self.assertTrue(prem["include_in_primary"])

        low = self.scorer.score(
            sports_relevance=0.4,
            primary_language="Other Language",
            playback_ok=True,
            success_count=1,
            failure_count=2,
            latency_ms=2500.0,
            epg_matched=False,
            source_reliability=0.3,
        )
        self.assertLess(low["total"], 60.0)
        self.assertEqual(low["tier"], "review")
        self.assertFalse(low["include_in_primary"])

    def test_scarcity_aware_scoring_floor(self):
        def _rec(cid, sport, score, sconf=0.85, playback="working", access="free"):
            return {
                "channel_id": cid,
                "name": cid,
                "sports": [{"sport": sport, "competitions": [], "confidence": sconf}],
                "stream": {"url": f"https://cdn.test/{cid}.m3u8"},
                "verification": {
                    "playback_status": playback,
                    "access_status": access,
                    "score": score,
                    "confidences": {"sports_confidence": sconf},
                },
            }

        records = [
            # Football already has 3 primary channels (>= 60) -> borderline Football must NOT promote
            _rec("fb1", "Football", 92.0),
            _rec("fb2", "Football", 88.0),
            _rec("fb3", "Football", 80.0),
            _rec("fb_low", "Football", 55.0),
            # Cricket only has 1 primary channel -> borderline Cricket (52.0) SHOULD promote,
            # while too-low Cricket (40.0) and low-confidence Cricket (0.4) must NOT promote
            _rec("cr1", "Cricket", 90.0),
            _rec("cr_borderline", "Cricket", 52.0, sconf=0.8),
            _rec("cr_too_low", "Cricket", 40.0, sconf=0.9),
            _rec("cr_low_conf", "Cricket", 55.0, sconf=0.4),
            _rec("cr_dead", "Cricket", 55.0, sconf=0.9, playback="inactive"),
        ]
        primary, review = select_primary_channels(
            records,
            min_primary_score=60.0,
            scarce_sport_min=45.0,
            scarce_sport_min_channels=3,
            scarce_sport_min_confidence=0.6,
            require_stream_url=True,
        )
        primary_ids = {r["channel_id"] for r in primary}
        review_ids = {r["channel_id"] for r in review}

        self.assertIn("cr_borderline", primary_ids)
        self.assertNotIn("fb_low", primary_ids)
        self.assertNotIn("cr_too_low", primary_ids)
        self.assertNotIn("cr_low_conf", primary_ids)
        self.assertNotIn("cr_dead", primary_ids)
        self.assertIn("fb_low", review_ids)
        self.assertIn("cr_too_low", review_ids)
        promoted = next(r for r in primary if r["channel_id"] == "cr_borderline")
        self.assertTrue(promoted["verification"].get("scarcity_promoted"))


if __name__ == "__main__":
    unittest.main()

