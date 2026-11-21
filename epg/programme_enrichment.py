"""Programme enrichment and now/next schedule lookup (precompiled regexes, no invented schedules)."""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Tuple

TEAMS_RE = re.compile(r"([A-Za-z0-9 .'-]{2,35})\s+(?:vs\.?|v)\s+([A-Za-z0-9 .'-]{2,35})", re.IGNORECASE)
RUGBY_MATCHUP_RE = re.compile(
    r"\b(?:ireland\s+(?:vs?\.?|against)\s+(?:south\s*africa|springboks)|"
    r"(?:south\s*africa|springboks)\s+(?:vs?\.?|against)\s+ireland)\b",
    re.IGNORECASE,
)
RUGBY_CTX_RE = re.compile(r"\b(rugby|nations|aviva|test|international|springboks)\b", re.IGNORECASE)


def _extract_teams_or_athletes(title: str) -> List[str]:
    m = TEAMS_RE.search(title)
    if m:
        return [m.group(1).strip(), m.group(2).strip()]
    return []


def _compile_matchers(sports_catalogue: Dict[str, Any]) -> Tuple[List[Tuple[re.Pattern, str, str]], List[Tuple[re.Pattern, str]]]:
    """Precompile competition (longest alias first) and sport regexes once per batch."""
    comp_entries: List[Tuple[int, str, str, str]] = []
    sport_matchers: List[Tuple[re.Pattern, str]] = []

    for sport_name, sdata in sports_catalogue.get("sports", {}).items():
        aliases = [a.lower().strip() for a in sdata.get("aliases", []) if a.strip()]
        if aliases:
            pat = re.compile(r"\b(?:" + "|".join(re.escape(a) for a in sorted(set(aliases), key=len, reverse=True)) + r")\b", re.IGNORECASE)
            sport_matchers.append((pat, sport_name))
        for _, clist in sdata.get("competition_types", {}).items():
            for comp in clist:
                cname = comp["name"]
                for alias in [cname] + comp.get("aliases", []):
                    a = alias.lower().strip()
                    if a:
                        comp_entries.append((len(a), a, sport_name, cname))

    comp_entries.sort(key=lambda x: x[0], reverse=True)
    comp_matchers = [
        (re.compile(rf"\b{re.escape(a)}\b", re.IGNORECASE), sp, cn)
        for _, a, sp, cn in comp_entries
    ]
    return comp_matchers, sport_matchers


def enrich_programmes(
    programmes: List[Dict[str, Any]],
    sports_catalogue: Dict[str, Any],
    epg_source_url: str = "",
) -> List[Dict[str, Any]]:
    """Tag parsed XMLTV programmes with sport, competition, and teams/athletes using precompiled regexes."""
    comp_matchers, sport_matchers = _compile_matchers(sports_catalogue)
    enriched: List[Dict[str, Any]] = []

    for p in programmes:
        text = f"{p.get('title', '')} {p.get('description', '')} {p.get('category', '')}"
        matched_sport = ""
        matched_comp = ""

        for pat, sp, cn in comp_matchers:
            if pat.search(text):
                matched_sport = sp
                matched_comp = cn
                break

        if not matched_sport:
            for pat, sp in sport_matchers:
                if pat.search(text):
                    matched_sport = sp
                    break

        if not matched_sport and RUGBY_MATCHUP_RE.search(text) and RUGBY_CTX_RE.search(text):
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
