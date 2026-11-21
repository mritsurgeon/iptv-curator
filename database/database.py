"""SQLite database manager for IPTV Curator 2.0 (Schema v2 + FTS5)."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import sqlite3
from typing import Any, Dict, Iterator, List, Optional

from database.migrations import run_migrations
from database.models import (
    ChannelRecord,
    EPGSourceRecord,
    ProgrammeRecord,
    SourceRecord,
    StreamEndpointRecord,
    VerificationResultRecord,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_fingerprint(vcodec: str, resolution: str, audio_languages: List[str]) -> str:
    res_bucket = "hd" if ("1080" in resolution or "2160" in resolution or "720" in resolution) else "sd"
    langs = ",".join(sorted(set(audio_languages or [])))
    return f"{vcodec or 'unk'}|{res_bucket}|{langs}"


class DatabaseManager:
    """Thread-safe SQLite persistence manager with FTS5, conditional HTTP cache & adaptive backoff."""

    def __init__(self, db_path: str = "data/iptv_curator.db"):
        self.db_path = db_path
        db_dir = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(db_dir, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            run_migrations(conn)

    # ------------------------------------------------------------------
    # Sources & Feed-Level Reliability + Conditional HTTP Cache
    # ------------------------------------------------------------------
    def upsert_source(self, src: SourceRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sources (
                    source_id, name, source_type, base_url, discovery_method,
                    supported_formats, last_successful_scan, last_attempted_scan,
                    reliability_score, failure_count, channels_discovered, enabled, country
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    name=excluded.name,
                    source_type=excluded.source_type,
                    base_url=excluded.base_url,
                    discovery_method=excluded.discovery_method,
                    supported_formats=excluded.supported_formats,
                    last_successful_scan=COALESCE(excluded.last_successful_scan, sources.last_successful_scan),
                    last_attempted_scan=COALESCE(excluded.last_attempted_scan, sources.last_attempted_scan),
                    reliability_score=excluded.reliability_score,
                    failure_count=excluded.failure_count,
                    channels_discovered=excluded.channels_discovered,
                    enabled=excluded.enabled,
                    country=COALESCE(excluded.country, sources.country)
                """,
                (
                    src.source_id,
                    src.name,
                    src.source_type,
                    src.base_url,
                    src.discovery_method,
                    json.dumps(src.supported_formats),
                    src.last_successful_scan,
                    src.last_attempted_scan,
                    src.reliability_score,
                    src.failure_count,
                    src.channels_discovered,
                    1 if src.enabled else 0,
                    src.country,
                ),
            )

    def record_source_scan(
        self,
        source_id: str,
        success: bool,
        channels_found: int = 0,
        channels_working: int = 0,
        channels_failed: int = 0,
    ) -> float:
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT reliability_score, failure_count, channels_discovered FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if not row:
                return 0.5
            old_score = float(row["reliability_score"])
            fail_count = int(row["failure_count"])
            total_disc = int(row["channels_discovered"])

            if not success:
                fail_count += 1
                new_score = max(0.05, round(old_score * 0.85, 4))
            else:
                fail_count = 0
                if channels_working + channels_failed > 0:
                    batch_rate = channels_working / (channels_working + channels_failed)
                    new_score = round(0.7 * old_score + 0.3 * batch_rate, 4)
                else:
                    new_score = min(1.0, round(0.9 * old_score + 0.1, 4))
                total_disc += channels_found

            conn.execute(
                """
                UPDATE sources SET
                    last_attempted_scan = ?,
                    last_successful_scan = CASE WHEN ? THEN ? ELSE last_successful_scan END,
                    reliability_score = ?,
                    failure_count = ?,
                    channels_discovered = ?
                WHERE source_id = ?
                """,
                (now, 1 if success else 0, now, new_score, fail_count, total_disc, source_id),
            )
            conn.execute(
                """
                INSERT INTO source_reliability (
                    source_id, recorded_at, channels_found, channels_working, channels_failed, reliability_score
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source_id, now, channels_found, channels_working, channels_failed, new_score),
            )
            return new_score

    def get_all_sources(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY reliability_score DESC").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["supported_formats"] = json.loads(d.get("supported_formats") or "[]")
                d["enabled"] = bool(d["enabled"])
                result.append(d)
            return result

    def get_feed_cache(self, feed_url: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM feed_sources WHERE feed_url = ?", (feed_url,)
            ).fetchone()
            return dict(row) if row else {}

    def upsert_feed_source(
        self,
        provider_id: str,
        feed_url: str,
        feed_type: str = "m3u",
        etag: str = "",
        last_modified: str = "",
        content_sha256: str = "",
        channels_found: int = 0,
        channels_working: int = 0,
        success: bool = True,
    ) -> str:
        feed_id = hashlib.sha1(feed_url.encode("utf-8")).hexdigest()[:16]
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT reliability_score, failure_count FROM feed_sources WHERE feed_id = ?",
                (feed_id,),
            ).fetchone()
            old_rel = float(row["reliability_score"]) if row else 0.75
            fails = int(row["failure_count"]) if row else 0
            if not success:
                fails += 1
                new_rel = max(0.05, round(old_rel * 0.85, 4))
            else:
                fails = 0
                if channels_found > 0:
                    ratio = min(1.0, channels_working / max(1, channels_found))
                    new_rel = round(0.7 * old_rel + 0.3 * ratio, 4) if channels_working > 0 else old_rel
                else:
                    new_rel = old_rel

            conn.execute(
                """
                INSERT INTO feed_sources (
                    feed_id, provider_id, feed_url, feed_type, etag, last_modified,
                    content_sha256, reliability_score, channels_found, channels_working,
                    failure_count, last_fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feed_id) DO UPDATE SET
                    etag=CASE WHEN excluded.etag != '' THEN excluded.etag ELSE feed_sources.etag END,
                    last_modified=CASE WHEN excluded.last_modified != '' THEN excluded.last_modified ELSE feed_sources.last_modified END,
                    content_sha256=CASE WHEN excluded.content_sha256 != '' THEN excluded.content_sha256 ELSE feed_sources.content_sha256 END,
                    reliability_score=excluded.reliability_score,
                    channels_found=feed_sources.channels_found + excluded.channels_found,
                    channels_working=feed_sources.channels_working + excluded.channels_working,
                    failure_count=excluded.failure_count,
                    last_fetched_at=excluded.last_fetched_at
                """,
                (
                    feed_id, provider_id, feed_url, feed_type, etag, last_modified,
                    content_sha256, new_rel, channels_found, channels_working, fails, now,
                ),
            )
            return feed_id

    # ------------------------------------------------------------------
    # Search Queries & UCB1 Bandit Stats
    # ------------------------------------------------------------------
    def ensure_search_query(
        self, provider_id: str, query_text: str, origin: str = "config", sport_hint: str = ""
    ) -> str:
        qid = hashlib.sha1(f"{provider_id}:{query_text}".encode("utf-8")).hexdigest()[:16]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO search_queries (
                    query_id, provider_id, query_text, origin, sport_hint
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (qid, provider_id, query_text, origin, sport_hint),
            )
        return qid

    def get_search_queries(self, provider_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if provider_id:
                rows = conn.execute(
                    "SELECT * FROM search_queries WHERE enabled = 1 AND provider_id = ?",
                    (provider_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM search_queries WHERE enabled = 1").fetchall()
            return [dict(r) for r in rows]

    def record_query_reward(
        self,
        query_id: str,
        reward: float,
        results_discovered: int = 0,
        new_source_urls: int = 0,
        new_playlists: int = 0,
        new_epg_feeds: int = 0,
        new_candidate_channels: int = 0,
        verified_channels: int = 0,
        execution_sec: float = 0.0,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE search_queries
                SET pulls = pulls + 1,
                    total_reward = total_reward + ?,
                    results_discovered = results_discovered + ?,
                    new_source_urls = new_source_urls + ?,
                    new_playlists = new_playlists + ?,
                    new_epg_feeds = new_epg_feeds + ?,
                    new_candidate_channels = new_candidate_channels + ?,
                    verified_channels = verified_channels + ?,
                    total_execution_sec = total_execution_sec + ?,
                    last_used_at = ?
                WHERE query_id = ?
                """,
                (
                    max(0.0, float(reward)),
                    int(results_discovered),
                    int(new_source_urls),
                    int(new_playlists),
                    int(new_epg_feeds),
                    int(new_candidate_channels),
                    int(verified_channels),
                    float(execution_sec),
                    utc_now_iso(),
                    query_id,
                ),
            )

    # ------------------------------------------------------------------
    # Orchestration Stage Scheduling & Weekly DB Maintenance
    # ------------------------------------------------------------------
    def is_stage_due(self, stage_name: str, interval_hours: float, now_dt: Optional[datetime] = None) -> bool:
        now = now_dt or datetime.now(timezone.utc)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_run_at FROM stage_runs WHERE stage_name = ?",
                (stage_name,),
            ).fetchone()
            if not row or not row["last_run_at"]:
                return True
            try:
                last_dt = datetime.fromisoformat(row["last_run_at"])
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                return (now - last_dt) >= timedelta(hours=interval_hours)
            except Exception:
                return True

    def record_stage_run(
        self,
        stage_name: str,
        interval_hours: float,
        status: str = "ok",
        duration_sec: float = 0.0,
        summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stage_runs (
                    stage_name, interval_hours, last_run_at, last_status, last_duration_sec, last_summary_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(stage_name) DO UPDATE SET
                    interval_hours=excluded.interval_hours,
                    last_run_at=excluded.last_run_at,
                    last_status=excluded.last_status,
                    last_duration_sec=excluded.last_duration_sec,
                    last_summary_json=excluded.last_summary_json
                """,
                (
                    stage_name,
                    float(interval_hours),
                    utc_now_iso(),
                    status,
                    round(float(duration_sec), 2),
                    json.dumps(summary or {}),
                ),
            )

    def run_maintenance(self, retain_history_days: int = 60) -> Dict[str, Any]:
        """Weekly DB maintenance: prune stale history/programmes, verify integrity, VACUUM."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retain_history_days)).isoformat()
        prog_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with self._connect() as conn:
            cur1 = conn.execute("DELETE FROM verification_history WHERE checked_at < ?", (cutoff,))
            cur2 = conn.execute("DELETE FROM programmes WHERE stop_utc < ?", (prog_cutoff,))
            cur3 = conn.execute("DELETE FROM sporting_events WHERE stop_utc < ?", (prog_cutoff,))
            conn.execute("INSERT INTO programmes_fts(programmes_fts) VALUES('rebuild')")
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            pruned = {
                "verification_history_pruned": cur1.rowcount,
                "programmes_pruned": cur2.rowcount,
                "events_pruned": cur3.rowcount,
                "integrity": integrity,
            }
        # VACUUM must run outside an active transaction
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()
        return pruned

    # ------------------------------------------------------------------
    # Candidate Queue (Time-Budgeted Carry-Over + Unknown Preservation)
    # ------------------------------------------------------------------
    def enqueue_candidates(self, items: List[Dict[str, Any]]) -> int:
        if not items:
            return 0
        now = utc_now_iso()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO candidate_queue (
                    url, name, feed_id, provider_id, tvg_id, tvg_name, tvg_logo,
                    group_title, broadcaster, extinf, triage_status, priority_score, enqueued_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    triage_status=CASE WHEN excluded.triage_status = 'high_relevance' THEN 'high_relevance' ELSE candidate_queue.triage_status END,
                    priority_score=MAX(candidate_queue.priority_score, excluded.priority_score)
                """,
                [
                    (
                        it["url"],
                        it.get("name", "Unknown"),
                        it.get("feed_id", ""),
                        it.get("provider_id", ""),
                        it.get("tvg_id", ""),
                        it.get("tvg_name", ""),
                        it.get("tvg_logo", ""),
                        it.get("group_title", ""),
                        it.get("broadcaster", ""),
                        it.get("extinf", ""),
                        it.get("triage_status", "unknown_candidate"),
                        float(it.get("priority_score", 0.5)),
                        now,
                    )
                    for it in items
                ],
            )
            return len(items)

    def fetch_queued_candidates(self, triage_status: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM candidate_queue
                WHERE triage_status = ?
                ORDER BY priority_score DESC, enqueued_at ASC
                LIMIT ?
                """,
                (triage_status, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def remove_queued_candidates(self, urls: List[str]) -> None:
        if not urls:
            return
        with self._connect() as conn:
            conn.executemany("DELETE FROM candidate_queue WHERE url = ?", [(u,) for u in urls])

    # ------------------------------------------------------------------
    # Channels & Aliases
    # ------------------------------------------------------------------
    def upsert_channel(
        self,
        ch: ChannelRecord,
        confidences: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = utc_now_iso()
        first_disc = ch.first_discovered or now
        last_upd = ch.last_updated or now
        conf = confidences or {}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO channels (
                    channel_id, name, broadcaster, country, primary_language,
                    secondary_languages, sports_json, tvg_id, tvg_logo,
                    epg_source, epg_status, epg_confidence, score,
                    score_breakdown, tier, status, first_discovered, last_updated,
                    language_confidence, sports_confidence, sports_duty_cycle,
                    competition_confidence, endpoint_reliability, source_reliability, channel_role
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    name=excluded.name,
                    broadcaster=CASE WHEN excluded.broadcaster != '' THEN excluded.broadcaster ELSE channels.broadcaster END,
                    country=CASE WHEN excluded.country != '' THEN excluded.country ELSE channels.country END,
                    primary_language=excluded.primary_language,
                    secondary_languages=excluded.secondary_languages,
                    sports_json=excluded.sports_json,
                    tvg_id=CASE WHEN excluded.tvg_id != '' THEN excluded.tvg_id ELSE channels.tvg_id END,
                    tvg_logo=CASE WHEN excluded.tvg_logo != '' THEN excluded.tvg_logo ELSE channels.tvg_logo END,
                    epg_source=CASE WHEN excluded.epg_source != '' THEN excluded.epg_source ELSE channels.epg_source END,
                    epg_status=excluded.epg_status,
                    epg_confidence=excluded.epg_confidence,
                    score=excluded.score,
                    score_breakdown=excluded.score_breakdown,
                    tier=excluded.tier,
                    status=excluded.status,
                    last_updated=excluded.last_updated,
                    language_confidence=excluded.language_confidence,
                    sports_confidence=excluded.sports_confidence,
                    sports_duty_cycle=excluded.sports_duty_cycle,
                    competition_confidence=excluded.competition_confidence,
                    endpoint_reliability=excluded.endpoint_reliability,
                    source_reliability=excluded.source_reliability,
                    channel_role=excluded.channel_role
                """,
                (
                    ch.channel_id,
                    ch.name,
                    ch.broadcaster,
                    ch.country,
                    ch.primary_language,
                    json.dumps(ch.secondary_languages),
                    json.dumps(ch.sports),
                    ch.tvg_id,
                    ch.tvg_logo,
                    ch.epg_source,
                    ch.epg_status,
                    ch.epg_confidence,
                    ch.score,
                    json.dumps(ch.score_breakdown),
                    ch.tier,
                    ch.status,
                    first_disc,
                    last_upd,
                    float(conf.get("language_confidence", 0.0)),
                    float(conf.get("sports_confidence", 0.0)),
                    float(conf.get("sports_duty_cycle", 0.0)),
                    float(conf.get("competition_confidence", 0.0)),
                    float(conf.get("endpoint_reliability", 0.0)),
                    float(conf.get("source_reliability", 0.0)),
                    str(conf.get("channel_role", "sports")),
                ),
            )
            for alias in ch.aliases:
                if alias and alias.strip():
                    conn.execute(
                        "INSERT OR IGNORE INTO channel_aliases (channel_id, alias) VALUES (?, ?)",
                        (ch.channel_id, alias.strip()),
                    )

    def get_all_channels(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM channels WHERE status = ? ORDER BY score DESC, name ASC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM channels ORDER BY score DESC, name ASC").fetchall()
            channels = []
            for r in rows:
                d = dict(r)
                d["secondary_languages"] = json.loads(d.get("secondary_languages") or "[]")
                d["sports"] = json.loads(d.pop("sports_json") or "[]")
                d["score_breakdown"] = json.loads(d.get("score_breakdown") or "{}")
                aliases = conn.execute(
                    "SELECT alias FROM channel_aliases WHERE channel_id = ?", (d["channel_id"],)
                ).fetchall()
                d["aliases"] = [a["alias"] for a in aliases]
                endpoints = conn.execute(
                    """
                    SELECT * FROM stream_endpoints
                    WHERE channel_id = ?
                    ORDER BY (status = 'active') DESC, ewma_availability DESC, latency_ms ASC
                    """,
                    (d["channel_id"],),
                ).fetchall()
                ep_list = []
                for ep in endpoints:
                    epd = dict(ep)
                    epd["audio_languages"] = json.loads(epd.get("audio_languages") or "[]")
                    prov_rows = conn.execute(
                        """
                        SELECT provider_id, source_id, repo_name, repo_url, file_path,
                               playlist_url, record_ref, discovery_query,
                               first_discovered_at, last_observed_at, is_original
                        FROM endpoint_provenance
                        WHERE endpoint_url = ?
                        ORDER BY is_original DESC, first_discovered_at ASC
                        """,
                        (epd["url"],),
                    ).fetchall()
                    epd["provenance"] = [dict(pr) for pr in prov_rows]
                    ep_list.append(epd)
                d["endpoints"] = ep_list
                channels.append(d)
            return channels

    # ------------------------------------------------------------------
    # Stream Endpoints, Survival State Machine & Adaptive Backoff
    # ------------------------------------------------------------------
    def upsert_endpoint(self, ep: StreamEndpointRecord, feed_id: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stream_endpoints (
                    url, channel_id, source_id, source_url, stream_format,
                    video_codec, audio_codec, resolution, fps, audio_languages,
                    status, access_status, last_checked, last_success, last_failure,
                    success_count, failure_count, latency_ms, failure_reason, feed_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    channel_id=excluded.channel_id,
                    source_id=CASE WHEN excluded.source_id != '' THEN excluded.source_id ELSE stream_endpoints.source_id END,
                    source_url=CASE WHEN excluded.source_url != '' THEN excluded.source_url ELSE stream_endpoints.source_url END,
                    feed_id=CASE WHEN excluded.feed_id != '' THEN excluded.feed_id ELSE stream_endpoints.feed_id END,
                    stream_format=CASE WHEN excluded.stream_format != 'unknown' THEN excluded.stream_format ELSE stream_endpoints.stream_format END
                """,
                (
                    ep.url, ep.channel_id, ep.source_id, ep.source_url, ep.stream_format,
                    ep.video_codec, ep.audio_codec, ep.resolution, ep.fps, json.dumps(ep.audio_languages),
                    ep.status, ep.access_status, ep.last_checked, ep.last_success, ep.last_failure,
                    ep.success_count, ep.failure_count, ep.latency_ms, ep.failure_reason, feed_id,
                ),
            )

    def record_verification(self, channel_id: str, res: VerificationResultRecord) -> str:
        """
        Persist verification telemetry with survival state machine & exponential backoff:
          - Passed -> active (next check in 6h)
          - Auth required -> auth_required (next check in 7d)
          - Failed (1st fail on previously high-EWMA active stream) -> degraded (next check in 6h)
          - Failed (otherwise) -> inactive (backoff: 24h -> 72h -> 14d)
        Returns the resulting endpoint status.
        """
        now_dt = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO verification_history (
                    url, checked_at, is_accessible, requires_auth, has_video,
                    has_audio, playback_ok, latency_ms, failure_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    res.url, res.checked_at,
                    1 if res.is_accessible else 0,
                    1 if res.requires_auth else 0,
                    1 if res.has_video else 0,
                    1 if res.has_audio else 0,
                    1 if res.playback_ok else 0,
                    res.latency_ms, res.failure_reason,
                ),
            )
            row = conn.execute(
                """
                SELECT status, success_count, failure_count, ewma_availability, consecutive_failures
                FROM stream_endpoints WHERE url = ?
                """,
                (res.url,),
            ).fetchone()
            prev_status = row["status"] if row else "pending"
            succ = int(row["success_count"]) if row else 0
            fail = int(row["failure_count"]) if row else 0
            old_ewma = float(row["ewma_availability"]) if row and row["ewma_availability"] is not None else 0.5
            consec_fail = int(row["consecutive_failures"]) if row and row["consecutive_failures"] is not None else 0

            passed = res.is_accessible and not res.requires_auth and res.playback_ok
            ewma = round(0.25 * (1.0 if passed else 0.0) + 0.75 * old_ewma, 4)
            fp = _make_fingerprint(res.video_codec, res.resolution, res.audio_languages)

            if passed:
                succ += 1
                consec_fail = 0
                status = "active"
                access_status = "free"
                next_check = (now_dt + timedelta(hours=6)).isoformat()
            elif res.requires_auth:
                fail += 1
                consec_fail += 1
                status = "auth_required"
                access_status = "auth_required"
                next_check = (now_dt + timedelta(days=7)).isoformat()
            else:
                fail += 1
                consec_fail += 1
                access_status = "blocked" if not res.is_accessible else "free"
                if prev_status == "active" and consec_fail == 1 and old_ewma >= 0.85:
                    status = "degraded"
                    next_check = (now_dt + timedelta(hours=6)).isoformat()
                else:
                    status = "inactive"
                    if consec_fail <= 3:
                        next_check = (now_dt + timedelta(hours=24)).isoformat()
                    elif consec_fail <= 10:
                        next_check = (now_dt + timedelta(hours=72)).isoformat()
                    else:
                        next_check = (now_dt + timedelta(days=14)).isoformat()

            conn.execute(
                """
                INSERT INTO stream_endpoints (
                    url, channel_id, stream_format, video_codec, audio_codec,
                    resolution, fps, audio_languages, status, access_status,
                    last_checked, last_success, last_failure, success_count,
                    failure_count, latency_ms, failure_reason,
                    ewma_availability, consecutive_failures, next_check_at, fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    stream_format=CASE WHEN excluded.stream_format != 'unknown' THEN excluded.stream_format ELSE stream_endpoints.stream_format END,
                    video_codec=CASE WHEN excluded.video_codec != '' THEN excluded.video_codec ELSE stream_endpoints.video_codec END,
                    audio_codec=CASE WHEN excluded.audio_codec != '' THEN excluded.audio_codec ELSE stream_endpoints.audio_codec END,
                    resolution=CASE WHEN excluded.resolution != '' THEN excluded.resolution ELSE stream_endpoints.resolution END,
                    fps=CASE WHEN excluded.fps > 0 THEN excluded.fps ELSE stream_endpoints.fps END,
                    audio_languages=excluded.audio_languages,
                    status=excluded.status,
                    access_status=excluded.access_status,
                    last_checked=excluded.last_checked,
                    last_success=CASE WHEN ? THEN excluded.last_checked ELSE stream_endpoints.last_success END,
                    last_failure=CASE WHEN ? THEN excluded.last_checked ELSE stream_endpoints.last_failure END,
                    success_count=excluded.success_count,
                    failure_count=excluded.failure_count,
                    latency_ms=excluded.latency_ms,
                    failure_reason=excluded.failure_reason,
                    ewma_availability=excluded.ewma_availability,
                    consecutive_failures=excluded.consecutive_failures,
                    next_check_at=excluded.next_check_at,
                    fingerprint=CASE WHEN excluded.fingerprint != '' THEN excluded.fingerprint ELSE stream_endpoints.fingerprint END
                """,
                (
                    res.url, channel_id, res.stream_format, res.video_codec, res.audio_codec,
                    res.resolution, res.fps, json.dumps(res.audio_languages), status, access_status,
                    res.checked_at, res.checked_at if passed else None, res.checked_at if not passed else None,
                    succ, fail, res.latency_ms, res.failure_reason,
                    ewma, consec_fail, next_check, fp,
                    1 if passed else 0, 1 if not passed else 0,
                ),
            )

            active_cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM stream_endpoints WHERE channel_id = ? AND status IN ('active', 'degraded')",
                (channel_id,),
            ).fetchone()["c"]
            ch_status = "active" if active_cnt > 0 else "inactive"
            conn.execute(
                "UPDATE channels SET status = ?, last_updated = ? WHERE channel_id = ?",
                (ch_status, res.checked_at, channel_id),
            )
            return status

    def get_known_endpoint_urls(self) -> set:
        with self._connect() as conn:
            rows = conn.execute("SELECT url FROM stream_endpoints").fetchall()
            return {r["url"] for r in rows}

    def get_endpoints_for_health_check(
        self,
        only_due: bool = True,
        priority_channel_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        now = utc_now_iso()
        prio_set = set(priority_channel_ids or [])
        with self._connect() as conn:
            if only_due:
                rows = conn.execute(
                    """
                    SELECT e.*, c.name AS channel_name, c.tvg_id
                    FROM stream_endpoints e
                    JOIN channels c ON e.channel_id = c.channel_id
                    WHERE e.next_check_at IS NULL OR e.next_check_at <= ?
                    ORDER BY (e.status IN ('active', 'degraded')) DESC, e.last_checked ASC NULLS FIRST
                    """,
                    (now,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT e.*, c.name AS channel_name, c.tvg_id
                    FROM stream_endpoints e
                    JOIN channels c ON e.channel_id = c.channel_id
                    ORDER BY (e.status IN ('active', 'degraded')) DESC, e.last_checked ASC NULLS FIRST
                    """
                ).fetchall()
            items = [dict(r) for r in rows]
            if prio_set:
                items.sort(key=lambda x: (x["channel_id"] not in prio_set, x["status"] not in ("active", "degraded")))
            return items

    # ------------------------------------------------------------------
    # EPG Sources, EPG Channels, Programmes, Sporting Events & FTS5
    # ------------------------------------------------------------------
    def get_epg_source_cache(self, url: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM epg_sources WHERE url = ?", (url,)).fetchone()
            return dict(row) if row else {}

    def upsert_epg_source(
        self,
        epg: EPGSourceRecord,
        etag: str = "",
        last_modified: str = "",
        content_sha256: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO epg_sources (
                    url, source_name, fmt, status, last_checked, last_updated,
                    channel_count, programme_count, is_stale, reliability_score,
                    etag, last_modified, content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    source_name=CASE WHEN excluded.source_name != '' THEN excluded.source_name ELSE epg_sources.source_name END,
                    fmt=excluded.fmt,
                    status=excluded.status,
                    last_checked=excluded.last_checked,
                    last_updated=excluded.last_updated,
                    channel_count=excluded.channel_count,
                    programme_count=excluded.programme_count,
                    is_stale=excluded.is_stale,
                    reliability_score=excluded.reliability_score,
                    etag=CASE WHEN excluded.etag != '' THEN excluded.etag ELSE epg_sources.etag END,
                    last_modified=CASE WHEN excluded.last_modified != '' THEN excluded.last_modified ELSE epg_sources.last_modified END,
                    content_sha256=CASE WHEN excluded.content_sha256 != '' THEN excluded.content_sha256 ELSE epg_sources.content_sha256 END
                """,
                (
                    epg.url, epg.source_name, epg.fmt, epg.status, epg.last_checked,
                    epg.last_updated, epg.channel_count, epg.programme_count,
                    1 if epg.is_stale else 0, epg.reliability_score,
                    etag, last_modified, content_sha256,
                ),
            )

    def upsert_epg_channels(self, epg_channels: List[Dict[str, Any]]) -> int:
        if not epg_channels:
            return 0
        now = utc_now_iso()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO epg_channels (
                    epg_channel_id, epg_source_url, display_names_json, icon_url,
                    sports_programme_count, total_programme_count, sports_duty_cycle, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(epg_channel_id) DO UPDATE SET
                    epg_source_url=excluded.epg_source_url,
                    display_names_json=excluded.display_names_json,
                    icon_url=CASE WHEN excluded.icon_url != '' THEN excluded.icon_url ELSE epg_channels.icon_url END,
                    sports_programme_count=excluded.sports_programme_count,
                    total_programme_count=excluded.total_programme_count,
                    sports_duty_cycle=excluded.sports_duty_cycle,
                    last_seen_at=excluded.last_seen_at
                """,
                [
                    (
                        c["epg_channel_id"],
                        c.get("epg_source_url", ""),
                        json.dumps(c.get("display_names", [])),
                        c.get("icon_url", ""),
                        int(c.get("sports_programme_count", 0)),
                        int(c.get("total_programme_count", 0)),
                        float(c.get("sports_duty_cycle", 0.0)),
                        now,
                    )
                    for c in epg_channels
                ],
            )
            return len(epg_channels)

    def get_epg_channels_with_sports(self, min_duty_cycle: float = 0.05) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM epg_channels
                WHERE sports_duty_cycle >= ? AND sports_programme_count > 0
                ORDER BY sports_duty_cycle DESC, sports_programme_count DESC
                """,
                (min_duty_cycle,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["display_names"] = json.loads(d.get("display_names_json") or "[]")
                out.append(d)
            return out

    def get_epg_channel(self, epg_channel_id: str) -> Optional[Dict[str, Any]]:
        if not epg_channel_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM epg_channels WHERE epg_channel_id = ?", (epg_channel_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["display_names"] = json.loads(d.get("display_names_json") or "[]")
            return d

    def upsert_programmes(self, progs: List[ProgrammeRecord], rebuild_fts: bool = True) -> int:
        if not progs:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO programmes (
                    channel_tvg_id, title, start_utc, stop_utc, description,
                    sport, competition, teams_or_athletes, source_type, epg_source_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_tvg_id, start_utc, title) DO UPDATE SET
                    stop_utc=excluded.stop_utc,
                    description=excluded.description,
                    sport=excluded.sport,
                    competition=excluded.competition,
                    teams_or_athletes=excluded.teams_or_athletes,
                    source_type=excluded.source_type,
                    epg_source_url=excluded.epg_source_url
                """,
                [
                    (
                        p.channel_tvg_id, p.title, p.start_utc, p.stop_utc,
                        p.description, p.sport, p.competition,
                        json.dumps(p.teams_or_athletes), p.source_type, p.epg_source_url,
                    )
                    for p in progs
                ],
            )
            if rebuild_fts:
                conn.execute("INSERT INTO programmes_fts(programmes_fts) VALUES('rebuild')")
            return len(progs)

    def search_programmes_fts(self, fts_query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Full-text search across indexed EPG programmes using SQLite FTS5."""
        if not fts_query.strip():
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.* FROM programmes_fts f
                JOIN programmes p ON p.id = f.rowid
                WHERE programmes_fts MATCH ?
                ORDER BY p.start_utc ASC
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["teams_or_athletes"] = json.loads(d.get("teams_or_athletes") or "[]")
                out.append(d)
            return out

    def get_programmes_for_channel(self, tvg_id: str) -> List[Dict[str, Any]]:
        if not tvg_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM programmes WHERE channel_tvg_id = ? ORDER BY start_utc ASC",
                (tvg_id,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["teams_or_athletes"] = json.loads(d.get("teams_or_athletes") or "[]")
                out.append(d)
            return out

    def upsert_sporting_events(self, events: List[Dict[str, Any]]) -> int:
        if not events:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO sporting_events (
                    event_id, sport, competition, event_title, teams_json,
                    start_utc, stop_utc, channel_id, epg_channel_id, evidence_type, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    stop_utc=excluded.stop_utc,
                    channel_id=CASE WHEN excluded.channel_id != '' THEN excluded.channel_id ELSE sporting_events.channel_id END,
                    evidence_type=excluded.evidence_type,
                    confidence=MAX(sporting_events.confidence, excluded.confidence)
                """,
                [
                    (
                        ev["event_id"], ev["sport"], ev["competition"], ev["event_title"],
                        json.dumps(ev.get("teams", [])), ev["start_utc"], ev["stop_utc"],
                        ev.get("channel_id", ""), ev.get("epg_channel_id", ""),
                        ev.get("evidence_type", "confirmed_schedule"), float(ev.get("confidence", 0.9)),
                    )
                    for ev in events
                ],
            )
            return len(events)

    def get_upcoming_sporting_events(self, hours_ahead: int = 48, now_dt: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now_dt or datetime.now(timezone.utc)
        cutoff = (now + timedelta(hours=hours_ahead)).isoformat()
        now_iso = now.isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sporting_events
                WHERE stop_utc >= ? AND start_utc <= ?
                ORDER BY start_utc ASC
                """,
                (now_iso, cutoff),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["teams"] = json.loads(d.get("teams_json") or "[]")
                out.append(d)
            return out

    # ------------------------------------------------------------------
    # Exclusions & Self-Improving Competition Suggestions
    # ------------------------------------------------------------------
    def add_exclusion(self, url_or_name: str, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exclusions (url_or_name, reason, excluded_at)
                VALUES (?, ?, ?)
                ON CONFLICT(url_or_name) DO UPDATE SET reason=excluded.reason, excluded_at=excluded.excluded_at
                """,
                (url_or_name, reason, utc_now_iso()),
            )

    def record_competition_suggestion(
        self, name: str, sport_guess: str = "", sample_channel: str = ""
    ) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO competition_suggestions (
                    suggested_name, sport_guess, occurrence_count, sample_channel, first_seen, last_seen
                ) VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(suggested_name) DO UPDATE SET
                    occurrence_count = competition_suggestions.occurrence_count + 1,
                    last_seen = excluded.last_seen
                """,
                (name, sport_guess, sample_channel, now, now),
            )

    def get_competition_suggestions(self, status: str = "pending") -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM competition_suggestions WHERE status = ? ORDER BY occurrence_count DESC",
                (status,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_epg_sources(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM epg_sources ORDER BY reliability_score DESC").fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Missing Channel Investigations (EPG Confirmed, No Verified Stream)
    # ------------------------------------------------------------------
    def has_verified_endpoint_for_tvg(self, tvg_id: str) -> bool:
        if not tvg_id:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM channels c
                JOIN stream_endpoints e ON e.channel_id = c.channel_id
                WHERE c.tvg_id = ? AND e.status IN ('active', 'degraded')
                """,
                (tvg_id,),
            ).fetchone()
            return bool(row and row["c"] > 0)

    def upsert_missing_channel_investigation(
        self,
        epg_channel_id: str,
        channel_name: str,
        broadcaster: str = "",
        relevant_event: str = "",
        competition: str = "",
        providers_searched: Optional[List[str]] = None,
        candidates_found: int = 0,
        rejection_reasons: Optional[List[str]] = None,
        needs_investigation: bool = True,
    ) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO missing_channel_investigations (
                    epg_channel_id, channel_name, broadcaster, relevant_event, competition,
                    providers_searched_json, candidates_found, rejection_reasons_json,
                    last_investigated_at, needs_investigation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(epg_channel_id) DO UPDATE SET
                    channel_name=excluded.channel_name,
                    broadcaster=CASE WHEN excluded.broadcaster != '' THEN excluded.broadcaster ELSE missing_channel_investigations.broadcaster END,
                    relevant_event=CASE WHEN excluded.relevant_event != '' THEN excluded.relevant_event ELSE missing_channel_investigations.relevant_event END,
                    competition=CASE WHEN excluded.competition != '' THEN excluded.competition ELSE missing_channel_investigations.competition END,
                    providers_searched_json=excluded.providers_searched_json,
                    candidates_found=excluded.candidates_found,
                    rejection_reasons_json=excluded.rejection_reasons_json,
                    last_investigated_at=excluded.last_investigated_at,
                    needs_investigation=excluded.needs_investigation
                """,
                (
                    epg_channel_id,
                    channel_name,
                    broadcaster,
                    relevant_event,
                    competition,
                    json.dumps(providers_searched or []),
                    int(candidates_found),
                    json.dumps(rejection_reasons or []),
                    now,
                    1 if needs_investigation else 0,
                ),
            )

    def get_missing_channel_investigations(self, only_pending: bool = True) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if only_pending:
                rows = conn.execute(
                    "SELECT * FROM missing_channel_investigations WHERE needs_investigation = 1 ORDER BY last_investigated_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM missing_channel_investigations ORDER BY last_investigated_at DESC"
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["providers_searched"] = json.loads(d.pop("providers_searched_json") or "[]")
                d["rejection_reasons"] = json.loads(d.pop("rejection_reasons_json") or "[]")
                d["needs_investigation"] = bool(d["needs_investigation"])
                out.append(d)
            return out

    # ------------------------------------------------------------------
    # Source Provenance, Pipeline Traces & Multi-Level Performance Stats
    # ------------------------------------------------------------------
    def record_endpoint_provenance(
        self,
        endpoint_url: str,
        provider_id: str = "",
        source_id: str = "",
        repo_name: str = "",
        repo_url: str = "",
        file_path: str = "",
        playlist_url: str = "",
        record_ref: str = "",
        discovery_query: str = "",
    ) -> None:
        """
        Preserve every source origin for an endpoint without overwriting the original discoverer.
        """
        if not endpoint_url:
            return
        now = utc_now_iso()
        origin_seed = f"{provider_id}|{source_id}|{repo_name}|{file_path}|{playlist_url}"
        origin_key = hashlib.sha1(origin_seed.encode("utf-8")).hexdigest()[:20]

        with self._connect() as conn:
            has_any = conn.execute(
                "SELECT COUNT(*) AS c FROM endpoint_provenance WHERE endpoint_url = ?",
                (endpoint_url,),
            ).fetchone()["c"]
            is_orig = 1 if has_any == 0 else 0
            conn.execute(
                """
                INSERT INTO endpoint_provenance (
                    endpoint_url, origin_key, provider_id, source_id, repo_name, repo_url,
                    file_path, playlist_url, record_ref, discovery_query,
                    first_discovered_at, last_observed_at, is_original
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(endpoint_url, origin_key) DO UPDATE SET
                    last_observed_at = excluded.last_observed_at,
                    record_ref = CASE WHEN excluded.record_ref != '' THEN excluded.record_ref ELSE endpoint_provenance.record_ref END,
                    discovery_query = CASE WHEN excluded.discovery_query != '' THEN excluded.discovery_query ELSE endpoint_provenance.discovery_query END
                """,
                (
                    endpoint_url, origin_key, provider_id, source_id, repo_name, repo_url,
                    file_path, playlist_url, record_ref, discovery_query,
                    now, now, is_orig,
                ),
            )

    def get_endpoint_provenance(self, endpoint_url: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM endpoint_provenance
                WHERE endpoint_url = ?
                ORDER BY is_original DESC, first_discovered_at ASC
                """,
                (endpoint_url,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["is_original"] = bool(d["is_original"])
                out.append(d)
            return out

    def record_candidate_trace(
        self,
        url: str,
        stage: str,
        status: str = "passed",
        channel_id: str = "",
        provider_id: str = "",
        repo_name: str = "",
        playlist_url: str = "",
        rejection_stage: str = "",
        rejection_reason: str = "",
    ) -> None:
        """Compact single-row-per-URL pipeline stage trace."""
        if not url:
            return
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT stages_json FROM candidate_traces WHERE url = ?", (url,)
            ).fetchone()
            stages = json.loads(row["stages_json"]) if row and row["stages_json"] else {}
            stages[stage] = {"status": status, "at": now}
            if rejection_reason:
                stages[stage]["reason"] = rejection_reason

            conn.execute(
                """
                INSERT INTO candidate_traces (
                    url, channel_id, provider_id, repo_name, playlist_url,
                    stage_reached, status, rejection_stage, rejection_reason,
                    stages_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    channel_id=CASE WHEN excluded.channel_id != '' THEN excluded.channel_id ELSE candidate_traces.channel_id END,
                    provider_id=CASE WHEN excluded.provider_id != '' THEN excluded.provider_id ELSE candidate_traces.provider_id END,
                    repo_name=CASE WHEN excluded.repo_name != '' THEN excluded.repo_name ELSE candidate_traces.repo_name END,
                    playlist_url=CASE WHEN excluded.playlist_url != '' THEN excluded.playlist_url ELSE candidate_traces.playlist_url END,
                    stage_reached=excluded.stage_reached,
                    status=excluded.status,
                    rejection_stage=CASE WHEN excluded.rejection_stage != '' THEN excluded.rejection_stage ELSE candidate_traces.rejection_stage END,
                    rejection_reason=CASE WHEN excluded.rejection_reason != '' THEN excluded.rejection_reason ELSE candidate_traces.rejection_reason END,
                    stages_json=excluded.stages_json,
                    updated_at=excluded.updated_at
                """,
                (
                    url, channel_id, provider_id, repo_name, playlist_url,
                    stage, status, rejection_stage, rejection_reason,
                    json.dumps(stages), now,
                ),
            )

    def get_candidate_trace(self, url: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM candidate_traces WHERE url = ?", (url,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["stages"] = json.loads(d.pop("stages_json") or "{}")
            return d

    def get_source_performance_stats(self) -> Dict[str, Any]:
        """
        Compute independent provider-level and repository-level performance statistics
        so one bad repository does not distort its parent provider's reliability.
        """
        with self._connect() as conn:
            prov_rows = conn.execute(
                """
                SELECT
                    ep.provider_id,
                    ep.repo_name,
                    ep.playlist_url,
                    ep.endpoint_url,
                    ep.is_original,
                    ep.last_observed_at,
                    se.channel_id,
                    se.status AS endpoint_status,
                    se.last_success,
                    ct.status AS trace_status,
                    ct.rejection_stage
                FROM endpoint_provenance ep
                LEFT JOIN stream_endpoints se ON se.url = ep.endpoint_url
                LEFT JOIN candidate_traces ct ON ct.url = ep.endpoint_url
                """
            ).fetchall()

        def _init_bucket() -> Dict[str, Any]:
            return {
                "total_observations": 0,
                "unique_endpoints": set(),
                "unique_channels": set(),
                "duplicates": 0,
                "rejected": 0,
                "verified_attempted": 0,
                "working_endpoints": set(),
                "last_discovery": "",
                "last_working_contribution": "",
            }

        by_provider: Dict[str, Dict[str, Any]] = {}
        by_repo: Dict[str, Dict[str, Any]] = {}

        for r in prov_rows:
            pid = r["provider_id"] or "unknown"
            repo = r["repo_name"] or ""
            url = r["endpoint_url"]

            targets = [(by_provider, pid)]
            if repo:
                targets.append((by_repo, repo))

            for container, key in targets:
                b = container.setdefault(key, _init_bucket())
                b["total_observations"] += 1
                b["unique_endpoints"].add(url)
                if not r["is_original"]:
                    b["duplicates"] += 1
                if r["channel_id"]:
                    b["unique_channels"].add(r["channel_id"])
                if r["trace_status"] == "rejected" or r["rejection_stage"]:
                    b["rejected"] += 1
                if r["endpoint_status"] in ("active", "degraded", "inactive", "auth_required"):
                    b["verified_attempted"] += 1
                if r["endpoint_status"] in ("active", "degraded"):
                    b["working_endpoints"].add(url)
                    if r["last_success"] and r["last_success"] > b["last_working_contribution"]:
                        b["last_working_contribution"] = r["last_success"]
                if r["last_observed_at"] and r["last_observed_at"] > b["last_discovery"]:
                    b["last_discovery"] = r["last_observed_at"]

        def _finalize(raw_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
            out = []
            for name, b in raw_map.items():
                tot = b["total_observations"]
                uniq_ep = len(b["unique_endpoints"])
                working = len(b["working_endpoints"])
                ver_att = b["verified_attempted"]
                out.append(
                    {
                        "name": name,
                        "total_candidates": tot,
                        "unique_endpoints": uniq_ep,
                        "unique_channels": len(b["unique_channels"]),
                        "working_endpoints": working,
                        "duplicate_rate": round(b["duplicates"] / tot, 3) if tot else 0.0,
                        "rejection_rate": round(b["rejected"] / max(1, uniq_ep), 3),
                        "verification_success_pct": round(100.0 * working / ver_att, 1) if ver_att else 0.0,
                        "last_successful_discovery": b["last_discovery"],
                        "last_working_contribution": b["last_working_contribution"],
                    }
                )
            out.sort(key=lambda x: (x["working_endpoints"], x["unique_channels"], x["total_candidates"]), reverse=True)
            return out

        return {
            "by_provider": _finalize(by_provider),
            "by_repository": _finalize(by_repo),
        }
