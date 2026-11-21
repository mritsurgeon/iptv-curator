"""Event-to-channel intelligence layer persisting confirmed sporting_events in SQLite."""

import hashlib
from typing import Any, Dict, List

from database.database import DatabaseManager
from epg.xmltv_parser import format_display_time


class EventMapper:
    """
    Connects Sport -> Competition -> Event -> Programme -> Channel.
    Persists confirmed XMLTV events into `sporting_events` and strictly distinguishes
    confirmed schedules from inferred competition associations.
    """

    def __init__(self, db: DatabaseManager, display_tz: str = "Africa/Johannesburg"):
        self.db = db
        self.display_tz = display_tz

    def sync_confirmed_events(self, channels: List[Dict[str, Any]]) -> int:
        """Persist confirmed sports programmes from active channels into `sporting_events`."""
        events_to_store: List[Dict[str, Any]] = []
        for ch in channels:
            if ch.get("status") != "active":
                continue
            tvg_id = ch.get("tvg_id") or ch.get("epg", {}).get("tvg_id", "")
            if not tvg_id:
                continue
            progs = self.db.get_programmes_for_channel(tvg_id)
            for p in progs:
                sport = p.get("sport")
                if not sport:
                    continue
                comp = p.get("competition") or "General"
                eid = hashlib.sha1(
                    f"{sport}|{comp}|{p['title']}|{p['start_utc']}|{tvg_id}".encode("utf-8")
                ).hexdigest()[:20]
                events_to_store.append(
                    {
                        "event_id": eid,
                        "sport": sport,
                        "competition": comp,
                        "event_title": p["title"],
                        "teams": p.get("teams_or_athletes", []),
                        "start_utc": p["start_utc"],
                        "stop_utc": p["stop_utc"],
                        "channel_id": ch["channel_id"],
                        "epg_channel_id": tvg_id,
                        "evidence_type": "confirmed_schedule",
                        "confidence": 0.95,
                    }
                )
        return self.db.upsert_sporting_events(events_to_store)

    def build_event_index(self, channels: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build structured Sport -> Competition -> Event -> Channels map from confirmed programmes."""
        self.sync_confirmed_events(channels)
        events: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

        for ch in channels:
            if ch.get("status") != "active":
                continue
            tvg_id = ch.get("tvg_id") or ch.get("epg", {}).get("tvg_id", "")
            if not tvg_id:
                continue
            progs = self.db.get_programmes_for_channel(tvg_id)
            for p in progs:
                sport = p.get("sport")
                if not sport:
                    continue
                comp_key = p.get("competition") or "General"
                events.setdefault(sport, {}).setdefault(comp_key, []).append(
                    {
                        "event": p["title"],
                        "start_utc": p["start_utc"],
                        "stop_utc": p["stop_utc"],
                        "display_start": format_display_time(p["start_utc"], self.display_tz),
                        "channel_id": ch["channel_id"],
                        "channel_name": ch["name"],
                        "tvg_id": tvg_id,
                        "language": ch.get("primary_language") or ch.get("language", {}).get("primary", "Unknown"),
                        "stream_status": ch.get("status", "active"),
                        "evidence_type": "confirmed_schedule",
                    }
                )
        return events
