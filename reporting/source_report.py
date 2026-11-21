"""Source reliability report and root source_registry.json snapshot exporter."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List

from database.database import DatabaseManager


def export_source_registry_snapshot(
    db: DatabaseManager,
    output_path: str = "source_registry.json",
) -> Dict[str, Any]:
    """Export current source registry state and reliability scores to root source_registry.json."""
    sources = db.get_all_sources()
    payload = {
        "version": "2.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_sources": len(sources),
        "sources": sources,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return payload


def generate_source_report(db: DatabaseManager) -> Dict[str, Any]:
    sources = db.get_all_sources()
    top_sources = sorted(sources, key=lambda s: s.get("reliability_score", 0.0), reverse=True)
    return {
        "total_sources": len(sources),
        "enabled_sources": sum(1 for s in sources if s.get("enabled")),
        "top_reliable_sources": [
            {
                "source_id": s["source_id"],
                "name": s["name"],
                "source_type": s["source_type"],
                "reliability_score": s["reliability_score"],
                "channels_discovered": s["channels_discovered"],
                "failure_count": s["failure_count"],
            }
            for s in top_sources[:10]
        ],
    }

