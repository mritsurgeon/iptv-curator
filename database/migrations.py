"""SQLite schema definitions and forward-only migrations (v1 -> v2 with FTS5)."""

import sqlite3

SCHEMA_V1_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    base_url TEXT NOT NULL,
    discovery_method TEXT NOT NULL,
    supported_formats TEXT NOT NULL DEFAULT '[]',
    last_successful_scan TEXT,
    last_attempted_scan TEXT,
    reliability_score REAL NOT NULL DEFAULT 0.75,
    failure_count INTEGER NOT NULL DEFAULT 0,
    channels_discovered INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    country TEXT
);

CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    broadcaster TEXT DEFAULT '',
    country TEXT DEFAULT '',
    primary_language TEXT DEFAULT 'Unknown',
    secondary_languages TEXT DEFAULT '[]',
    sports_json TEXT DEFAULT '[]',
    tvg_id TEXT DEFAULT '',
    tvg_logo TEXT DEFAULT '',
    epg_source TEXT DEFAULT '',
    epg_status TEXT DEFAULT 'unmatched',
    epg_confidence REAL DEFAULT 0.0,
    score REAL DEFAULT 0.0,
    score_breakdown TEXT DEFAULT '{}',
    tier TEXT DEFAULT 'exclude',
    status TEXT DEFAULT 'active',
    first_discovered TEXT NOT NULL,
    last_updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    UNIQUE(channel_id, alias),
    FOREIGN KEY(channel_id) REFERENCES channels(channel_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS stream_endpoints (
    url TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    source_id TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    stream_format TEXT DEFAULT 'unknown',
    video_codec TEXT DEFAULT '',
    audio_codec TEXT DEFAULT '',
    resolution TEXT DEFAULT '',
    fps INTEGER DEFAULT 0,
    audio_languages TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    access_status TEXT DEFAULT 'unknown',
    last_checked TEXT,
    last_success TEXT,
    last_failure TEXT,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    latency_ms REAL DEFAULT 0.0,
    failure_reason TEXT DEFAULT '',
    FOREIGN KEY(channel_id) REFERENCES channels(channel_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS verification_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    is_accessible INTEGER NOT NULL,
    requires_auth INTEGER NOT NULL,
    has_video INTEGER NOT NULL,
    has_audio INTEGER NOT NULL,
    playback_ok INTEGER NOT NULL,
    latency_ms REAL DEFAULT 0.0,
    failure_reason TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sports_categories (
    sport_name TEXT PRIMARY KEY,
    playlist_file TEXT NOT NULL,
    aliases_json TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS competitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sport_name TEXT NOT NULL,
    competition_type TEXT NOT NULL,
    competition_name TEXT NOT NULL,
    aliases_json TEXT DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(sport_name, competition_name)
);

CREATE TABLE IF NOT EXISTS epg_sources (
    url TEXT PRIMARY KEY,
    source_name TEXT DEFAULT '',
    fmt TEXT DEFAULT 'xml',
    status TEXT DEFAULT 'active',
    last_checked TEXT,
    last_updated TEXT,
    channel_count INTEGER DEFAULT 0,
    programme_count INTEGER DEFAULT 0,
    is_stale INTEGER DEFAULT 0,
    reliability_score REAL DEFAULT 0.8
);

CREATE TABLE IF NOT EXISTS programmes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_tvg_id TEXT NOT NULL,
    title TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    stop_utc TEXT NOT NULL,
    description TEXT DEFAULT '',
    sport TEXT DEFAULT '',
    competition TEXT DEFAULT '',
    teams_or_athletes TEXT DEFAULT '[]',
    source_type TEXT DEFAULT 'confirmed_broadcaster',
    epg_source_url TEXT DEFAULT '',
    UNIQUE(channel_tvg_id, start_utc, title)
);

CREATE TABLE IF NOT EXISTS source_reliability (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    channels_found INTEGER DEFAULT 0,
    channels_working INTEGER DEFAULT 0,
    channels_failed INTEGER DEFAULT 0,
    reliability_score REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS exclusions (
    url_or_name TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    excluded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competition_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggested_name TEXT NOT NULL UNIQUE,
    sport_guess TEXT DEFAULT '',
    occurrence_count INTEGER DEFAULT 1,
    sample_channel TEXT DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    status TEXT DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_endpoints_channel ON stream_endpoints(channel_id);
CREATE INDEX IF NOT EXISTS idx_endpoints_status ON stream_endpoints(status);
CREATE INDEX IF NOT EXISTS idx_verif_url ON verification_history(url);
CREATE INDEX IF NOT EXISTS idx_programmes_tvg ON programmes(channel_tvg_id, start_utc);
"""

SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS feed_sources (
    feed_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    feed_url TEXT NOT NULL UNIQUE,
    feed_type TEXT NOT NULL DEFAULT 'm3u',
    etag TEXT DEFAULT '',
    last_modified TEXT DEFAULT '',
    content_sha256 TEXT DEFAULT '',
    reliability_score REAL NOT NULL DEFAULT 0.75,
    channels_found INTEGER DEFAULT 0,
    channels_working INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    last_fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS search_queries (
    query_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    query_text TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'config',
    sport_hint TEXT DEFAULT '',
    pulls INTEGER DEFAULT 0,
    total_reward REAL DEFAULT 0.0,
    last_used_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS broadcasters (
    broadcaster_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    aliases_json TEXT DEFAULT '[]',
    default_language TEXT DEFAULT 'Unknown',
    country TEXT DEFAULT '',
    logo_url TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS candidate_queue (
    url TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    feed_id TEXT DEFAULT '',
    provider_id TEXT DEFAULT '',
    tvg_id TEXT DEFAULT '',
    tvg_name TEXT DEFAULT '',
    tvg_logo TEXT DEFAULT '',
    group_title TEXT DEFAULT '',
    broadcaster TEXT DEFAULT '',
    extinf TEXT DEFAULT '',
    triage_status TEXT NOT NULL DEFAULT 'unknown_candidate',
    priority_score REAL NOT NULL DEFAULT 0.5,
    enqueued_at TEXT NOT NULL,
    attempts INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS epg_channels (
    epg_channel_id TEXT PRIMARY KEY,
    epg_source_url TEXT NOT NULL,
    display_names_json TEXT DEFAULT '[]',
    icon_url TEXT DEFAULT '',
    sports_programme_count INTEGER DEFAULT 0,
    total_programme_count INTEGER DEFAULT 0,
    sports_duty_cycle REAL DEFAULT 0.0,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sporting_events (
    event_id TEXT PRIMARY KEY,
    sport TEXT NOT NULL,
    competition TEXT NOT NULL,
    event_title TEXT NOT NULL,
    teams_json TEXT DEFAULT '[]',
    start_utc TEXT NOT NULL,
    stop_utc TEXT NOT NULL,
    channel_id TEXT DEFAULT '',
    epg_channel_id TEXT DEFAULT '',
    evidence_type TEXT NOT NULL DEFAULT 'confirmed_schedule',
    confidence REAL NOT NULL DEFAULT 0.9
);

CREATE VIRTUAL TABLE IF NOT EXISTS programmes_fts USING fts5(
    title,
    description,
    sport,
    competition,
    channel_tvg_id UNINDEXED,
    content='programmes',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE INDEX IF NOT EXISTS idx_queue_priority ON candidate_queue(triage_status, priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_events_start ON sporting_events(start_utc);
CREATE INDEX IF NOT EXISTS idx_epg_duty ON epg_channels(sports_duty_cycle DESC);
"""

CHANNEL_V2_COLS = [
    ("language_confidence", "REAL DEFAULT 0.0"),
    ("sports_confidence", "REAL DEFAULT 0.0"),
    ("sports_duty_cycle", "REAL DEFAULT 0.0"),
    ("competition_confidence", "REAL DEFAULT 0.0"),
    ("endpoint_reliability", "REAL DEFAULT 0.0"),
    ("source_reliability", "REAL DEFAULT 0.0"),
    ("channel_role", "TEXT DEFAULT 'sports'"),
]

ENDPOINT_V2_COLS = [
    ("feed_id", "TEXT DEFAULT ''"),
    ("ewma_availability", "REAL DEFAULT 0.5"),
    ("consecutive_failures", "INTEGER DEFAULT 0"),
    ("next_check_at", "TEXT"),
    ("is_event_only", "INTEGER DEFAULT 0"),
    ("fingerprint", "TEXT DEFAULT ''"),
]

EPG_SOURCE_V2_COLS = [
    ("etag", "TEXT DEFAULT ''"),
    ("last_modified", "TEXT DEFAULT ''"),
    ("content_sha256", "TEXT DEFAULT ''"),
]

SCHEMA_V3_SQL = """
CREATE TABLE IF NOT EXISTS stage_runs (
    stage_name TEXT PRIMARY KEY,
    interval_hours REAL NOT NULL,
    last_run_at TEXT,
    last_status TEXT DEFAULT 'never',
    last_duration_sec REAL DEFAULT 0.0,
    last_summary_json TEXT DEFAULT '{}'
);
"""

SCHEMA_V4_SQL = """
CREATE TABLE IF NOT EXISTS missing_channel_investigations (
    epg_channel_id TEXT PRIMARY KEY,
    channel_name TEXT NOT NULL,
    broadcaster TEXT DEFAULT '',
    relevant_event TEXT DEFAULT '',
    competition TEXT DEFAULT '',
    providers_searched_json TEXT DEFAULT '[]',
    candidates_found INTEGER DEFAULT 0,
    rejection_reasons_json TEXT DEFAULT '[]',
    last_investigated_at TEXT NOT NULL,
    needs_investigation INTEGER NOT NULL DEFAULT 1
);
"""

SCHEMA_V5_SQL = """
CREATE TABLE IF NOT EXISTS endpoint_provenance (
    endpoint_url TEXT NOT NULL,
    origin_key TEXT NOT NULL,
    provider_id TEXT DEFAULT '',
    source_id TEXT DEFAULT '',
    repo_name TEXT DEFAULT '',
    repo_url TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    playlist_url TEXT DEFAULT '',
    record_ref TEXT DEFAULT '',
    discovery_query TEXT DEFAULT '',
    first_discovered_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    is_original INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (endpoint_url, origin_key)
);

CREATE TABLE IF NOT EXISTS candidate_traces (
    url TEXT PRIMARY KEY,
    channel_id TEXT DEFAULT '',
    provider_id TEXT DEFAULT '',
    repo_name TEXT DEFAULT '',
    playlist_url TEXT DEFAULT '',
    stage_reached TEXT NOT NULL DEFAULT 'discovered',
    status TEXT NOT NULL DEFAULT 'in_progress',
    rejection_stage TEXT DEFAULT '',
    rejection_reason TEXT DEFAULT '',
    stages_json TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prov_url ON endpoint_provenance(endpoint_url);
CREATE INDEX IF NOT EXISTS idx_prov_provider ON endpoint_provenance(provider_id);
CREATE INDEX IF NOT EXISTS idx_prov_repo ON endpoint_provenance(repo_name);
CREATE INDEX IF NOT EXISTS idx_traces_stage ON candidate_traces(stage_reached, status);
"""

SEARCH_QUERY_V3_COLS = [
    ("results_discovered", "INTEGER DEFAULT 0"),
    ("new_source_urls", "INTEGER DEFAULT 0"),
    ("new_playlists", "INTEGER DEFAULT 0"),
    ("new_epg_feeds", "INTEGER DEFAULT 0"),
    ("new_candidate_channels", "INTEGER DEFAULT 0"),
    ("verified_channels", "INTEGER DEFAULT 0"),
    ("total_execution_sec", "REAL DEFAULT 0.0"),
]


def _ensure_columns(conn: sqlite3.Connection, table: str, cols: list) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col_name, col_def in cols:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")


def run_migrations(conn: sqlite3.Connection) -> int:
    """Apply forward-only migrations (v1 -> v2 -> v3) idempotently and return current version."""
    conn.executescript(SCHEMA_V1_SQL)
    cur = conn.cursor()
    cur.execute("SELECT MAX(version) FROM schema_version")
    row = cur.fetchone()
    current = int(row[0]) if row and row[0] is not None else 0

    if current < 1:
        cur.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, datetime('now'))")
        current = 1

    if current < 2:
        conn.executescript(SCHEMA_V2_SQL)
        _ensure_columns(conn, "channels", CHANNEL_V2_COLS)
        _ensure_columns(conn, "stream_endpoints", ENDPOINT_V2_COLS)
        _ensure_columns(conn, "epg_sources", EPG_SOURCE_V2_COLS)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_endpoints_next_check ON stream_endpoints(next_check_at)")
        cur.execute("INSERT INTO schema_version (version, applied_at) VALUES (2, datetime('now'))")
        current = 2

    if current < 3:
        conn.executescript(SCHEMA_V3_SQL)
        _ensure_columns(conn, "search_queries", SEARCH_QUERY_V3_COLS)
        cur.execute("INSERT INTO schema_version (version, applied_at) VALUES (3, datetime('now'))")
        current = 3

    if current < 4:
        conn.executescript(SCHEMA_V4_SQL)
        cur.execute("INSERT INTO schema_version (version, applied_at) VALUES (4, datetime('now'))")
        current = 4

    if current < 5:
        conn.executescript(SCHEMA_V5_SQL)
        cur.execute("INSERT INTO schema_version (version, applied_at) VALUES (5, datetime('now'))")
        current = 5

    conn.commit()
    return current
