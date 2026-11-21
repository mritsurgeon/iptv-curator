"""Data models for IPTV Curator 2.0 database and canonical catalogue."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SourceRecord:
    source_id: str
    name: str
    source_type: str
    base_url: str
    discovery_method: str
    supported_formats: List[str] = field(default_factory=list)
    last_successful_scan: Optional[str] = None
    last_attempted_scan: Optional[str] = None
    reliability_score: float = 0.75
    failure_count: int = 0
    channels_discovered: int = 0
    enabled: bool = True
    country: Optional[str] = None


@dataclass
class ChannelRecord:
    channel_id: str
    name: str
    aliases: List[str] = field(default_factory=list)
    broadcaster: str = ""
    country: str = ""
    primary_language: str = "Unknown"
    secondary_languages: List[str] = field(default_factory=list)
    sports: List[Dict[str, Any]] = field(default_factory=list)
    tvg_id: str = ""
    tvg_logo: str = ""
    epg_source: str = ""
    epg_status: str = "unmatched"
    epg_confidence: float = 0.0
    score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    tier: str = "exclude"
    status: str = "active"
    first_discovered: str = ""
    last_updated: str = ""


@dataclass
class StreamEndpointRecord:
    url: str
    channel_id: str
    source_id: str = ""
    source_url: str = ""
    stream_format: str = "unknown"
    video_codec: str = ""
    audio_codec: str = ""
    resolution: str = ""
    fps: int = 0
    audio_languages: List[str] = field(default_factory=list)
    status: str = "pending"  # pending, active, inactive, auth_required
    access_status: str = "unknown"  # free, auth_required, blocked
    last_checked: Optional[str] = None
    last_success: Optional[str] = None
    last_failure: Optional[str] = None
    success_count: int = 0
    failure_count: int = 0
    latency_ms: float = 0.0
    failure_reason: str = ""


@dataclass
class VerificationResultRecord:
    url: str
    checked_at: str
    is_accessible: bool
    requires_auth: bool
    has_video: bool
    has_audio: bool
    playback_ok: bool
    stream_format: str = "unknown"
    resolution: str = ""
    video_codec: str = ""
    audio_codec: str = ""
    audio_languages: List[str] = field(default_factory=list)
    latency_ms: float = 0.0
    failure_reason: str = ""
    fps: int = 0


@dataclass
class EPGSourceRecord:
    url: str
    source_name: str = ""
    fmt: str = "xml"
    status: str = "active"
    last_checked: Optional[str] = None
    last_updated: Optional[str] = None
    channel_count: int = 0
    programme_count: int = 0
    is_stale: bool = False
    reliability_score: float = 0.8


@dataclass
class ProgrammeRecord:
    channel_tvg_id: str
    title: str
    start_utc: str
    stop_utc: str
    description: str = ""
    sport: str = ""
    competition: str = ""
    teams_or_athletes: List[str] = field(default_factory=list)
    source_type: str = "confirmed_broadcaster"  # confirmed_broadcaster, official_fixture, inferred_event
    epg_source_url: str = ""

