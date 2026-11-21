"""Conditional streaming EPG fetcher computing Sports Duty Cycle & FTS5 index."""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Set

from database.database import DatabaseManager
from database.models import EPGSourceRecord, ProgrammeRecord
from discovery.base_provider import http_get_conditional
from epg.programme_enrichment import enrich_programmes
from epg.xmltv_parser import parse_xmltv


RADIO_CHANNEL_RE = re.compile(
    r"(?:\b(?:radio|fm|am)\b|^bbc\.r\d|^bbc\.r\.|\.radio\.)",
    re.IGNORECASE,
)


def is_radio_only_channel(channel_id: str, display_names: List[str]) -> bool:
    """Detect radio-only EPG channels (e.g. BBC.R.Ulster.uk, RTÉ.Radio.1.uk) so they are excluded from TV sports."""
    blob = f"{channel_id} {' '.join(display_names)}"
    return bool(RADIO_CHANNEL_RE.search(blob))


def _compile_sports_prefilter(sports_catalogue: Dict[str, Any]) -> re.Pattern:
    terms: List[str] = ["sport", "live", "match", "cup", "league", "championship", "open", "tour", "test", "t20"]
    for sdata in sports_catalogue.get("sports", {}).values():
        terms.extend(sdata.get("aliases", []))
        for clist in sdata.get("competition_types", {}).values():
            for comp in clist:
                terms.append(comp["name"])
                terms.extend(comp.get("aliases", []))
    escaped = [re.escape(t.lower()) for t in set(terms) if len(t.strip()) >= 2]
    return re.compile("|".join(escaped), re.IGNORECASE)


class EPGFetcher:
    """
    Conditionally fetches XMLTV feeds (ETag / If-Modified-Since / SHA-256),
    stream-parses without alphabetical truncation, computes per-channel
    sports_duty_cycle, persists epg_channels + programmes, and rebuilds FTS5.
    """

    def __init__(self, db: DatabaseManager, stale_hours: int = 48):
        self.db = db
        self.stale_hours = stale_hours

    def fetch_and_store(
        self,
        epg_urls: List[Dict[str, str]],
        sports_catalogue: Dict[str, Any],
        active_tvg_ids: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        channels_index: Dict[str, Dict[str, Any]] = {}
        active_ids = active_tvg_ids or set()
        prefilter_re = _compile_sports_prefilter(sports_catalogue)

        stats = {
            "sources_checked": len(epg_urls),
            "active_sources": 0,
            "not_modified_sources": 0,
            "stale_sources": 0,
            "failed_sources": 0,
            "programmes_stored": 0,
            "epg_channels_indexed": 0,
        }

        for item in epg_urls:
            url = item.get("url", "")
            if not url:
                continue
            name = item.get("source_name", "")
            fmt = item.get("fmt", "xml.gz" if url.lower().endswith(".gz") else "xml")
            cache = self.db.get_epg_source_cache(url)

            try:
                resp = http_get_conditional(
                    url,
                    etag=cache.get("etag", ""),
                    last_modified=cache.get("last_modified", ""),
                    prev_sha256=cache.get("content_sha256", ""),
                    timeout=15,
                    retries=1,
                )
                if resp["not_modified"]:
                    stats["not_modified_sources"] += 1
                    stats["active_sources"] += 1
                    continue

                def keep_prog(p: Dict[str, str]) -> bool:
                    if p["channel_tvg_id"] in active_ids:
                        return True
                    blob = f"{p['title']} {p['description']} {p['category']}"
                    return bool(prefilter_re.search(blob))

                parsed = parse_xmltv(
                    resp["data"],
                    stale_hours=self.stale_hours,
                    programme_filter=keep_prog,
                )
                if not parsed["valid"]:
                    stats["failed_sources"] += 1
                    self.db.upsert_epg_source(
                        EPGSourceRecord(
                            url=url, source_name=name, fmt=fmt, status="invalid",
                            last_checked=now, is_stale=True, reliability_score=0.2,
                        )
                    )
                    continue

                is_stale = parsed["is_stale"]
                if is_stale:
                    stats["stale_sources"] += 1
                else:
                    stats["active_sources"] += 1

                enriched = enrich_programmes(parsed["programmes"], sports_catalogue, epg_source_url=url)

                # Compute per-channel sports_duty_cycle across the entire XMLTV feed
                sports_counts: Dict[str, int] = {}
                for p in enriched:
                    if p.get("sport"):
                        cid = p["channel_tvg_id"]
                        sports_counts[cid] = sports_counts.get(cid, 0) + 1

                epg_ch_rows: List[Dict[str, Any]] = []
                radio_ids: Set[str] = set()
                totals = parsed.get("channel_totals", {})
                for cid, meta in parsed["channels"].items():
                    dnames = meta.get("display_names", [])
                    if is_radio_only_channel(cid, dnames):
                        radio_ids.add(cid)
                        continue
                    tot = max(totals.get(cid, 0), sports_counts.get(cid, 0))
                    sp_cnt = sports_counts.get(cid, 0)
                    duty = round(sp_cnt / tot, 4) if tot > 0 else 0.0
                    epg_ch_rows.append(
                        {
                            "epg_channel_id": cid,
                            "epg_source_url": url,
                            "display_names": dnames,
                            "icon_url": meta.get("icon", ""),
                            "sports_programme_count": sp_cnt,
                            "total_programme_count": tot,
                            "sports_duty_cycle": duty,
                        }
                    )
                    if cid not in channels_index:
                        channels_index[cid] = {
                            "display_names": dnames,
                            "icon": meta.get("icon", ""),
                            "epg_source": url,
                            "is_stale": is_stale,
                            "sports_duty_cycle": duty,
                        }

                stats["epg_channels_indexed"] += self.db.upsert_epg_channels(epg_ch_rows)

                records = [
                    ProgrammeRecord(
                        channel_tvg_id=p["channel_tvg_id"],
                        title=p["title"],
                        start_utc=p["start_utc"],
                        stop_utc=p["stop_utc"],
                        description=p.get("description", ""),
                        sport=p.get("sport", ""),
                        competition=p.get("competition", ""),
                        teams_or_athletes=p.get("teams_or_athletes", []),
                        source_type="confirmed_broadcaster",
                        epg_source_url=url,
                    )
                    for p in enriched
                    if p["channel_tvg_id"] not in radio_ids
                    and (p.get("sport") or p["channel_tvg_id"] in active_ids)
                ]
                stats["programmes_stored"] += self.db.upsert_programmes(records, rebuild_fts=True)

                self.db.upsert_epg_source(
                    EPGSourceRecord(
                        url=url,
                        source_name=name,
                        fmt=fmt,
                        status="stale" if is_stale else "active",
                        last_checked=now,
                        last_updated=parsed.get("latest_stop_utc") or now,
                        channel_count=len(parsed["channels"]),
                        programme_count=len(records),
                        is_stale=is_stale,
                        reliability_score=0.5 if is_stale else 0.95,
                    ),
                    etag=resp.get("etag", ""),
                    last_modified=resp.get("last_modified", ""),
                    content_sha256=resp.get("sha256", ""),
                )
            except Exception:
                stats["failed_sources"] += 1
                self.db.upsert_epg_source(
                    EPGSourceRecord(
                        url=url, source_name=name, fmt=fmt, status="unreachable",
                        last_checked=now, is_stale=True, reliability_score=0.1,
                    )
                )

        return {"channels_index": channels_index, "stats": stats}
