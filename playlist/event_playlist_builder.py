"""Client-native dynamic live_now.m3u and upcoming_events.m3u builders (SAST display)."""

from datetime import datetime, timedelta, timezone
import os
from typing import Any, Dict, List, Optional

from database.database import DatabaseManager
from epg.xmltv_parser import format_display_time
from playlist.master_builder import DEFAULT_EPG_URL, format_extinf_line


def build_event_playlists(
    db: DatabaseManager,
    channels: List[Dict[str, Any]],
    base_dir: str = ".",
    display_tz: str = "Africa/Johannesburg",
    epg_url: str = DEFAULT_EPG_URL,
    now_dt: Optional[datetime] = None,
) -> Dict[str, int]:
    """
    Generate standard-IPTV-client-compatible:
      - live_now.m3u: verified channels with a confirmed live or <2h target-sport event
      - upcoming_events.m3u: verified channels with confirmed target-sport events in next 48h
    Never invents schedules; strictly uses confirmed XMLTV programmes.
    """
    now = now_dt or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    live_cutoff_iso = (now + timedelta(hours=2)).isoformat()

    ch_by_id = {c["channel_id"]: c for c in channels}
    ch_by_tvg = {c.get("epg", {}).get("tvg_id"): c for c in channels if c.get("epg", {}).get("tvg_id")}

    upcoming = db.get_upcoming_sporting_events(hours_ahead=48, now_dt=now)

    live_lines = [
        f'#EXTM3U url-tvg="{epg_url}" x-tvg-url="{epg_url}"\n',
        "#PLAYLIST:Live Now & Starting Soon Sports (Confirmed EPG)\n\n",
    ]
    upcoming_lines = [
        f'#EXTM3U url-tvg="{epg_url}" x-tvg-url="{epg_url}"\n',
        "#PLAYLIST:Upcoming 48h Sports Events (Confirmed EPG)\n\n",
    ]

    live_seen = set()
    upcoming_seen = set()
    live_count = 0
    upcoming_count = 0

    for ev in upcoming:
        ch = ch_by_id.get(ev.get("channel_id", "")) or ch_by_tvg.get(ev.get("epg_channel_id", ""))
        if not ch:
            continue
        url = ch.get("stream", {}).get("url", "")
        if not url:
            continue

        disp_time = format_display_time(ev["start_utc"], display_tz)
        short_time = disp_time.split(" ")[1] + " " + disp_time.split(" ")[2] if len(disp_time.split(" ")) >= 3 else disp_time
        sport = ev.get("sport", "Sports")
        comp = ev.get("competition", "General")
        title = ev.get("event_title", "")

        # 1. Live Now / Starting within 2h
        is_live_or_soon = ev["start_utc"] <= live_cutoff_iso and ev["stop_utc"] >= now_iso
        if is_live_or_soon and ch["channel_id"] not in live_seen:
            live_seen.add(ch["channel_id"])
            tag = "LIVE" if ev["start_utc"] <= now_iso else short_time
            ch_copy = dict(ch)
            ch_copy["name"] = f"{ch['name']} | {tag}: {title}"
            ext = format_extinf_line(ch_copy, f"LIVE & SOON - {sport}")
            live_lines.append(f"{ext}\n{url}\n\n")
            live_count += 1

        # 2. Upcoming 48h Events
        ev_key = (ch["channel_id"], ev["event_id"])
        if ev_key not in upcoming_seen:
            upcoming_seen.add(ev_key)
            ch_copy = dict(ch)
            ch_copy["name"] = f"{ch['name']} | {disp_time}: {title}"
            ext = format_extinf_line(ch_copy, f"{sport} - {comp}")
            upcoming_lines.append(f"{ext}\n{url}\n\n")
            upcoming_count += 1

    with open(os.path.join(base_dir, "live_now.m3u"), "w", encoding="utf-8") as f:
        f.writelines(live_lines)
    with open(os.path.join(base_dir, "upcoming_events.m3u"), "w", encoding="utf-8") as f:
        f.writelines(upcoming_lines)

    return {"live_now.m3u": live_count, "upcoming_events.m3u": upcoming_count}

