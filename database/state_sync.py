"""
Durable SQLite state persistence via an orphan Git branch ('state').
Provides:
  - Pull & SHA-256 + PRAGMA integrity_check validation
  - Atomic VACUUM + gzip compression + manifest generation
  - Optimistic stale-write guard against concurrent pushes
  - Hash-gated change detection for main-branch playlist commits
"""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sqlite_integrity(db_path: str) -> bool:
    """Return True iff SQLite PRAGMA integrity_check passes."""
    if not os.path.exists(db_path):
        return False
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row and row[0] == "ok")
    except Exception:
        return False
    finally:
        conn.close()


def prepare_snapshot(db_path: str, gz_out_path: str, manifest_path: str, active_hash: str = "") -> Dict:
    """Checkpoint WAL, VACUUM, verify integrity, gzip-compress, and write manifest.json."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"SQLite integrity check failed before snapshot: {row}")
        ver_row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        schema_ver = int(ver_row[0]) if ver_row and ver_row[0] else 0
    finally:
        conn.close()

    os.makedirs(os.path.dirname(os.path.abspath(gz_out_path)), exist_ok=True)
    with open(db_path, "rb") as f_in, gzip.open(gz_out_path, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)

    manifest = {
        "sha256": sha256_file(gz_out_path),
        "schema_version": schema_ver,
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "utc_timestamp": datetime.now(timezone.utc).isoformat(),
        "active_outputs_hash": active_hash,
    }
    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)
    return manifest


def restore_snapshot(gz_path: str, manifest_path: str, db_out_path: str) -> bool:
    """Validate SHA-256 manifest, decompress snapshot, and run PRAGMA integrity_check."""
    if not os.path.exists(gz_path):
        return False
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as mf:
            manifest = json.load(mf)
        expected = manifest.get("sha256", "")
        if expected and sha256_file(gz_path) != expected:
            return False

    os.makedirs(os.path.dirname(os.path.abspath(db_out_path)), exist_ok=True)
    tmp_db = db_out_path + ".tmp"
    with gzip.open(gz_path, "rb") as f_in, open(tmp_db, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    if not verify_sqlite_integrity(tmp_db):
        os.remove(tmp_db)
        return False
    os.replace(tmp_db, db_out_path)
    return True


def compute_active_outputs_hash(catalogue_path: str = "channel_catalogue.json") -> str:
    """
    Compute a deterministic hash over active (channel_id, url, tvg_id, sports) tuples,
    ignoring volatile timestamps so no-op runs do not trigger noisy Git commits.
    """
    if not os.path.exists(catalogue_path):
        return ""
    with open(catalogue_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    canonical_items: List[Dict] = []
    for ch in data.get("channels", []):
        if ch.get("verification", {}).get("playback_status") != "working":
            continue
        canonical_items.append(
            {
                "id": ch.get("channel_id"),
                "url": ch.get("stream", {}).get("url"),
                "tvg_id": ch.get("epg", {}).get("tvg_id"),
                "sports": ch.get("sports", []),
                "lang": ch.get("language", {}).get("primary"),
            }
        )
    canonical_items.sort(key=lambda x: str(x["id"]))
    raw = json.dumps(canonical_items, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _get_remote_branch_sha(branch: str = "state") -> str:
    res = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        capture_output=True,
        text=True,
    )
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip().split()[0]
    return ""


def pull_state_branch(db_path: str = "data/iptv_curator.db", branch: str = "state", sha_file: str = "data/.state_sha") -> bool:
    """Fetch orphan state branch and restore SQLite DB with integrity verification."""
    remote_sha = _get_remote_branch_sha(branch)
    os.makedirs(os.path.dirname(os.path.abspath(sha_file)), exist_ok=True)
    with open(sha_file, "w", encoding="utf-8") as f:
        f.write(remote_sha)

    if not remote_sha:
        print(f"ℹ️ Remote branch '{branch}' does not exist yet; starting fresh DB.")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        res = subprocess.run(
            ["git", "clone", "--depth", "2", "--branch", branch, "--single-branch", ".", tmp],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            # Fetch directly from origin
            subprocess.run(["git", "fetch", "--depth=2", "origin", branch], capture_output=True)
            res = subprocess.run(
                ["git", "clone", "--depth", "2", "--branch", branch, "--single-branch", ".", tmp],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                return False

        gz_p = os.path.join(tmp, "iptv_curator.db.gz")
        mf_p = os.path.join(tmp, "manifest.json")
        if restore_snapshot(gz_p, mf_p, db_path):
            print(f"✅ Restored verified SQLite state from '{branch}' ({remote_sha[:8]}).")
            return True

        # Fallback recovery: try HEAD~1 if latest snapshot was corrupted
        chk = subprocess.run(["git", "-C", tmp, "checkout", "HEAD~1"], capture_output=True)
        if chk.returncode == 0 and restore_snapshot(gz_p, mf_p, db_path):
            print(f"⚠️ Recovered SQLite state from previous snapshot HEAD~1 on '{branch}'.")
            return True
    return False


def push_state_branch(
    db_path: str = "data/iptv_curator.db",
    branch: str = "state",
    sha_file: str = "data/.state_sha",
    catalogue_path: str = "channel_catalogue.json",
) -> bool:
    """Atomically publish compressed SQLite snapshot to orphan state branch with stale-write guard."""
    if not os.path.exists(db_path):
        print("⚠️ No database file to publish.")
        return False

    expected_sha = ""
    if os.path.exists(sha_file):
        with open(sha_file, "r", encoding="utf-8") as f:
            expected_sha = f.read().strip()

    current_remote_sha = _get_remote_branch_sha(branch)
    if expected_sha and current_remote_sha and current_remote_sha != expected_sha:
        print(f"⚠️ Stale-write guard triggered: remote '{branch}' moved ({expected_sha[:8]} -> {current_remote_sha[:8]}). Skipping push.")
        return False

    active_hash = compute_active_outputs_hash(catalogue_path)
    with tempfile.TemporaryDirectory() as tmp:
        gz_p = os.path.join(tmp, "iptv_curator.db.gz")
        mf_p = os.path.join(tmp, "manifest.json")
        prepare_snapshot(db_path, gz_p, mf_p, active_hash=active_hash)

        remote_url = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
        extra_hdr = subprocess.run(
            ["git", "config", "--get", "http.https://github.com/.extraheader"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", tmp, "init", "-b", branch], check=True, capture_output=True)
        subprocess.run(["git", "-C", tmp, "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "-C", tmp, "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "-C", tmp, "add", "iptv_curator.db.gz", "manifest.json"], check=True)
        subprocess.run(["git", "-C", tmp, "commit", "-m", f"State snapshot {active_hash[:8]} [skip ci]"], check=True, capture_output=True)
        push_cmd = ["git", "-C", tmp]
        if extra_hdr:
            push_cmd.extend(["-c", f"http.https://github.com/.extraheader={extra_hdr}"])
        elif os.environ.get("GITHUB_TOKEN") and remote_url.startswith("https://github.com/"):
            remote_url = remote_url.replace(
                "https://github.com/",
                f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/",
            )
        push_cmd.extend(["push", "--force", remote_url, f"HEAD:{branch}"])
        push = subprocess.run(push_cmd, capture_output=True, text=True)
        if push.returncode == 0:
            print(f"☁️ Published verified SQLite snapshot to orphan branch '{branch}'.")
            return True
        print(f"⚠️ Failed to push state branch: {push.stderr}")
        return False


def main():
    parser = argparse.ArgumentParser(description="SQLite orphan state branch sync utility")
    parser.add_argument("action", choices=["pull", "push", "hash"])
    parser.add_argument("--db", default="data/iptv_curator.db")
    parser.add_argument("--branch", default="state")
    args = parser.parse_args()

    if args.action == "pull":
        pull_state_branch(db_path=args.db, branch=args.branch)
    elif args.action == "push":
        ok = push_state_branch(db_path=args.db, branch=args.branch)
        sys.exit(0 if ok else 1)
    elif args.action == "hash":
        print(compute_active_outputs_hash())


if __name__ == "__main__":
    main()

