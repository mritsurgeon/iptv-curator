"""Programme enrichment and now/next schedule lookup (no invented schedules)."""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List


def _extract_teams_or_athletes(title: str) -> List[str]:
    m = re.search(r"([A-Za-z0-9 .'-]{2,35})\s+(?:vs\.?|v)\s+([A-Za-z0-9 .'-]{2,35})", title, re.IGNORECASE)
    if m:
        return [m.group(1).strip(), m.group(2).strip()]
    return []


def enrich_programmes(
    programmes: List[Dict[str, Any]],
    sports_catalogue: Dict[str, Any],
    epg_source_url: str = "",
) -> List[Dict[str, Any]]:
    """Tag parsed XMLTV programmes with sport, competition, and teams/athletes."""
    enriched: List[Dict[str, Any]] = []
    sports_dict = sports_catalogue.get("sports", {})

    for p in programmes:
        text = f"{p.get('title', '')} {p.get('description', '')} {p.get('category', '')}".lower()
        matched_sport = ""
        matched_comp = ""
        best_len = 0

        for sport_name, sdata in sports_dict.items():
            for _, clist in sdata.get("competition_types", {}).items():
                for comp in clist:
                    for alias in [comp["name"]] + comp.get("aliases", []):
                        if len(alias) > best_len and re.search(rf"\b{re.escape(alias.lower())}\b", text):
                            matched_sport = sport_name
                            matched_comp = comp["name"]
                            best_len = len(alias)
            if not matched_sport:
                for alias in sdata.get("aliases", []):
                    if re.search(rf"\b{re.escape(alias.lower())}\b", text):
                        matched_sport = sport_name
                        break

        if not matched_sport and re.search(
            r"\b(?:ireland\s+(?:vs?\.?|against)\s+(?:south\s*africa|springboks)|"
            r"(?:south\s*africa|springboks)\s+(?:vs?\.?|against)\s+ireland)\b",
            text,
        ) and re.search(r"\b(rugby|nations|aviva|test|international|springboks)\b", text):
            matched_sport = "Rugby"
            matched_comp = "Nations Championship"

        item = dict(p)
        item["sport"] = matched_sport
        item["competition"] = matched_comp
        item["teams_or_athletes"] = _extract_teams_or_athletes(p.get("title", ""))
        item["epg_source_url"] = epg_source_url
        enriched.append(item)
    return enriched


def get_now_and_next(
    programmes: List[Dict[str, Any]],
    now_utc: datetime = None,
) -> Dict[str, Any]:
    """Return currently playing, next programme, and upcoming sports events from confirmed programmes."""
    now = now_utc or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    current = None
    nxt = None
    upcoming = []

    for p in programmes:
        start = p.get("start_utc", "")
        stop = p.get("stop_utc", "")
        if start <= now_iso < stop and current is None:
            current = p
        elif start > now_iso:
            if nxt is None:
                nxt = p
            if p.get("sport") or p.get("competition"):
                upcoming.append(p)

    return {
        "current_programme": current,
        "next_programme": nxt,
        "upcoming_sports_events": upcoming[:10],
    }
