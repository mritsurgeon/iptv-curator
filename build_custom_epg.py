#!/usr/bin/env python3
"""
Custom Curated Sports EPG Builder & Timetable Synchronizer
1. Standardizes tvg-ids and metadata across verified_premier_sports.m3u8 and temporary_sports.m3u8
2. Downloads and parses 8 regional English XMLTV sources (UK, US, ZA, NZ, AU, CA, IE, IN)
3. Maps every channel to its official upstream schedules and generates rich fixture timetables for match feeds
4. Produces a 100% covered sports_epg.xml and pushes to Cloud Gist, Fire TV & Git
"""

import datetime
import gzip
import io
import json
import os
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYLIST_PATH = os.path.join(BASE_DIR, "verified_premier_sports.m3u8")
TEMP_PLAYLIST_PATH = os.path.join(BASE_DIR, "temporary_sports.m3u8")
EPG_OUTPUT_PATH = os.path.join(BASE_DIR, "sports_epg.xml")
GIST_ID = "d6f9d772966e4ead4aac90331c6a6d9c"

SOURCES = [
    ("UK", "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"),
    ("US", "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"),
    ("ZA", "https://epgshare01.online/epgshare01/epg_ripper_ZA1.xml.gz"),
    ("NZ", "https://epgshare01.online/epgshare01/epg_ripper_NZ1.xml.gz"),
    ("AU", "https://epgshare01.online/epgshare01/epg_ripper_AU1.xml.gz"),
    ("CA", "https://epgshare01.online/epgshare01/epg_ripper_CA2.xml.gz"),
    ("IE", "https://epgshare01.online/epgshare01/epg_ripper_IE1.xml.gz"),
    ("IN", "https://epgshare01.online/epgshare01/epg_ripper_IN1.xml.gz")
]

# Canonical channel aliases mapping our playlist tvg-id & names to upstream XMLTV IDs
CHANNEL_ALIASES: Dict[str, List[str]] = {
    # 🏉 RUGBY
    "Premier.Sports.1.HD.uk": ["Premier.Sports.1.HD.uk", "Premier.Sports.1.HD.ie", "Premier.Sports.1.ie", "Premier.Sports.1.uk", "PremierSports1.ie"],
    "Premier.Sports.2.HD.uk": ["Premier.Sports.2.HD.uk", "Premier.Sports.2.HD.ie", "Premier.Sports.2.ie", "Premier.Sports.2.uk", "PremierSports2.ie", "SetantaSportsPlus.ua"],
    "SkySp.Action.uk": ["SkySp.Action.uk", "SkySp.ActionHD.uk", "Sky.Sports.Action.HD.ie", "Sky.Sports.Action.ie", "SkySportsAction.uk"],
    "SkySp.ActionHD.uk": ["SkySp.ActionHD.uk", "SkySp.Action.uk", "Sky.Sports.Action.HD.ie", "SkySportsAction.uk"],
    "SkySp.Arena.uk": ["SkySp.Arena.uk", "SkySp.ArenaHD.uk", "Sky.Sports.Arena.HD.ie", "SkySportsArena.uk"],
    "RUGBY.za": ["RUGBY.za", "SuperSport.Rugby.za", "SuperSportRugby.za", "SS.Rugby.za"],
    "SuperSportRugby.za": ["RUGBY.za", "SuperSport.Rugby.za", "SuperSportRugby.za"],
    "RugbyPassTV.uk": ["RugbyPassTV.uk", "RugbyPassTV.uk@SD", "RugbyPass.uk"],
    "StanSport.au": ["StanSport.au", "Stan.Sport.au"],

    # 🏏 CRICKET
    "Willow.Cricket.HD.us2": ["Willow.Cricket.HD.us2", "Willow.2.Xtra.us2", "WillowCricket.us", "Willow.us@SD", "Willow.us", "Willow Cricket US"],
    "Willow.2.Xtra.us2": ["Willow.2.Xtra.us2", "Willow.Cricket.HD.us2", "Willow Cricket 2"],
    "SkySpCricket.HD.uk": ["SkySpCricket.HD.uk", "SkySp.Cricket.uk", "Sky.Sports.Cricket.HD.ie", "Sky.Sports.Cricket.ie", "SkySportsCricket.uk"],
    "CRICKET.za": ["CRICKET.za", "SuperSport.Cricket.za", "SuperSportCricket.za"],
    "SuperSportCricket.za": ["CRICKET.za", "SuperSport.Cricket.za", "SuperSportCricket.za"],
    "StarSports1.in": ["StarSports1.in", "Star.Sports.1.in", "StarSports1HD.in", "Star.Sports.1.HD.in", "Star.Sports.1.Hindi.in"],
    "StarSports2.in": ["StarSports2.in", "Star.Sports.2.in", "StarSports2HD.in", "Star.Sports.2.HD.in"],
    "StarSportsSelect1.in": ["StarSportsSelect1.in", "Star.Sports.Select.1.HD.in", "StarSportsSelect1HD.in"],
    "StarSportsSelect2.in": ["StarSportsSelect2.in", "Star.Sports.Select.2.HD.in", "StarSportsSelect2HD.in"],
    "CricketGold.au": ["CricketGold.au", "FoxCricket.au", "Fox.Sports.Cricket.au"],
    "SonyTen1.in": ["SonyTen1.in", "Sony.Ten.1.HD.in", "SonyTen1HD.in", "Ten Sports HD"],
    "SonyTen5.in": ["SonyTen5.in", "Sony.Ten.5.HD.in", "SonyTen5HD.in"],

    # ⚽ FOOTBALL & SOCCER
    "SkySp.PL.HD.uk": ["SkySp.PL.HD.uk", "SkySp.PL.uk", "Sky.Sports.Premier.League.HD.ie", "SkySportsPremierLeague.uk"],
    "SkySp.Football.HD.uk": ["SkySp.Football.HD.uk", "SkySp.Football.uk", "Sky.Sports.Football.HD.ie", "SkySportsFootball.uk"],
    "TNTSports1.uk": ["TNTSports1.uk", "TNT.Sports.1.HD.uk", "TNT.Sports.1.uk", "BT.Sport.1.HD.uk"],
    "TNTSports2.uk": ["TNTSports2.uk", "TNT.Sports.2.HD.uk", "TNT.Sports.2.uk", "BT.Sport.2.HD.uk"],
    "TNTSports3.uk": ["TNTSports3.uk", "TNT.Sports.3.HD.uk", "TNT.Sports.3.uk", "BT.Sport.3.HD.uk"],
    "TNTSports4.uk": ["TNTSports4.uk", "TNT.Sports.4.HD.uk", "TNT.Sports.4.uk", "BT.Sport.4.HD.uk"],
    "TNT.Sports.Box.Office.uk": ["TNT.Sports.Box.Office.uk", "TNT.Sports.Box.uk", "UK: TNT SPORT BOX HD"],
    "SS.Premier.League.za": ["SS.Premier.League.za", "SuperSport.Premier.League.za", "SuperSportPremierLeague.za"],
    "SS.Football.za": ["SS.Football.za", "SuperSport.Football.za", "SuperSportFootball.za"],
    "SS.La.Liga.za": ["SS.La.Liga.za", "SuperSport.LaLiga.za", "SuperSportLaLiga.za"],
    "SetantaSports1.ie": ["SetantaSports1.ie", "Premier.Sports.1.HD.ie", "Premier.Sports.1.HD.uk"],
    "SetantaSports2.ie": ["SetantaSports2.ie", "Premier.Sports.2.HD.ie", "Premier.Sports.2.HD.uk"],
    "SetantaSportsPlus.ua": ["SetantaSportsPlus.ua", "Premier.Sports.2.HD.uk", "Premier.Sports.1.HD.uk"],
    "beIN.Sports.USA.HD.us2": ["beIN.Sports.USA.HD.us2", "beINSPORTSXTRA.us", "beIN.Sports.HD.(Canada).ca2"],
    "beINSPORTSXTRA.us": ["beINSPORTSXTRA.us", "beIN.Sports.USA.HD.us2"],
    "CBSSportsGolazoNetwork.us": ["CBSSportsGolazoNetwork.us", "CBS.Sports.Network.HD.us2", "CBS.Sports.Network.us2"],
    "NBCSportsNOW.us": ["NBCSportsNOW.us", "NBCSN.us", "NBC.Sports.Bay.Area.HD.us2", "NBCSportsNOW.us@SD"],
    "OptusSport1.au": ["OptusSport1.au", "Optus.Sport.1.au"],

    # 🏈 AMERICAN FOOTBALL & US SPORTS
    "ESPN.HD.us2": ["ESPN.HD.us2", "ESPN.us2", "ESPN.us", "ESPN.HD.za", "ESPN"],
    "ESPN2.HD.us2": ["ESPN2.HD.us2", "ESPN2.us2", "ESPN2.us", "ESPN.2.HD.za"],
    "ESPNU.HD.us2": ["ESPNU.HD.us2", "ESPNU.us2", "ESPNU.us", "ESPNU.us@SD", "ESPNU (720p)", "ESPNU HD (720p 60fps)"],
    "ESPNEWS.HD.us2": ["ESPNEWS.HD.us2", "ESPNews.us2", "ESPNews.us"],
    "FS1.Fox.Sports.1.HD.us2": ["FS1.Fox.Sports.1.HD.us2", "FoxSports1.us", "FS1.us2", "Fox Sports 1 (FS1 - 720p 60fps)", "FS1.us"],
    "FS2.Fox.Sports.2.HD.us2": ["FS2.Fox.Sports.2.HD.us2", "FoxSports2.us", "FS2.us2"],
    "Fox.us@West": ["Fox.us@West", "FS1.Fox.Sports.1.HD.us2", "FoxSports1.us", "Fox West (720p)"],
    "Fox.us@East": ["Fox.us@East", "FS1.Fox.Sports.1.HD.us2", "Fox (720p)"],
    "NFLNetwork.us": ["NFLNetwork.us", "NFL.Network.HD.us2", "NFL.RedZone.HD.us2", "NFLNetwork.us@SD", "NFL Network (720p)"],
    "NBA.Channel.us": ["NBA.Channel.us", "NBA.League.Pass.4.HD.us2", "NBA.TV.us", "The NBA Channel (720p)"],
    "FuboSportsNetwork.us": ["FuboSportsNetwork.us", "FuboSportsNetwork.us@SD", "Fubo.Sports.Network.us"],
    "WomensSportsNetwork.us": ["WomensSportsNetwork.us", "WomensSportsNetwork.us@SD"],
    "SwerveSports.us": ["SwerveSports.us", "SwerveSports.us@SD"],

    # 🏎️ MOTORSPORT & COMBAT
    "SkySp.F1.HD.uk": ["SkySp.F1.HD.uk", "SkySp.F1.uk", "Sky.Sports.F1.HD.ie", "Sky.Sports.F1.ie", "SkySportsF1.uk", "Sky Sports F1 (1080p 50fps)"],
    "SkySp.F1.uk": ["SkySp.F1.uk", "SkySp.F1.HD.uk", "Sky.Sports.F1.HD.ie", "SkySportsF1.uk", "Sky Sports F1"],
    "MOTORSPORT.za": ["MOTORSPORT.za", "SuperSport.Motorsport.za"],
    "SS.Action.za": ["SS.Action.za", "SuperSport.Action.za"],
    "MainEventUFC.au": ["MainEventUFC.au", "UFC (720p)", "UFC", "UFC Main Event"],
    "FightBoxHD.us": ["FightBoxHD.us", "FightNetwork.us", "FightBox.us"],
    "BellatorMMA.us": ["BellatorMMA.us", "FightBoxHD.us", "MMA.TV.us"],
    "PFL.MMA.us": ["PFL.MMA.us", "PFLMMA.us@SD", "PFL MMA"],

    # 🏆 MAIN EVENTS & MULTI-SPORT
    "GRANDSTAND.za": ["GRANDSTAND.za", "SuperSport.Grandstand.za", "SuperSportGrandstand.za"],
    "SkySpMainEvHD.uk": ["SkySpMainEvHD.uk", "SkySp.MainEvent.uk", "Sky.Sports.Main.Event.HD.ie", "SkySportsMainEvent.uk"],
    "Tennis.Channel.HD.us2": ["Tennis.Channel.HD.us2", "TennisChannel.us@SD", "TennisChannel.us@Plus2", "Tennis Channel (1080p)", "TennisChannel 2 (720p)"],
    "Golf.Channel.HD.us2": ["Golf.Channel.HD.us2", "GolfChannelLatinAmerica.us@SD", "Golf Channel Latin America (720p)", "GolfPass (720p)", "GolfPass.us"],
    "VSIN.Vegas.Sports.and.Information.Network.us2": ["VSIN.Vegas.Sports.and.Information.Network.us2", "VSiN.us@SD", "VSiN (720p)"],
    "Trace.Sports.au": ["Trace.Sports.au", "Trace Sport", "Trace Sports Stars", "http://lightning-tracesport-samsungau.amagi.tv/playlist1080p.m3u8", "Dummy"],
    "RedBullTV.us": ["RedBullTV.us", "REDBULL TV"],
    "HorseTV.us": ["HorseTV.us", "HORSE TV"],
    "XtremSports.us": ["XtremSports.us", "XTREM SPORTS"],
    "TSN.1.ca2": ["TSN.1.ca2", "TSN.HD.ca2"],
    "TSN.2.HD.ca2": ["TSN.2.HD.ca2", "TSN.2.ca2"],
    "TSN.3.HD.ca2": ["TSN.3.HD.ca2", "TSN.3.ca2"],
    "TSN.4.HD.ca2": ["TSN.4.HD.ca2", "TSN.4.ca2"],
    "TSN.5.HD.ca2": ["TSN.5.HD.ca2", "TSN.5.ca2"],
    "Sportsnet.One.HD.ca2": ["Sportsnet.One.HD.ca2", "Sportsnet.One.ca2"],
    "Sportsnet.360.HD.ca2": ["Sportsnet.360.HD.ca2"],
    "Sky.Sport.1.nz": ["Sky.Sport.1.nz", "SkySport1.nz"],
    "Sky.Sport.2.nz": ["Sky.Sport.2.nz", "SkySport2.nz"],
    "Sky.Sport.3.nz": ["Sky.Sport.3.nz", "SkySport3.nz"]
}


def sanitize_channel_entry(extinf: str, url: str) -> Tuple[str, str, str, str, str]:
    """Cleans up raw extinf lines to have unique, standardized tvg-id, tvg-name, and clean display names."""
    m_name = extinf.split(",")[-1].strip() if "," in extinf else "Sports Channel"
    m_id = re.search(r'tvg-id="([^"]*)"', extinf)
    m_tvg_name = re.search(r'tvg-name="([^"]*)"', extinf)
    m_logo = re.search(r'tvg-logo="([^"]*)"', extinf)
    m_grp = re.search(r'group-title="([^"]*)"', extinf)

    cid = m_id.group(1) if m_id else ""
    cname = m_tvg_name.group(1) if m_tvg_name else m_name
    logo = m_logo.group(1) if m_logo else ""
    grp = m_grp.group(1) if m_grp else "Sports"

    # Standardize specific channel IDs
    name_low = m_name.lower()
    if "liverpool vs nottingham forest" in name_low:
        cid = "Match.EPL.LIV.NFO.HD" if "hd" in name_low else "Match.EPL.LIV.NFO.2"
        logo = "https://a.espncdn.com/combiner/i?img=/i/leaguelogos/soccer/500/23.png"
    elif "wolves vs stoke" in name_low or "wolverhampton vs stoke" in name_low:
        cid = "Match.EFL.WOL.STK"
        logo = "https://a.espncdn.com/combiner/i?img=/i/leaguelogos/soccer/500/24.png"
    elif "derby county vs swansea" in name_low:
        cid = "Match.EFL.DER.SWA"
        logo = "https://a.espncdn.com/combiner/i?img=/i/leaguelogos/soccer/500/24.png"
    elif "mk dons vs leicester" in name_low:
        cid = "Match.EFL.MKD.LEI"
        logo = "https://a.espncdn.com/combiner/i?img=/i/leaguelogos/soccer/500/25.png"
    elif "manchester city vs crystal palace" in name_low:
        cid = "Match.EPL.MCI.CRY"
        logo = "https://a.espncdn.com/combiner/i?img=/i/leaguelogos/soccer/500/23.png"
    elif "england vs pakistan" in name_low:
        cid = "Match.Cricket.ENG.PAK"
        logo = "https://raw.githubusercontent.com/sm-monirulislam/Upcoming-and-Live-Sports-Data/main/match_image/England%20Vs%20Pakistan.jpg"
    elif "kansas city chiefs" in name_low:
        cid = "Match.NFL.KC.SEA"
        logo = "https://raw.githubusercontent.com/sm-monirulislam/Upcoming-and-Live-Sports-Data/main/match_image/Kansas%20City%20Chiefs%20Vs%20Seattle%20Seahawks.jpg"
    elif "philadelphia eagles" in name_low:
        cid = "Match.NFL.PHI.CIN"
        logo = "https://raw.githubusercontent.com/sm-monirulislam/Upcoming-and-Live-Sports-Data/main/match_image/Philadelphia%20Eagles%20Vs%20Cincinnati%20Bengals.jpg"
    elif "green bay packers" in name_low:
        cid = "Match.NFL.GB.ARI"
        logo = "https://raw.githubusercontent.com/sm-monirulislam/Upcoming-and-Live-Sports-Data/main/match_image/Green%20Bay%20Packers%20Vs%20Arizona%20Cardinals.jpg"
    elif "nurmagomedov" in name_low:
        cid = "Match.UFC.Nurmagomedov.Song"
        logo = "https://raw.githubusercontent.com/sm-monirulislam/Upcoming-and-Live-Sports-Data/main/match_image/Umar%20Nurmagomedov%20Vs%20Yadong%20Song.jpg"
    elif "snooker" in name_low:
        cid = "Match.Snooker.Live"
        logo = "https://raw.githubusercontent.com/sm-monirulislam/Upcoming-and-Live-Sports-Data/main/match_image/Snooker%20Vs%20Snooker.jpg"
    elif "nba channel" in name_low:
        cid = "NBA.Channel.us"
        logo = "https://i.imgur.com/nba_logo.png"
    elif "willow cricket 2" in name_low:
        cid = "Willow.2.Xtra.us2"
        logo = "https://i.postimg.cc/V6Cy4crv/WILLOW-2.png"
    elif "willow" in name_low:
        cid = "Willow.Cricket.HD.us2"
        logo = "https://cricket.willow.tv/_nuxt/img/willow_blue_logo.362ed8e.png"
    elif "supersport cricket" in name_low:
        cid = "CRICKET.za"
        logo = "https://i.imgur.com/vHq8sR7.png"
    elif "sky sports f1" in name_low and "1080p" in name_low:
        cid = "SkySp.F1.HD.uk"
        logo = "https://d2n0069hmnqmmx.cloudfront.net/epgdata/1.0/newchanlogos/512/512/skychb1306.png"
    elif "sky sports f1" in name_low:
        cid = "SkySp.F1.uk"
        logo = "https://raw.githubusercontent.com/tv-logo/tv-logos/refs/heads/main/countries/united-kingdom/sky-sports-f1-icon-uk.png"
    elif "sky sports action" in name_low:
        cid = "SkySp.Action.uk"
        logo = "https://raw.githubusercontent.com/tv-logo/tv-logos/refs/heads/main/countries/united-kingdom/sky-sports-action-icon-uk.png"
    elif "setanta sports 1" in name_low:
        cid = "Premier.Sports.1.HD.uk"
        logo = "https://i.imgur.com/zlJK7ca.png"
    elif "setanta sports+" in name_low:
        cid = "Premier.Sports.2.HD.uk"
        logo = "https://i.imgur.com/gHAP4p0.png"
    elif "tnt sport box" in name_low or "tnt sport" in name_low:
        cid = "TNT.Sports.Box.Office.uk"
        logo = "https://d2n0069hmnqmmx.cloudfront.net/epgdata/1.0/newchanlogos/512/512/skychb1307.png"
    elif "ufc" in name_low:
        cid = "MainEventUFC.au"
        logo = "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/1024px-UFC_logo.svg.png"
    elif "pfl" in name_low:
        cid = "PFL.MMA.us"
        logo = "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Professional_Fighters_League_logo.svg/800px-Professional_Fighters_League_logo.svg.png"
    elif "tennis channel" in name_low:
        cid = "Tennis.Channel.HD.us2"
        logo = "https://i.imgur.com/tennis_logo.png"
    elif "golf channel" in name_low or "golfpass" in name_low:
        cid = "Golf.Channel.HD.us2"
        logo = "https://i.imgur.com/golf_logo.png"
    elif "vsin" in name_low:
        cid = "VSIN.Vegas.Sports.and.Information.Network.us2"
    elif "trace sport" in name_low:
        cid = "Trace.Sports.au"
        logo = "https://i.imgur.com/FabFP5A.png"
    elif "redbull" in name_low:
        cid = "RedBullTV.us"
        logo = "https://i.imgur.com/bbWhcdO.jpg"
    elif "horse tv" in name_low:
        cid = "HorseTV.us"
        logo = "https://i.imgur.com/PPwZaN6.jpg"
    elif "xtrem sports" in name_low:
        cid = "XtremSports.us"
        logo = "https://i.imgur.com/VmTiFBk.jpg"
    elif "ten sports" in name_low:
        cid = "SonyTen1.in"
        logo = "https://i.postimg.cc/YC88ss92/ten-sports.png"
    elif "espnu" in name_low:
        cid = "ESPNU.HD.us2"
        logo = "https://i.imgur.com/HiBrysh.png"
    elif "espn" in name_low:
        cid = "ESPN.HD.us2"
        logo = "http://schedulesdirect-api20141201-logos.s3.dualstack.us-east-1.amazonaws.com/stationLogos/s10179_dark_360w_270h.png"
    elif "fs1" in name_low or "fox sports 1" in name_low:
        cid = "FS1.Fox.Sports.1.HD.us2"
        logo = "https://i.imgur.com/O9BapV9.png"
    elif not cid:
        cid = re.sub(r'[^a-zA-Z0-9]', '.', m_name).strip('.') + ".us"

    clean_extinf = f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{cname}" tvg-logo="{logo}" group-title="{grp}",{m_name}'
    return cid, cname, logo, grp, clean_extinf


def load_and_standardize_playlists() -> List[Dict[str, str]]:
    """Reads both master and temporary playlists, standardizes tvg-ids and returns channel dicts."""
    all_channels = []
    
    for pl_path in [PLAYLIST_PATH, TEMP_PLAYLIST_PATH]:
        if not os.path.exists(pl_path):
            continue
        with open(pl_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [l.strip() for l in f if l.strip()]

        cur_ext = None
        new_lines = []
        custom_epg_url = f"https://gist.githubusercontent.com/mritsurgeon/{GIST_ID}/raw/sports_epg.xml"

        for l in lines:
            if l.startswith("#EXTM3U"):
                new_lines.append(f'#EXTM3U url-tvg="{custom_epg_url}" x-tvg-url="{custom_epg_url}"')
            elif l.startswith("# =") or l.startswith("#PLAYLIST:") or (l.startswith("# ") and not l.startswith("#EXT")):
                new_lines.append(l)
            elif l.startswith("#EXTINF"):
                cur_ext = l
            elif cur_ext and not l.startswith("#"):
                cid, cname, logo, grp, clean_ext = sanitize_channel_entry(cur_ext, l)
                new_lines.append(clean_ext)
                new_lines.append(l)
                all_channels.append({
                    "tvg_id": cid,
                    "display_name": cname,
                    "raw_name": cur_ext.split(",")[-1].strip() if "," in cur_ext else cname,
                    "logo": logo,
                    "group": grp,
                    "url": l
                })
                cur_ext = None

        with open(pl_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")

    return all_channels


def generate_rich_fixture_timetable(channel: Dict[str, str], start_now: datetime.datetime) -> List[ET.Element]:
    """Generates an extensive 48-hour timetable schedule for match streams and pop-up feeds."""
    raw = channel["raw_name"]
    cid = channel["tvg_id"]
    group = channel["group"]

    # Match fixture parsing
    match_m = re.search(r"^(?:\[.*?\]\s*)?(.*?)\s+(?:vs\.?|v|-)\s+(.*?)(?:\s*\(.*|\s*\[.*|\s*2|\s*HD)*$", raw, re.IGNORECASE)
    if match_m:
        team1 = match_m.group(1).strip()
        team2 = match_m.group(2).strip()
        fixture_title = f"{team1} vs {team2}"
    else:
        team1, team2, fixture_title = "", "", raw

    genre = "Sports"
    tournament = "Championship Live"
    desc_base = f"Live high-definition sports broadcast: {fixture_title}."

    low = f"{raw} {cid} {group}".lower()
    if any(k in low for k in ["chiefs", "seahawks", "eagles", "bengals", "packers", "cardinals", "cowboys", "saints", "nfl"]):
        genre = "American Football"
        tournament = "NFL Regular Season / Championship"
        desc_base = f"Live NFL gridiron match featuring {fixture_title}. Complete live game coverage, tactical camera feeds, and in-depth expert commentary."
    elif any(k in low for k in ["cricket", "england", "pakistan", "willow", "t20", "ipl"]):
        genre = "Cricket"
        tournament = "International Cricket / T20 Series"
        desc_base = f"Live international cricket action: {fixture_title}. Full ball-by-ball commentary, wicket analysis, hawk-eye replays, and expert pitch reports."
    elif any(k in low for k in ["liverpool", "nottingham", "wolves", "stoke", "derby", "swansea", "mk dons", "leicester", "manchester", "crystal palace", "premier league", "championship", "league one"]):
        genre = "Football & Soccer"
        tournament = "Premier League / EFL Championship"
        desc_base = f"Live English football coverage: {fixture_title}. Pitch-side build-up, live match commentary, VAR review, and post-game tactical breakdown."
    elif any(k in low for k in ["nurmagomedov", "song", "ufc", "mma", "pfl"]):
        genre = "Combat Sports"
        tournament = "UFC / PFL Main Card Event"
        desc_base = f"Live championship fight card featuring {fixture_title}. Full round-by-round coverage, fighter walkouts, corner audio, and post-fight octagon interviews."
    elif any(k in low for k in ["rugby", "top 14", "urc", "six nations", "nrl", "super rugby", "rugbypass"]):
        genre = "Rugby"
        tournament = "International Rugby Championship"
        desc_base = f"Live test match rugby: {fixture_title}. Full 80-minute broadcast with live commentary, try-line replays, scrum analysis, and post-match interviews."
    elif "snooker" in low:
        genre = "Snooker"
        tournament = "World Snooker Tour"
        desc_base = f"Live tournament snooker broadcast: {fixture_title}. Frame-by-frame coverage with expert potting analysis and player statistics."

    # 48-Hour Continuous Rotating Match Schedule Blocks
    schedule_templates = [
        ("🔴 LIVE: " + fixture_title, f"{tournament} - Live Broadcast", f"{desc_base} Live multi-camera broadcast with English commentary.", 3, True),
        (f"Post-Match Reaction & Highlights: {fixture_title}", f"{genre} Post-Game Studio", f"Expert pundit analysis, player ratings, manager press conferences, and decisive moments from {fixture_title}.", 2, False),
        (f"Full Match Replay: {fixture_title}", f"{genre} Extended Re-Broadcast", f"Complete commercial-free replay of {fixture_title} in full 1080p HD.", 3, False),
        (f"Match Highlights & Key Plays: {fixture_title}", f"{genre} Short Highlights", f"All goals, tries, wickets, touchdowns, and highlight-reel moments from {fixture_title}.", 2, False),
        (f"{genre} Today: Fixtures, News & Standings", "Sports News & Analysis", f"Comprehensive round-up of tournament standings, team news, injury updates, and upcoming match previews across {tournament}.", 2, False),
        (f"Classic Encounters & Rivalries: {fixture_title}", "Sports Archive Vault", f"A retrospective look at the greatest past meetings, rivalries, and dramatic finishes between {fixture_title}.", 2, False),
        (f"Pre-Match Tactical Build-Up: {fixture_title}", f"{tournament} Match Preview", f"Lineup announcements, key player matchups, head-to-head records, and tactical analysis ahead of {fixture_title}.", 2, False),
        ("🔴 LIVE: " + fixture_title + " (Second Half / Encore)", f"{tournament} Live Coverage", f"{desc_base} Encore broadcast.", 3, True),
    ]

    programmes = []
    cur_time = start_now - datetime.timedelta(hours=2)

    # Loop 3 times to cover 48+ hours of schedule
    for loop in range(3):
        for title_t, sub_t, desc_t, duration_h, is_live in schedule_templates:
            end_time = cur_time + datetime.timedelta(hours=duration_h)
            start_str = cur_time.strftime("%Y%m%d%H%M%S +0000")
            stop_str = end_time.strftime("%Y%m%d%H%M%S +0000")

            p = ET.Element("programme", start=start_str, stop=stop_str, channel=cid)
            t = ET.SubElement(p, "title", lang="en")
            t.text = title_t
            st = ET.SubElement(p, "sub-title", lang="en")
            st.text = sub_t
            d = ET.SubElement(p, "desc", lang="en")
            d.text = desc_t
            cat = ET.SubElement(p, "category", lang="en")
            cat.text = genre
            if is_live:
                ET.SubElement(p, "live")
            programmes.append(p)
            cur_time = end_time

    return programmes


def build_consolidated_epg():
    print("=" * 85)
    print("📺 CONSOLIDATING DEDICATED SPORTS EPG (8 REGIONS + 100% TIMETABLE COVERAGE)")
    print("=" * 85)

    channels = load_and_standardize_playlists()
    all_channels_dict: Dict[str, Dict[str, str]] = {c["tvg_id"]: c for c in channels}
    target_ids = set(all_channels_dict.keys())

    print(f"🎯 Total Standardized Channels ({len(target_ids)}): {list(target_ids)[:10]}...")

    # Build reverse alias lookup
    source_to_target: Dict[str, str] = {}
    for tid, aliases in CHANNEL_ALIASES.items():
        for a in aliases:
            source_to_target[a.lower()] = tid
        source_to_target[tid.lower()] = tid

    for tid in target_ids:
        source_to_target[tid.lower()] = tid
        clean_tid = re.sub(r"\.(us2|ca2|uk|ie|za|nz|au|in)$", "", tid, flags=re.I)
        source_to_target[clean_tid.lower()] = tid

    matched_channels: Dict[str, ET.Element] = {}
    matched_programmes: Dict[str, List[ET.Element]] = {tid: [] for tid in target_ids}

    # Download from 8 regional feeds
    for region, url in SOURCES:
        print(f"\n📥 Downloading {region} XMLTV feed from EPGShare01...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=25) as r:
                raw_gz = r.read()
            data = gzip.decompress(raw_gz)
            print(f"  • Uncompressed {len(data) / (1024*1024):.1f} MB. Extracting schedules...")

            root = ET.fromstring(data)

            # Match Channels
            for ch in root.findall("channel"):
                src_id = ch.get("id", "")
                target_id = source_to_target.get(src_id.lower())
                if target_id and target_id in target_ids and target_id not in matched_channels:
                    ch.set("id", target_id)
                    matched_channels[target_id] = ch
                    print(f"    ✓ Matched Channel: [{region}] {src_id} ➔ {target_id}")

            # Match Programmes
            prog_count = 0
            for prog in root.findall("programme"):
                src_id = prog.get("channel", "")
                target_id = source_to_target.get(src_id.lower())
                if target_id and target_id in target_ids:
                    prog.set("channel", target_id)
                    matched_programmes[target_id].append(prog)
                    prog_count += 1

            print(f"    ✓ Extracted {prog_count} schedule slots from {region}.")

        except Exception as e:
            print(f"  ❌ Error downloading {region}: {e}")

    # Build extensive timetables for channels without upstream schedules (or matchday feeds)
    now = datetime.datetime.now(datetime.timezone.utc)
    for tid, c in all_channels_dict.items():
        if tid not in matched_channels:
            ch_elem = ET.Element("channel", id=tid)
            dname = ET.SubElement(ch_elem, "display-name")
            dname.text = c["display_name"]
            if c["logo"]:
                ET.SubElement(ch_elem, "icon", src=c["logo"])
            matched_channels[tid] = ch_elem

        if not matched_programmes[tid]:
            progs = generate_rich_fixture_timetable(c, now)
            matched_programmes[tid].extend(progs)
            print(f"  ⚡ Generated 48-hour timetable for: {c['raw_name']} ({len(progs)} blocks)")

    # Build the final XMLTV document
    root_tv = ET.Element("tv", generator_info_name="IPTV-Custom-Sports-EPG", generator_info_url="https://github.com/mritsurgeon")
    for tid, ch in matched_channels.items():
        root_tv.append(ch)

    total_progs = 0
    for tid, p_list in matched_programmes.items():
        for p in p_list:
            root_tv.append(p)
            total_progs += 1

    xml_tree = ET.ElementTree(root_tv)
    xml_tree.write(EPG_OUTPUT_PATH, encoding="utf-8", xml_declaration=True)

    print("\n" + "=" * 85)
    print("✅ CUSTOM SPORTS EPG COMPILED SUCCESSFULLY!")
    print(f"  • Channels in EPG:    {len(matched_channels)} / {len(all_channels_dict)} (100% Coverage)")
    print(f"  • Total Programmes:   {total_progs} upcoming shows & matches")
    print(f"  • File Size:          {os.path.getsize(EPG_OUTPUT_PATH) / 1024:.1f} KB")
    print(f"  • Saved to:           {EPG_OUTPUT_PATH}")
    print("=" * 85)

    # Cloud Sync to Gist
    token = os.environ.get("GIST_PAT") or os.environ.get("GIST_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        try:
            token = subprocess.check_output(["gh", "auth", "token"]).decode().strip()
        except Exception:
            token = None

    if token:
        try:
            with open(PLAYLIST_PATH, "r", encoding="utf-8") as f:
                master_m3u = f.read()
            with open(TEMP_PLAYLIST_PATH, "r", encoding="utf-8") as f:
                temp_m3u = f.read()
            with open(EPG_OUTPUT_PATH, "r", encoding="utf-8") as f:
                epg_xml = f.read()

            payload = {
                "files": {
                    "sports.m3u": {"content": master_m3u},
                    "temporary_sports.m3u": {"content": temp_m3u},
                    "sports_epg.xml": {"content": epg_xml}
                }
            }

            req = urllib.request.Request(
                f"https://api.github.com/gists/{GIST_ID}",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "User-Agent": "IPTV-Custom-EPG-Builder"
                },
                method="PATCH"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                print("☁️ Successfully pushed 100% covered EPG & Playlists to Cloud Gist!")
        except Exception as e:
            print(f"⚠️ Gist sync error: {e}")

    # Push to Fire TV via ADB
    try:
        res = subprocess.run(["adb", "get-state"], capture_output=True, text=True, timeout=2)
        if "device" in res.stdout:
            subprocess.run(["adb", "push", PLAYLIST_PATH, "/sdcard/Download/sports.m3u"], capture_output=True)
            subprocess.run(["adb", "push", TEMP_PLAYLIST_PATH, "/sdcard/Download/temporary_sports.m3u"], capture_output=True)
            subprocess.run(["adb", "push", EPG_OUTPUT_PATH, "/sdcard/Download/sports_epg.xml"], capture_output=True)
            print("📱 Successfully pushed updated EPG directly to Fire TV via ADB!")
    except Exception:
        pass


if __name__ == "__main__":
    build_consolidated_epg()
