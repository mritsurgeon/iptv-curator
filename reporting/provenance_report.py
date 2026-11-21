"""Source provenance reporting & individual channel origin inspector."""

from typing import Any, Dict, List, Optional

from database.database import DatabaseManager


def build_provenance_report(
    db: DatabaseManager,
    catalogue_channels: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build source performance, multi-origin, unknown-provenance & EPG contribution metrics."""
    perf = db.get_source_performance_stats()
    epg_sources = db.get_epg_sources()

    multi_origin = []
    unknown_prov = []
    for ch in catalogue_channels:
        src = ch.get("source", {})
        origins = src.get("all_origins", [])
        distinct_sources = {
            (o.get("provider_id"), o.get("repo_name"), o.get("playlist_url"))
            for o in origins
        }
        if len(distinct_sources) >= 2:
            multi_origin.append(
                {
                    "channel_id": ch["channel_id"],
                    "name": ch["name"],
                    "origin_count": len(distinct_sources),
                }
            )
        if not origins or src.get("source_id") in ("", "unknown"):
            unknown_prov.append({"channel_id": ch["channel_id"], "name": ch["name"]})

    most_failed = sorted(
        perf["by_repository"] or perf["by_provider"],
        key=lambda x: (x["rejection_rate"], x["total_candidates"]),
        reverse=True,
    )[:10]

    stopped = [
        s for s in db.get_all_sources()
        if s.get("failure_count", 0) >= 3 or not s.get("enabled", True)
    ]

    contributing_epgs = [
        {
            "url": e["url"],
            "source_name": e.get("source_name") or e["url"],
            "status": e.get("status", "unknown"),
            "channel_count": e.get("channel_count", 0),
            "programme_count": e.get("programme_count", 0),
        }
        for e in epg_sources
        if e.get("programme_count", 0) > 0
    ]

    return {
        "top_providers": perf["by_provider"][:10],
        "top_repositories": perf["by_repository"][:10],
        "most_failed_sources": most_failed,
        "stopped_sources": [
            {"source_id": s["source_id"], "name": s["name"], "failure_count": s["failure_count"], "enabled": s["enabled"]}
            for s in stopped
        ],
        "multi_origin_channels": multi_origin,
        "unknown_provenance_channels": unknown_prov,
        "contributing_epg_sources": contributing_epgs,
    }


def inspect_channel_provenance(db: DatabaseManager, channel_query: str) -> Optional[Dict[str, Any]]:
    """
    Inspect the complete stream & EPG provenance and verification trace of an individual channel
    by channel_id or case-insensitive channel name substring.
    """
    q = channel_query.strip().lower()
    if not q:
        return None
    channels = db.get_all_channels()
    match = next((c for c in channels if c["channel_id"].lower() == q), None)
    if not match:
        match = next((c for c in channels if q in c["name"].lower()), None)
    if not match:
        return None

    endpoints_detail = []
    for ep in match.get("endpoints", []):
        origins = db.get_endpoint_provenance(ep["url"])
        trace = db.get_candidate_trace(ep["url"])
        endpoints_detail.append(
            {
                "url": ep["url"],
                "status": ep.get("status", "unknown"),
                "access_status": ep.get("access_status", "unknown"),
                "last_checked": ep.get("last_checked"),
                "origins": origins if origins else [{"provider_id": "unknown", "repo_name": "unknown", "is_original": True}],
                "pipeline_trace": trace,
            }
        )

    epg_src = match.get("epg_source") or "unknown"
    return {
        "channel_id": match["channel_id"],
        "name": match["name"],
        "broadcaster": match.get("broadcaster") or "unknown",
        "status": match.get("status", "unknown"),
        "stream_endpoints": endpoints_detail,
        "epg_provenance": {
            "tvg_id": match.get("tvg_id") or "unknown",
            "epg_feed_url": epg_src,
            "epg_status": match.get("epg_status", "unmatched"),
            "epg_confidence": match.get("epg_confidence", 0.0),
        },
    }

