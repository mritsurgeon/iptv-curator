# IPTV Curator 2.0 — Autonomous Sports IPTV Discovery, Verification & Curation Engine

IPTV Curator 2.0 is an autonomous, continuously improving sports television discovery, verification, classification, EPG matching, and playlist generation engine.

## Objectives

- Continuously discover publicly indexed free sports IPTV streams and XMLTV/EPG sources across GitHub, GitLab, Codeberg, SourceHut, public datasets, official broadcasters, and search engines.
- Reuse the single stream-opening and playback verification gatekeeper (`HTTP` + `ffprobe` + 3-second `ffmpeg` live decode) with standard HTTPS certificate validation.
- Classify broadcast language (`English`, `English + Other`, `Other Language`, `Unknown`) and multi-sport / competition relevance without geographic filtering or scoring.
- Maintain persistent historical records in SQLite (`data/iptv_curator.db`) and export a single canonical source of truth (`channel_catalogue.json`).
- Generate clean, organised sport-specific, language-specific, and master M3U playlists alongside `sports_epg.xml` and daily monitoring reports.
- Preserve compatibility with existing Fire TV ADB deployment (`deploy_to_firetv.py`) and local HTTP server (`serve_playlist.py`).

## Architecture

```text
iptv-curator/
├── config/
│   ├── sports_catalogue.json      # Hierarchical Sport -> Competition Type -> Competition config
│   ├── source_registry.json       # Configurable discovery sources & reliability metadata
│   ├── discovery_queries.json     # Platform, format, sport, EPG & broadcaster query templates
│   ├── scoring_weights.json       # Configurable 0-100 channel scoring weights & thresholds
│   └── settings.json              # Pipeline timeouts, paths & timezone settings
├── discovery/                     # Modular multi-source discovery providers + DiscoveryManager
├── verification/                  # Unified stream-opening gatekeeper & scheduled HealthMonitor
├── classification/                # LanguageDetector, SportsClassifier, CompetitionMapper, ChannelScorer
├── epg/                           # XMLTV parser, EPGFetcher, EPGMatcher, ProgrammeEnrichment, EventMapper
├── catalogue/                     # ChannelDeduplicator, canonical channel_catalogue.json exporter
├── playlist/                      # Master, Sport, English, and EPG builders (derived from catalogue)
├── database/                      # SQLite schema, migrations, and persistence manager
├── reporting/                     # daily_report.md, channel_health_report.json, source_registry.json
├── tests/                         # Unit & integration tests (mocked streams, zero live dependency)
└── daily_curator_pipeline.py      # Main staged CLI & autonomous orchestrator
```

## Stream Verification & Scoring

1. **Mandatory Gatekeeper**: Every candidate must pass `verification/stream_validator.py:validate_stream_opening()` (HTTP 200/206/3xx without auth wall, `ffprobe` video/audio inspection, and 3-second `ffmpeg` live decode).
2. **Scoring (0–100)** (`config/scoring_weights.json`):
   - Sports relevance: `30`
   - English language confidence: `20`
   - Playback stability: `20`
   - Metadata quality: `10`
   - EPG availability: `10`
   - Source reliability: `10`
3. **Tiers**:
   - `90–100`: `premium`
   - `75–89`: `high_quality`
   - `60–74`: `acceptable`
   - `<60`: `review` (excluded from primary playlists, tracked in `channel_health_report.json`)

## Configuration Guide

- **Add a new sport or competition**: Edit `config/sports_catalogue.json` under `sports` → `competition_types` with `name`, `aliases`, `active`, and `broadcasters`. No Python code changes required.
- **Add a new discovery source**: Add an entry to `config/source_registry.json` (`source_id`, `source_type`, `base_url`, `discovery_method`, `enabled`).
- **Add a new discovery provider**: Subclass `BaseDiscoveryProvider` in `discovery/` and register it in `DiscoveryManager._build_provider()`.

## Generated Outputs

- `channel_catalogue.json` — Canonical single source of truth
- `master_sports.m3u` (and `verified_premier_sports.m3u8` for Fire TV compatibility)
- `english_sports.m3u`
- `football.m3u`, `rugby.m3u`, `cricket.m3u`, `tennis.m3u`, `combat_sports.m3u`
- `sports_epg.xml` (timestamps stored in UTC internally; `Africa/Johannesburg` for display)
- `channel_health_report.json`, `source_registry.json`, `daily_report.md`

## Running Manually & Tests

```bash
# Run full pipeline
python3 daily_curator_pipeline.py --stage all

# Run individual stages
python3 daily_curator_pipeline.py --stage discover
python3 daily_curator_pipeline.py --stage health
python3 daily_curator_pipeline.py --stage epg
python3 daily_curator_pipeline.py --stage classify
python3 daily_curator_pipeline.py --stage build

# Run automated test suite
python3 -m unittest discover -s tests -v
```

## GitHub Actions Schedules

- **Daily Full Curation** (`.github/workflows/daily_iptv_curator.yml`): Runs daily at `03:00 UTC` with SQLite cache persistence and meaningful-diff commits.
- **6-Hour Health & EPG Refresh** (`.github/workflows/health_check.yml`): Runs every 6 hours to deactivate dead streams and auto-restore recovered ones.
- **Automated Tests** (`.github/workflows/tests.yml`): Runs `unittest` on every push and PR.

