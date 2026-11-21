"""Tests for XMLTV parsing, EPG matching, programme enrichment & event mapping."""

from datetime import datetime, timedelta, timezone
import gzip
import os
import tempfile
import unittest

from database.database import DatabaseManager
from database.models import ProgrammeRecord
from epg.epg_matcher import EPGMatcher
from epg.event_mapper import EventMapper
from epg.programme_enrichment import enrich_programmes, get_now_and_next
from epg.xmltv_parser import format_display_time, parse_xmltv


def _build_sample_xmltv(offset_hours: int = 0) -> bytes:
    now = datetime.now(timezone.utc) + timedelta(hours=offset_hours)
    s1 = now.strftime("%Y%m%d%H%M%S +0000")
    e1 = (now + timedelta(hours=2)).strftime("%Y%m%d%H%M%S +0000")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="RUGBY.za">
    <display-name>SuperSport Rugby</display-name>
    <icon src="https://example.com/rugby.png" />
  </channel>
  <programme start="{s1}" stop="{e1}" channel="RUGBY.za">
    <title>United Rugby Championship: Bulls vs Stormers</title>
    <desc>Live URC derby match</desc>
    <category>Rugby</category>
  </programme>
</tv>"""
    return xml.encode("utf-8")


class TestEPGAndXMLTV(unittest.TestCase):
    def test_parse_xmltv_plain_and_gzip_and_stale(self):
        fresh = parse_xmltv(_build_sample_xmltv(offset_hours=0), stale_hours=48)
        self.assertTrue(fresh["valid"])
        self.assertFalse(fresh["is_stale"])
        self.assertIn("RUGBY.za", fresh["channels"])
        self.assertEqual(len(fresh["programmes"]), 1)

        gz_bytes = gzip.compress(_build_sample_xmltv(offset_hours=-100))
        stale = parse_xmltv(gz_bytes, stale_hours=48)
        self.assertTrue(stale["valid"])
        self.assertTrue(stale["is_stale"])

    def test_utc_to_johannesburg_display(self):
        disp = format_display_time("2026-11-21T12:00:00+00:00", "Africa/Johannesburg")
        self.assertIn("14:00", disp)
        self.assertIn("SAST", disp)

    def test_epg_matcher_and_event_mapper(self):
        fresh = parse_xmltv(_build_sample_xmltv(offset_hours=0))
        idx = {
            "RUGBY.za": {
                "display_names": ["SuperSport Rugby"],
                "icon": "https://example.com/rugby.png",
                "epg_source": "https://example.com/epg.xml",
                "is_stale": False,
            }
        }
        matcher = EPGMatcher(idx)
        m1 = matcher.match("SuperSport Rugby", tvg_id="RUGBY.za")
        self.assertEqual(m1["status"], "matched")
        self.assertEqual(m1["match_method"], "exact_id")
        self.assertGreater(m1["confidence"], 0.9)

        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseManager(os.path.join(tmp, "epg.db"))
            import json
            with open("config/sports_catalogue.json", "r", encoding="utf-8") as f:
                cat = json.load(f)
            enriched = enrich_programmes(fresh["programmes"], cat, "https://example.com/epg.xml")
            self.assertEqual(enriched[0]["sport"], "Rugby")
            self.assertEqual(enriched[0]["competition"], "United Rugby Championship")
            self.assertEqual(enriched[0]["teams_or_athletes"], ["Bulls", "Stormers"])

            db.upsert_programmes([
                ProgrammeRecord(
                    channel_tvg_id=p["channel_tvg_id"],
                    title=p["title"],
                    start_utc=p["start_utc"],
                    stop_utc=p["stop_utc"],
                    description=p["description"],
                    sport=p["sport"],
                    competition=p["competition"],
                    teams_or_athletes=p["teams_or_athletes"],
                )
                for p in enriched
            ])
            nn = get_now_and_next(db.get_programmes_for_channel("RUGBY.za"))
            self.assertIsNotNone(nn["current_programme"])

            ev_map = EventMapper(db).build_event_index([
                {
                    "channel_id": "ss_rugby",
                    "name": "SuperSport Rugby",
                    "tvg_id": "RUGBY.za",
                    "status": "active",
                    "primary_language": "English",
                }
            ])
            self.assertIn("Rugby", ev_map)
            self.assertIn("United Rugby Championship", ev_map["Rugby"])


if __name__ == "__main__":
    unittest.main()

