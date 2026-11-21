"""Discovery Manager orchestrating Bandit-allocated providers, EPG-inverted targets & deduplication."""

import concurrent.futures
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from database.database import DatabaseManager
from database.models import SourceRecord
from discovery.bandit_allocator import UCB1BanditAllocator
from discovery.base_provider import (
    BaseDiscoveryProvider,
    CandidateChannel,
    CandidateEPG,
    DiscoveryResult,
    normalize_stream_url,
)
from discovery.codeberg_provider import CodebergProvider
from discovery.epg_discovery_provider import EPGDiscoveryProvider
from discovery.epg_inverted_provider import EPGInvertedDiscovery
from discovery.github_provider import GitHubProvider
from discovery.gitlab_provider import GitLabProvider
from discovery.google_dork_provider import GoogleDorkProvider
from discovery.official_broadcaster_provider import OfficialBroadcasterProvider
from discovery.public_dataset_provider import PublicDatasetProvider
from discovery.search_engine_provider import SearchEngineProvider
from discovery.sourcehut_provider import SourceHutProvider


class DiscoveryManager:
    """
    Runs enabled source adapters steered by UCB1 Bandit query allocation,
    EPG-inverted FTA discovery, and event-first surge queries.
    """

    def __init__(
        self,
        db: DatabaseManager,
        source_registry_path: str = "config/source_registry.json",
        queries_path: str = "config/discovery_queries.json",
        sports_catalogue_path: str = "config/sports_catalogue.json",
    ):
        self.db = db
        self.source_registry_path = source_registry_path
        self.queries_path = queries_path
        self.sports_catalogue_path = sports_catalogue_path

        self.registry = self._load_json(source_registry_path, {"sources": []})
        self.queries = self._load_json(queries_path, {})
        self.sports_catalogue = self._load_json(sports_catalogue_path, {"sports": {}})
        self.bandit = UCB1BanditAllocator()
        self.epg_inverted = EPGInvertedDiscovery(db)
        self._sync_registry_to_db()
        self._seed_search_queries()

    @staticmethod
    def _load_json(path: str, default: Any) -> Any:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return default

    def _sync_registry_to_db(self) -> None:
        for src in self.registry.get("sources", []):
            self.db.upsert_source(
                SourceRecord(
                    source_id=src["source_id"],
                    name=src["name"],
                    source_type=src["source_type"],
                    base_url=src["base_url"],
                    discovery_method=src["discovery_method"],
                    supported_formats=src.get("supported_formats", []),
                    last_successful_scan=src.get("last_successful_scan"),
                    last_attempted_scan=src.get("last_attempted_scan"),
                    reliability_score=float(src.get("reliability_score", 0.75)),
                    failure_count=int(src.get("failure_count", 0)),
                    channels_discovered=int(src.get("channels_discovered", 0)),
                    enabled=bool(src.get("enabled", True)),
                    country=src.get("country"),
                )
            )

    def _seed_search_queries(self) -> None:
        for plat, pdata in self.queries.get("platforms", {}).items():
            pid = f"{plat}_code_search" if plat == "github" else f"{plat}_public"
            for q in pdata.get("queries", []):
                self.db.ensure_search_query(pid, q, origin="config")

    def _build_provider(
        self,
        src: Dict[str, Any],
        queries_override: Optional[Dict[str, Any]] = None,
        mode: str = "incremental",
    ) -> Optional[BaseDiscoveryProvider]:
        sid = src.get("source_id", "")
        stype = src.get("source_type", "")
        qcfg = queries_override or self.queries

        # Deep-only search providers run exclusively during daily deep discovery
        if stype in ("deep_search", "search_engine") and mode != "deep":
            return None

        if sid == "google_dork_discovery" or stype == "deep_search":
            bc_path = os.path.join(os.path.dirname(self.sports_catalogue_path), "broadcaster_catalogue.json")
            bc_cat = self._load_json(bc_path, {}).get("broadcasters", {})
            return GoogleDorkProvider(
                src,
                qcfg,
                db=self.db,
                sports_catalogue=self.sports_catalogue,
                broadcaster_catalogue=bc_cat,
            )
        if sid == "github_code_search":
            return GitHubProvider(src, qcfg)
        if sid == "gitlab_public":
            return GitLabProvider(src, qcfg)
        if sid == "codeberg_public":
            return CodebergProvider(src, qcfg)
        if sid == "sourcehut_public":
            return SourceHutProvider(src, qcfg) if mode == "deep" else None
        if stype == "search_engine":
            return SearchEngineProvider(src, qcfg)
        if stype == "official_broadcaster":
            return OfficialBroadcasterProvider(src, qcfg)
        if stype == "public_dataset":
            return PublicDatasetProvider(src, qcfg)
        if stype == "epg_source":
            return EPGDiscoveryProvider(src, qcfg)
        return None

    def get_feedback_queries(self) -> List[str]:
        """Generate feedback queries from active verified broadcasters without mutating config."""
        extra_queries: List[str] = []
        channels = self.db.get_all_channels(status="active")
        seen: Set[str] = set()
        for ch in channels[:20]:
            broadcaster = ch.get("broadcaster") or ""
            if broadcaster and broadcaster.lower() not in seen:
                seen.add(broadcaster.lower())
                q = f'"{broadcaster}" m3u8'
                self.db.ensure_search_query("github_code_search", q, origin="feedback")
                extra_queries.append(q)
        return extra_queries[:5]

    def run_discovery(
        self,
        max_results_per_provider: int = 30,
        dataset_retrieval_limit: int = 0,
        candidate_ingestion_limit: int = 5000,
        incremental: bool = True,
        mode: str = "incremental",
    ) -> Tuple[List[CandidateChannel], List[CandidateEPG], Dict[str, Any]]:
        """
        Execute enabled providers steered by EPG-inverted targets & UCB1 Bandit allocation.
        Decouples dataset retrieval limits (0 = full dataset) from search provider limits
        and downstream verification budgets.
        """
        epg_targets = self.epg_inverted.discover_targets()
        self.get_feedback_queries()

        # Allocate top GitHub queries via UCB1 Bandit
        gh_all_queries = self.db.get_search_queries("github_code_search")
        selected_gh = self.bandit.select_queries(gh_all_queries, budget=10 if mode == "deep" else 6)
        queries_override = json.loads(json.dumps(self.queries))
        if selected_gh:
            queries_override.setdefault("platforms", {}).setdefault("github", {})["queries"] = [
                q["query_text"] for q in selected_gh
            ]

        providers: List[BaseDiscoveryProvider] = []
        for src in self.registry.get("sources", []):
            if not src.get("enabled", True):
                continue
            prov = self._build_provider(src, queries_override=queries_override, mode=mode)
            if prov:
                providers.append(prov)

        known_urls = self.db.get_known_endpoint_urls() if incremental else set()
        seen_urls: Set[str] = set()
        deduped_channels: List[CandidateChannel] = []
        epg_map: Dict[str, CandidateEPG] = {}
        stats: Dict[str, Any] = {
            "sources_scanned": len(providers),
            "provider_results": {},
            "epg_inverted": epg_targets,
            "bandit_queries_selected": len(selected_gh),
            "new_candidates": 0,
            "skipped_known": 0,
            "epgs_discovered": 0,
        }

        def run_one(p: BaseDiscoveryProvider) -> DiscoveryResult:
            try:
                # Public datasets use dataset_retrieval_limit (0 = complete dataset, no truncation)
                if isinstance(p, PublicDatasetProvider):
                    return p.discover(max_results=dataset_retrieval_limit)
                return p.discover(max_results=max_results_per_provider)
            except Exception as e:
                return DiscoveryResult(source_id=p.source_id, success=False, error=str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(run_one, providers))

        for res in results:
            self.db.record_source_scan(
                source_id=res.source_id,
                success=res.success,
                channels_found=len(res.channels),
            )
            stats["provider_results"][res.source_id] = {
                "success": res.success,
                "channels_found": len(res.channels),
                "epgs_found": len(res.epg_urls),
                "error": res.error,
            }
            if res.source_id == "github_code_search":
                reward = min(1.0, len(res.channels) / 20.0) if res.success else 0.0
                for sq in selected_gh:
                    self.db.record_query_reward(sq["query_id"], reward)

            for ch in res.channels:
                norm_url = normalize_stream_url(ch.stream_url)
                if not norm_url:
                    continue
                # Always preserve provenance even when URL is already known or duplicated!
                self.db.record_endpoint_provenance(
                    endpoint_url=norm_url,
                    provider_id=res.source_id,
                    source_id=ch.source_id,
                    repo_name=ch.repo_name,
                    repo_url=ch.repo_url,
                    file_path=ch.file_path,
                    playlist_url=ch.source_url,
                    record_ref=ch.record_ref,
                    discovery_query=ch.discovery_query,
                )
                if candidate_ingestion_limit > 0 and len(deduped_channels) >= candidate_ingestion_limit:
                    continue
                if norm_url in known_urls:
                    stats["skipped_known"] += 1
                    continue
                if norm_url in seen_urls:
                    continue
                seen_urls.add(norm_url)
                ch.stream_url = norm_url
                self.db.record_candidate_trace(
                    url=norm_url,
                    stage="deduplicated",
                    status="passed",
                    provider_id=res.source_id,
                    repo_name=ch.repo_name,
                    playlist_url=ch.source_url,
                )
                deduped_channels.append(ch)

            for epg in res.epg_urls:
                if epg.url and epg.url not in epg_map:
                    epg_map[epg.url] = epg

        # Record missing-channel investigations for EPG-confirmed targets lacking verified streams
        providers_searched = [p.source_id for p in providers]
        self.epg_inverted.record_missing_channel_investigations(
            discovered_candidates=deduped_channels,
            providers_searched=providers_searched,
        )

        stats["new_candidates"] = len(deduped_channels)
        stats["epgs_discovered"] = len(epg_map)
        return deduped_channels, list(epg_map.values()), stats
