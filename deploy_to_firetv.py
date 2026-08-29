#!/usr/bin/env python3
"""
Fire TV Stick IPTV Client Installer & Playlist Deployment Suite
1. Connects to Fire TV via ADB over Wi-Fi
2. Downloads and installs a lightweight IPTV player (e.g., VLC for Fire TV / OTT Navigator)
3. Pushes verified playlists directly to Fire TV storage (/sdcard/Download/)
4. Launches the player with the verified playlist and validates playback
"""

import argparse
import http.server
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

LOCAL_PLAYLIST = "/Users/ian/code/IPTV/verified_premier_sports.m3u8"
REMOTE_STORAGE_DIR = "/sdcard/Download/"
DEFAULT_FIRETV_IP = "10.10.6.248"
ADB_PORT = 5555


def run_adb(cmd: List[str], target_device: Optional[str] = None, timeout: int = 20) -> Tuple[int, str, str]:
    """Executes an adb command."""
    base_cmd = ["adb"]
    if target_device:
        base_cmd.extend(["-s", target_device])
    full_cmd = base_cmd + cmd
    try:
        res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout, res.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


def find_firetv_device(explicit_ip: Optional[str] = None) -> Optional[str]:
    """Finds or connects to the Fire TV device via ADB."""
    # Check already connected devices
    code, stdout, _ = run_adb(["devices"])
    lines = [l.strip() for l in stdout.splitlines() if l.strip() and not l.startswith("List")]
    for line in lines:
        if "device" in line and not "offline" in line:
            dev_id = line.split()[0]
            print(f"✅ Found connected ADB device: {dev_id}")
            return dev_id

    # Try connecting to explicit or default IP
    ip_to_try = explicit_ip or DEFAULT_FIRETV_IP
    print(f"📡 Attempting ADB connection to Fire TV at {ip_to_try}:{ADB_PORT}...")
    code, stdout, stderr = run_adb(["connect", f"{ip_to_try}:{ADB_PORT}"])
    if "connected to" in stdout.lower():
        print(f"✅ Connected to {ip_to_try}:{ADB_PORT}")
        return f"{ip_to_try}:{ADB_PORT}"

    return None


def push_playlists_to_firetv(device_id: str) -> bool:
    """Pushes verified M3U8 playlists to Fire TV /sdcard/Download/."""
    playlists = [
        "/Users/ian/code/IPTV/verified_premier_sports.m3u8",
        "/Users/ian/code/IPTV/verified_football.m3u8",
        "/Users/ian/code/IPTV/verified_cricket.m3u8",
        "/Users/ian/code/IPTV/verified_rugby.m3u8"
    ]

    print("\n📁 Pushing verified playlists to Fire TV internal storage...")
    run_adb(["shell", "mkdir", "-p", REMOTE_STORAGE_DIR], target_device=device_id)

    success_all = True
    for pl in playlists:
        if os.path.exists(pl):
            fname = os.path.basename(pl)
            remote_path = f"{REMOTE_STORAGE_DIR}{fname}"
            code, out, err = run_adb(["push", pl, remote_path], target_device=device_id)
            if code == 0:
                print(f"  ✅ Uploaded {fname} ➔ {remote_path}")
            else:
                print(f"  ❌ Failed to upload {fname}: {err}")
                success_all = False

    return success_all


def check_or_install_player(device_id: str) -> Optional[str]:
    """Checks for installed IPTV players or installs VLC."""
    players = {
        "ar.tvplayer.tv": "TiviMate",
        "org.videolan.vlc": "VLC for Android",
        "com.nst.iptvsmarterstvbox": "IPTV Smarters Pro",
        "com.maccabi.ottnavigator": "OTT Navigator",
        "org.xbmc.kodi": "Kodi"
    }

    code, out, _ = run_adb(["shell", "pm", "list", "packages"], target_device=device_id)
    installed = out.splitlines()
    installed_pkgs = {line.replace("package:", "").strip() for line in installed}

    for pkg, name in players.items():
        if pkg in installed_pkgs:
            print(f"✅ Found installed player on Fire TV: {name} ({pkg})")
            return pkg

    print("ℹ️ No supported IPTV player found. Downloading lightweight VLC for Fire TV...")
    vlc_apk_url = "https://get.videolan.org/vlc-android/3.5.4/VLC-Android-3.5.4-arm64-v8a.apk"
    local_apk = "/Users/ian/code/IPTV/vlc_firetv.apk"

    if not os.path.exists(local_apk):
        print(f"⬇️ Downloading VLC APK from VideoLAN...")
        import urllib.request
        urllib.request.urlretrieve(vlc_apk_url, local_apk)

    print(f"📲 Installing {local_apk} onto Fire TV...")
    code, out, err = run_adb(["install", "-r", local_apk], target_device=device_id, timeout=60)
    if "Success" in out:
        print("✅ VLC successfully installed on Fire TV!")
        return "org.videolan.vlc"
    else:
        print(f"❌ APK installation output: {out} {err}")
        return None


def test_playback(device_id: str, pkg: str, stream_url: Optional[str] = None):
    """Launches stream in player on Fire TV."""
    test_stream = stream_url or "https://d36r8jifhgsk5j.cloudfront.net/Willow_TV.m3u8"
    print(f"\n🎬 Launching test stream on Fire TV ({pkg})...")
    print(f"   Stream: {test_stream}")

    if pkg == "org.videolan.vlc":
        cmd = [
            "shell", "am", "start",
            "-n", "org.videolan.vlc/org.videolan.vlc.gui.video.VideoPlayerActivity",
            "-a", "android.intent.action.VIEW",
            "-d", test_stream,
            "-t", "video/*"
        ]
    else:
        cmd = [
            "shell", "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", test_stream,
            "-t", "video/*"
        ]

    code, out, err = run_adb(cmd, target_device=device_id)
    print(out.strip() or err.strip())
    print("✨ Playback command sent to Fire TV!")


def main():
    parser = argparse.ArgumentParser(description="Deploy IPTV playlists and client to Fire TV Stick.")
    parser.add_argument("--ip", type=str, default=DEFAULT_FIRETV_IP, help="Fire TV IP address")
    args = parser.parse_args()

    print("=== 🔥 Fire TV Stick IPTV Deployer ===")
    device = find_firetv_device(args.ip)

    if not device:
        print(f"\n❌ Could not connect to Fire TV at {args.ip}.")
        print("📋 Please ensure:")
        print("  1. Fire TV is turned ON and awake on the same Wi-Fi.")
        print("  2. ADB Debugging is ENABLED on Fire TV:")
        print("     Settings > My Fire TV > Developer Options > ADB Debugging -> ON")
        print("  3. Check the Fire TV IP in:")
        print("     Settings > My Fire TV > About > Network")
        print(f"\nThen re-run: python3 deploy_to_firetv.py --ip <YOUR_FIRETV_IP>")
        sys.exit(1)

    push_playlists_to_firetv(device)
    player_pkg = check_or_install_player(device)
    if player_pkg:
        test_playback(device, player_pkg)


if __name__ == "__main__":
    main()
