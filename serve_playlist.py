#!/usr/bin/env python3
"""
Enhanced Local IPTV Playlist Server & PVR Client Monitor
Serves verified playlists and monitors client connections in real-time.
Logs all requests from Kodi PVR Simple Client, TiviMate, OTT Navigator, VLC, etc.
"""

import http.server
import os
import socket
import socketserver
import sys
import time
from datetime import datetime

PORT = 8080
PLAYLIST_FILE = "/Users/ian/code/IPTV/verified_premier_sports.m3u8"
ACCESS_LOG = "/Users/ian/code/IPTV/pvr_access_log.txt"


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "10.10.3.185"
    finally:
        s.close()
    return ip


def log_access(client_ip: str, user_agent: str, path: str, method: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] 📺 {client_ip} ({user_agent}) ➔ {method} {path}"
    print(entry, flush=True)
    with open(ACCESS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{entry}\n")


class PlaylistHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Custom logger handled in do_GET/HEAD

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Type", "application/vnd.apple.mpegurl; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        client_ip = self.client_address[0]
        user_agent = self.headers.get("User-Agent", "Unknown Client")
        log_access(client_ip, user_agent, self.path, "HEAD")

        content = self.get_playlist_content(self.path)
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()

    def do_GET(self):
        client_ip = self.client_address[0]
        user_agent = self.headers.get("User-Agent", "Unknown Client")
        log_access(client_ip, user_agent, self.path, "GET")

        clean_path = self.path.split("?")[0].strip("/")

        # If Kodi File Manager browses the root directory, return an HTML directory listing
        if not clean_path or clean_path == "":
            html = """<!DOCTYPE html>
<html>
<head><title>IPTV Playlist Server</title></head>
<body>
<h2>📺 IPTV Playlists</h2>
<ul>
  <li><a href="/sports.m3u8">sports.m3u8 (Master Flagship Sports)</a></li>
  <li><a href="/verified_football.m3u8">verified_football.m3u8 (Football & Soccer)</a></li>
  <li><a href="/verified_cricket.m3u8">verified_cricket.m3u8 (Cricket)</a></li>
  <li><a href="/verified_rugby.m3u8">verified_rugby.m3u8 (Rugby)</a></li>
</ul>
</body>
</html>"""
            content = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        content = self.get_playlist_content(self.path)
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def get_playlist_content(self, path: str) -> bytes:
        clean_path = path.split("?")[0].strip("/")
        
        target_file = PLAYLIST_FILE
        if "football" in clean_path:
            target_file = "/Users/ian/code/IPTV/verified_football.m3u8"
        elif "cricket" in clean_path:
            target_file = "/Users/ian/code/IPTV/verified_cricket.m3u8"
        elif "rugby" in clean_path:
            target_file = "/Users/ian/code/IPTV/verified_rugby.m3u8"
        elif os.path.exists(os.path.join("/Users/ian/code/IPTV", clean_path)):
            target_file = os.path.join("/Users/ian/code/IPTV", clean_path)

        try:
            with open(target_file, "rb") as f:
                return f.read()
        except Exception:
            with open(PLAYLIST_FILE, "rb") as f:
                return f.read()


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    local_ip = get_local_ip()
    print("=" * 75)
    print(f"📡 IPTV Playlist Server & PVR Client Monitor Active")
    print("=" * 75)
    print(f"👉 Local Server IP: http://{local_ip}:{PORT}/sports.m3u8")
    print(f"📝 Logging connections to: {ACCESS_LOG}")
    print("=" * 75)

    with ReusableTCPServer(("", PORT), PlaylistHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")


if __name__ == "__main__":
    main()
