"""Catalogue manager reading/writing channel_catalogue.json as single source of truth."""

import json
import os
from typing import Any, Dict, List

from catalogue.channel_catalogue import export_channel_catalogue, select_primary_channels
from database.database import DatabaseManager


class CatalogueManager:
    """Manages canonical channel_catalogue.json and active/review channel views."""

    def __init__(self, db: DatabaseManager, catalogue_path: str = "channel_catalogue.json"):
        self.db = db
        self.catalogue_path = catalogue_path

    def rebuild_catalogue(
        self,
        min_primary_score: float = 60.0,
        scarce_sport_min: float = 45.0,
        scarce_sport_min_channels: int = 3,
        scarce_sport_min_confidence: float = 0.6,
    ) -> Dict[str, Any]:
        db_channels = self.db.get_all_channels()
        return export_channel_catalogue(
            db_channels,
            output_path=self.catalogue_path,
            min_primary_score=min_primary_score,
            scarce_sport_min=scarce_sport_min,
            scarce_sport_min_channels=scarce_sport_min_channels,
            scarce_sport_min_confidence=scarce_sport_min_confidence,
        )

    def load_catalogue(self) -> Dict[str, Any]:
        if os.path.exists(self.catalogue_path):
            with open(self.catalogue_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"channels": []}

    def get_primary_channels(
        self,
        min_score: float = 60.0,
        scarce_sport_min: float = 45.0,
        scarce_sport_min_channels: int = 3,
        scarce_sport_min_confidence: float = 0.6,
    ) -> List[Dict[str, Any]]:
        cat = self.load_catalogue()
        primary, _ = select_primary_channels(
            cat.get("channels", []),
            min_primary_score=min_score,
            scarce_sport_min=scarce_sport_min,
            scarce_sport_min_channels=scarce_sport_min_channels,
            scarce_sport_min_confidence=scarce_sport_min_confidence,
            require_stream_url=True,
        )
        return primary

    def get_review_channels(
        self,
        min_score: float = 60.0,
        scarce_sport_min: float = 45.0,
        scarce_sport_min_channels: int = 3,
        scarce_sport_min_confidence: float = 0.6,
    ) -> List[Dict[str, Any]]:
        cat = self.load_catalogue()
        _, review = select_primary_channels(
            cat.get("channels", []),
            min_primary_score=min_score,
            scarce_sport_min=scarce_sport_min,
            scarce_sport_min_channels=scarce_sport_min_channels,
            scarce_sport_min_confidence=scarce_sport_min_confidence,
            require_stream_url=True,
        )
        return review

