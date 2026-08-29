#!/usr/bin/env python3
"""
Fire TV Stick ADB Setup & Validator
Automates inspection, player detection, configuration push, and validation.
"""

import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

FIRETV_IP = "10.10.6.248"
ADB_PORT = 5555

def run_adb(cmd: List[str], timeout: int = 15) -> Tuple[int, str, str]:
    """Runs an ADB command and returns (returncode, stdout, stderr)."""
    full_cmd = ["adb"] + cmd
    try:
        res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout, res.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)

def connect_device(ip: str = FIRETV_IP) -> bool:
    """Attempts to connect to the Fire TV device via ADB."""
    print(f"Connecting to Fire TV at {ip}:{ADB_PORT}...")
    code, stdout, stderr = run_adb(["connect", f"{ip}:{ADB_PORT}"])
    print(stdout.strip() or stderr.strip())
    return "connected to" in stdout.lower()

def inspect_device() -> Dict[str, str]:
    """Inspects Fire TV OS, CPU arch, model, and storage."""
    info = {}
    
    # Model
    _, out, _ = run_adb(["shell", "getprop", "ro.product.model"])
    info["model"] = out.strip()
    
    # FireOS / Android Version
    _, out, _ = run_adb(["shell", "getprop", "ro.build.version.fireos"])
    info["fireos_version"] = out.strip()
    _, out, _ = run_adb(["shell", "getprop", "ro.build.version.release"])
    info["android_version"] = out.strip()
    
    # CPU ABI
    _, out, _ = run_adb(["shell", "getprop", "ro.product.cpu.abi"])
    info["cpu_abi"] = out.strip()
    
    # Storage
    _, out, _ = run_adb(["shell", "df", "-h", "/data"])
    info["storage_data"] = out.strip()
    
    return info

def check_installed_players() -> List[Dict[str, str]]:
    """Checks for known IPTV players installed on the Fire TV."""
    known_packages = {
        "org.xbmc.kodi": "Kodi",
        "ar.tvplayer.tv": "TiviMate",
        "com.nst.iptvsmarterstvbox": "IPTV Smarters Pro",
        "com.maccabi.ottnavigator": "OTT Navigator",
        "org.videolan.vlc": "VLC for Android",
        "com.google.android.youtube.tv": "YouTube TV"
    }
    
    _, out, _ = run_adb(["shell", "pm", "list", "packages"])
    installed_lines = out.splitlines()
    installed_pkgs = {line.replace("package:", "").strip() for line in installed_lines}
    
    found = []
    for pkg, name in known_packages.items():
        if pkg in installed_pkgs:
            # Get version
            _, v_out, _ = run_adb(["shell", "dumpsys", "package", pkg])
            version = "unknown"
            for line in v_out.splitlines():
                if "versionName=" in line:
                    version = line.split("versionName=")[-1].strip()
                    break
            found.append({"name": name, "package": pkg, "version": version})
            
    return found

if __name__ == "__main__":
    if connect_device():
        print("\n--- Device Information ---")
        for k, v in inspect_device().items():
            print(f"{k}: {v}")
            
        print("\n--- Installed IPTV Players ---")
        players = check_installed_players()
        if players:
            for p in players:
                print(f"- {p['name']} ({p['package']}) Version: {p['version']}")
        else:
            print("No known IPTV players found.")
    else:
        print(f"Could not connect to {FIRETV_IP}. Check Wi-Fi connection and ADB Debugging on Fire TV.")
