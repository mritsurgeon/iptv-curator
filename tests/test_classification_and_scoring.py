"""Tests for language detection, multi-sport classification, competition mapping & scoring."""

import unittest

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


if __name__ == "__main__":
    unittest.main()

