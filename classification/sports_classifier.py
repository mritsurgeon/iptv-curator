"""Multi-signal, multi-sport relevance classifier backed by config/sports_catalogue.json."""

import json
import os
import re
from typing import Any, Dict, List, Optional

NON_SPORTS_EXCLUSION_RE = re.compile(
    r"\b(movies?|cinemas?|films?|novelas?|series|cooking|chef|kitchen|"
    r"cartoons?|disney|nickelodeon|weather|shopping|gospel|radio|poker|chess|"
    r"e-?sports?|fishing|hunting)\b",
    re.IGNORECASE,
)

# Channels that legitimately carry multiple flagship sports across competitions
MULTI_SPORT_FLAGSHIP_RE = re.compile(
    r"\b(supersport\s*grandstand|sky\s*sports\s*main\s*event|tnt\s*sports\s*[1-4]|"
    r"espn\s*[12]?|fox\s*sports\s*[12]|eurosport\s*[12]?|sabc\s*sport)\b",
    re.IGNORECASE,
)

# Context-gated international rugby matchup pattern (avoids false positives on bare country names)
RUGBY_MATCHUP_RE = re.compile(
    r"\b(?:ireland\s+(?:vs?\.?|against)\s+(?:south\s*africa|springboks)|"
    r"(?:south\s*africa|springboks)\s+(?:vs?\.?|against)\s+ireland)\b",
    re.IGNORECASE,
)
RUGBY_CONTEXT_RE = re.compile(
    r"\b(rugby|nations|aviva|test\s*match|international|springboks|six\s*nations|itv1|virgin\s*media|supersport\s*rugby|rugbypass)\b",
    re.IGNORECASE,
)


class SportsClassifier:
    """Classifies channels across multiple sports and competitions with confidence scores."""

    def __init__(self, catalogue_path: str = "config/sports_catalogue.json"):
        self.catalogue_path = catalogue_path
        self.catalogue = self._load_catalogue(catalogue_path)

    @staticmethod
    def _load_catalogue(path: str) -> Dict[str, Any]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"sports": {}}

    def classify(
        self,
        name: str,
        extinf: str = "",
        broadcaster: str = "",
        group_title: str = "",
        epg_programmes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Return:
          {
            "is_sports": bool,
            "sports": [{"sport": str, "competitions": [str], "confidence": float}],
            "relevance_score": float (0.0-1.0),
            "suggested_competitions": [str]
          }
        """
        prog_text = ""
        if epg_programmes:
            prog_text = " ".join(
                f"{p.get('title', '')} {p.get('description', '')} {p.get('competition', '')}"
                for p in epg_programmes[:15]
            )

        clean_ext = re.sub(r'tvg-logo="[^"]*"', "", extinf)
        clean_ext = re.sub(r"https?://\S+", "", clean_ext)
        name_low = name.lower().strip()
        full_text = f"{name} {broadcaster} {group_title} {clean_ext} {prog_text}".lower()
        is_multi_flagship = bool(MULTI_SPORT_FLAGSHIP_RE.search(name_low))

        has_non_sports = bool(NON_SPORTS_EXCLUSION_RE.search(f"{name} {group_title}"))

        matched_sports: List[Dict[str, Any]] = []
        max_conf = 0.0

        for sport_name, sport_data in self.catalogue.get("sports", {}).items():
            matched_comps: List[str] = []
            signal_hits = 0

            # Sport-level aliases
            for alias in sport_data.get("aliases", []):
                if re.search(rf"\b{re.escape(alias.lower())}\b", full_text):
                    signal_hits += 1

            # Context-gated rugby matchup (e.g. Ireland v South Africa with rugby/nations/aviva context)
            if sport_name == "Rugby" and RUGBY_MATCHUP_RE.search(full_text) and RUGBY_CONTEXT_RE.search(full_text):
                signal_hits += 2
                if "Nations Championship" not in matched_comps:
                    matched_comps.append("Nations Championship")

            # Competition-level aliases & specific-channel broadcaster rights
            for _, comp_list in sport_data.get("competition_types", {}).items():
                for comp in comp_list:
                    if not comp.get("active", True):
                        continue
                    comp_name = comp["name"]
                    comp_matched = False
                    for alias in [comp_name] + comp.get("aliases", []):
                        if re.search(rf"\b{re.escape(alias.lower())}\b", full_text):
                            comp_matched = True
                            signal_hits += 2
                            break
                    if not comp_matched:
                        for bc in comp.get("broadcasters", []):
                            bc_low = bc.lower().strip()
                            # Match specific channel name (e.g. "supersport rugby" in "supersport rugby hd")
                            # Avoid generic parent-brand bleed ("supersport" matching "supersport rugby" for Football/WWE)
                            if len(bc_low) >= 4 and re.search(rf"\b{re.escape(bc_low)}\b", name_low):
                                # If bc is a generic parent brand (single word like "supersport" or "sky sports")
                                # only inherit when the channel itself is a multi-sport flagship!
                                if bc_low in ("supersport", "sky sports", "bbc sport", "itv sport", "fox sports", "espn") and not is_multi_flagship:
                                    continue
                                comp_matched = True
                                signal_hits += 1
                                break
                    if comp_matched and comp_name not in matched_comps:
                        matched_comps.append(comp_name)

            if signal_hits > 0:
                conf = min(1.0, round(0.45 + 0.15 * min(signal_hits, 4), 2))
                if matched_comps:
                    conf = max(conf, 0.75)
                matched_sports.append(
                    {
                        "sport": sport_name,
                        "competitions": matched_comps,
                        "confidence": conf,
                    }
                )
                max_conf = max(max_conf, conf)

        # General multi-sport flagship fallback
        if not matched_sports and not has_non_sports:
            if re.search(r"\b(supersport|sky sports|tnt sports|espn|fox sports|bein sports|tsn|sportsnet|dazn|eurosport)\b", full_text):
                matched_sports.append({"sport": "Football", "competitions": [], "confidence": 0.55})
                max_conf = 0.55
            elif re.search(r"\b(sports?|live match)\b", full_text):
                matched_sports.append({"sport": "Football", "competitions": [], "confidence": 0.40})
                max_conf = 0.40

        if has_non_sports and max_conf < 0.7:
            return {
                "is_sports": False,
                "sports": [],
                "relevance_score": 0.0,
                "suggested_competitions": [],
            }

        suggestions: List[str] = []
        known_lower = {
            c["name"].lower()
            for sp in self.catalogue.get("sports", {}).values()
            for cl in sp.get("competition_types", {}).values()
            for c in cl
        }
        for m in re.findall(r"\b([A-Z][A-Za-z0-9'&-]+(?:\s+[A-Z][A-Za-z0-9'&-]+){0,3}\s+(?:Cup|Championship|League|Open|Tour|Trophy))\b", f"{name} {prog_text}"):
            if m.lower() not in known_lower and len(m) > 6:
                suggestions.append(m)

        return {
            "is_sports": bool(matched_sports),
            "sports": matched_sports,
            "relevance_score": round(max_conf, 2),
            "suggested_competitions": list(dict.fromkeys(suggestions))[:5],
        }
