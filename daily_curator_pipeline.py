#!/usr/bin/env python3
"""
IPTV Curator 2.0 — Autonomous Sports IPTV Discovery, Verification & Curation Engine
Three-engine architecture:
  1. EPG-First Engine (conditional streaming XMLTV -> SQLite FTS5 + Sports Duty Cycle)
  2. Event-First Engine (48h confirmed sporting_events -> Matchday Surge priority & queries)
  3. Playlist-First Engine (UCB1 Bandit multi-source discovery -> 3-tier pre-gatekeeper triage)
Mandatory Gatekeeper:
  - Existing HTTP -> ffprobe -> 3s ffmpeg decode test (validate_stream_opening) with time budget
Canonical Outputs:
  - channel_catalogue.json -> master_sports.m3u, english_sports.m3u, sport *.m3u,
    live_now.m3u, upcoming_events.m3u, verified_premier_sports.m3u8, sports_epg.xml, reports
"""

import argparse
from datetime import datetime, timezone
import glob
import json
import os
import subprocess
import time
import urllib.request
from typing import Any, Dict, List, Optional

from catalogue.catalogue_manager import CatalogueManager
from catalogue.channel_deduplicator import ChannelDeduplicator
from classification.channel_enrichment import enrich_channel_metadata
from classification.channel_scorer import ChannelScorer
from classification.language_detector import LanguageDetector
from classification.sports_classifier import NON_SPORTS_EXCLUSION_RE, SportsClassifier
from database.database import DatabaseManager
from database.models import ChannelRecord, StreamEndpointRecord, VerificationResultRecord
from discovery.base_provider import CandidateChannel, detect_stream_format, normalize_stream_url
from discovery.discovery_manager import DiscoveryManager
from epg.epg_fetcher import EPGFetcher
from epg.epg_matcher import EPGMatcher
from epg.event_mapper import EventMapper
from playlist.epg_builder import build_sports_epg
from playlist.event_playlist_builder import build_event_playlists
from playlist.language_builder import build_english_playlist
from playlist.master_builder import build_master_playlist
from playlist.sport_builder import build_sport_playlists
from playlist_parser import parse_m3u
from reporting.daily_report import generate_daily_report
from reporting.health_report import generate_health_report
from reporting.provenance_report import build_provenance_report, inspect_channel_provenance
from reporting.source_report import export_source_registry_snapshot, generate_source_report
from verification.health_monitor import HealthMonitor
from verification.stream_validator import StreamValidator, validate_stream_opening

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GIST_ID = "d6f9d772966e4ead4aac90331c6a6d9c"


def load_settings(base_dir: str = BASE_DIR) -> Dict[str, Any]:
    path = os.path.join(base_dir, "config", "settings.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_github_token() -> Optional[str]:
    token = os.environ.get("GIST_PAT") or os.environ.get("GIST_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        try:
            token = subprocess.check_output(["gh", "auth", "token"], stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            token = None
    return token


def test_stream_health(ch: Dict[str, str]) -> Optional[VerificationResultRecord]:
    """Backward-compatible wrapper around the unified stream-opening gatekeeper."""
    res = validate_stream_opening(ch["url"])
    return res if (res.is_accessible and not res.requires_auth and res.playback_ok) else None


class CuratorPipeline:
    """Time-budgeted, three-engine autonomous curation pipeline backed by SQLite + FTS5."""

    def __init__(
        self,
        base_dir: str = BASE_DIR,
        validator: Optional[StreamValidator] = None,
    ):
        self.base_dir = base_dir
        self.settings = load_settings(base_dir)
        ver_cfg = self.settings.get("verification", {})
        disc_cfg = self.settings.get("discovery", {})

        db_path = os.path.join(base_dir, self.settings.get("database_path", "data/iptv_curator.db"))
        self.db = DatabaseManager(db_path)

        self.sports_cat_path = os.path.join(base_dir, "config", "sports_catalogue.json")
        self.broadcaster_cat_path = os.path.join(base_dir, "config", "broadcaster_catalogue.json")
        self.source_reg_path = os.path.join(base_dir, "config", "source_registry.json")
        self.queries_path = os.path.join(base_dir, "config", "discovery_queries.json")
        self.weights_path = os.path.join(base_dir, "config", "scoring_weights.json")

        self.discovery_mgr = DiscoveryManager(
            self.db,
            source_registry_path=self.source_reg_path,
            queries_path=self.queries_path,
            sports_catalogue_path=self.sports_cat_path,
        )
        self.validator = validator or StreamValidator(
            max_workers=int(ver_cfg.get("max_workers", 25)),
            http_timeout=float(ver_cfg.get("http_timeout_sec", 4.0)),
            ffprobe_timeout=float(ver_cfg.get("ffprobe_timeout_sec", 5.0)),
            ffmpeg_decode_sec=int(ver_cfg.get("ffmpeg_decode_sec", 3)),
            ffmpeg_timeout=float(ver_cfg.get("ffmpeg_timeout_sec", 6.0)),
        )
        self.health_monitor = HealthMonitor(self.db, self.validator)
        self.lang_detector = LanguageDetector()
        self.sports_classifier = SportsClassifier(self.sports_cat_path)
        self.scorer = ChannelScorer(self.weights_path)
        self.epg_fetcher = EPGFetcher(self.db, stale_hours=int(ver_cfg.get("epg_stale_hours", 48)))
        self.catalogue_mgr = CatalogueManager(
            self.db,
            catalogue_path=os.path.join(base_dir, self.settings.get("catalogue_path", "channel_catalogue.json")),
        )
        self.max_results_per_provider = int(disc_cfg.get("max_results_per_provider", 30))
        self.dataset_retrieval_limit = int(disc_cfg.get("dataset_retrieval_limit", 0))
        self.candidate_ingestion_limit = int(disc_cfg.get("candidate_ingestion_limit", 5000))
        self.verify_time_budget_sec = float(ver_cfg.get("verify_time_budget_sec", 720.0))
        self.health_time_budget_sec = float(ver_cfg.get("health_time_budget_sec", 480.0))

    def seed_local_playlists(self) -> List[CandidateChannel]:
        """Load any existing local .m3u/.m3u8 files not yet in the database."""
        known = self.db.get_known_endpoint_urls()
        local_files = [
            os.path.join(self.base_dir, "verified_premier_sports.m3u8"),
            os.path.join(self.base_dir, "master_sports.m3u"),
        ] + glob.glob(os.path.join(self.base_dir, "verified_*.m3u8"))

        seeded: List[CandidateChannel] = []
        seen = set()
        for lf in local_files:
            if not os.path.exists(lf):
                continue
            try:
                with open(lf, "r", encoding="utf-8", errors="ignore") as f:
                    for ch in parse_m3u(f.read()):
                        u = normalize_stream_url(ch.stream_url)
                        if u and u not in known and u not in seen:
                            seen.add(u)
                            seeded.append(
                                CandidateChannel(
                                    name=ch.name,
                                    stream_url=u,
                                    source_id="local_seed",
                                    source_url=lf,
                                    tvg_id=ch.tvg_id,
                                    tvg_name=ch.tvg_name,
                                    tvg_logo=ch.tvg_logo,
                                    group_title=ch.group_title,
                                    stream_format=detect_stream_format(u),
                                )
                            )
            except Exception:
                continue
        return seeded

    def triage_and_enqueue(
        self,
        candidates: List[CandidateChannel],
        epg_channels_index: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, int]:
        """
        Zero-cost 3-tier pre-gatekeeper triage:
          - high_relevance: matches sports catalogue, broadcaster, or EPG sports duty cycle
          - unknown_candidate: ambiguous (e.g. FTA channel like ITV4/SABC 1) -> preserved in queue!
          - explicit_non_sports: matches negative exclusion regex with zero EPG sports -> excluded before ffmpeg
        """
        epg_matcher = EPGMatcher(epg_channels_index or {}, self.broadcaster_cat_path)
        sources_map = {s["source_id"]: s for s in self.db.get_all_sources()}
        upcoming_epg_ids = {
            ev.get("epg_channel_id")
            for ev in self.db.get_upcoming_sporting_events(hours_ahead=48)
            if ev.get("epg_channel_id")
        }

        to_queue: List[Dict[str, Any]] = []
        counts = {"high_relevance": 0, "unknown_candidate": 0, "excluded_pre_triage": 0}

        for cand in candidates:
            meta = enrich_channel_metadata(
                {
                    "name": cand.name,
                    "tvg_name": cand.tvg_name,
                    "broadcaster": cand.broadcaster,
                    "aliases": [],
                }
            )
            epg_m = epg_matcher.match(meta["name"], tvg_id=cand.tvg_id, aliases=meta["aliases"], broadcaster=meta["broadcaster"])
            epg_ch = self.db.get_epg_channel(epg_m["tvg_id"]) if epg_m["tvg_id"] else None
            duty_cycle = float(epg_ch["sports_duty_cycle"]) if epg_ch else 0.0
            progs = self.db.get_programmes_for_channel(epg_m["tvg_id"]) if epg_m["tvg_id"] else []

            pre_sports = self.sports_classifier.classify(
                name=meta["name"],
                extinf=cand.extinf,
                broadcaster=meta["broadcaster"],
                group_title=cand.group_title,
                epg_programmes=progs,
            )

            has_neg = bool(NON_SPORTS_EXCLUSION_RE.search(f"{meta['name']} {cand.group_title}"))
            if has_neg and not pre_sports["is_sports"] and duty_cycle <= 0.0:
                self.db.add_exclusion(cand.stream_url, "pre_triage_non_sports")
                self.db.record_endpoint_provenance(
                    endpoint_url=cand.stream_url,
                    provider_id=cand.source_id,
                    source_id=cand.source_id,
                    repo_name=cand.repo_name,
                    repo_url=cand.repo_url,
                    file_path=cand.file_path,
                    playlist_url=cand.source_url,
                    record_ref=cand.record_ref,
                    discovery_query=cand.discovery_query,
                )
                self.db.record_candidate_trace(
                    url=cand.stream_url,
                    stage="relevance_filtered",
                    status="rejected",
                    provider_id=cand.source_id,
                    repo_name=cand.repo_name,
                    playlist_url=cand.source_url,
                    rejection_stage="pre_triage",
                    rejection_reason="pre_triage_non_sports",
                )
                counts["excluded_pre_triage"] += 1
                continue

            self.db.record_endpoint_provenance(
                endpoint_url=cand.stream_url,
                provider_id=cand.source_id,
                source_id=cand.source_id,
                repo_name=cand.repo_name,
                repo_url=cand.repo_url,
                file_path=cand.file_path,
                playlist_url=cand.source_url,
                record_ref=cand.record_ref,
                discovery_query=cand.discovery_query,
            )

            src_rel = float(sources_map.get(cand.source_id, {}).get("reliability_score", 0.75))
            event_boost = 1.0 if (epg_m["tvg_id"] and epg_m["tvg_id"] in upcoming_epg_ids) else 0.0
            epg_hit = 1.0 if epg_m["status"] == "matched" else 0.0

            if pre_sports["is_sports"] or duty_cycle >= 0.05:
                triage_status = "high_relevance"
                s_triage = max(pre_sports["relevance_score"], duty_cycle)
                counts["high_relevance"] += 1
            else:
                triage_status = "unknown_candidate"
                s_triage = 0.25
                counts["unknown_candidate"] += 1

            priority = round(0.35 * src_rel + 0.30 * s_triage + 0.25 * event_boost + 0.10 * epg_hit, 4)
            to_queue.append(
                {
                    "url": cand.stream_url,
                    "name": cand.name,
                    "provider_id": cand.source_id,
                    "tvg_id": cand.tvg_id,
                    "tvg_name": cand.tvg_name,
                    "tvg_logo": cand.tvg_logo,
                    "group_title": cand.group_title,
                    "broadcaster": cand.broadcaster,
                    "extinf": cand.extinf,
                    "triage_status": triage_status,
                    "priority_score": priority,
                }
            )

        self.db.enqueue_candidates(to_queue)
        return counts

    def process_candidates(
        self,
        candidates: List[CandidateChannel],
        epg_channels_index: Optional[Dict[str, Any]] = None,
        time_budget_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Optimised pipeline stage:
          1. Run zero-cost 3-tier triage & enqueue into SQLite candidate_queue.
          2. Pop highest-priority high_relevance items (85% of budget) + unknown_candidate items (15% of budget).
          3. Run mandatory stream-opening gatekeeper within time_budget_sec; unfinished items stay queued!
        """
        budget = time_budget_sec if time_budget_sec is not None else self.verify_time_budget_sec
        triage_stats = self.triage_and_enqueue(candidates, epg_channels_index=epg_channels_index)

        high_q = self.db.fetch_queued_candidates("high_relevance", limit=400)
        unk_q = self.db.fetch_queued_candidates("unknown_candidate", limit=100)

        # Interleave: prioritize high_relevance while reserving 15% of budget for unknown exploration
        ordered_queue: List[Dict[str, Any]] = []
        hi_idx, unk_idx = 0, 0
        while hi_idx < len(high_q) or unk_idx < len(unk_q):
            for _ in range(6):
                if hi_idx < len(high_q):
                    ordered_queue.append(high_q[hi_idx])
                    hi_idx += 1
            if unk_idx < len(unk_q):
                ordered_queue.append(unk_q[unk_idx])
                unk_idx += 1

        dedup = ChannelDeduplicator(self.db.get_all_channels())
        epg_matcher = EPGMatcher(epg_channels_index or {}, self.broadcaster_cat_path)
        sources_map = {s["source_id"]: s for s in self.db.get_all_sources()}
        now = datetime.now(timezone.utc).isoformat()

        stats: Dict[str, Any] = {
            "triage": triage_stats,
            "tested": 0,
            "verified": 0,
            "failed": 0,
            "excluded_non_sports": triage_stats["excluded_pre_triage"],
            "deferred_by_budget": 0,
        }

        t_start = time.monotonic()
        batch_size = max(1, min(self.validator.max_workers, 25))

        for i in range(0, len(ordered_queue), batch_size):
            if (time.monotonic() - t_start) >= budget:
                stats["deferred_by_budget"] = len(ordered_queue) - i
                break

            chunk = ordered_queue[i : i + batch_size]
            urls = [item["url"] for item in chunk]
            ver_results = self.validator.verify_batch(urls)
            processed_urls: List[str] = []

            for item in chunk:
                url = item["url"]
                vr = ver_results.get(url)
                if not vr:
                    continue
                processed_urls.append(url)
                stats["tested"] += 1

                meta = enrich_channel_metadata(
                    {
                        "name": item["name"],
                        "tvg_name": item.get("tvg_name", ""),
                        "broadcaster": item.get("broadcaster", ""),
                        "aliases": [],
                    }
                )
                cid = dedup.resolve_channel_id(meta["name"], item.get("tvg_id", ""), meta["aliases"])

                passed = vr.is_accessible and not vr.requires_auth and vr.playback_ok
                if not passed:
                    stats["failed"] += 1
                    self.db.record_candidate_trace(
                        url=url,
                        stage="stream_verification",
                        status="rejected",
                        channel_id=cid,
                        provider_id=item.get("provider_id", ""),
                        rejection_stage="stream_verification",
                        rejection_reason=vr.failure_reason or "verification_failed",
                    )
                    self.db.upsert_channel(
                        ChannelRecord(
                            channel_id=cid,
                            name=meta["name"],
                            aliases=meta["aliases"],
                            broadcaster=meta["broadcaster"],
                            status="inactive",
                            first_discovered=now,
                            last_updated=now,
                        )
                    )
                    self.db.upsert_endpoint(
                        StreamEndpointRecord(
                            url=url,
                            channel_id=cid,
                            source_id=item.get("provider_id", ""),
                            stream_format=vr.stream_format,
                        )
                    )
                    self.db.record_verification(cid, vr)
                    continue

                epg_match = epg_matcher.match(
                    meta["name"],
                    tvg_id=item.get("tvg_id", ""),
                    aliases=meta["aliases"],
                    broadcaster=meta["broadcaster"],
                )
                epg_ch = self.db.get_epg_channel(epg_match["tvg_id"]) if epg_match["tvg_id"] else None
                duty_cycle = float(epg_ch["sports_duty_cycle"]) if epg_ch else 0.0
                progs = self.db.get_programmes_for_channel(epg_match["tvg_id"]) if epg_match["tvg_id"] else []

                lang_res = self.lang_detector.detect(
                    name=meta["name"],
                    extinf=item.get("extinf", ""),
                    audio_languages=vr.audio_languages,
                    broadcaster=meta["broadcaster"],
                    epg_descriptions=[p.get("description", "") for p in progs[:5]],
                )
                sports_res = self.sports_classifier.classify(
                    name=meta["name"],
                    extinf=item.get("extinf", ""),
                    broadcaster=meta["broadcaster"],
                    group_title=item.get("group_title", ""),
                    epg_programmes=progs,
                )

                if not sports_res["is_sports"] and duty_cycle < 0.05:
                    stats["excluded_non_sports"] += 1
                    self.db.add_exclusion(url, "non_sports_content")
                    self.db.record_candidate_trace(
                        url=url,
                        stage="classified",
                        status="rejected",
                        channel_id=cid,
                        provider_id=item.get("provider_id", ""),
                        rejection_stage="sports_classification",
                        rejection_reason="non_sports_content",
                    )
                    continue

                effective_sports_rel = max(sports_res["relevance_score"], min(1.0, duty_cycle * 1.2))
                channel_role = "dedicated_sports" if (duty_cycle >= 0.5 or sports_res["relevance_score"] >= 0.75) else "occasional_sports_fta"

                for sugg in sports_res.get("suggested_competitions", []):
                    self.db.record_competition_suggestion(sugg, sample_channel=meta["name"])

                src_rel = float(sources_map.get(item.get("provider_id", ""), {}).get("reliability_score", 0.75))
                score_res = self.scorer.score(
                    sports_relevance=effective_sports_rel,
                    primary_language=lang_res["primary"],
                    playback_ok=True,
                    success_count=1,
                    failure_count=0,
                    latency_ms=vr.latency_ms,
                    has_tvg_id=bool(epg_match["tvg_id"]),
                    has_logo=bool(epg_match["tvg_logo"] or item.get("tvg_logo")),
                    has_broadcaster=bool(meta["broadcaster"]),
                    has_resolution=bool(vr.resolution),
                    epg_matched=epg_match["status"] == "matched",
                    epg_confidence=epg_match["confidence"],
                    source_reliability=src_rel,
                )

                self.db.upsert_channel(
                    ChannelRecord(
                        channel_id=cid,
                        name=meta["name"],
                        aliases=meta["aliases"],
                        broadcaster=meta["broadcaster"],
                        primary_language=lang_res["primary"],
                        secondary_languages=lang_res["secondary"],
                        sports=sports_res["sports"],
                        tvg_id=epg_match["tvg_id"] or item.get("tvg_id", ""),
                        tvg_logo=epg_match["tvg_logo"] or item.get("tvg_logo", ""),
                        epg_source=epg_match["epg_source"],
                        epg_status=epg_match["status"],
                        epg_confidence=epg_match["confidence"],
                        score=score_res["total"],
                        score_breakdown=score_res["breakdown"],
                        tier=score_res["tier"],
                        status="active",
                        first_discovered=now,
                        last_updated=now,
                    ),
                    confidences={
                        "language_confidence": lang_res["confidence"],
                        "sports_confidence": effective_sports_rel,
                        "sports_duty_cycle": duty_cycle,
                        "competition_confidence": 0.95 if progs else (0.75 if sports_res["sports"] else 0.0),
                        "endpoint_reliability": 0.625,
                        "source_reliability": src_rel,
                        "channel_role": channel_role,
                    },
                )
                self.db.upsert_endpoint(
                    StreamEndpointRecord(
                        url=url,
                        channel_id=cid,
                        source_id=item.get("provider_id", ""),
                        stream_format=vr.stream_format,
                    )
                )
                self.db.record_verification(cid, vr)
                self.db.record_candidate_trace(
                    url=url,
                    stage="catalogue_inclusion",
                    status="passed",
                    channel_id=cid,
                    provider_id=item.get("provider_id", ""),
                )
                stats["verified"] += 1

            self.db.remove_queued_candidates(processed_urls)

        return stats

    def reclassify_all(self, epg_channels_index: Optional[Dict[str, Any]] = None) -> int:
        """Re-score and update classifications/EPG mappings for all stored channels."""
        epg_matcher = EPGMatcher(epg_channels_index or {}, self.broadcaster_cat_path)
        sources_map = {s["source_id"]: s for s in self.db.get_all_sources()}
        channels = self.db.get_all_channels()
        updated = 0

        for ch in channels:
            epg_match = epg_matcher.match(
                ch["name"],
                tvg_id=ch.get("tvg_id", ""),
                aliases=ch.get("aliases", []),
                broadcaster=ch.get("broadcaster", ""),
            )
            epg_ch = self.db.get_epg_channel(epg_match["tvg_id"]) if epg_match["tvg_id"] else None
            duty_cycle = float(epg_ch["sports_duty_cycle"]) if epg_ch else float(ch.get("sports_duty_cycle", 0.0))
            progs = self.db.get_programmes_for_channel(epg_match["tvg_id"]) if epg_match["tvg_id"] else []
            eps = ch.get("endpoints", [])
            primary_ep = eps[0] if eps else {}
            audio_langs = primary_ep.get("audio_languages", [])

            lang_res = self.lang_detector.detect(
                name=ch["name"],
                audio_languages=audio_langs,
                broadcaster=ch.get("broadcaster", ""),
                epg_descriptions=[p.get("description", "") for p in progs[:5]],
            )
            sports_res = self.sports_classifier.classify(
                name=ch["name"],
                broadcaster=ch.get("broadcaster", ""),
                epg_programmes=progs,
            )
            if not sports_res["is_sports"] and duty_cycle < 0.05:
                continue

            src_rel = float(sources_map.get(primary_ep.get("source_id", ""), {}).get("reliability_score", 0.75))
            effective_sports_rel = max(sports_res["relevance_score"], min(1.0, duty_cycle * 1.2))
            score_res = self.scorer.score(
                sports_relevance=effective_sports_rel,
                primary_language=lang_res["primary"],
                playback_ok=primary_ep.get("status") in ("active", "degraded"),
                success_count=primary_ep.get("success_count", 0),
                failure_count=primary_ep.get("failure_count", 0),
                latency_ms=primary_ep.get("latency_ms", 500.0),
                has_tvg_id=bool(epg_match["tvg_id"]),
                has_logo=bool(epg_match["tvg_logo"] or ch.get("tvg_logo")),
                has_broadcaster=bool(ch.get("broadcaster")),
                has_resolution=bool(primary_ep.get("resolution")),
                epg_matched=epg_match["status"] == "matched",
                epg_confidence=epg_match["confidence"],
                source_reliability=src_rel,
            )

            self.db.upsert_channel(
                ChannelRecord(
                    channel_id=ch["channel_id"],
                    name=ch["name"],
                    aliases=ch.get("aliases", []),
                    broadcaster=ch.get("broadcaster", ""),
                    country=ch.get("country", ""),
                    primary_language=lang_res["primary"],
                    secondary_languages=lang_res["secondary"],
                    sports=sports_res["sports"],
                    tvg_id=epg_match["tvg_id"] or ch.get("tvg_id", ""),
                    tvg_logo=epg_match["tvg_logo"] or ch.get("tvg_logo", ""),
                    epg_source=epg_match["epg_source"] or ch.get("epg_source", ""),
                    epg_status=epg_match["status"],
                    epg_confidence=epg_match["confidence"],
                    score=score_res["total"],
                    score_breakdown=score_res["breakdown"],
                    tier=score_res["tier"],
                    status=ch.get("status", "active"),
                    first_discovered=ch.get("first_discovered", ""),
                    last_updated=datetime.now(timezone.utc).isoformat(),
                ),
                confidences={
                    "language_confidence": lang_res["confidence"],
                    "sports_confidence": effective_sports_rel,
                    "sports_duty_cycle": duty_cycle,
                    "competition_confidence": 0.95 if progs else 0.70,
                    "endpoint_reliability": float(primary_ep.get("ewma_availability", 0.5)),
                    "source_reliability": src_rel,
                    "channel_role": "dedicated_sports" if duty_cycle >= 0.5 else "occasional_sports_fta",
                },
            )
            updated += 1
        return updated

    def build_outputs(
        self,
        discovery_stats: Optional[Dict[str, Any]] = None,
        epg_stats: Optional[Dict[str, Any]] = None,
        health_check_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Rebuild canonical channel_catalogue.json, all playlists (incl. live_now & upcoming), EPG & reports."""
        min_score = float(self.scorer.thresholds.get("acceptable_min", 60))
        scarce_min = float(self.scorer.thresholds.get("scarce_sport_min", 45))
        scarce_min_ch = int(self.scorer.thresholds.get("scarce_sport_min_channels", 3))
        scarce_min_conf = float(self.scorer.thresholds.get("scarce_sport_min_confidence", 0.6))
        prev_health_path = os.path.join(self.base_dir, self.settings.get("health_report_path", "channel_health_report.json"))
        prev_summary = {}
        if os.path.exists(prev_health_path):
            try:
                with open(prev_health_path, "r", encoding="utf-8") as f:
                    prev_data = json.load(f)
                    prev_summary = {"working": prev_data.get("by_verification_status", {}).get("working", 0)}
            except Exception:
                pass

        catalogue = self.catalogue_mgr.rebuild_catalogue(
            min_primary_score=min_score,
            scarce_sport_min=scarce_min,
            scarce_sport_min_channels=scarce_min_ch,
            scarce_sport_min_confidence=scarce_min_conf,
        )
        primary_channels = self.catalogue_mgr.get_primary_channels(
            min_score=min_score,
            scarce_sport_min=scarce_min,
            scarce_sport_min_channels=scarce_min_ch,
            scarce_sport_min_confidence=scarce_min_conf,
        )
        all_channels = catalogue.get("channels", [])

        display_tz = self.settings.get("display_timezone", "Africa/Johannesburg")
        event_mapper = EventMapper(self.db, display_tz=display_tz)
        events_index = event_mapper.build_event_index(self.db.get_all_channels(status="active"))
        with open(os.path.join(self.base_dir, "sports_calendar.json"), "w", encoding="utf-8") as f:
            json.dump(events_index, f, indent=2)

        master_counts = build_master_playlist(
            primary_channels, base_dir=self.base_dir, sports_catalogue_path=self.sports_cat_path
        )
        sport_counts = build_sport_playlists(
            primary_channels, base_dir=self.base_dir, sports_catalogue_path=self.sports_cat_path
        )
        english_count = build_english_playlist(
            primary_channels, base_dir=self.base_dir, sports_catalogue_path=self.sports_cat_path
        )
        event_pl_counts = build_event_playlists(
            self.db, primary_channels, base_dir=self.base_dir, display_tz=display_tz
        )
        epg_out = build_sports_epg(
            self.db,
            primary_channels,
            output_path=os.path.join(self.base_dir, self.settings.get("epg_output_path", "sports_epg.xml")),
        )

        health_rep = generate_health_report(all_channels, output_path=prev_health_path)
        export_source_registry_snapshot(
            self.db,
            output_path=os.path.join(self.base_dir, self.settings.get("source_registry_output_path", "source_registry.json")),
        )
        src_rep = generate_source_report(self.db)
        prov_rep = build_provenance_report(self.db, all_channels)
        suggestions = self.db.get_competition_suggestions()
        generate_daily_report(
            catalogue_channels=all_channels,
            health_report=health_rep,
            source_report=src_rep,
            discovery_stats=discovery_stats,
            epg_stats=epg_stats,
            health_check_stats=health_check_stats,
            new_suggestions=suggestions,
            prev_summary=prev_summary,
            provenance_report=prov_rep,
            output_path=os.path.join(self.base_dir, self.settings.get("daily_report_path", "daily_report.md")),
        )

        return {
            "primary_channels": len(primary_channels),
            "master_counts": master_counts,
            "sport_counts": sport_counts,
            "english_count": english_count,
            "event_playlists": event_pl_counts,
            "epg": epg_out,
        }

    def sync_cloud_and_firetv(self, token: Optional[str]) -> None:
        if token:
            try:
                files_to_sync = [
                    ("sports.m3u", "master_sports.m3u"),
                    ("english_sports.m3u", "english_sports.m3u"),
                    ("live_now.m3u", "live_now.m3u"),
                    ("sports_epg.xml", "sports_epg.xml"),
                    ("channel_catalogue.json", "channel_catalogue.json"),
                ]
                payload_files = {}
                for gist_name, local_name in files_to_sync:
                    p = os.path.join(self.base_dir, local_name)
                    if os.path.exists(p):
                        with open(p, "r", encoding="utf-8") as f:
                            payload_files[gist_name] = {"content": f.read()}
                if payload_files:
                    req = urllib.request.Request(
                        f"https://api.github.com/gists/{GIST_ID}",
                        data=json.dumps({"files": payload_files}).encode("utf-8"),
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/vnd.github+json",
                            "Content-Type": "application/json",
                            "User-Agent": "IPTV-Curator-2.0",
                        },
                        method="PATCH",
                    )
                    with urllib.request.urlopen(req, timeout=15):
                        print("☁️ Synced playlists, catalogue & EPG to Cloud Gist.")
            except Exception as e:
                print(f"⚠️ Gist sync notice: {e}")

        try:
            res = subprocess.run(["adb", "get-state"], capture_output=True, text=True, timeout=2)
            if "device" in res.stdout:
                for fname in ("verified_premier_sports.m3u8", "master_sports.m3u", "live_now.m3u", "sports_epg.xml"):
                    lp = os.path.join(self.base_dir, fname)
                    if os.path.exists(lp):
                        subprocess.run(["adb", "push", lp, f"/sdcard/Download/{fname}"], capture_output=True)
                print("📱 Pushed updated playlists & EPG to Fire TV via ADB.")
        except Exception:
            pass

    STAGE_INTERVALS_HOURS = {
        "health": 3.5,               # Runs every 4-hour orchestration tick
        "epg": 7.5,                  # Every ~8 hours (every 2nd tick)
        "discover_incremental": 11.5,# Twice daily (~12h)
        "discover_deep": 23.5,       # Daily deep keyless search & exploration
        "reconcile": 23.5,           # Daily full reconciliation
        "maintenance": 167.0,        # Weekly DB integrity, prune & VACUUM
    }

    def run_event_targeted(self, events_csv: str, sport_hint: str = "") -> Dict[str, Any]:
        """
        Fast, targeted matchday pipeline for specific user-requested events
        (e.g. 'Springboks vs Ireland, England vs New Zealand').
        Still enforces the full mandatory validate_stream_opening() gatekeeper!
        """
        import re
        from playlist.master_builder import write_m3u

        stop_words = {"new", "the", "and", "live", "cup", "open", "tour", "test", "match", "sports", "sport"}
        event_list = [e.strip() for e in events_csv.split(",") if e.strip()]
        terms = set()
        for ev in event_list:
            terms.add(ev.lower())
            for tok in re.split(r"\b(?:vs?\.?|and|&)\b|[\s:,-]+", ev.lower()):
                if len(tok) >= 4 and tok not in stop_words:
                    terms.add(tok)
        if sport_hint:
            terms.add(sport_hint.lower())

        print(f"🎯 [Event Mode] Targeting: {event_list} (terms: {sorted(terms)})", flush=True)

        # 1. Fast regional EPG sync (UK1, IE1, ZA1)
        epg_sources = [
            {"url": s["base_url"], "source_name": s["name"], "fmt": "xml.gz"}
            for s in self.db.get_all_sources()
            if s.get("source_id") in ("epgshare01_uk1", "epgshare01_ie1", "epgshare01_za1") and s.get("enabled")
        ]
        epg_res = self.epg_fetcher.fetch_and_store(epg_sources, self.discovery_mgr.sports_catalogue)
        epg_index = epg_res["channels_index"]

        # Find EPG channels airing any of the requested events
        matched_epg_tvg_ids = set()
        matched_channel_names = set()
        for t in terms:
            if len(t) < 4:
                continue
            for p in self.db.search_programmes_fts(f'"{t}"', limit=50):
                blob = f"{p.get('title', '')} {p.get('description', '')}".lower()
                if any(ev_tok in blob for ev_tok in terms if len(ev_tok) >= 4):
                    cid = p["channel_tvg_id"]
                    matched_epg_tvg_ids.add(cid)
                    for dn in epg_index.get(cid, {}).get("display_names", []):
                        matched_channel_names.add(dn.lower())

        # 2. Fast discovery across public datasets & GitHub
        candidates, _, disc_stats = self.discovery_mgr.run_discovery(
            max_results_per_provider=15,
            dataset_retrieval_limit=0,
            candidate_ingestion_limit=20000,
            incremental=False,
            mode="incremental",
        )

        # Prioritize exact EPG-matched broadcasters & rugby channels first
        priority_broadcasters = ("rugby", "supersport", "itv1", "itv4", "stv", "utv", "virgin media", "tnt sports", "rugbypass", "nations", "s4c")
        target_keywords = terms | matched_channel_names | set(priority_broadcasters)
        targeted = [
            c for c in candidates
            if (c.tvg_id and c.tvg_id in matched_epg_tvg_ids)
            or any(k in f"{c.name} {c.group_title} {c.tvg_id}".lower() for k in target_keywords if len(k) >= 4)
        ]
        targeted.sort(
            key=lambda c: (
                0 if (c.tvg_id and c.tvg_id in matched_epg_tvg_ids) else 1,
                0 if any(pb in c.name.lower() for pb in priority_broadcasters) else 1,
            )
        )
        print(f"🎯 [Event Mode] Narrowed {len(candidates)} -> {len(targeted)} targeted candidates. Running full gatekeeper...", flush=True)

        # 3. Full mandatory stream-opening gatekeeper on top 250 targeted candidates
        proc = self.process_candidates(targeted[:250], epg_channels_index=epg_index, time_budget_sec=240.0)
        build_res = self.build_outputs(discovery_stats=disc_stats, epg_stats=epg_res["stats"])

        # 4. Write dedicated event_matchday.m3u from verified primary channels matching targets
        primary = self.catalogue_mgr.get_primary_channels(
            min_score=float(self.scorer.thresholds.get("acceptable_min", 60)),
            scarce_sport_min=float(self.scorer.thresholds.get("scarce_sport_min", 45)),
            scarce_sport_min_channels=int(self.scorer.thresholds.get("scarce_sport_min_channels", 3)),
            scarce_sport_min_confidence=float(self.scorer.thresholds.get("scarce_sport_min_confidence", 0.6)),
        )
        event_channels = [
            ch for ch in primary
            if (ch.get("epg", {}).get("tvg_id") in matched_epg_tvg_ids)
            or any(s.get("sport", "").lower() == (sport_hint.lower() or "rugby") for s in ch.get("sports", []))
        ]
        ev_path = os.path.join(self.base_dir, "event_matchday.m3u")
        ev_cnt = write_m3u(
            ev_path,
            event_channels,
            lambda ch: f"MATCHDAY - {sport_hint or 'Sports'}",
            playlist_title=f"Matchday: {', '.join(event_list)}",
        )
        return {
            "events": event_list,
            "epg_matched_channels": sorted(matched_channel_names)[:20],
            "targeted_candidates": len(targeted),
            "processing": proc,
            "event_matchday.m3u": ev_cnt,
            "build": build_res,
        }

    def run(self, stage: str = "auto") -> Dict[str, Any]:
        discovery_stats: Dict[str, Any] = {}
        epg_stats: Dict[str, Any] = {}
        health_stats: Dict[str, Any] = {}
        maintenance_stats: Dict[str, Any] = {}
        epg_index: Dict[str, Any] = {}
        executed_stages: List[str] = []

        def should_run(name: str) -> bool:
            if stage == "all":
                return True
            if stage == name or (stage == "discover" and name == "discover_incremental"):
                return True
            if stage == "classify" and name == "reconcile":
                return True
            if stage == "auto":
                return self.db.is_stage_due(name, self.STAGE_INTERVALS_HOURS[name])
            return False

        if should_run("epg"):
            print("📺 [Stage: epg] Fetching regional XMLTV feeds & building FTS5 index...", flush=True)
            t0 = time.monotonic()
            active_tvg_ids = {
                ch["tvg_id"] for ch in self.db.get_all_channels(status="active") if ch.get("tvg_id")
            }
            epg_sources = [
                {"url": s["base_url"], "source_name": s["name"], "fmt": "xml.gz"}
                for s in self.db.get_all_sources()
                if s.get("source_type") == "epg_source" and s.get("enabled")
                and s["base_url"].endswith((".xml", ".gz"))
            ]
            epg_res = self.epg_fetcher.fetch_and_store(
                epg_sources,
                self.discovery_mgr.sports_catalogue,
                active_tvg_ids=active_tvg_ids,
            )
            epg_index = epg_res["channels_index"]
            epg_stats = epg_res["stats"]
            self.db.record_stage_run("epg", self.STAGE_INTERVALS_HOURS["epg"], "ok", time.monotonic() - t0, epg_stats)
            executed_stages.append("epg")

        if should_run("discover_deep") or should_run("discover_incremental"):
            is_deep = should_run("discover_deep")
            stage_key = "discover_deep" if is_deep else "discover_incremental"
            print(f"🔎 [Stage: {stage_key}] Running multi-source discovery & 3-tier triage...", flush=True)
            t0 = time.monotonic()
            seeded = self.seed_local_playlists()
            candidates, discovered_epgs, discovery_stats = self.discovery_mgr.run_discovery(
                max_results_per_provider=self.max_results_per_provider,
                dataset_retrieval_limit=self.dataset_retrieval_limit,
                candidate_ingestion_limit=self.candidate_ingestion_limit,
                incremental=True,
                mode="deep" if is_deep else "incremental",
            )
            all_candidates = seeded + candidates
            if discovered_epgs and not epg_index:
                epg_res = self.epg_fetcher.fetch_and_store(
                    [{"url": e.url, "source_name": e.source_name, "fmt": e.fmt} for e in discovered_epgs[:10]],
                    self.discovery_mgr.sports_catalogue,
                )
                epg_index.update(epg_res["channels_index"])
            proc_stats = self.process_candidates(all_candidates, epg_channels_index=epg_index)
            discovery_stats["processing"] = proc_stats
            elapsed = time.monotonic() - t0
            self.db.record_stage_run(stage_key, self.STAGE_INTERVALS_HOURS[stage_key], "ok", elapsed, proc_stats)
            if is_deep:
                self.db.record_stage_run("discover_incremental", self.STAGE_INTERVALS_HOURS["discover_incremental"], "ok", elapsed, proc_stats)
            executed_stages.append(stage_key)

        if should_run("health"):
            print("🩺 [Stage: health] Running adaptive stream health checks...", flush=True)
            t0 = time.monotonic()
            upcoming = self.db.get_upcoming_sporting_events(hours_ahead=6)
            prio_ids = [ev["channel_id"] for ev in upcoming if ev.get("channel_id")]
            health_stats = self.health_monitor.run_health_check(
                only_due=True,
                time_budget_sec=self.health_time_budget_sec,
                priority_channel_ids=prio_ids,
            )
            self.db.record_stage_run("health", self.STAGE_INTERVALS_HOURS["health"], "ok", time.monotonic() - t0, {"checked": health_stats["checked"]})
            executed_stages.append("health")

        if should_run("reconcile"):
            t0 = time.monotonic()
            reclassified = self.reclassify_all(epg_channels_index=epg_index)
            self.db.record_stage_run("reconcile", self.STAGE_INTERVALS_HOURS["reconcile"], "ok", time.monotonic() - t0, {"reclassified": reclassified})
            executed_stages.append("reconcile")

        if should_run("maintenance"):
            t0 = time.monotonic()
            maintenance_stats = self.db.run_maintenance(retain_history_days=60)
            self.db.record_stage_run("maintenance", self.STAGE_INTERVALS_HOURS["maintenance"], "ok", time.monotonic() - t0, maintenance_stats)
            executed_stages.append("maintenance")

        build_res = self.build_outputs(
            discovery_stats=discovery_stats,
            epg_stats=epg_stats,
            health_check_stats=health_stats,
        )

        if stage in ("all", "auto"):
            self.sync_cloud_and_firetv(get_github_token())

        return {
            "stage": stage,
            "executed_stages": executed_stages,
            "discovery": discovery_stats,
            "epg": epg_stats,
            "health": health_stats,
            "maintenance": maintenance_stats,
            "build": build_res,
        }


def main():
    parser = argparse.ArgumentParser(description="IPTV Curator 2.0 Autonomous Sports Pipeline")
    parser.add_argument(
        "--stage",
        choices=[
            "auto", "all", "health", "epg",
            "discover", "discover_incremental", "discover_deep",
            "classify", "reconcile", "maintenance", "build",
        ],
        default="auto",
        help="Pipeline stage to run (default: auto, executes only due stages from SQLite stage_runs)",
    )
    parser.add_argument(
        "--inspect-channel",
        type=str,
        default="",
        help="Inspect stream & EPG provenance for an individual channel (ID or name) and exit",
    )
    parser.add_argument(
        "--event",
        type=str,
        default="",
        help="Fast targeted matchday run for comma-separated events (e.g. 'Springboks vs Ireland, England vs New Zealand')",
    )
    parser.add_argument(
        "--sport",
        type=str,
        default="Rugby",
        help="Sport hint used with --event mode (default: Rugby)",
    )
    args = parser.parse_args()

    pipeline = CuratorPipeline()
    if args.inspect_channel:
        info = inspect_channel_provenance(pipeline.db, args.inspect_channel)
        print(json.dumps(info or {"error": "channel_not_found", "query": args.inspect_channel}, indent=2))
        return

    if args.event:
        summary = pipeline.run_event_targeted(args.event, sport_hint=args.sport)
        print(json.dumps(summary, indent=2))
        return

    print(f"🚀 Running IPTV Curator 2.0 Pipeline (stage={args.stage})...")
    summary = pipeline.run(stage=args.stage)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
