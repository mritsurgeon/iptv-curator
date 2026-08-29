#!/usr/bin/env python3
"""
EPGShare01 Metadata Standardizer & Enricher
Ensures all verified sports channels have accurate tvg-id, tvg-name, tvg-logo,
and EPGShare01 XMLTV URLs embedded in both the playlist headers and track metadata.
"""

import os
import re

EPG_MASTER_URL = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"

# Canonical EPGShare01 ID & Logo Dictionary for Flagship Channels
EPG_CANONICAL = {
    # CRICKET
    "willow sports": {
        "tvg_id": "WillowCricket.us",
        "tvg_name": "Willow Cricket HD",
        "logo": "https://cricket.willow.tv/_nuxt/img/willow_blue_logo.362ed8e.png"
    },
    "willow": {
        "tvg_id": "WillowCricket.us",
        "tvg_name": "Willow Cricket",
        "logo": "https://cricket.willow.tv/_nuxt/img/willow_blue_logo.362ed8e.png"
    },
    "sky sports cricket": {
        "tvg_id": "SkySportsCricket.uk",
        "tvg_name": "Sky Sports Cricket",
        "logo": "https://d2n0069hmnqmmx.cloudfront.net/epgdata/1.0/newchanlogos/512/512/skychb1302.png"
    },
    "star sports 1": {
        "tvg_id": "StarSports1.in",
        "tvg_name": "Star Sports 1 HD",
        "logo": "https://i.imgur.com/E5jjKHI.png"
    },
    "star sports 2": {
        "tvg_id": "StarSports2.in",
        "tvg_name": "Star Sports 2 HD",
        "logo": "https://xstreamcp-assets-msp.streamready.in/assets/LIVETV/LIVECHANNEL/LIVETV_LIVETVCHANNEL_STAR_SPORTS_2/images/LOGO_HD/image.png"
    },
    "star sports select 2": {
        "tvg_id": "StarSportsSelect2.in",
        "tvg_name": "Star Sports Select 2 HD",
        "logo": "https://i.imgur.com/FtRT73R.png"
    },
    "cricket gold": {
        "tvg_id": "CricketGold.au",
        "tvg_name": "Cricket Gold",
        "logo": "https://resources.cricket-australia.pulselive.com/cricket-australia/photo/2025/07/25/836eddae-4329-4542-ad17-dcd37e9d951a/Cricket-Gold-1920x1080_noBG.png"
    },

    # FOOTBALL & SOCCER
    "setanta sports 1": {
        "tvg_id": "SetantaSports1.ie",
        "tvg_name": "Setanta Sports 1",
        "logo": "https://i.imgur.com/zlJK7ca.png"
    },
    "setanta sports 2": {
        "tvg_id": "SetantaSports2.ie",
        "tvg_name": "Setanta Sports 2",
        "logo": "https://i.imgur.com/ouHunnK.png"
    },
    "setanta sports+": {
        "tvg_id": "SetantaSportsPlus.ua",
        "tvg_name": "Setanta Sports+",
        "logo": "https://i.imgur.com/gHAP4p0.png"
    },
    "setanta sports": {
        "tvg_id": "SetantaSports.ie",
        "tvg_name": "Setanta Sports",
        "logo": "https://i.imgur.com/zlJK7ca.png"
    },
    "premier sports 1": {
        "tvg_id": "PremierSports1.ie",
        "tvg_name": "Premier Sports 1",
        "logo": "https://i.imgur.com/eOybZMU.png"
    },
    "premier sports 2": {
        "tvg_id": "PremierSports2.ie",
        "tvg_name": "Premier Sports 2",
        "logo": "https://i.imgur.com/Fx1n84p.png"
    },
    "bein sports xtra": {
        "tvg_id": "beINSPORTSXTRA.us",
        "tvg_name": "beIN SPORTS XTRA",
        "logo": "https://i.ibb.co/HT49GPmB/XTRA-2.png"
    },
    "beinsports xtra": {
        "tvg_id": "beINSPORTSXTRA.us",
        "tvg_name": "beIN SPORTS XTRA",
        "logo": "https://i.ibb.co/HT49GPmB/XTRA-2.png"
    },
    "nbc sports": {
        "tvg_id": "NBCSportsNetwork.us",
        "tvg_name": "NBC Sports NOW",
        "logo": "https://i.imgur.com/EzNf2Yx.png"
    },
    "cbs sports": {
        "tvg_id": "CBSSportsNetwork.us",
        "tvg_name": "CBS Sports Golazo",
        "logo": "https://i.imgur.com/eMjutHS.png"
    },

    # MAIN EVENTS & FLAGSHIP
    "sky sports main event": {
        "tvg_id": "SkySportsMainEvent.uk",
        "tvg_name": "Sky Sports Main Event",
        "logo": "https://d2n0069hmnqmmx.cloudfront.net/epgdata/1.0/newchanlogos/512/512/skychb1301.png"
    },
    "sky sports f1": {
        "tvg_id": "SkySportsF1.uk",
        "tvg_name": "Sky Sports F1",
        "logo": "https://d2n0069hmnqmmx.cloudfront.net/epgdata/1.0/newchanlogos/512/512/skychb1306.png"
    },
    "espnu": {
        "tvg_id": "ESPNU.us",
        "tvg_name": "ESPNU HD",
        "logo": "https://i.imgur.com/HiBrysh.png"
    },
    "espn 3": {
        "tvg_id": "ESPN3.us",
        "tvg_name": "ESPN 3",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/51/ESPN3_Logo.png/960px-ESPN3_Logo.png"
    },
    "espn 4": {
        "tvg_id": "ESPN4.br",
        "tvg_name": "ESPN 4",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/78/ESPN_4_logo.svg/960px-ESPN_4_logo.svg.png"
    },
    "espn deportes": {
        "tvg_id": "ESPNDeportes.us",
        "tvg_name": "ESPN Deportes HD",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d5/ESPN_Deportes.svg/960px-ESPN_Deportes.svg.png"
    },
    "espn": {
        "tvg_id": "ESPN.us",
        "tvg_name": "ESPN HD",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/ESPN_wordmark.svg/960px-ESPN_wordmark.svg.png"
    },
    "fox sports 1": {
        "tvg_id": "FoxSports1.us",
        "tvg_name": "Fox Sports 1 (FS1)",
        "logo": "https://i.imgur.com/O9BapV9.png"
    },
    "fox sports 2": {
        "tvg_id": "FoxSports2.us",
        "tvg_name": "Fox Sports 2 (FS2)",
        "logo": "https://i.imgur.com/LHtxKI8.png"
    },
    "fox sports": {
        "tvg_id": "FoxSports1.us",
        "tvg_name": "Fox Sports",
        "logo": "https://i.imgur.com/O9BapV9.png"
    },
    "bellator mma": {
        "tvg_id": "BellatorMMA.us",
        "tvg_name": "Bellator MMA",
        "logo": "https://i.imgur.com/VBKoLHk.png"
    }
}


def find_canonical_meta(channel_name: str):
    clean = channel_name.lower()
    for key, meta in EPG_CANONICAL.items():
        if key in clean:
            return meta
    return None


def enrich_playlist(filepath: str):
    if not os.path.exists(filepath):
        return

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        lines = [l.strip() for l in f if l.strip()]

    header = (
        f'#EXTM3U url-tvg="{EPG_MASTER_URL}" x-tvg-url="{EPG_MASTER_URL}"\n\n'
    )

    enriched_entries = []
    cur_extinf = None

    for line in lines:
        if line.startswith("#EXTINF"):
            cur_extinf = line
        elif cur_extinf and not line.startswith("#"):
            url = line
            name = cur_extinf.split(",")[-1].strip() if "," in cur_extinf else "Unknown Channel"
            
            # Extract group
            group_match = re.search(r'group-title="([^"]*)"', cur_extinf)
            group = group_match.group(1) if group_match else "Sports"

            meta = find_canonical_meta(name)
            if meta:
                tvg_id = meta["tvg_id"]
                tvg_name = meta["tvg_name"]
                logo = meta["logo"]
                new_extinf = f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{tvg_name}" tvg-logo="{logo}" group-title="{group}",{tvg_name}'
            else:
                new_extinf = cur_extinf

            enriched_entries.append((new_extinf, url))
            cur_extinf = None

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(header)
        for ext, u in enriched_entries:
            f.write(f"{ext}\n{u}\n\n")

    print(f"✅ Enriched {len(enriched_entries)} channels with EPGShare01 in: {os.path.basename(filepath)}")


def main():
    targets = [
        "/Users/ian/code/IPTV/verified_premier_sports.m3u8",
        "/Users/ian/code/IPTV/verified_football.m3u8",
        "/Users/ian/code/IPTV/verified_cricket.m3u8",
        "/Users/ian/code/IPTV/verified_rugby.m3u8"
    ]
    print("✨ Standardizing EPGShare01 metadata across all verified playlists...\n")
    for t in targets:
        enrich_playlist(t)
    print("\n🎉 All playlists enriched and mapped to EPGShare01!")


if __name__ == "__main__":
    main()
