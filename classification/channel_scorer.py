"""Transparent configurable channel scoring engine (0-100)."""

import json
import os
from typing import Any, Dict


class ChannelScorer:
    """
    Scores verified, freely accessible channels out of 100:
      - Sports relevance (default 30)
      - English language confidence (default 20)
      - Playback stability (default 20)
      - Metadata quality (default 10)
      - EPG availability (default 10)
      - Source reliability (default 10)
    Geographic/country attributes are never used in scoring.
    """

    def __init__(self, weights_path: str = "config/scoring_weights.json"):
        self.config = self._load_config(weights_path)
        self.weights = self.config.get("weights", {})
        self.thresholds = self.config.get("thresholds", {})
        self.lang_scores = self.config.get("language_scores", {})

    @staticmethod
    def _load_config(path: str) -> Dict[str, Any]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "weights": {
                "sports_relevance": 30,
                "english_language_confidence": 20,
                "playback_stability": 20,
                "metadata_quality": 10,
                "epg_availability": 10,
                "source_reliability": 10,
            },
            "thresholds": {
                "premium_min": 90,
                "high_quality_min": 75,
                "acceptable_min": 60,
                "exclude_below": 60,
            },
            "language_scores": {
                "English": 1.0,
                "English + Other": 0.75,
                "Unknown": 0.35,
                "Other Language": 0.1,
            },
        }

    def score(
        self,
        sports_relevance: float,
        primary_language: str,
        playback_ok: bool,
        success_count: int = 1,
        failure_count: int = 0,
        latency_ms: float = 500.0,
        has_tvg_id: bool = False,
        has_logo: bool = False,
        has_broadcaster: bool = False,
        has_resolution: bool = False,
        epg_matched: bool = False,
        epg_confidence: float = 0.0,
        source_reliability: float = 0.75,
    ) -> Dict[str, Any]:
        w = self.weights

        # 1. Sports relevance
        s_sports = round(max(0.0, min(1.0, sports_relevance)) * w.get("sports_relevance", 30), 2)

        # 2. English language confidence
        lang_factor = self.lang_scores.get(primary_language, 0.35)
        s_lang = round(lang_factor * w.get("english_language_confidence", 20), 2)

        # 3. Playback stability
        total_checks = success_count + failure_count
        uptime = (success_count / total_checks) if total_checks > 0 else (1.0 if playback_ok else 0.0)
        latency_factor = 1.0 if latency_ms <= 800 else (0.85 if latency_ms <= 2000 else 0.7)
        stab_factor = (uptime * latency_factor) if playback_ok else 0.0
        s_stab = round(stab_factor * w.get("playback_stability", 20), 2)

        # 4. Metadata quality
        meta_parts = sum([bool(has_tvg_id), bool(has_logo), bool(has_broadcaster), bool(has_resolution)])
        s_meta = round((meta_parts / 4.0) * w.get("metadata_quality", 10), 2)

        # 5. EPG availability
        epg_factor = max(0.2, min(1.0, epg_confidence)) if epg_matched else 0.0
        s_epg = round(epg_factor * w.get("epg_availability", 10), 2)

        # 6. Source reliability
        s_src = round(max(0.0, min(1.0, source_reliability)) * w.get("source_reliability", 10), 2)

        total = round(s_sports + s_lang + s_stab + s_meta + s_epg + s_src, 2)

        if total >= self.thresholds.get("premium_min", 90):
            tier = "premium"
        elif total >= self.thresholds.get("high_quality_min", 75):
            tier = "high_quality"
        elif total >= self.thresholds.get("acceptable_min", 60):
            tier = "acceptable"
        else:
            tier = "review"

        return {
            "total": total,
            "tier": tier,
            "include_in_primary": total >= self.thresholds.get("exclude_below", 60),
            "breakdown": {
                "sports_relevance": s_sports,
                "english_language_confidence": s_lang,
                "playback_stability": s_stab,
                "metadata_quality": s_meta,
                "epg_availability": s_epg,
                "source_reliability": s_src,
            },
        }

