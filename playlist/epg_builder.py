"""Consolidated XMLTV sports_epg.xml builder deriving from canonical catalogue & SQLite."""

from datetime import datetime, timezone
import os
from typing import Any, Dict, List
import xml.etree.ElementTree as ET

from database.database import DatabaseManager


def _iso_to_xmltv(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")
    except Exception:
        return ""


def build_sports_epg(
    db: DatabaseManager,
    channels: List[Dict[str, Any]],
    output_path: str = "sports_epg.xml",
) -> Dict[str, int]:
    """
    Build sports_epg.xml for all active channels in the canonical catalogue
    using only confirmed programme schedules stored in SQLite (no invented schedules).
    """
    tv = ET.Element(
        "tv",
        {
            "generator-info-name": "IPTV-Curator-2.0",
            "date": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S +0000"),
        },
    )

    seen_tvg_ids = set()
    channel_count = 0
    programme_count = 0

    for ch in channels:
        epg = ch.get("epg", {})
        tvg_id = epg.get("tvg_id") or ch["channel_id"]
        if tvg_id in seen_tvg_ids:
            continue
        seen_tvg_ids.add(tvg_id)

        ch_el = ET.SubElement(tv, "channel", {"id": tvg_id})
        dn = ET.SubElement(ch_el, "display-name")
        dn.text = ch["name"]
        if epg.get("tvg_logo"):
            ET.SubElement(ch_el, "icon", {"src": epg["tvg_logo"]})
        channel_count += 1

        progs = db.get_programmes_for_channel(tvg_id)
        for p in progs:
            start_str = _iso_to_xmltv(p["start_utc"])
            stop_str = _iso_to_xmltv(p["stop_utc"])
            if not start_str or not stop_str:
                continue
            p_el = ET.SubElement(
                tv,
                "programme",
                {"start": start_str, "stop": stop_str, "channel": tvg_id},
            )
            t_el = ET.SubElement(p_el, "title", {"lang": "en"})
            t_el.text = p["title"]
            if p.get("description"):
                d_el = ET.SubElement(p_el, "desc", {"lang": "en"})
                d_el.text = p["description"]
            if p.get("sport"):
                c_el = ET.SubElement(p_el, "category", {"lang": "en"})
                c_el.text = p["sport"]
            programme_count += 1

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    tree = ET.ElementTree(tv)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return {"channels": channel_count, "programmes": programme_count}

