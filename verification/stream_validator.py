"""
Unified Stream Verification Gatekeeper for IPTV Curator 2.0.
Reuses the existing HTTP -> ffprobe -> 3-second ffmpeg live decode verification
with normal HTTPS certificate validation (no ssl.CERT_NONE).
"""

import concurrent.futures
from datetime import datetime, timezone
import json
import ssl
import subprocess
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

from database.models import VerificationResultRecord
from discovery.base_provider import DEFAULT_USER_AGENT, detect_stream_format


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_stream_opening(
    url: str,
    http_timeout: float = 4.0,
    ffprobe_timeout: float = 5.0,
    ffmpeg_decode_sec: int = 3,
    ffmpeg_timeout: float = 6.0,
    user_agent: str = DEFAULT_USER_AGENT,
) -> VerificationResultRecord:
    """
    Existing stream-opening and playback verification gatekeeper:
      1. HTTP connectivity & free accessibility (normal TLS cert validation)
      2. ffprobe video/audio/resolution/codec/language inspection
      3. Live ffmpeg packet decode test
    """
    checked_at = _utc_now()
    fmt = detect_stream_format(url)
    ssl_ctx = ssl.create_default_context()

    # 1. HTTP Connectivity, Auth Check & TTFB Latency
    t0 = time.time()
    latency_ms = 0.0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=http_timeout, context=ssl_ctx) as resp:
            latency_ms = round((time.time() - t0) * 1000, 1)
            if resp.status in (401, 403):
                return VerificationResultRecord(
                    url=url,
                    checked_at=checked_at,
                    is_accessible=False,
                    requires_auth=True,
                    has_video=False,
                    has_audio=False,
                    playback_ok=False,
                    stream_format=fmt,
                    latency_ms=latency_ms,
                    failure_reason=f"http_{resp.status}_auth_required",
                )
            if resp.status not in (200, 206, 301, 302, 307, 308):
                return VerificationResultRecord(
                    url=url,
                    checked_at=checked_at,
                    is_accessible=False,
                    requires_auth=False,
                    has_video=False,
                    has_audio=False,
                    playback_ok=False,
                    stream_format=fmt,
                    latency_ms=latency_ms,
                    failure_reason=f"http_status_{resp.status}",
                )
    except urllib.error.HTTPError as e:
        latency_ms = round((time.time() - t0) * 1000, 1)
        is_auth = e.code in (401, 403)
        return VerificationResultRecord(
            url=url,
            checked_at=checked_at,
            is_accessible=False,
            requires_auth=is_auth,
            has_video=False,
            has_audio=False,
            playback_ok=False,
            stream_format=fmt,
            latency_ms=latency_ms,
            failure_reason=f"http_error_{e.code}",
        )
    except Exception as e:
        return VerificationResultRecord(
            url=url,
            checked_at=checked_at,
            is_accessible=False,
            requires_auth=False,
            has_video=False,
            has_audio=False,
            playback_ok=False,
            stream_format=fmt,
            latency_ms=0.0,
            failure_reason=f"connection_error: {type(e).__name__}",
        )

    # 2. FFprobe inspection for video, audio, resolution, codecs & audio languages
    has_video = False
    has_audio = False
    res_str = ""
    fps = 0
    vcodec = ""
    acodec = ""
    audio_languages: List[str] = []

    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            "-user_agent",
            user_agent,
            url,
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=ffprobe_timeout)
        if p.returncode != 0 or not p.stdout:
            return VerificationResultRecord(
                url=url,
                checked_at=checked_at,
                is_accessible=True,
                requires_auth=False,
                has_video=False,
                has_audio=False,
                playback_ok=False,
                stream_format=fmt,
                latency_ms=latency_ms,
                failure_reason="ffprobe_failed",
            )
        probe_data = json.loads(p.stdout)
        for s in probe_data.get("streams", []):
            ctype = s.get("codec_type")
            if ctype == "video" and not has_video:
                has_video = True
                vcodec = s.get("codec_name", "")
                w, h = int(s.get("width") or 0), int(s.get("height") or 0)
                fps_s = s.get("r_frame_rate", "0/1")
                try:
                    num, den = fps_s.split("/")
                    fps = round(float(num) / float(den)) if float(den) > 0 else 0
                except Exception:
                    fps = 0
                if h >= 2160 or w >= 3840:
                    res_str = f"2160p ({fps}fps)" if fps else "2160p 4K"
                elif h >= 1080 or w >= 1920:
                    res_str = f"1080p ({fps}fps)" if fps else "1080p FHD"
                elif h >= 720 or w >= 1280:
                    res_str = f"720p ({fps}fps)" if fps else "720p HD"
                elif h > 0:
                    res_str = f"{w}x{h}"
            elif ctype == "audio":
                has_audio = True
                if not acodec:
                    acodec = s.get("codec_name", "")
                lang = (s.get("tags") or {}).get("language", "").lower().strip()
                if lang and lang not in audio_languages:
                    audio_languages.append(lang)
    except Exception as e:
        return VerificationResultRecord(
            url=url,
            checked_at=checked_at,
            is_accessible=True,
            requires_auth=False,
            has_video=False,
            has_audio=False,
            playback_ok=False,
            stream_format=fmt,
            latency_ms=latency_ms,
            failure_reason=f"ffprobe_exception: {type(e).__name__}",
        )

    if not has_video:
        return VerificationResultRecord(
            url=url,
            checked_at=checked_at,
            is_accessible=True,
            requires_auth=False,
            has_video=False,
            has_audio=has_audio,
            playback_ok=False,
            stream_format=fmt,
            audio_codec=acodec,
            audio_languages=audio_languages,
            latency_ms=latency_ms,
            failure_reason="no_video_stream",
        )

    # 3. Live 3-second ffmpeg packet decode test
    try:
        cmd_decode = [
            "ffmpeg",
            "-v",
            "error",
            "-user_agent",
            user_agent,
            "-t",
            str(ffmpeg_decode_sec),
            "-i",
            url,
            "-f",
            "null",
            "-",
        ]
        p_decode = subprocess.run(cmd_decode, capture_output=True, text=True, timeout=ffmpeg_timeout)
        if p_decode.returncode != 0:
            return VerificationResultRecord(
                url=url,
                checked_at=checked_at,
                is_accessible=True,
                requires_auth=False,
                has_video=has_video,
                has_audio=has_audio,
                playback_ok=False,
                stream_format=fmt,
                resolution=res_str,
                video_codec=vcodec,
                audio_codec=acodec,
                audio_languages=audio_languages,
                latency_ms=latency_ms,
                fps=fps,
                failure_reason="ffmpeg_decode_failed",
            )
    except Exception as e:
        return VerificationResultRecord(
            url=url,
            checked_at=checked_at,
            is_accessible=True,
            requires_auth=False,
            has_video=has_video,
            has_audio=has_audio,
            playback_ok=False,
            stream_format=fmt,
            resolution=res_str,
            video_codec=vcodec,
            audio_codec=acodec,
            audio_languages=audio_languages,
            latency_ms=latency_ms,
            fps=fps,
            failure_reason=f"ffmpeg_exception: {type(e).__name__}",
        )

    return VerificationResultRecord(
        url=url,
        checked_at=checked_at,
        is_accessible=True,
        requires_auth=False,
        has_video=has_video,
        has_audio=has_audio,
        playback_ok=True,
        stream_format=fmt,
        resolution=res_str,
        video_codec=vcodec,
        audio_codec=acodec,
        audio_languages=audio_languages,
        latency_ms=latency_ms,
        fps=fps,
        failure_reason="",
    )


class StreamValidator:
    """Concurrent batch runner around the single stream-opening gatekeeper."""

    def __init__(
        self,
        max_workers: int = 25,
        http_timeout: float = 4.0,
        ffprobe_timeout: float = 5.0,
        ffmpeg_decode_sec: int = 3,
        ffmpeg_timeout: float = 6.0,
        validator_fn: Optional[Any] = None,
    ):
        self.max_workers = max_workers
        self.http_timeout = http_timeout
        self.ffprobe_timeout = ffprobe_timeout
        self.ffmpeg_decode_sec = ffmpeg_decode_sec
        self.ffmpeg_timeout = ffmpeg_timeout
        self._validator_fn = validator_fn or validate_stream_opening

    def verify_one(self, url: str) -> VerificationResultRecord:
        if self._validator_fn is validate_stream_opening:
            return validate_stream_opening(
                url,
                http_timeout=self.http_timeout,
                ffprobe_timeout=self.ffprobe_timeout,
                ffmpeg_decode_sec=self.ffmpeg_decode_sec,
                ffmpeg_timeout=self.ffmpeg_timeout,
            )
        return self._validator_fn(url)

    def verify_batch(self, urls: List[str]) -> Dict[str, VerificationResultRecord]:
        results: Dict[str, VerificationResultRecord] = {}
        if not urls:
            return results
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            future_map = {ex.submit(self.verify_one, u): u for u in urls}
            for fut in concurrent.futures.as_completed(future_map):
                u = future_map[fut]
                try:
                    results[u] = fut.result()
                except Exception as e:
                    results[u] = VerificationResultRecord(
                        url=u,
                        checked_at=_utc_now(),
                        is_accessible=False,
                        requires_auth=False,
                        has_video=False,
                        has_audio=False,
                        playback_ok=False,
                        failure_reason=f"batch_error: {type(e).__name__}",
                    )
        return results

