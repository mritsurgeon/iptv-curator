"""Base interfaces, candidate models, and conditional HTTPS helpers for discovery providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import gzip
import hashlib
import json
import ssl
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class CandidateChannel:
    """Standardised candidate channel returned by all discovery providers."""

    name: str
    stream_url: str
    source_id: str
    source_url: str = ""
    extinf: str = ""
    tvg_id: str = ""
    tvg_name: str = ""
    tvg_logo: str = ""
    group_title: str = ""
    broadcaster: str = ""
    country: str = ""  # Optional metadata only; never used for filtering/scoring
    language_hint: str = ""
    stream_format: str = ""
    repo_name: str = ""
    repo_url: str = ""
    file_path: str = ""
    record_ref: str = ""
    discovery_query: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


def extract_repo_provenance(source_url: str) -> Dict[str, str]:
    """
    Extract repository name, repository URL, and file path from raw Git hosting URLs.
    Returns empty strings for non-repository feeds (never fabricates repo metadata).
    """
    if not source_url:
        return {"repo_name": "", "repo_url": "", "file_path": ""}
    parsed = urllib.parse.urlsplit(source_url)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]

    if host == "raw.githubusercontent.com" and len(parts) >= 4:
        owner, repo, _branch = parts[0], parts[1], parts[2]
        fpath = "/".join(parts[3:])
        return {
            "repo_name": f"{owner}/{repo}",
            "repo_url": f"https://github.com/{owner}/{repo}",
            "file_path": fpath,
        }
    if host == "gitlab.com" and "-" in parts and "raw" in parts:
        dash_idx = parts.index("-")
        if dash_idx >= 2 and len(parts) > dash_idx + 3:
            repo_name = "/".join(parts[:dash_idx])
            fpath = "/".join(parts[dash_idx + 3 :])
            return {
                "repo_name": repo_name,
                "repo_url": f"https://gitlab.com/{repo_name}",
                "file_path": fpath,
            }
    if host == "codeberg.org" and "raw" in parts and "branch" in parts:
        raw_idx = parts.index("raw")
        if raw_idx >= 2 and len(parts) > raw_idx + 3:
            repo_name = "/".join(parts[:raw_idx])
            fpath = "/".join(parts[raw_idx + 3 :])
            return {
                "repo_name": repo_name,
                "repo_url": f"https://codeberg.org/{repo_name}",
                "file_path": fpath,
            }
    return {"repo_name": "", "repo_url": "", "file_path": ""}


@dataclass
class CandidateEPG:
    """Standardised candidate EPG source discovered by providers."""

    url: str
    source_id: str
    fmt: str = "xml"
    source_name: str = ""


@dataclass
class DiscoveryResult:
    """Standardised result bundle returned by a provider run."""

    source_id: str
    success: bool
    channels: List[CandidateChannel] = field(default_factory=list)
    epg_urls: List[CandidateEPG] = field(default_factory=list)
    error: str = ""


def http_get_conditional(
    url: str,
    etag: str = "",
    last_modified: str = "",
    prev_sha256: str = "",
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 12.0,
    retries: int = 2,
    backoff_sec: float = 1.0,
) -> Dict[str, Any]:
    """
    Perform conditional HTTPS GET with standard certificate validation,
    ETag / If-Modified-Since headers, SHA-256 delta check, and retry/backoff.
    """
    req_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        req_headers.update(headers)
    if etag:
        req_headers["If-None-Match"] = etag
    if last_modified:
        req_headers["If-Modified-Since"] = last_modified

    ssl_ctx = ssl.create_default_context()
    last_err: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                raw = resp.read()
                new_etag = resp.headers.get("ETag", "") or ""
                new_lm = resp.headers.get("Last-Modified", "") or ""
                digest = hashlib.sha256(raw).hexdigest()
                if prev_sha256 and digest == prev_sha256:
                    return {
                        "not_modified": True,
                        "data": b"",
                        "etag": new_etag or etag,
                        "last_modified": new_lm or last_modified,
                        "sha256": digest,
                    }
                data = raw
                if url.lower().endswith(".gz") or raw[:2] == b"\x1f\x8b":
                    try:
                        data = gzip.decompress(raw)
                    except Exception:
                        data = raw
                return {
                    "not_modified": False,
                    "data": data,
                    "etag": new_etag,
                    "last_modified": new_lm,
                    "sha256": digest,
                }
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return {
                    "not_modified": True,
                    "data": b"",
                    "etag": etag,
                    "last_modified": last_modified,
                    "sha256": prev_sha256,
                }
            last_err = e
            if e.code in (429, 503) and attempt < retries:
                retry_after = e.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff_sec * (2 ** attempt)
                time.sleep(min(wait, 10.0))
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff_sec * (2 ** attempt))
                continue
            raise
    if last_err:
        raise last_err
    return {"not_modified": True, "data": b"", "etag": etag, "last_modified": last_modified, "sha256": prev_sha256}


def http_get_bytes(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    retries: int = 2,
    backoff_sec: float = 1.0,
) -> bytes:
    res = http_get_conditional(
        url, headers=headers, timeout=timeout, retries=retries, backoff_sec=backoff_sec
    )
    return res["data"]


def http_get_text(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    retries: int = 2,
) -> str:
    return http_get_bytes(url, headers=headers, timeout=timeout, retries=retries).decode(
        "utf-8", errors="ignore"
    )


def http_get_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    retries: int = 2,
) -> Any:
    return json.loads(http_get_text(url, headers=headers, timeout=timeout, retries=retries))


def normalize_stream_url(url: str) -> str:
    """Normalize stream URL for consistent deduplication."""
    cleaned = url.strip()
    if not cleaned:
        return ""
    parsed = urllib.parse.urlsplit(cleaned)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def detect_stream_format(url: str) -> str:
    low = url.lower().split("?")[0]
    if low.endswith(".m3u8") or "m3u8" in low:
        return "hls"
    if low.endswith(".mpd"):
        return "dash"
    if low.endswith(".ts"):
        return "mpegts"
    if low.startswith("rtmp://"):
        return "rtmp"
    return "unknown"


class BaseDiscoveryProvider(ABC):
    """Abstract base class for all IPTV & EPG discovery providers."""

    source_id: str = "base"

    def __init__(self, source_config: Dict[str, Any], queries_config: Dict[str, Any]):
        self.source_config = source_config
        self.queries_config = queries_config
        self.source_id = source_config.get("source_id", self.source_id)

    @abstractmethod
    def discover(self, max_results: int = 30) -> DiscoveryResult:
        """Execute discovery and return standardised CandidateChannel & CandidateEPG items."""
        raise NotImplementedError
