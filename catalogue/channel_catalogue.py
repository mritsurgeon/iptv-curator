"""Canonical channel_catalogue.json builder & exporter (single source of truth)."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Tuple


def build_canonical_record(db_channel: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a DB channel + endpoints dict into the canonical channel_catalogue.json schema
    supporting both primary stream view and full endpoints list.
    """
    endpoints = db_channel.get("endpoints", [])
    active_eps = [e for e in endpoints if e.get("status") in ("active", "degraded")]
    sorted_eps = sorted(
        active_eps or endpoints,
        key=lambda e: (
            e.get("status") == "active",
            e.get("status") == "degraded",
            float(e.get("ewma_availability", 0.5)),
            -(e.get("latency_ms") or 9999.0),
        ),
        reverse=True,
    )
    primary_ep = sorted_eps[0] if sorted_eps else {}

    playback_status = "working" if primary_ep.get("status") in ("active", "degraded") else (
        "auth_required" if primary_ep.get("status") == "auth_required" else "inactive"
    )

    # Aggregate all stream origins across endpoints without overwriting original provenance
    all_origins: List[Dict[str, Any]] = []
    seen_origin_keys = set()
    for ep in sorted_eps:
        for pr in ep.get("provenance", []):
            okey = (pr.get("provider_id"), pr.get("repo_name"), pr.get("playlist_url"), ep.get("url"))
            if okey not in seen_origin_keys:
                seen_origin_keys.add(okey)
                all_origins.append(
                    {
                        "endpoint_url": ep.get("url", ""),
                        "provider_id": pr.get("provider_id") or "unknown",
                        "repo_name": pr.get("repo_name") or "unknown",
                        "repo_url": pr.get("repo_url") or "",
                        "file_path": pr.get("file_path") or "",
                        "playlist_url": pr.get("playlist_url") or "",
                        "record_ref": pr.get("record_ref") or "",
                        "discovery_query": pr.get("discovery_query") or "",
                        "first_discovered_at": pr.get("first_discovered_at") or "",
                        "last_observed_at": pr.get("last_observed_at") or "",
                        "is_original": bool(pr.get("is_original")),
                    }
                )

    orig_entry = next((o for o in all_origins if o["is_original"]), all_origins[0] if all_origins else None)
    primary_source_id = (orig_entry["provider_id"] if orig_entry else None) or primary_ep.get("source_id") or "unknown"
    primary_source_url = (orig_entry["playlist_url"] if orig_entry else None) or primary_ep.get("source_url") or "unknown"

    return {
        "channel_id": db_channel["channel_id"],
        "name": db_channel["name"],
        "aliases": db_channel.get("aliases", []),
        "broadcaster": db_channel.get("broadcaster", ""),
        "country": db_channel.get("country", ""),
        "language": {
            "primary": db_channel.get("primary_language", "Unknown"),
            "secondary": db_channel.get("secondary_languages", []),
        },
        "sports": db_channel.get("sports", []),
        "stream": {
            "url": primary_ep.get("url", ""),
            "format": primary_ep.get("stream_format", "unknown"),
            "video_codec": primary_ep.get("video_codec", ""),
            "audio_codec": primary_ep.get("audio_codec", ""),
            "resolution": primary_ep.get("resolution", ""),
            "latency_ms": primary_ep.get("latency_ms", 0.0),
            "status": "verified" if primary_ep.get("status") == "active" else primary_ep.get("status", "pending"),
            "endpoints": [
                {
                    "url": ep.get("url", ""),
                    "format": ep.get("stream_format", "unknown"),
                    "status": ep.get("status", "pending"),
                    "access_status": ep.get("access_status", "unknown"),
                    "latency_ms": ep.get("latency_ms", 0.0),
                    "resolution": ep.get("resolution", ""),
                    "last_checked": ep.get("last_checked"),
                    "origin_count": len(ep.get("provenance", [])),
                }
                for ep in sorted_eps
            ],
        },
        "epg": {
            "tvg_id": db_channel.get("tvg_id", ""),
            "tvg_logo": db_channel.get("tvg_logo", ""),
            "epg_provider": "epg_source" if db_channel.get("epg_source") else "unknown",
            "source": db_channel.get("epg_source", ""),
            "status": db_channel.get("epg_status", "unmatched"),
            "confidence": db_channel.get("epg_confidence", 0.0),
        },
        "verification": {
            "last_checked": primary_ep.get("last_checked") or db_channel.get("last_updated", ""),
            "playback_status": playback_status,
            "access_status": primary_ep.get("access_status", "unknown"),
            "score": db_channel.get("score", 0.0),
            "tier": db_channel.get("tier", "review"),
            "score_breakdown": db_channel.get("score_breakdown", {}),
            "confidences": {
                "language_confidence": db_channel.get("language_confidence", 0.0),
                "sports_confidence": db_channel.get("sports_confidence", 0.0),
                "sports_duty_cycle": db_channel.get("sports_duty_cycle", 0.0),
                "competition_confidence": db_channel.get("competition_confidence", 0.0),
                "endpoint_reliability": db_channel.get("endpoint_reliability", 0.0),
                "source_reliability": db_channel.get("source_reliability", 0.0),
            },
            "channel_role": db_channel.get("channel_role", "sports"),
        },
        "source": {
            "source_id": primary_source_id,
            "source_url": primary_source_url,
            "repo_name": (orig_entry["repo_name"] if orig_entry else "unknown"),
            "first_discovered": db_channel.get("first_discovered", ""),
            "last_updated": db_channel.get("last_updated", ""),
            "origin_count": len(all_origins),
            "all_origins": all_origins,
        },
    }


def select_primary_channels(
    records: List[Dict[str, Any]],
    min_primary_score: float = 60.0,
    scarce_sport_min: float = 45.0,
    scarce_sport_min_channels: int = 3,
    scarce_sport_min_confidence: float = 0.6,
    require_stream_url: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Partition working, freely accessible channels into (primary, review_candidates).
    Applies a category-aware scarcity floor: if a sport has fewer than
    `scarce_sport_min_channels` primary channels (>= `min_primary_score`),
    working free channels for that sport with `score >= scarce_sport_min` and
    `sports_confidence >= scarce_sport_min_confidence` are promoted into primary
    until the sport reaches `scarce_sport_min_channels`.
    """
    working_free = [
        r for r in records
        if r.get("verification", {}).get("playback_status") == "working"
        and r.get("verification", {}).get("access_status") == "free"
        and (not require_stream_url or bool(r.get("stream", {}).get("url")))
    ]

    primary: List[Dict[str, Any]] = []
    below_threshold: List[Dict[str, Any]] = []
    sport_counts: Dict[str, int] = {}

    for r in working_free:
        score = float(r.get("verification", {}).get("score", 0.0))
        if score >= min_primary_score:
            primary.append(r)
            for s in r.get("sports", []):
                sname = s.get("sport", "") if isinstance(s, dict) else str(s)
                if sname:
                    sport_counts[sname] = sport_counts.get(sname, 0) + 1
        else:
            below_threshold.append(r)

    # Sort borderline candidates highest-score first so the best scarce-sport channels promote first
    below_threshold.sort(key=lambda r: float(r.get("verification", {}).get("score", 0.0)), reverse=True)
    review: List[Dict[str, Any]] = []

    for r in below_threshold:
        ver = r.get("verification", {})
        score = float(ver.get("score", 0.0))
        overall_sconf = float(ver.get("confidences", {}).get("sports_confidence", 0.0))
        promoted = False

        if (
            scarce_sport_min_channels > 0
            and score >= scarce_sport_min
            and (overall_sconf >= scarce_sport_min_confidence or overall_sconf == 0.0)
        ):
            qualifying_sports: List[str] = []
            for s in r.get("sports", []):
                if isinstance(s, dict):
                    sname = s.get("sport", "")
                    sconf = float(s.get("confidence", 0.0))
                else:
                    sname = str(s)
                    sconf = overall_sconf
                if sname and sconf >= scarce_sport_min_confidence and sport_counts.get(sname, 0) < scarce_sport_min_channels:
                    qualifying_sports.append(sname)

            if qualifying_sports:
                ver["scarcity_promoted"] = True
                ver["scarcity_promoted_sports"] = qualifying_sports
                primary.append(r)
                for sname in qualifying_sports:
                    sport_counts[sname] = sport_counts.get(sname, 0) + 1
                promoted = True

        if not promoted:
            review.append(r)

    return primary, review


def export_channel_catalogue(
    db_channels: List[Dict[str, Any]],
    output_path: str = "channel_catalogue.json",
    min_primary_score: float = 60.0,
    scarce_sport_min: float = 45.0,
    scarce_sport_min_channels: int = 3,
    scarce_sport_min_confidence: float = 0.6,
) -> Dict[str, Any]:
    """Export canonical channel_catalogue.json and return the loaded dict."""
    records = [build_canonical_record(ch) for ch in db_channels]
    active_primary, review_candidates = select_primary_channels(
        records,
        min_primary_score=min_primary_score,
        scarce_sport_min=scarce_sport_min,
        scarce_sport_min_channels=scarce_sport_min_channels,
        scarce_sport_min_confidence=scarce_sport_min_confidence,
        require_stream_url=False,
    )

    payload = {
        "version": "2.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_channels": len(records),
        "active_primary_count": len(active_primary),
        "scarcity_promoted_count": sum(
            1 for r in active_primary if r.get("verification", {}).get("scarcity_promoted")
        ),
        "review_candidates_count": len(review_candidates),
        "channels": records,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return payload

