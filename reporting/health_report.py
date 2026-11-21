"""Channel health report builder deriving from canonical catalogue and DB."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List


def generate_health_report(
    catalogue_channels: List[Dict[str, Any]],
    output_path: str = "channel_health_report.json",
) -> Dict[str, Any]:
    """Generate channel_health_report.json grouped by verification status, sport, language, and broadcaster."""
    by_status: Dict[str, int] = {}
    by_sport: Dict[str, int] = {}
    by_language: Dict[str, int] = {}
    by_broadcaster: Dict[str, int] = {}
    review_candidates: List[Dict[str, Any]] = []
    auth_required: List[Dict[str, Any]] = []
    inactive: List[Dict[str, Any]] = []

    for ch in catalogue_channels:
        ver = ch.get("verification", {})
        status = ver.get("playback_status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1

        lang = ch.get("language", {}).get("primary", "Unknown")
        by_language[lang] = by_language.get(lang, 0) + 1

        bc = ch.get("broadcaster") or "Unspecified"
        by_broadcaster[bc] = by_broadcaster.get(bc, 0) + 1

        for s in ch.get("sports", []):
            sname = s.get("sport", "Other")
            by_sport[sname] = by_sport.get(sname, 0) + 1

        if ver.get("access_status") == "auth_required":
            auth_required.append({"channel_id": ch["channel_id"], "name": ch["name"], "url": ch.get("stream", {}).get("url", "")})
        elif status != "working":
            inactive.append({"channel_id": ch["channel_id"], "name": ch["name"], "url": ch.get("stream", {}).get("url", "")})
        elif ver.get("score", 0.0) < 60.0:
            review_candidates.append({
                "channel_id": ch["channel_id"],
                "name": ch["name"],
                "score": ver.get("score", 0.0),
                "language": lang,
            })

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_channels": len(catalogue_channels),
        "by_verification_status": by_status,
        "by_sport": by_sport,
        "by_language": by_language,
        "by_broadcaster": by_broadcaster,
        "auth_required_count": len(auth_required),
        "inactive_count": len(inactive),
        "review_candidates": review_candidates,
        "auth_required_channels": auth_required[:50],
        "inactive_channels": inactive[:50],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report

