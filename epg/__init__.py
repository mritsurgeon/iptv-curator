"""EPG discovery, XMLTV parsing, matching & programme intelligence package."""

from epg.epg_fetcher import EPGFetcher
from epg.epg_matcher import EPGMatcher
from epg.event_mapper import EventMapper
from epg.programme_enrichment import enrich_programmes, get_now_and_next
from epg.xmltv_parser import format_display_time, parse_xmltv, parse_xmltv_timestamp

__all__ = [
    "EPGFetcher",
    "EPGMatcher",
    "EventMapper",
    "parse_xmltv",
    "parse_xmltv_timestamp",
    "format_display_time",
    "enrich_programmes",
    "get_now_and_next",
]

