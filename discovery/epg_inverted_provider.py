"""EPG-First & Event-First discovery engine + Missing-Channel Investigation tracker."""

from typing import Any, Dict, List, Optional, Set

from classification.channel_enrichment import extract_broadcaster
from database.database import DatabaseManager
from discovery.base_provider import CandidateChannel
from playlist_parser import normalize_name


class EPGInvertedDiscovery:
    """
    Uses indexed XMLTV channels (`epg_channels`) and confirmed sports programmes
    (`programmes` / `sporting_events`) to:
      1. Identify channels airing target sports (even FTA channels without 'Sport' in their name).
      2. Produce structured discovery targets: competition, event, broadcaster, channel, tvg_id, search_terms.
      3. Register targeted `epg_inverted` and `event_surge` search queries in SQLite.
      4. Record `missing_channel_investigations` whenever an EPG-confirmed channel has no verified stream.
    Note: EPG presence confirms broadcast schedule only — never stream availability or free access.
    """

    def __init__(self, db: DatabaseManager):
        self.db = db

    def discover_targets(self, min_duty_cycle: float = 0.01) -> Dict[str, Any]:
        epg_sports_channels = self.db.get_epg_channels_with_sports(min_duty_cycle=min_duty_cycle)
        upcoming_events = self.db.get_upcoming_sporting_events(hours_ahead=48)

        targets: List[Dict[str, Any]] = []
        discovered_names: List[str] = []
        seen: Set[str] = set()
        queries_registered = 0

        for ch in epg_sports_channels[:40]:
            tvg_id = ch["epg_channel_id"]
            names = ch.get("display_names") or [tvg_id]
            primary = names[0].strip()
            if not primary or primary.lower() in seen:
                continue
            seen.add(primary.lower())
            discovered_names.append(primary)

            progs = self.db.get_programmes_for_channel(tvg_id)
            sports_progs = [p for p in progs if p.get("sport")]
            top_prog = sports_progs[0] if sports_progs else {}
            comp = top_prog.get("competition", "")
            ev_title = top_prog.get("title", "")
            broadcaster = extract_broadcaster(primary)

            search_terms = [f'"{primary}" m3u8']
            if comp:
                search_terms.append(f'"{comp}" "{primary}" m3u')
            if broadcaster and broadcaster.lower() != primary.lower():
                search_terms.append(f'"{broadcaster}" m3u8')

            for term in search_terms[:2]:
                self.db.ensure_search_query(
                    provider_id="github_code_search",
                    query_text=term,
                    origin="epg_inverted",
                    sport_hint=top_prog.get("sport", ""),
                )
                queries_registered += 1

            targets.append(
                {
                    "tvg_id": tvg_id,
                    "channel": primary,
                    "broadcaster": broadcaster,
                    "competition": comp,
                    "event": ev_title,
                    "sports_duty_cycle": ch.get("sports_duty_cycle", 0.0),
                    "has_verified_stream": self.db.has_verified_endpoint_for_tvg(tvg_id),
                    "search_terms": search_terms,
                }
            )

        event_channel_ids: Set[str] = set()
        event_epg_ids: Set[str] = set()
        event_competitions: Set[str] = set()

        for ev in upcoming_events:
            if ev.get("channel_id"):
                event_channel_ids.add(ev["channel_id"])
            if ev.get("epg_channel_id"):
                event_epg_ids.add(ev["epg_channel_id"])
            comp = ev.get("competition", "")
            if comp and comp not in event_competitions:
                event_competitions.add(comp)
                self.db.ensure_search_query(
                    provider_id="github_code_search",
                    query_text=f'"{comp}" m3u8',
                    origin="event_surge",
                    sport_hint=ev.get("sport", ""),
                )
                queries_registered += 1

        return {
            "epg_sports_channel_count": len(epg_sports_channels),
            "discovered_channel_names": discovered_names,
            "targets": targets,
            "upcoming_events_48h": len(upcoming_events),
            "priority_channel_ids": list(event_channel_ids),
            "priority_epg_ids": list(event_epg_ids),
            "queries_registered": queries_registered,
        }

    def record_missing_channel_investigations(
        self,
        discovered_candidates: List[CandidateChannel],
        providers_searched: List[str],
        rejection_map: Optional[Dict[str, List[str]]] = None,
    ) -> int:
        """
        For every EPG-confirmed sports channel lacking a verified active stream endpoint,
        record/update an entry in `missing_channel_investigations`.
        """
        rejection_map = rejection_map or {}
        targets = self.discover_targets(min_duty_cycle=0.01)["targets"]
        norm_cands = {normalize_name(c.name): c for c in discovered_candidates}
        tvg_cands = {c.tvg_id: c for c in discovered_candidates if c.tvg_id}

        recorded = 0
        for t in targets:
            tvg_id = t["tvg_id"]
            if t["has_verified_stream"]:
                self.db.upsert_missing_channel_investigation(
                    epg_channel_id=tvg_id,
                    channel_name=t["channel"],
                    broadcaster=t["broadcaster"],
                    relevant_event=t["event"],
                    competition=t["competition"],
                    providers_searched=providers_searched,
                    candidates_found=1,
                    rejection_reasons=[],
                    needs_investigation=False,
                )
                continue

            matched_cand = tvg_cands.get(tvg_id) or norm_cands.get(normalize_name(t["channel"]))
            cands_found = 1 if matched_cand else 0
            reasons = rejection_map.get(tvg_id, [] if matched_cand else ["no_candidate_endpoint_found"])

            self.db.upsert_missing_channel_investigation(
                epg_channel_id=tvg_id,
                channel_name=t["channel"],
                broadcaster=t["broadcaster"],
                relevant_event=t["event"],
                competition=t["competition"],
                providers_searched=providers_searched,
                candidates_found=cands_found,
                rejection_reasons=reasons,
                needs_investigation=True,
            )
            recorded += 1
        return recorded
