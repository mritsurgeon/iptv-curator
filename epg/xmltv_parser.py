"""Streaming XMLTV parser (ET.iterparse), validator & timezone helper (UTC internal, Africa/Johannesburg display)."""

from datetime import datetime, timedelta, timezone
import gzip
import io
import re
from typing import Any, Callable, Dict, List, Optional
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("Africa/Johannesburg")


def parse_xmltv_timestamp(ts: str) -> Optional[datetime]:
    """Parse XMLTV timestamp like '20261121153000 +0200' or '20261121153000' into UTC datetime."""
    if not ts:
        return None
    s = ts.strip()
    m = re.match(r"^(\d{14})(?:\s*([+-]\d{4}))?", s)
    if not m:
        return None
    base, offset = m.group(1), m.group(2)
    try:
        dt = datetime.strptime(base, "%Y%m%d%H%M%S")
        if offset:
            sign = 1 if offset[0] == "+" else -1
            oh = int(offset[1:3])
            om = int(offset[3:5])
            tz = timezone(timedelta(hours=sign * oh, minutes=sign * om))
            dt = dt.replace(tzinfo=tz).astimezone(timezone.utc)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def format_display_time(utc_iso: str, tz_name: str = "Africa/Johannesburg") -> str:
    """Convert internal UTC ISO timestamp to display timezone (default Africa/Johannesburg)."""
    try:
        dt = datetime.fromisoformat(utc_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return utc_iso


def parse_xmltv(
    raw_bytes: bytes,
    stale_hours: int = 48,
    programme_filter: Optional[Callable[[Dict[str, str]], bool]] = None,
) -> Dict[str, Any]:
    """
    Stream-parse and validate XMLTV bytes (plain XML or gzipped XML) using ET.iterparse
    with elem.clear() for constant-memory parsing on large (100+ MB) XMLTV feeds.
    Also computes per-channel total programme counts across the entire feed.
    """
    data = raw_bytes
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except Exception as e:
            return {
                "valid": False,
                "channels": {},
                "programmes": [],
                "channel_totals": {},
                "is_stale": True,
                "latest_stop_utc": None,
                "error": f"gzip_error: {e}",
            }

    channels: Dict[str, Dict[str, Any]] = {}
    programmes: List[Dict[str, str]] = []
    channel_totals: Dict[str, int] = {}
    latest_stop: Optional[datetime] = None
    now_utc = datetime.now(timezone.utc)
    saw_tv_root = False

    try:
        stream = io.BytesIO(data)
        for event, elem in ET.iterparse(stream, events=("start", "end")):
            if event == "start" and elem.tag == "tv":
                saw_tv_root = True
                continue
            if event != "end":
                continue

            if elem.tag == "channel":
                cid = (elem.get("id") or "").strip()
                if cid:
                    names = [
                        dn.text.strip()
                        for dn in elem.findall("display-name")
                        if dn.text and dn.text.strip()
                    ]
                    icon_el = elem.find("icon")
                    icon = icon_el.get("src", "").strip() if icon_el is not None else ""
                    channels[cid] = {"display_names": names, "icon": icon}
                elem.clear()
            elif elem.tag == "programme":
                cid = (elem.get("channel") or "").strip()
                start_dt = parse_xmltv_timestamp(elem.get("start", ""))
                stop_dt = parse_xmltv_timestamp(elem.get("stop", ""))
                if cid and start_dt:
                    if not stop_dt:
                        stop_dt = start_dt + timedelta(hours=1)
                    if latest_stop is None or stop_dt > latest_stop:
                        latest_stop = stop_dt

                    channel_totals[cid] = channel_totals.get(cid, 0) + 1
                    title_el = elem.find("title")
                    desc_el = elem.find("desc")
                    cat_el = elem.find("category")
                    title = title_el.text.strip() if title_el is not None and title_el.text else ""
                    if title:
                        prog = {
                            "channel_tvg_id": cid,
                            "title": title,
                            "description": desc_el.text.strip() if desc_el is not None and desc_el.text else "",
                            "category": cat_el.text.strip() if cat_el is not None and cat_el.text else "",
                            "start_utc": start_dt.isoformat(),
                            "stop_utc": stop_dt.isoformat(),
                        }
                        if programme_filter is None or programme_filter(prog):
                            programmes.append(prog)
                elem.clear()
    except Exception as e:
        return {
            "valid": False,
            "channels": {},
            "programmes": [],
            "channel_totals": {},
            "is_stale": True,
            "latest_stop_utc": None,
            "error": f"xml_parse_error: {e}",
        }

    if not saw_tv_root:
        return {
            "valid": False,
            "channels": {},
            "programmes": [],
            "channel_totals": {},
            "is_stale": True,
            "latest_stop_utc": None,
            "error": "missing_tv_root",
        }

    is_stale = True
    if latest_stop is not None:
        is_stale = latest_stop < (now_utc - timedelta(hours=stale_hours))

    return {
        "valid": True,
        "channels": channels,
        "programmes": programmes,
        "channel_totals": channel_totals,
        "is_stale": is_stale,
        "latest_stop_utc": latest_stop.isoformat() if latest_stop else None,
        "error": "",
    }
