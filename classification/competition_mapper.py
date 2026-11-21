"""Maps channels and events to the hierarchical Sport -> Competition Type -> Competition structure."""

import json
import os
from typing import Any, Dict, List


class CompetitionMapper:
    """Resolves competition hierarchies and playlist groupings from config/sports_catalogue.json."""

    def __init__(self, catalogue_path: str = "config/sports_catalogue.json"):
        self.catalogue: Dict[str, Any] = {}
        if os.path.exists(catalogue_path):
            with open(catalogue_path, "r", encoding="utf-8") as f:
                self.catalogue = json.load(f)

    def get_competition_type(self, sport: str, competition: str) -> str:
        sport_obj = self.catalogue.get("sports", {}).get(sport, {})
        for comp_type, comps in sport_obj.get("competition_types", {}).items():
            for c in comps:
                if c["name"].lower() == competition.lower():
                    return comp_type
        return "General"

    def get_playlist_group(self, sport: str, competitions: List[str]) -> str:
        """Build clean group-title like 'Rugby - Club and provincial competitions'."""
        if not sport:
            return "Sports"
        if competitions:
            ctype = self.get_competition_type(sport, competitions[0])
            if ctype and ctype != "General":
                return f"{sport} - {ctype}"
        return sport

