"""Readable daily curation report generator (daily_report.md)."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def generate_daily_report(
    catalogue_channels: List[Dict[str, Any]],
    health_report: Dict[str, Any],
    source_report: Dict[str, Any],
    discovery_stats: Optional[Dict[str, Any]] = None,
    epg_stats: Optional[Dict[str, Any]] = None,
    health_check_stats: Optional[Dict[str, Any]] = None,
    new_suggestions: Optional[List[Dict[str, Any]]] = None,
    prev_summary: Optional[Dict[str, Any]] = None,
    provenance_report: Optional[Dict[str, Any]] = None,
    output_path: str = "daily_report.md",
) -> str:
    """Write a comprehensive, readable daily report without geographic comparisons."""
    discovery_stats = discovery_stats or {}
    epg_stats = epg_stats or {}
    health_check_stats = health_check_stats or {}
    new_suggestions = new_suggestions or []
    prev_summary = prev_summary or {}

    working = health_report.get("by_verification_status", {}).get("working", 0)
    inactive = health_report.get("inactive_count", 0)
    auth_req = health_report.get("auth_required_count", 0)
    english_cnt = (
        health_report.get("by_language", {}).get("English", 0)
        + health_report.get("by_language", {}).get("English + Other", 0)
    )
    epg_matched = sum(1 for ch in catalogue_channels if ch.get("epg", {}).get("status") == "matched")

    prev_working = prev_summary.get("working", working)
    delta_working = working - prev_working

    # Group by competition
    by_comp: Dict[str, int] = {}
    for ch in catalogue_channels:
        for s in ch.get("sports", []):
            for c in s.get("competitions", []):
                by_comp[c] = by_comp.get(c, 0) + 1

    lines = [
        "# IPTV Curator 2.0 — Daily Curation Report",
        f"Generated (UTC): `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        f"- **Total sources scanned**: {discovery_stats.get('sources_scanned', source_report.get('enabled_sources', 0))}",
        f"- **New candidate streams discovered**: {discovery_stats.get('new_candidates', 0)}",
        f"- **Channels successfully verified (working)**: {working} (delta vs previous: {delta_working:+d})",
        f"- **Channels failing playback (inactive)**: {inactive}",
        f"- **Channels requiring authentication**: {auth_req}",
        f"- **English / English + Other channels**: {english_cnt}",
        f"- **Channels with matched EPG**: {epg_matched}",
        f"- **Stale EPG sources**: {epg_stats.get('stale_sources', 0)}",
        f"- **Deactivated in health check**: {len(health_check_stats.get('deactivated', []))}",
        f"- **Restored in health check**: {len(health_check_stats.get('restored', []))}",
        "",
        "## Channels by Sport",
    ]
    for k, v in sorted(health_report.get("by_sport", {}).items(), key=lambda x: x[1], reverse=True):
        lines.append(f"- {k}: {v}")

    lines.extend(["", "## Channels by Language"])
    for k, v in sorted(health_report.get("by_language", {}).items(), key=lambda x: x[1], reverse=True):
        lines.append(f"- {k}: {v}")

    lines.extend(["", "## Top Competitions"])
    for k, v in sorted(by_comp.items(), key=lambda x: x[1], reverse=True)[:15]:
        lines.append(f"- {k}: {v}")

    lines.extend(["", "## Most Reliable Sources"])
    for s in source_report.get("top_reliable_sources", [])[:10]:
        lines.append(
            f"- **{s['name']}** (`{s['source_type']}`): score `{s['reliability_score']}`, discovered `{s['channels_discovered']}`"
        )

    review = health_report.get("review_candidates", [])
    lines.extend(["", f"## Borderline Candidates Requiring Review ({len(review)})"])
    for rc in review[:15]:
        lines.append(f"- {rc['name']} (score: {rc['score']}, lang: {rc['language']})")

    if new_suggestions:
        lines.extend(["", "## Proposed New Competitions (Pending Review)"])
        for sg in new_suggestions[:10]:
            lines.append(f"- {sg['suggested_name']} (seen {sg['occurrence_count']}x)")

    if provenance_report:
        lines.extend(["", "## Source Provenance & Discovery Intelligence"])
        lines.append("### Top Contributing Providers")
        for p in provenance_report.get("top_providers", [])[:5]:
            lines.append(
                f"- `{p['name']}`: {p['working_endpoints']} working / {p['unique_endpoints']} unique "
                f"(verify: {p['verification_success_pct']}%, dup: {p['duplicate_rate']:.0%}, rej: {p['rejection_rate']:.0%})"
            )
        lines.append("")
        lines.append("### Top Contributing Repositories")
        for r in provenance_report.get("top_repositories", [])[:5]:
            lines.append(
                f"- `{r['name']}`: {r['working_endpoints']} working / {r['unique_channels']} channels "
                f"(verify: {r['verification_success_pct']}%)"
            )
        multi = provenance_report.get("multi_origin_channels", [])
        unk = provenance_report.get("unknown_provenance_channels", [])
        lines.append("")
        lines.append(f"- **Channels with multiple independent origins**: {len(multi)}")
        lines.append(f"- **Channels with unknown stream provenance**: {len(unk)}")
        lines.append(f"- **Stopped / disabled sources**: {len(provenance_report.get('stopped_sources', []))}")
        epgs = provenance_report.get("contributing_epg_sources", [])
        if epgs:
            lines.append("")
            lines.append("### Contributing EPG Sources")
            for e in epgs[:5]:
                lines.append(f"- {e['source_name']}: {e['programme_count']} programmes across {e['channel_count']} channels (`{e['status']}`)")

    content = "\n".join(lines) + "\n"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return content

