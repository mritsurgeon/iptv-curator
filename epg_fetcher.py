#!/usr/bin/env python3
"""
EPGShare01 Fetcher & Indexer
Fetches country reference channel lists and XMLTV feeds from EPGShare01.
"""

import gzip
import os
import re
import urllib.request
from typing import Dict, List, Optional, Set

BASE_URL = "https://epgshare01.online/epgshare01/"

def format_source_name(source: str) -> str:
    source = source.strip()
    if source.endswith("1") or source.endswith("2"):
        return f"epg_ripper_{source.upper()}"
    return f"epg_ripper_{source.upper()}1"

def get_country_source_url(source: str) -> str:
    """Returns the URL to the XMLTV .xml.gz feed."""
    return f"{BASE_URL}{format_source_name(source)}.xml.gz"

def fetch_country_channel_ids(source: str) -> List[str]:
    """
    Fetches the list of valid EPG channel IDs for a given country code/source.
    """
    txt_url = f"{BASE_URL}{format_source_name(source)}.txt"
    req = urllib.request.Request(txt_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            lines = response.read().decode("utf-8", errors="ignore").splitlines()
            channel_ids = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith("--") or line.isdigit():
                    continue
                channel_ids.append(line)
            return channel_ids
    except Exception as e:
        print(f"Error fetching channel list for {country_code}: {e}")
        return []

def download_and_extract_xmltv(country_code: str, output_dir: str = "data") -> Optional[str]:
    """Downloads and extracts the XMLTV file locally if needed."""
    os.makedirs(output_dir, exist_ok=True)
    gz_url = get_country_source_url(country_code)
    out_xml_path = os.path.join(output_dir, f"epg_{country_code.lower()}.xml")
    
    print(f"Downloading EPG for {country_code} from {gz_url}...")
    req = urllib.request.Request(gz_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            gz_data = resp.read()
            xml_data = gzip.decompress(gz_data)
            with open(out_xml_path, "wb") as f:
                f.write(xml_data)
            print(f"Saved extracted XMLTV to {out_xml_path} ({len(xml_data) / 1024 / 1024:.2f} MB)")
            return out_xml_path
    except Exception as e:
        print(f"Failed to download/extract XMLTV: {e}")
        return None

if __name__ == "__main__":
    import sys
    country = sys.argv[1] if len(sys.argv) > 1 else "ZA"
    print(f"Fetching EPG channel IDs for country: {country}")
    ids = fetch_country_channel_ids(country)
    print(f"Found {len(ids)} channels in EPGShare01 for {country}:")
    for cid in ids[:15]:
        print(f"  - {cid}")
    if len(ids) > 15:
        print(f"  ... and {len(ids) - 15} more")
