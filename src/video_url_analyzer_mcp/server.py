"""
Video Analyzer MCP Server
Unified MCP server for analyzing videos from YouTube, TikTok, and Instagram
using Google's Gemini API with full audio + visual analysis.
"""

import hashlib
import ipaddress
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import Request as UrllibRequest
from urllib.request import urlopen as urllib_urlopen
from urllib.error import URLError

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.utilities.types import Audio, Image
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field
from typing import Any, Literal, Optional, cast

from .slideshow import (
    SlideshowError,
    analyze_slideshow,
    detect_post_type,
    download_slideshow,
)

DetailLevel = Literal["compact", "standard", "full"]


class VideoMoment(BaseModel):
    start: str = Field(description="Timestamp in MM:SS or HH:MM:SS format")
    end: str = Field(description="Timestamp in MM:SS or HH:MM:SS format")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=300)
    audio_evidence: list[str] = Field(default_factory=list, max_length=10)
    visual_evidence: list[str] = Field(default_factory=list, max_length=10)


class MomentSearchResult(BaseModel):
    query: str
    analyzed_by: Literal["gemini"]
    model_used: str
    detail: Literal["compact", "standard", "full"]
    moments: list[VideoMoment]
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    full_result_ref: Optional[str] = None


class EvidenceItem(BaseModel):
    timestamp: str
    type: Literal["audio", "visual", "text_on_screen", "mixed"]
    detail: str = Field(max_length=200)


class SegmentAnalysisResult(BaseModel):
    analyzed_by: Literal["gemini"]
    model_used: str
    detail: Literal["compact", "standard", "full"]
    segment_start: str
    segment_end: str
    direct_answer: str
    concise_summary: str
    key_points: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    full_result_ref: Optional[str] = None
    extended_analysis: Optional[str] = None


class VideoContextEvidence(BaseModel):
    timestamp: str
    type: Literal["visual", "audio", "text_on_screen", "mixed"]
    detail: str
    confidence: float = Field(ge=0.0, le=1.0)


class VideoContextSegment(BaseModel):
    start: str
    end: str
    summary: str
    visual_summary: str
    audio_summary: str
    text_on_screen_summary: str
    entities: list[str]
    objects: list[str]
    actions: list[str]
    topics: list[str]
    evidence: list[VideoContextEvidence]
    confidence: float = Field(ge=0.0, le=1.0)


class ContextQuality(BaseModel):
    coverage_score: float = Field(ge=0.0, le=1.0, default=0.5)
    timeline_density: float = Field(ge=0.0, default=0.0)
    evidence_count: int = Field(ge=0, default=0)
    missing_modalities: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class VideoContextAnswerEvidence(BaseModel):
    timestamp: str
    type: str
    detail: str
    segment_start: str | None = None
    segment_end: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class FollowupAnswer(BaseModel):
    asked_at: str
    question: str
    answer: str
    evidence: list[VideoContextAnswerEvidence] = Field(default_factory=list)
    model_used: str


class SavedVideoContext(BaseModel):
    schema_version: str = "1.3.0"
    video_id: str
    url: str
    normalized_url: str
    created_at: str
    updated_at: str
    analyzed_by: Literal["gemini"]
    model_used: str
    detail: Literal["compact", "standard", "full"]
    title_guess: str | None = None
    duration_estimate: str | None = None
    language: str | None = None
    summary: str
    visual_summary: str
    audio_summary: str
    spoken_content_summary: str
    text_on_screen_summary: str
    topics: list[str]
    entities: list[str]
    objects: list[str]
    key_moments: list[VideoContextSegment]
    timeline: list[VideoContextSegment]
    warnings: list[str]
    limitations: list[str]
    raw_full_result_ref: str | None = None
    context_quality: Optional[ContextQuality] = None
    gemini_followups: list[FollowupAnswer] = Field(default_factory=list)


class PrepareVideoContextResult(BaseModel):
    video_id: str
    url: str
    analyzed_by: Literal["gemini"]
    model_used: str
    detail: str
    reused_existing: bool
    gemini_called: bool
    title_guess: str | None
    summary: str
    timeline_count: int
    topics: list[str]
    entities: list[str]
    warnings: list[str]
    limitations: list[str]
    context_ref: str


class AskVideoContextResult(BaseModel):
    video_id: str
    question: str
    answer: str
    source: Literal["saved_video_context", "gemini_reanalysis", "context_missing"]
    gemini_called: bool
    context_missing: bool
    insufficient_context: bool
    evidence: list[VideoContextAnswerEvidence]
    relevant_segments: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str]
    limitations: list[str]
    context_ref: str | None


class VideoAssetResult(BaseModel):
    asset_id: str
    video_id: str
    type: Literal["image", "video_clip"]
    request: str | None = None
    timestamp: str | None = None
    start: str | None = None
    end: str | None = None
    duration_seconds: float | None = None
    mime_type: str
    asset_ref: str
    source_context_ref: str | None = None
    matched_reason: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    gemini_called: bool = False
    reused_existing: bool = False
    insufficient_context: bool = False
    created_at: str
    warnings: list[str]
    limitations: list[str]

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("video-analyzer")

# Default Gemini models. Override priority at call time:
#   1. explicit per-tool ``model=`` argument
#   2. ``GEMINI_FAST_MODEL`` / ``GEMINI_DEEP_MODEL`` env vars (see _resolve_model)
#   3. ``VIDEO_ANALYZER_MODEL`` umbrella env var
#   4. the hardcoded fallbacks below
# ``gemini-flash-latest`` is kept as a documented stable fallback option but is
# no longer the default.
FAST_MODEL_FALLBACK = "gemini-3.5-flash"
DEEP_MODEL_FALLBACK = "gemini-3.1-pro-preview"
_VIDEO_ANALYZER_MODEL_OVERRIDE = (
    os.environ.get("VIDEO_ANALYZER_MODEL", "").strip() or None
)
FAST_MODEL_DEFAULT = _VIDEO_ANALYZER_MODEL_OVERRIDE or FAST_MODEL_FALLBACK
DEEP_MODEL_DEFAULT = _VIDEO_ANALYZER_MODEL_OVERRIDE or DEEP_MODEL_FALLBACK
FAST_SUPPORTS_THINKING = True
DEEP_SUPPORTS_THINKING = True
FAST_SUPPORTS_MEDIA_RESOLUTION = True
DEEP_SUPPORTS_MEDIA_RESOLUTION = True
DEEP_MAX_OUTPUT_TOKENS = 32000

_client: genai.Client | None = None
_client_lock = threading.Lock()


def _model_id(model: str) -> str:
    return model.strip().removeprefix("models/").lower()


def _thinking_config_for_model(
    model: str,
    thinking_name: str = "HIGH",
) -> types.ThinkingConfig | None:
    """Build model-family compatible thinking config.

    Gemini 3 uses thinking_level. Gemini 2.5 rejects thinking_level and uses
    thinking_budget instead.
    """
    normalized = _model_id(model)
    name = thinking_name.strip().lower()
    if normalized.startswith("gemini-3"):
        return types.ThinkingConfig(thinking_level=name)
    if normalized.startswith("gemini-2.5"):
        budget_by_level = {
            "minimal": 0,
            "low": 1024,
            "medium": 4096,
            "high": -1,
        }
        return types.ThinkingConfig(
            thinking_budget=budget_by_level.get(name, -1)
        )
    return None


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer environment override, falling back safely."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s must be an integer; using default %s", name, default)
        return default
    if value < minimum:
        logger.warning("%s must be >= %s; using default %s", name, minimum, default)
        return default
    return value


def _download_timeout(default: int) -> int:
    return _env_int("VIDEO_DOWNLOAD_TIMEOUT", default)


def _ffmpeg_timeout(default: int) -> int:
    return _env_int("VIDEO_FFMPEG_TIMEOUT", default)


def _gemini_http_options() -> types.HttpOptions | None:
    timeout_seconds = _env_int("VIDEO_GEMINI_TIMEOUT", 0, minimum=0)
    if timeout_seconds <= 0:
        return None
    return types.HttpOptions(timeout=timeout_seconds * 1000)


def _get_client() -> genai.Client:
    """Create the Gemini client only when a Gemini-backed tool is called."""
    global _client
    with _client_lock:
        if _client is None:
            key = os.environ.get("GEMINI_API_KEY", "").strip()
            if not key:
                raise RuntimeError(
                    "GEMINI_API_KEY is not set. Configure it in .env or the "
                    "shell environment before calling Gemini-backed tools."
                )
            kwargs: dict[str, Any] = {"api_key": key}
            http_options = _gemini_http_options()
            if http_options is not None:
                kwargs["http_options"] = http_options
            _client = genai.Client(**kwargs)
        return _client


def _gemini_error_status_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None)
    if code is None:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def _is_retryable_gemini_error(exc: Exception) -> bool:
    if isinstance(exc, errors.ClientError):
        return False
    code = _gemini_error_status_code(exc)
    return code is not None and 500 <= code < 600


def _call_gemini_with_retry(operation: str, call):
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except Exception as exc:
            if not _is_retryable_gemini_error(exc) or attempt == max_attempts:
                raise
            code = _gemini_error_status_code(exc)
            delay = 2 ** (attempt - 1)
            logger.warning(
                "Gemini %s transient HTTP %s on attempt %d/%d; retrying in %ss",
                operation,
                code,
                attempt,
                max_attempts,
                delay,
            )
            time.sleep(delay)


def _generate_content_with_retry(**kwargs):
    return _call_gemini_with_retry(
        "generate_content",
        lambda: _get_client().models.generate_content(**kwargs),
    )


def _require_response_text(response: Any) -> str:
    """Return ``response.text`` or raise a structured error if Gemini returned no text.

    Gemini SDK types ``response.text`` as ``str | None``; we surface the empty
    case as a retryable structured error instead of a downstream NoneType crash.
    """
    text = getattr(response, "text", None)
    if not text:
        raise VideoAnalyzerError(
            "Gemini returned an empty response.",
            code="empty_gemini_response",
            retryable=True,
        )
    return text


class VideoAnalyzerError(RuntimeError):
    """Structured runtime error that can be returned safely through MCP."""

    def __init__(
        self,
        message: str,
        code: str = "runtime_error",
        details: Optional[dict[str, Any]] = None,
        retryable: bool = False,
        platform: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.retryable = retryable
        self.platform = platform


def _short_tail(text: str | None, limit: int = 500) -> str:
    return (text or "")[-limit:]


def _exception_details(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, VideoAnalyzerError):
        return {
            "code": exc.code,
            "message": str(exc),
            "retryable": exc.retryable,
            "details": exc.details,
            "platform": exc.platform,
        }
    return {
        "code": "runtime_error",
        "message": str(exc),
        "retryable": False,
        "details": {},
        "platform": None,
    }


def _error_response(
    tool: str,
    message: str,
    code: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    retryable: bool = False,
    video_id: Optional[str] = None,
    platform: Optional[str] = None,
) -> str:
    payload: dict[str, Any] = {
        "status": "error",
        "tool": tool,
        "code": code or "runtime_error",
        "message": message,
        "retryable": bool(retryable),
        "details": details or {},
    }
    if video_id:
        payload["video_id"] = video_id
    if platform:
        payload["platform"] = platform
    return json.dumps(payload, indent=2, ensure_ascii=False)


# Default model — alias to the Phase 0 selected Flash model.
# Override per-call with the `model` arg for anything specific.
DEFAULT_MODEL = FAST_MODEL_DEFAULT


def _build_analysis_config(model: str = DEFAULT_MODEL) -> types.GenerateContentConfig:
    """High-fidelity multimodal analysis config.

    - MEDIA_RESOLUTION_HIGH: processes images at the highest supported
      resolution globally, so small on-screen text (captions, benchmark
      tables, fine details in carousel slides) is readable.
    - Gemini 3 uses ThinkingLevel.HIGH; Gemini 2.5 falls back to dynamic
      thinkingBudget because 2.5 rejects thinkingLevel.
    """
    kwargs: dict[str, Any] = {
        "media_resolution": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
    }
    thinking_config = _thinking_config_for_model(model, "HIGH")
    if thinking_config is not None:
        kwargs["thinking_config"] = thinking_config
    return types.GenerateContentConfig(**kwargs)

# MCP Server
mcp = FastMCP("video-analyzer")

PROJECT_ROOT = Path(__file__).resolve().parent

# Analyses output directory
ANALYSES_DIR = Path(os.environ.get("ANALYSES_DIR", "./analyses"))
ANALYSES_DIR.mkdir(parents=True, exist_ok=True)

# Structured local video memory directory for v1.3.0.
VIDEO_CONTEXT_DIR = Path(os.environ.get("VIDEO_CONTEXT_DIR", "video_contexts"))
VIDEO_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_SOURCE_DIR = Path(os.environ.get("VIDEO_SOURCE_DIR", "video_sources"))
VIDEO_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_ASSET_DIR = Path(os.environ.get("VIDEO_ASSET_DIR", "video_assets"))
VIDEO_ASSET_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Security limits
# ---------------------------------------------------------------------------
MAX_DOWNLOAD_SIZE_MB = 100
MAX_CONCURRENT_JOBS = 10
JOB_EXPIRY_SECONDS = 3600  # 1 hour
MAX_ANALYSES_STORED = 200
MAX_GEMINI_RESPONSE_CHARS = 500_000  # ~500KB text
ENABLE_BROWSER_COOKIES = os.environ.get(
    "VIDEO_ANALYZER_COOKIES", ""
).lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Background job system — prevents Claude Desktop timeout on slow downloads
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _expire_jobs() -> None:
    """Remove completed/failed jobs older than JOB_EXPIRY_SECONDS. Caller must hold _jobs_lock."""
    now = datetime.now()
    expired = [
        jid for jid, job in _jobs.items()
        if job["status"] in ("completed", "failed") and job.get("completed_at")
        and (now - datetime.fromisoformat(job["completed_at"])).total_seconds() > JOB_EXPIRY_SECONDS
    ]
    for jid in expired:
        del _jobs[jid]
    if expired:
        logger.info("Expired %d old background jobs", len(expired))


def _create_job(tool_name: str, url: str) -> str:
    """Create a new background job and return its ID."""
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _expire_jobs()
        active = sum(1 for j in _jobs.values() if j["status"] == "processing")
        if active >= MAX_CONCURRENT_JOBS:
            raise RuntimeError(
                f"Too many concurrent jobs ({active}/{MAX_CONCURRENT_JOBS}). "
                f"Wait for existing jobs to complete."
            )
        _jobs[job_id] = {
            "status": "processing",
            "tool": tool_name,
            "url": url,
            "result": None,
            "error": None,
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
        }
    return job_id


def _coerce_json_result(result: Any) -> Any:
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result
    return result


def _job_error_from_result(result: Any) -> dict[str, Any] | None:
    """Return a structured job error if a completed worker returned failure."""
    if isinstance(result, Exception):
        details = _exception_details(result)
        return {
            "error_code": details["code"],
            "message": details["message"],
            "retryable": details["retryable"],
            "details": details["details"],
        }
    data = _coerce_json_result(result)
    if isinstance(data, dict):
        status = str(data.get("status", "")).lower()
        if status in {"error", "failed"}:
            return {
                "error_code": data.get("code") or data.get("error_code") or status,
                "message": str(data.get("message") or data.get("error") or status),
                "retryable": bool(data.get("retryable", False)),
                "details": data.get("details") if isinstance(data.get("details"), dict) else {},
            }
    text = result if isinstance(result, str) else str(result)
    lowered = text.lower()
    if (
        text.startswith("Error ")
        or "filestate.failed" in lowered
        or "file processing failed" in lowered
        or '"status": "error"' in lowered
        or '"status":"error"' in lowered
        or '"status": "failed"' in lowered
        or '"status":"failed"' in lowered
    ):
        return {
            "error_code": "worker_result_error",
            "message": _short_tail(text, 1000),
            "retryable": False,
            "details": {},
        }
    return None


def _is_error_result(result: Any) -> bool:
    return _job_error_from_result(result) is not None


def _complete_job(job_id: str, result: Any):
    """Mark a job as completed with its result."""
    with _jobs_lock:
        if job_id in _jobs:
            error = _job_error_from_result(result)
            if error is not None:
                _jobs[job_id]["status"] = "failed"
                _jobs[job_id]["error"] = error
                _jobs[job_id]["result"] = result
            else:
                _jobs[job_id]["status"] = "completed"
                _jobs[job_id]["result"] = result
            _jobs[job_id]["completed_at"] = datetime.now().isoformat()


def _fail_job(job_id: str, error: Any):
    """Mark a job as failed with an error message."""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = _job_error_from_result(error) or {
                "error_code": "runtime_error",
                "message": str(error),
                "retryable": False,
                "details": {},
            }
            _jobs[job_id]["completed_at"] = datetime.now().isoformat()


def _run_in_background(job_id: str, func, *args, **kwargs):
    """Run a function in a background thread and update the job on completion."""
    def _worker():
        try:
            result = func(*args, **kwargs)
            _complete_job(job_id, result)
        except Exception as e:
            logger.error("Background job %s failed: %s", job_id, e, exc_info=True)
            _fail_job(job_id, e)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


# Tutorial analysis prompt for watch-and-build workflow
TUTORIAL_ANALYSIS_PROMPT = """You are a technical tutorial analyzer. Watch this video carefully and extract ALL technical information.

Output ONLY valid JSON (no markdown fences, no extra text) with this exact structure:

If the video IS a technical tutorial:
{
  "is_tutorial": true,
  "title": "what the tutorial teaches",
  "summary": "brief description of what you'll learn",
  "language": "language spoken in the video",
  "prerequisites": ["tool1", "package2", "account3"],
  "steps": [
    {
      "step_number": 1,
      "timestamp": "MM:SS",
      "description": "what this step does",
      "commands": ["command1", "command2"],
      "code_snippets": ["code here"],
      "file_paths": ["/path/to/file"],
      "notes": "any warnings or important details"
    }
  ],
  "final_result": "what you'll have when done",
  "tools_mentioned": ["tool1", "tool2"],
  "urls_mentioned": ["https://..."]
}

If the video is NOT a technical tutorial:
{
  "is_tutorial": false,
  "description": "what the video is actually about",
  "category": "entertainment/news/review/vlog/other"
}

Be extremely thorough. Extract every command, every file path, every code snippet, every URL shown or mentioned. Do not skip anything."""


SLIDESHOW_TUTORIAL_ANALYSIS_PROMPT = """You are analyzing a photo/slideshow post as a technical visual tutorial.

Output ONLY valid JSON (no markdown fences, no extra text) with this exact structure:

{
  "title": "short title for the post",
  "post_type": "slideshow",
  "platform": "tiktok/instagram/youtube_community",
  "prerequisites": [],
  "steps": [
    {
      "image_index": 1,
      "description": "what this slide teaches or shows",
      "extracted_text": "all readable text in this image",
      "key_visual_elements": ["important object, UI element, chart, or code visible"]
    }
  ],
  "summary": "overall tutorial or informational takeaway"
}

Use 1-based image_index values matching the original slide order. If the post is not a tutorial, still summarize the visual content using the same slideshow structure."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "instagram.com", "www.instagram.com",
}


def validate_url(url: str) -> None:
    """Validate that a URL is an allowed video platform. Raises ValueError if blocked."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Blocked URL scheme: {parsed.scheme!r}. Only http/https allowed.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("URL has no hostname.")

    # Block internal/private IPs
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_reserved:
            raise ValueError(f"Blocked internal IP: {hostname}")
    except ValueError as ip_err:
        if "Blocked" in str(ip_err):
            raise
        # Not an IP — check hostname allowlist

    if hostname in ("localhost",):
        raise ValueError(f"Blocked hostname: {hostname}")

    # Check against allowlist
    if hostname not in ALLOWED_HOSTS:
        raise ValueError(
            f"Unsupported host: {hostname!r}. "
            f"Allowed: YouTube, TikTok, Instagram."
        )


def detect_platform(url: str) -> str:
    """Detect the video platform from a URL."""
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "tiktok.com" in url_lower or "vm.tiktok.com" in url_lower or "vt.tiktok.com" in url_lower:
        return "tiktok"
    if "instagram.com" in url_lower:
        return "instagram"
    return "other"


def _is_tiktok_url(url: str) -> bool:
    return detect_platform(url) == "tiktok"


def _safe_yt_dlp_format_selector_for_url(url: str) -> Optional[str]:
    """Return a platform-specific selector that avoids unsupported codecs."""
    if not _is_tiktok_url(url):
        return None
    return (
        "b[ext=mp4][vcodec^=h264]/"
        "b[ext=mp4][vcodec^=avc1]/"
        "b[ext=mp4][vcodec^=h265]/"
        "b[ext=mp4][vcodec^=hvc1]/"
        "b[ext=mp4][vcodec^=hev1]/"
        "b[ext=mp4][vcodec^=hevc]/"
        "b[ext=mp4][vcodec!*=bvc][vcodec!*=bytevc2]"
    )


def _safe_yt_dlp_sort_for_url(url: str) -> Optional[str]:
    if not _is_tiktok_url(url):
        return None
    return "+codec:avc:m4a,res,ext:mp4:m4a"


def _normalize_youtube_url(url: str) -> str:
    """Normalize YouTube URL to standard format."""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if "youtu.be" in hostname:
        video_id = parsed.path.lstrip("/")
    else:
        qs = parse_qs(parsed.query)
        video_id = qs.get("v", [None])[0]
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return url


def _download_media_url(url: str, dest_path: str, timeout: int = 60) -> bytes | None:
    """Fetch a public media URL to disk. Uses urllib (system DNS) then curl_cffi.

    Returns the bytes written on success, None on failure.
    """
    timeout = _download_timeout(timeout)
    # urllib uses the OS DNS resolver, which is more reliable on Windows than
    # curl_cffi's bundled resolver for some CDN shards (observed for IG fbcdn).
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            req = UrllibRequest(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.instagram.com/",
                },
            )
            with urllib_urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if data:
                    with open(dest_path, "wb") as f:
                        f.write(data)
                    return data
                last_err = RuntimeError("empty body")
        except URLError as e:
            last_err = e
        except Exception as e:
            last_err = e
        time.sleep(0.8 * (attempt + 1))

    # Last-resort fallback: curl_cffi (impersonated TLS)
    try:
        from curl_cffi import requests as cffi_requests
        r = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
        if r.status_code == 200 and r.content:
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return r.content
        last_err = RuntimeError(f"curl_cffi HTTP {r.status_code}")
    except Exception as e:
        last_err = e

    logger.warning("Failed to download %s: %s", url[:80], last_err)
    return None


def _download_tiktok_api(url: str, tmp_dir: str) -> list[str] | None:
    """Download a TikTok video OR photo-slideshow using the tikwm.com API.

    Returns a list of local file paths on success, or None on failure.
    For videos, returns [video.mp4]. For photo posts, returns [img_0.jpg, ...].
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.warning("curl_cffi not installed — skipping TikTok API fallback")
        return None

    logger.info("Trying TikTok API fallback (tikwm.com)...")
    try:
        resp = cffi_requests.post(
            "https://www.tikwm.com/api/",
            data={"url": url, "hd": "1"},
            impersonate="chrome",
            timeout=_download_timeout(20),
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("tikwm API error: %s", data.get("msg", "unknown"))
            return None

        video_data = data.get("data", {})

        # Photo-slideshow post: tikwm returns "images" array of URLs
        images = video_data.get("images")
        if images and isinstance(images, list):
            logger.info("TikTok photo post detected (%d images)", len(images))
            paths = []
            for i, iurl in enumerate(images):
                p = os.path.join(tmp_dir, f"img_{i:02d}.jpg")
                if _download_media_url(iurl, p, timeout=_download_timeout(60)):
                    paths.append(p)
            if paths:
                logger.info("TikTok photos downloaded: %d files", len(paths))
                return paths
            return None

        # Video post
        download_url = video_data.get("hdplay") or video_data.get("play")
        if not download_url:
            logger.warning("tikwm API returned no video or image URL")
            return None

        logger.info("Downloading TikTok video from tikwm API...")
        vid_resp = cffi_requests.get(
            download_url,
            impersonate="chrome",
            timeout=_download_timeout(120),
        )
        fpath = os.path.join(tmp_dir, "video.mp4")
        with open(fpath, "wb") as f:
            f.write(vid_resp.content)

        size_mb = os.path.getsize(fpath) / 1e6
        logger.info("TikTok API download OK: %s (%.1f MB)", fpath, size_mb)
        return [fpath]
    except Exception as e:
        logger.warning("TikTok API fallback failed: %s", e)
        return None


def _extract_instagram_carousel_block(html: str) -> str | None:
    """Return JSON substring for the carousel_media array, or None if not found.

    Instagram's page HTML embeds related-post thumbnails alongside the main
    post's media. Scoping image extraction to the carousel_media array
    avoids picking up images from unrelated posts.
    """
    for key in ('"carousel_media":[', '"edge_sidecar_to_children":{"edges":['):
        idx = html.find(key)
        if idx < 0:
            continue
        start = idx + len(key)
        depth = 1
        i = start
        in_string = False
        escape = False
        n = len(html)
        while i < n and depth > 0:
            c = html[i]
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = not in_string
            elif not in_string:
                if c in "[{":
                    depth += 1
                elif c in "]}":
                    depth -= 1
            i += 1
        if depth == 0:
            return html[start:i - 1]
    return None


def _download_instagram_scrape(url: str, tmp_dir: str) -> list[str] | None:
    """Download Instagram video OR photo-carousel by scraping the page.

    Returns list of local file paths on success, or None on failure.
    For videos/reels: [video.mp4]. For photo posts/carousels: [img_0.jpg, ...].
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.warning("curl_cffi not installed — skipping Instagram scrape fallback")
        return None

    logger.info("Trying Instagram scrape fallback...")
    try:
        resp = cffi_requests.get(
            url,
            impersonate="chrome",
            timeout=_download_timeout(20),
        )
        if resp.status_code != 200:
            logger.warning("Instagram page returned status %d", resp.status_code)
            return None

        html = resp.text

        # Try video first (Reels, video posts)
        m = re.search(r'"video_versions":\[\{[^}]*"url":"([^"]+)"', html)
        if not m:
            m = re.search(r'"video_url":"([^"]+)"', html)
        if m:
            video_url = m.group(1).replace("\\/", "/").replace("\\u0026", "&")
            logger.info("Found Instagram video URL, downloading...")
            vid_resp = cffi_requests.get(
                video_url,
                impersonate="chrome",
                timeout=_download_timeout(120),
            )
            if vid_resp.status_code == 200:
                fpath = os.path.join(tmp_dir, "video.mp4")
                with open(fpath, "wb") as f:
                    f.write(vid_resp.content)
                size_mb = os.path.getsize(fpath) / 1e6
                logger.info("Instagram video download OK: %s (%.1f MB)", fpath, size_mb)
                return [fpath]
            logger.warning("Instagram video download returned %d", vid_resp.status_code)

        # Photo post / carousel: try to extract ONLY the carousel_media block
        # so we don't pick up thumbnails of related/suggested posts.
        carousel_block = _extract_instagram_carousel_block(html)
        search_text = carousel_block if carousel_block else html

        # Pattern 1: image_versions2 candidates (modern Instagram JSON)
        img_urls = re.findall(
            r'"image_versions2":\{"candidates":\[\{[^}]*?"url":"([^"]+)"',
            search_text,
        )
        # Pattern 2: display_url fallback
        if not img_urls:
            img_urls = re.findall(r'"display_url":"([^"]+)"', search_text)

        if not img_urls:
            logger.warning("No media URL found in Instagram page HTML")
            return None

        # Dedupe preserving order (carousels repeat main image in metadata)
        seen = set()
        unique_urls = []
        for u in img_urls:
            clean = u.replace("\\/", "/").replace("\\u0026", "&")
            if clean not in seen:
                seen.add(clean)
                unique_urls.append(clean)

        # Cap at reasonable carousel length. Real IG carousels max out at 20.
        MAX_CAROUSEL = 20
        if len(unique_urls) > MAX_CAROUSEL:
            logger.info(
                "Capping %d extracted IG images to first %d (likely includes "
                "suggested-post thumbnails)",
                len(unique_urls), MAX_CAROUSEL,
            )
            unique_urls = unique_urls[:MAX_CAROUSEL]

        logger.info("Instagram photo post: %d unique images", len(unique_urls))
        paths = []
        for i, iurl in enumerate(unique_urls):
            p = os.path.join(tmp_dir, f"img_{i:02d}.jpg")
            if _download_media_url(iurl, p, timeout=_download_timeout(60)):
                paths.append(p)

        if not paths:
            return None
        logger.info("Instagram photos downloaded: %d files", len(paths))
        return paths
    except Exception as e:
        logger.warning("Instagram scrape fallback failed: %s", e)
        return None


def _check_download_size(fpath: str) -> str:
    """Verify downloaded file is within size limit. Returns path or raises."""
    size_mb = os.path.getsize(fpath) / 1e6
    if size_mb > MAX_DOWNLOAD_SIZE_MB:
        try:
            os.remove(fpath)
        except OSError:
            pass
        raise RuntimeError(
            f"Downloaded file too large: {size_mb:.1f} MB "
            f"(limit: {MAX_DOWNLOAD_SIZE_MB} MB)"
        )
    return fpath


def _check_download_sizes(paths: list[str]) -> list[str]:
    """Verify each file in a list is within the size limit. Returns the list."""
    total_mb = 0.0
    for p in paths:
        size_mb = os.path.getsize(p) / 1e6
        total_mb += size_mb
    if total_mb > MAX_DOWNLOAD_SIZE_MB:
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
        raise RuntimeError(
            f"Downloaded media too large: {total_mb:.1f} MB total "
            f"(limit: {MAX_DOWNLOAD_SIZE_MB} MB)"
        )
    return paths


_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_AUDIO_SUFFIXES = (".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav")
_UNSUPPORTED_VIDEO_CODEC_MARKERS = ("bvc2", "bytevc2")


def _is_image_path(path: str | Path) -> bool:
    return str(path).lower().endswith(_IMAGE_SUFFIXES)


def _is_audio_path(path: str | Path) -> bool:
    return str(path).lower().endswith(_AUDIO_SUFFIXES)


def _remove_paths(paths: list[str]) -> None:
    parent = None
    for item in paths:
        try:
            path = Path(item)
            parent = path.parent
            if path.exists():
                path.unlink()
        except OSError:
            pass
    if parent and parent.exists() and parent.is_dir():
        try:
            if not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass


def _ffprobe_media_file(path: str | Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise VideoAnalyzerError(
            "ffprobe is required to validate downloaded video media.",
            code="ffprobe_unavailable",
        )
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,codec_tag_string,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=_ffmpeg_timeout(20),
    )
    if result.returncode != 0:
        raise VideoAnalyzerError(
            "ffprobe could not read the downloaded video file.",
            code="invalid_media",
            details={"stderr_tail": _short_tail(result.stderr or result.stdout)},
        )
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise VideoAnalyzerError(
            f"ffprobe returned invalid JSON: {exc}",
            code="invalid_media",
        ) from exc
    streams = data.get("streams") if isinstance(data, dict) else None
    if not streams:
        raise VideoAnalyzerError(
            "Downloaded media does not contain a readable video stream.",
            code="no_video_stream",
        )
    stream = streams[0]
    codec_name = str(stream.get("codec_name") or "").lower()
    codec_tag = str(stream.get("codec_tag_string") or "").lower()
    return {
        "codec": codec_name,
        "codec_tag": codec_tag,
        "width": stream.get("width"),
        "height": stream.get("height"),
    }


def _validate_media_file_for_processing(
    path: str | Path,
    url: Optional[str] = None,
    require_decodable: bool = False,
) -> dict[str, Any]:
    """Validate media before upload or local ffmpeg use."""
    media_path = Path(path)
    platform = detect_platform(url) if url else None
    if not media_path.exists():
        raise VideoAnalyzerError(
            "Downloaded media file does not exist.",
            code="missing_media_file",
            platform=platform,
        )
    size = media_path.stat().st_size
    if size <= 1024:
        raise VideoAnalyzerError(
            "Downloaded media file is too small or empty.",
            code="invalid_media",
            details={"bytes": size},
            platform=platform,
        )
    if _is_image_path(media_path):
        return {
            "source_valid": True,
            "source_downloaded": True,
            "codec": None,
            "bytes": size,
        }
    if _is_audio_path(media_path):
        return {
            "source_valid": True,
            "source_downloaded": True,
            "codec": None,
            "bytes": size,
        }

    info = _ffprobe_media_file(media_path)
    codec_text = " ".join(
        str(info.get(key) or "").lower() for key in ("codec", "codec_tag")
    )
    if any(marker in codec_text for marker in _UNSUPPORTED_VIDEO_CODEC_MARKERS):
        raise VideoAnalyzerError(
            "Unsupported TikTok video codec selected: ByteDance bvc2/bytevc2 "
            "cannot be decoded by the local ffmpeg build. Retry with the "
            "TikTok-safe H.264/H.265 yt-dlp selector.",
            code="unsupported_codec",
            details={
                **info,
                "selector": _safe_yt_dlp_format_selector_for_url(url or ""),
            },
            platform=platform,
        )

    if require_decodable:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise VideoAnalyzerError(
                "ffmpeg is required for local frame/clip extraction.",
                code="ffmpeg_unavailable",
                platform=platform,
            )
        decode = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(media_path),
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=_ffmpeg_timeout(30),
        )
        if decode.returncode != 0:
            raise VideoAnalyzerError(
                "Downloaded media exists but local ffmpeg could not decode it.",
                code="media_not_decodable",
                details={**info, "stderr_tail": _short_tail(decode.stderr)},
                platform=platform,
            )

    return {
        "source_valid": True,
        "source_downloaded": True,
        "bytes": size,
        **info,
    }


def _validate_media_paths_for_processing(
    paths: list[str],
    url: Optional[str],
    require_decodable: bool = False,
) -> list[dict[str, Any]]:
    return [
        _validate_media_file_for_processing(path, url, require_decodable)
        for path in paths
    ]


def _source_validation_summary(
    path: Optional[Path],
    url: Optional[str],
) -> dict[str, Any]:
    if path is None:
        return {
            "source_downloaded": False,
            "source_valid": False,
            "codec": None,
        }
    try:
        info = _validate_media_file_for_processing(path, url)
        return {
            "source_downloaded": True,
            "source_valid": True,
            "codec": info.get("codec"),
            "codec_tag": info.get("codec_tag"),
            "width": info.get("width"),
            "height": info.get("height"),
        }
    except Exception as exc:
        details = _exception_details(exc)
        return {
            "source_downloaded": True,
            "source_valid": False,
            "codec": details.get("details", {}).get("codec"),
            "error_code": details.get("code"),
            "message": details.get("message"),
        }


def _download_video(url: str) -> list[str]:
    """Download media (video or image set) and return list of local file paths.

    For videos returns [video.mp4]. For photo posts / carousels returns
    [img_00.jpg, img_01.jpg, ...].

    Strategy (fast-first):
      - TikTok:    tikwm.com API first → yt-dlp fallback
      - Instagram: page scrape first   → yt-dlp fallback
      - YouTube/other: yt-dlp only
    yt-dlp timeout is 30s for TikTok/Instagram (fast-fail), 180s for others.
    """
    tmp_dir = tempfile.mkdtemp(prefix="video_analyzer_")
    output_template = os.path.join(tmp_dir, "video.%(ext)s")
    platform = detect_platform(url)

    logger.info("Downloading media from %s (platform: %s)", url, platform)

    # --- Fast path: try API/scrape methods first for TikTok/Instagram ---
    if platform == "tiktok":
        api_paths = _download_tiktok_api(url, tmp_dir)
        if api_paths:
            try:
                checked = _check_download_sizes(api_paths)
                _validate_media_paths_for_processing(checked, url)
                return checked
            except VideoAnalyzerError as exc:
                logger.warning(
                    "TikTok API media rejected before processing: %s",
                    exc,
                )
                _remove_paths(api_paths)
        logger.info("TikTok API failed, falling back to yt-dlp...")

    if platform == "instagram":
        scrape_paths = _download_instagram_scrape(url, tmp_dir)
        if scrape_paths:
            try:
                checked = _check_download_sizes(scrape_paths)
                _validate_media_paths_for_processing(checked, url)
                return checked
            except VideoAnalyzerError as exc:
                logger.warning(
                    "Instagram scrape media rejected before processing: %s",
                    exc,
                )
                _remove_paths(scrape_paths)
        logger.info("Instagram scrape failed, falling back to yt-dlp...")

    # --- yt-dlp path ---
    ytdlp_cmd = [sys.executable, "-m", "yt_dlp"]

    base_args = [
        "--no-playlist",
        "--force-overwrites",
        "-o", output_template,
    ]

    safe_selector = _safe_yt_dlp_format_selector_for_url(url)
    safe_sort = _safe_yt_dlp_sort_for_url(url)
    if safe_selector:
        fmt_selector = safe_selector
        base_args.append("--merge-output-format")
        base_args.append("mp4")
    elif platform == "instagram":
        fmt_selector = (
            "best[filesize<100M]/"
            "bestvideo[filesize<100M]+bestaudio/bestvideo+bestaudio/"
            "best"
        )
        base_args.append("--merge-output-format")
        base_args.append("mp4")
    else:
        fmt_selector = (
            "best[vcodec^=h264][filesize<100M]/"
            "best[vcodec^=h264]/"
            "best[filesize<100M]/"
            "best"
        )

    # Shorter timeout for TikTok/Instagram (fast-fail to avoid 18-min waits)
    ytdlp_timeout = _download_timeout(
        30 if platform in ("tiktok", "instagram") else 180
    )

    if platform in ("tiktok", "instagram"):
        strategies = [
            ("impersonate only", ["--impersonate", "chrome"]),
        ]
        if ENABLE_BROWSER_COOKIES:
            strategies.append(
                ("cookies+impersonate", [
                    "--cookies-from-browser", "chrome",
                    "--impersonate", "chrome",
                ]),
            )
        strategies.append(("plain", []))
    else:
        strategies = [("plain", [])]

    last_error = ""
    success = False

    for strategy_name, extra_args in strategies:
        cmd = ytdlp_cmd + ["-f", fmt_selector]
        if safe_sort:
            cmd.extend(["-S", safe_sort])
        cmd.extend(extra_args + base_args + [url])

        logger.info("yt-dlp strategy '%s' (timeout=%ds)", strategy_name, ytdlp_timeout)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=ytdlp_timeout,
            )
            if result.returncode == 0:
                success = True
                break
            last_error = result.stderr
            logger.warning(
                "yt-dlp [%s] failed: %s",
                strategy_name, last_error[-300:],
            )
        except subprocess.TimeoutExpired:
            last_error = f"Timed out [{strategy_name}] after {ytdlp_timeout}s"
            logger.warning(last_error)

    if not success:
        if platform == "tiktok":
            raise VideoAnalyzerError(
                "No compatible TikTok MP4/H.264/H.265 format could be selected. "
                "The video may only expose an unsupported ByteDance bvc2/bytevc2 stream.",
                code="no_compatible_format",
                details={
                    "selector": fmt_selector,
                    "sort": safe_sort,
                    "stderr_tail": _short_tail(last_error),
                },
                platform=platform,
            )
        raise RuntimeError(
            f"Could not download video after all strategies. "
            f"Is the URL public and accessible?\n"
            f"Last error: {last_error[-500:]}"
        )

    # Find the downloaded file
    for fname in os.listdir(tmp_dir):
        fpath = os.path.join(tmp_dir, fname)
        if os.path.isfile(fpath):
            size_mb = os.path.getsize(fpath) / 1e6
            logger.info("Downloaded video to %s (%.1f MB)", fpath, size_mb)
            checked = _check_download_size(fpath)
            _validate_media_file_for_processing(checked, url)
            return [checked]

    raise RuntimeError("Download completed but no file was found.")


def _upload_to_gemini(file_path: str):
    """Upload a file to Gemini Files API and wait until it's processed."""
    _validate_media_file_for_processing(file_path)
    logger.info("Uploading %s to Gemini Files API...", file_path)
    uploaded = _get_client().files.upload(file=file_path)
    logger.info("Uploaded file: %s, state: %s", uploaded.name, uploaded.state)

    file_name = uploaded.name
    if not file_name:
        raise VideoAnalyzerError(
            "Gemini upload returned a file without a name.",
            code="gemini_upload_failed",
            retryable=True,
        )

    # Poll until processing is complete (state becomes ACTIVE)
    retries = 0
    max_retries = 60
    while retries < max_retries:
        if uploaded.state and uploaded.state.name == "ACTIVE":
            break
        if uploaded.state and uploaded.state.name == "FAILED":
            raise VideoAnalyzerError(
                f"Gemini File processing failed: {uploaded.state}",
                code="gemini_file_failed",
                details={"file_state": str(uploaded.state)},
            )
        time.sleep(2)
        uploaded = _get_client().files.get(name=file_name)
        retries += 1
        if retries % 10 == 0:
            logger.info("Still processing... (%d/%d)", retries, max_retries)

    if not uploaded.state or uploaded.state.name != "ACTIVE":
        raise RuntimeError(
            f"File processing timed out after {max_retries * 2}s "
            f"(last state: {uploaded.state})."
        )

    logger.info("File ready: %s", uploaded.name)
    return uploaded


def _cleanup(file_path=None, uploaded_file=None):
    """Clean up temporary files and uploaded Gemini files.

    file_path can be a single path (str) or a list of paths.
    uploaded_file can be a single uploaded file or a list.
    """
    paths = []
    if file_path:
        paths = file_path if isinstance(file_path, list) else [file_path]

    parent = None
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
                parent = os.path.dirname(p)
                logger.info("Cleaned up local file: %s", p)
            except OSError as e:
                logger.warning("Failed to clean up %s: %s", p, e)
    if parent and os.path.isdir(parent) and not os.listdir(parent):
        try:
            os.rmdir(parent)
        except OSError:
            pass

    uploads = []
    if uploaded_file:
        uploads = uploaded_file if isinstance(uploaded_file, list) else [uploaded_file]
    for uf in uploads:
        if uf is None:
            continue
        try:
            _get_client().files.delete(name=uf.name)
            logger.info("Deleted uploaded file: %s", uf.name)
        except Exception as e:
            logger.warning("Failed to delete uploaded file: %s", e)


def _truncate_response(text: str) -> str:
    """Truncate Gemini response if it exceeds the safety limit."""
    if text and len(text) > MAX_GEMINI_RESPONSE_CHARS:
        logger.warning(
            "Gemini response truncated: %d -> %d chars",
            len(text), MAX_GEMINI_RESPONSE_CHARS,
        )
        return text[:MAX_GEMINI_RESPONSE_CHARS]
    return text


# ---------------------------------------------------------------------------
# v1.2.0 structured tool helpers
# ---------------------------------------------------------------------------

def _enum_value(enum_cls: Any, *names: str) -> Any | None:
    """Return the first enum attribute that exists across SDK versions."""
    for name in names:
        if hasattr(enum_cls, name):
            return getattr(enum_cls, name)
    return None


def _resolve_model(user_model: Optional[str], detail: str) -> str:
    """Resolve the model role for a detail mode, with env/user overrides."""
    if user_model:
        return user_model
    if detail == "full":
        return os.environ.get("GEMINI_DEEP_MODEL", DEEP_MODEL_DEFAULT).strip() or DEEP_MODEL_DEFAULT
    return os.environ.get("GEMINI_FAST_MODEL", FAST_MODEL_DEFAULT).strip() or FAST_MODEL_DEFAULT


def _detail_settings(detail: str) -> dict:
    """Return output, thinking, media, and prompt bounds for a detail mode."""
    if detail == "compact":
        return {
            "max_output_tokens": 2000,
            "thinking_name": "LOW",
            "media_resolution_name": "MEDIUM",
            "max_moments": 5,
            "max_audio_evidence": 3,
            "max_visual_evidence": 3,
            "max_key_points": 5,
            "max_evidence_items": 5,
            "answer_max_chars": 500,
        }
    if detail == "standard":
        return {
            "max_output_tokens": 8000,
            "thinking_name": "MEDIUM",
            "media_resolution_name": "HIGH",
            "max_moments": 10,
            "max_audio_evidence": 5,
            "max_visual_evidence": 5,
            "max_key_points": 10,
            "max_evidence_items": 10,
            "answer_max_chars": 1500,
        }
    if detail == "full":
        return {
            "max_output_tokens": DEEP_MAX_OUTPUT_TOKENS or 32000,
            "thinking_name": "HIGH",
            "media_resolution_name": "HIGH",
            "max_moments": 20,
            "max_audio_evidence": 10,
            "max_visual_evidence": 10,
            "max_key_points": 20,
            "max_evidence_items": 20,
            "answer_max_chars": 4000,
        }
    raise ValueError(
        f"detail must be 'compact', 'standard', or 'full' (got {detail!r})"
    )


def _supports_thinking_for_model(model: str) -> bool:
    deep_env = os.environ.get("GEMINI_DEEP_MODEL", DEEP_MODEL_DEFAULT)
    if model == DEEP_MODEL_DEFAULT or model == deep_env:
        return DEEP_SUPPORTS_THINKING
    return FAST_SUPPORTS_THINKING


def _supports_media_for_model(model: str) -> bool:
    deep_env = os.environ.get("GEMINI_DEEP_MODEL", DEEP_MODEL_DEFAULT)
    if model == DEEP_MODEL_DEFAULT or model == deep_env:
        return DEEP_SUPPORTS_MEDIA_RESOLUTION
    return FAST_SUPPORTS_MEDIA_RESOLUTION


def _strip_gemini_unsupported_schema_fields(value: Any) -> Any:
    """Remove JSON Schema fields Gemini's response_schema API rejects."""
    if isinstance(value, dict):
        return {
            key: _strip_gemini_unsupported_schema_fields(item)
            for key, item in value.items()
            if key != "additionalProperties"
        }
    if isinstance(value, list):
        return [_strip_gemini_unsupported_schema_fields(item) for item in value]
    return value


def _gemini_response_schema(schema: type[BaseModel] | dict) -> type[BaseModel] | dict:
    """Return a Gemini-compatible structured response schema."""
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        raw_schema = schema.model_json_schema()
        return _strip_gemini_unsupported_schema_fields(raw_schema)
    if isinstance(schema, dict):
        raw_schema = json.loads(json.dumps(schema))
        return _strip_gemini_unsupported_schema_fields(raw_schema)
    return schema


def _build_structured_config(
    schema: type[BaseModel],
    detail: str,
    model: str,
    min_output_tokens: Optional[int] = None,
) -> types.GenerateContentConfig:
    """Build structured JSON config for the v1.2.0 tools."""
    settings = _detail_settings(detail)
    max_output_tokens = settings["max_output_tokens"]
    if min_output_tokens is not None:
        max_output_tokens = max(max_output_tokens, int(min_output_tokens))
    kwargs: dict[str, Any] = {
        "max_output_tokens": max_output_tokens,
        "response_mime_type": "application/json",
        "response_schema": _gemini_response_schema(schema),
    }

    thinking_config = _thinking_config_for_model(model, settings["thinking_name"])
    if thinking_config is not None:
        kwargs["thinking_config"] = thinking_config

    if _supports_media_for_model(model):
        media_name = settings["media_resolution_name"]
        media_resolution = _enum_value(
            types.MediaResolution,
            f"MEDIA_RESOLUTION_{media_name}",
            media_name,
        )
        if media_resolution is not None:
            kwargs["media_resolution"] = media_resolution

    return types.GenerateContentConfig(**kwargs)


def _save_full_result(url: str, payload: dict) -> str:
    """Save a full structured result to ANALYSES_DIR and return its path."""
    return _save_analysis(url, payload)


def _parse_timestamp(ts: str) -> str:
    """Normalize MM:SS or HH:MM:SS timestamps to Gemini offset seconds."""
    raw = ts.strip()
    parts = raw.split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise ValueError(
            f"Timestamp must be MM:SS or HH:MM:SS format (got {ts!r})"
        )

    nums = [int(part) for part in parts]
    if len(nums) == 2:
        minutes, seconds = nums
        if seconds >= 60:
            raise ValueError(f"Timestamp seconds must be 0-59 (got {ts!r})")
        total = minutes * 60 + seconds
    else:
        hours, minutes, seconds = nums
        if minutes >= 60 or seconds >= 60:
            raise ValueError(f"Timestamp minutes/seconds must be 0-59 (got {ts!r})")
        total = hours * 3600 + minutes * 60 + seconds
    return f"{total}s"


def _build_moment_prompt(
    query: str,
    max_results: int,
    context_seconds: int,
    detail: str,
) -> str:
    settings = _detail_settings(detail)
    top_n = min(max_results, settings["max_moments"])
    return (
        "Watch the full video with audio and visuals. Gemini performs all "
        "video/audio/visual reasoning. Return structured JSON only, with no "
        "markdown, no full transcript, and no long free-form analysis.\n\n"
        f"Find the top {top_n} moments matching this semantic query: {query!r}.\n"
        f"Use about {context_seconds} seconds of context around each match when "
        "choosing start/end boundaries. Use MM:SS or HH:MM:SS timestamps. "
        "For each moment, give confidence from 0.0 to 1.0, a short reason, "
        f"up to {settings['max_audio_evidence']} short audio_evidence strings, "
        f"and up to {settings['max_visual_evidence']} short visual_evidence "
        "strings. Keep evidence snippets short.\n\n"
        "Strict matching is required: return moments=[] if you cannot ground "
        "any moment in direct audio/visual evidence for the requested terms. "
        "Do not invent matches and do not return partially-related moments "
        "(for example, trains or maps when asked about cars).\n\n"
        "Set analyzed_by exactly to \"gemini\". Set detail exactly to "
        f"{detail!r}. Set model_used to the model name if known; the server "
        "will correct it after parsing. Include warnings and limitations only "
        "when useful."
    )


def _moment_match_threshold(detail: str) -> float:
    return {"compact": 0.6, "standard": 0.5, "full": 0.4}.get(detail, 0.6)


def _moment_matches_terms(moment: VideoMoment, terms: list[str], detail: str) -> bool:
    if moment.confidence < _moment_match_threshold(detail):
        return False
    if not terms:
        return True
    haystack = _flatten_text(
        [moment.reason, moment.audio_evidence, moment.visual_evidence]
    ).lower()
    return any(term in haystack for term in terms)


def _apply_strict_moment_filter(
    parsed: MomentSearchResult,
    query: str,
    detail: str,
) -> None:
    terms = _question_terms(query)
    if not terms:
        return
    before = len(parsed.moments)
    parsed.moments = [
        moment
        for moment in parsed.moments
        if _moment_matches_terms(moment, terms, detail)
    ]
    if not parsed.moments:
        warning = f"No matching evidence found for: {query}"
        if warning not in parsed.warnings:
            parsed.warnings.append(warning)
        parsed.limitations.append(
            "Strict matching removed ungrounded or only partially related moments."
        )
    elif len(parsed.moments) < before:
        parsed.warnings.append(
            "Strict matching removed moments that were not grounded in the requested terms."
        )


def _build_segment_prompt(
    start: str,
    end: str,
    user_prompt: str,
    detail: str,
) -> str:
    settings = _detail_settings(detail)
    if detail == "full":
        extended_rule = (
            "Populate extended_analysis with long-form analysis. The server "
            "may save it to full_result_ref instead of returning it inline."
        )
    else:
        extended_rule = "Leave extended_analysis null."

    return (
        "Analyze only the selected video segment with audio and visuals. "
        "Gemini performs all video/audio/visual reasoning. Return structured "
        "JSON only, with no markdown, no full transcript, and no free-form "
        "analysis outside the schema.\n\n"
        f"Selected segment: {start} to {end}.\n"
        f"User request: {user_prompt}\n\n"
        f"Keep direct_answer under {settings['answer_max_chars']} characters. "
        "Make concise_summary brief. "
        f"Return at most {settings['max_key_points']} key_points and at most "
        f"{settings['max_evidence_items']} timestamped evidence items. "
        "Evidence type must be one of audio, visual, text_on_screen, or mixed. "
        "Use confidence from 0.0 to 1.0. "
        f"{extended_rule} Set analyzed_by exactly to \"gemini\". Set detail "
        f"exactly to {detail!r}. Set model_used to the model name if known; "
        "the server will correct it after parsing."
    )


# ---------------------------------------------------------------------------
# v1.3.0 saved video context helpers
# ---------------------------------------------------------------------------

_QUESTION_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "about", "based", "be", "by",
    "answer", "can", "clip", "content", "does", "do", "evidence", "for",
    "from", "give", "has", "have", "how", "i", "image", "in", "is", "it",
    "appearance", "end", "first", "last", "me", "of", "on", "or", "related", "sec", "second", "seconds",
    "show", "shows", "strongest", "that", "the", "there", "this", "timestamp",
    "timestamps", "to", "video", "visual", "what", "when", "where", "with",
    "you",
}


def _normalize_video_url(url: str) -> str:
    """Normalize supported video URLs for stable context IDs."""
    validate_url(url)
    platform = detect_platform(url)
    if platform == "youtube":
        return _normalize_youtube_url(url)

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{host}{path}"


def _video_id_from_url(url: str) -> str:
    """Return a stable short SHA256 ID for a normalized URL."""
    normalized = _normalize_video_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _context_path(video_id: str) -> Path:
    """Return the JSON context path for a video ID."""
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", video_id.strip())
    if not safe_id:
        raise ValueError("video_id cannot be empty")
    return VIDEO_CONTEXT_DIR / f"{safe_id}.json"


def _load_video_context(video_id_or_url: str) -> SavedVideoContext | None:
    """Load a saved video context by video_id or URL."""
    raw = video_id_or_url.strip()
    if not raw:
        return None

    candidates: list[str] = []
    if "://" in raw:
        try:
            candidates.append(_video_id_from_url(raw))
            normalized = _normalize_video_url(raw)
        except Exception:
            normalized = raw
    else:
        candidates.append(re.sub(r"[^a-zA-Z0-9_-]", "", raw))
        normalized = raw

    for video_id in candidates:
        if not video_id:
            continue
        path = _context_path(video_id)
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        ctx = SavedVideoContext.model_validate(data)
        _ensure_context_quality(ctx)
        return ctx

    if "://" in raw:
        for path in VIDEO_CONTEXT_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                ctx = SavedVideoContext.model_validate(data)
            except Exception:
                continue
            if ctx.normalized_url == normalized or ctx.url == raw:
                _ensure_context_quality(ctx)
                return ctx
    return None


def _save_video_context(ctx: SavedVideoContext) -> str:
    """Persist a structured video context and return its path."""
    VIDEO_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_context_quality(ctx)
    path = _context_path(ctx.video_id)
    path.write_text(
        json.dumps(ctx.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Video context saved to %s", path)
    return str(path)


def _build_video_context_prompt(url: str, detail: str, chunk_seconds: int) -> str:
    settings = _detail_settings(detail)
    max_segments = {
        "compact": 8,
        "standard": 18,
        "full": 40,
    }[detail]
    max_evidence = settings["max_evidence_items"]
    return (
        "Analyze the entire video once using audio, visuals, and on-screen text. "
        "Return structured JSON only, with no markdown and no text outside the "
        "schema. Do not include a full transcript. Do not include long raw "
        "analysis. Summarize spoken content instead of transcribing everything. "
        "Keep all fields concise.\n\n"
        f"Source URL: {url}\n"
        f"Detail mode: {detail}\n"
        f"Use timeline chunks of about {chunk_seconds} seconds when possible. "
        f"Return at most {max_segments} timeline chunks and at most "
        f"{max_evidence} evidence items per important segment. Include "
        "timestamps in MM:SS or HH:MM:SS format. Include visual, audio, and "
        "text_on_screen evidence when present. Include warnings and limitations. "
        "For vehicles and objects, explicitly identify whether cars, BMW, "
        "Porsche, or other vehicles appear. If absent, say absent clearly. "
        "Do not invent entities. "
        "If a requested or notable entity is absent, say absent clearly in "
        "limitations or the relevant segment summary. Set analyzed_by exactly "
        "to \"gemini\". The server will fill video_id, timestamps, URL fields, "
        "and model_used after parsing."
    )


def _build_video_context_config(
    detail: str,
    schema: type[BaseModel],
    model: str,
) -> types.GenerateContentConfig:
    min_tokens = {
        "compact": 8000,
        "standard": 12000,
        "full": DEEP_MAX_OUTPUT_TOKENS or 32000,
    }[detail]
    return _build_structured_config(
        schema,
        detail,
        model,
        min_output_tokens=min_tokens,
    )


def _compact_prepare_result(
    ctx: SavedVideoContext,
    reused_existing: bool,
    gemini_called: bool,
) -> dict:
    quality = _ensure_context_quality(ctx)
    data = PrepareVideoContextResult(
        video_id=ctx.video_id,
        url=ctx.url,
        analyzed_by="gemini",
        model_used=ctx.model_used,
        detail=ctx.detail,
        reused_existing=reused_existing,
        gemini_called=gemini_called,
        title_guess=ctx.title_guess,
        summary=ctx.summary[:1200],
        timeline_count=len(ctx.timeline),
        topics=ctx.topics[:12],
        entities=ctx.entities[:12],
        warnings=ctx.warnings[:8],
        limitations=ctx.limitations[:8],
        context_ref=str(_context_path(ctx.video_id)),
    ).model_dump()
    data["context_quality"] = quality.model_dump()
    return data


def _question_terms(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", text.lower())
    terms: list[str] = []
    for token in tokens:
        if len(token) < 2 or token in _QUESTION_STOPWORDS:
            continue
        terms.append(token)
        if token.endswith("s") and len(token) > 3:
            terms.append(token[:-1])
    synonyms = {
        "car": ["cars", "vehicle", "vehicles", "automotive", "auto"],
        "cars": ["car", "vehicle", "vehicles", "automotive", "auto"],
        "vehicle": ["vehicles", "car", "cars", "automotive", "auto"],
        "vehicles": ["vehicle", "car", "cars", "automotive", "auto"],
        "bmw": ["m4", "bimmer"],
        "porsche": ["911", "cayenne", "panamera", "taycan"],
    }
    expanded = []
    for term in terms:
        expanded.append(term)
        expanded.extend(synonyms.get(term, []))
    seen = set()
    ordered = []
    for term in expanded:
        if term not in seen:
            seen.add(term)
            ordered.append(term)
    return ordered


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return _flatten_text(value.model_dump())
    if isinstance(value, dict):
        return " ".join(_flatten_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(v) for v in value)
    return str(value)


def _estimate_context_duration_seconds(ctx: SavedVideoContext) -> float:
    """Estimate video duration from saved timeline/end timestamps."""
    candidates: list[float] = []
    for segment in [*ctx.timeline, *ctx.key_moments]:
        try:
            candidates.append(_parse_timestamp_to_seconds(segment.end))
        except Exception:
            continue
    if ctx.duration_estimate:
        try:
            candidates.append(_parse_timestamp_to_seconds(ctx.duration_estimate))
        except Exception:
            pass
    return max(candidates, default=0.0)


def _compute_context_quality(ctx: SavedVideoContext) -> ContextQuality:
    """Compute a conservative quality signal for saved-only answers."""
    segments = [*ctx.timeline, *ctx.key_moments]
    evidence_items = [item for segment in segments for item in segment.evidence]
    evidence_count = len(evidence_items)
    duration_seconds = _estimate_context_duration_seconds(ctx)
    expected_chunks = max(1.0, duration_seconds / 30.0) if duration_seconds else 1.0
    timeline_density = round(len(ctx.timeline) / expected_chunks, 3)

    modality_fields = {
        "audio_summary": ctx.audio_summary,
        "visual_summary": ctx.visual_summary,
        "spoken_content_summary": ctx.spoken_content_summary,
        "text_on_screen_summary": ctx.text_on_screen_summary,
    }
    missing_modalities = [
        name
        for name, value in modality_fields.items()
        if len((value or "").strip()) < 12
    ]
    present_modalities = len(modality_fields) - len(missing_modalities)
    modality_score = present_modalities / max(1, len(modality_fields))
    evidence_score = min(1.0, evidence_count / 12.0)
    timeline_score = min(1.0, len(ctx.timeline) / 6.0)
    coverage_score = round(
        min(1.0, (0.45 * modality_score) + (0.35 * evidence_score) + (0.20 * timeline_score)),
        3,
    )
    confidences = [item.confidence for item in evidence_items]
    confidences.extend(segment.confidence for segment in segments)
    confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.5
    return ContextQuality(
        coverage_score=coverage_score,
        timeline_density=timeline_density,
        evidence_count=evidence_count,
        missing_modalities=missing_modalities,
        confidence=max(0.0, min(1.0, confidence)),
    )


def _ensure_context_quality(ctx: SavedVideoContext) -> ContextQuality:
    """Ensure old v1.3 context files expose a v1.4 quality block."""
    if ctx.context_quality is None:
        ctx.context_quality = _compute_context_quality(ctx)
    return ctx.context_quality


def _is_summary_question(question: str) -> bool:
    q = question.lower()
    return (
        "what is this video about" in q
        or "summarize" in q
        or "summary" in q
        or ("what" in q and "about" in q)
    )


def _is_evidence_question(question: str) -> bool:
    q = question.lower()
    return "evidence" in q or "timestamp" in q or "strongest" in q


def _is_existence_question(question: str) -> bool:
    q = question.lower().strip()
    starts = ("does", "do ", "is there", "are there", "can you see", "did it")
    return q.startswith(starts) or "show" in q or "contain" in q or "include" in q


def _segment_evidence(
    segment: VideoContextSegment,
    limit: int,
) -> list[VideoContextAnswerEvidence]:
    evidence = [
        VideoContextAnswerEvidence(
            timestamp=item.timestamp or segment.start,
            type=item.type,
            detail=item.detail,
            segment_start=segment.start,
            segment_end=segment.end,
            confidence=item.confidence,
        )
        for item in segment.evidence[:limit]
    ]
    if not evidence:
        evidence.append(
            VideoContextAnswerEvidence(
                timestamp=segment.start,
                type="mixed",
                detail=segment.summary[:220],
                segment_start=segment.start,
                segment_end=segment.end,
                confidence=segment.confidence,
            )
        )
    return evidence


def _time_window_seconds(text: str, direction: str) -> float | None:
    pattern = rf"{direction}\s+(\d{{1,2}})\s*(?:seconds|second|sec|s)\b"
    match = re.search(pattern, text.lower())
    return float(match.group(1)) if match else None


def _segment_start_seconds(segment: VideoContextSegment) -> float:
    try:
        return _parse_timestamp_to_seconds(segment.start)
    except Exception:
        return 0.0


def _segment_end_seconds(segment: VideoContextSegment) -> float:
    try:
        return _parse_timestamp_to_seconds(segment.end)
    except Exception:
        return _segment_start_seconds(segment)


def _score_context_segments(
    ctx: SavedVideoContext,
    question: str,
) -> list[tuple[float, VideoContextSegment, list[str]]]:
    terms = _question_terms(question)
    evidence_mode = _is_evidence_question(question)
    lowered = question.lower()
    first_mode = "first appearance" in lowered or "first " in lowered
    end_mode = "at the end" in lowered or "end of the video" in lowered
    last_mode = "last appearance" in lowered or "last " in lowered
    strongest_mode = "strongest" in lowered
    first_window = _time_window_seconds(question, "first") or (5.0 if first_mode else None)
    last_window = _time_window_seconds(question, "last") or (10.0 if (end_mode or last_mode) else None)
    all_segments = [*ctx.key_moments, *ctx.timeline]
    max_end = max((_segment_end_seconds(segment) for segment in all_segments), default=0.0)
    scored: list[tuple[float, VideoContextSegment, list[str]]] = []
    seen_segments = set()

    for segment in all_segments:
        key = (segment.start, segment.end, segment.summary)
        if key in seen_segments:
            continue
        seen_segments.add(key)

        haystack = _flatten_text(segment).lower()
        matches = [term for term in terms if term in haystack]
        max_ev_conf = max(
            [item.confidence for item in segment.evidence] + [segment.confidence]
        )
        start_seconds = _segment_start_seconds(segment)
        end_seconds = _segment_end_seconds(segment)
        term_grounded = not terms or bool(matches)
        if terms and not matches:
            score = 0.0
        else:
            score = float(len(set(matches))) + (0.5 * max_ev_conf)
        if evidence_mode and term_grounded:
            score += max_ev_conf + min(len(segment.evidence), 5) * 0.15
        if strongest_mode and term_grounded:
            score += max_ev_conf * 1.5
        if first_window is not None and term_grounded and start_seconds <= first_window:
            score += 2.5
        elif first_mode and term_grounded:
            score += max(0.0, 1.5 - (start_seconds / 30.0))
        if (
            last_window is not None
            and term_grounded
            and max_end
            and end_seconds >= max(0.0, max_end - last_window)
        ):
            score += 2.5
        elif (end_mode or last_mode) and term_grounded:
            score += min(2.0, end_seconds / max(1.0, max_end) * 2.0)
        if any(term in " ".join(segment.entities + segment.objects).lower() for term in terms):
            score += 1.0
        if terms and not matches and ("where" in lowered or "image of" in lowered or "clip where" in lowered):
            score *= 0.35
        if score > 0:
            scored.append((score, segment, sorted(set(matches))))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def _score_evidence_for_asset_request(
    ctx: SavedVideoContext,
    request: str,
) -> list[tuple[float, VideoContextSegment, list[str]]]:
    """Compatibility helper for v1.4 evidence asset selection."""
    return _score_context_segments(ctx, request)


def _parse_timestamp_to_seconds(ts: str) -> float:
    """Parse seconds, 90s, MM:SS, or HH:MM:SS into seconds."""
    raw = str(ts).strip().lower()
    if not raw:
        raise ValueError("timestamp cannot be empty")
    if raw.endswith("s") and raw[:-1].replace(".", "", 1).isdigit():
        value = float(raw[:-1])
        if value < 0:
            raise ValueError("timestamp cannot be negative")
        return value
    if raw.replace(".", "", 1).isdigit():
        value = float(raw)
        if value < 0:
            raise ValueError("timestamp cannot be negative")
        return value
    parts = raw.split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise ValueError(
            f"timestamp must be seconds, MM:SS, or HH:MM:SS (got {ts!r})"
        )
    nums = [int(part) for part in parts]
    if len(nums) == 2:
        minutes, seconds = nums
        if seconds >= 60:
            raise ValueError(f"timestamp seconds must be 0-59 (got {ts!r})")
        return float(minutes * 60 + seconds)
    hours, minutes, seconds = nums
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"timestamp minutes/seconds must be 0-59 (got {ts!r})")
    return float(hours * 3600 + minutes * 60 + seconds)


def _format_seconds_to_timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _safe_timestamp_for_filename(ts: str) -> str:
    safe = re.sub(r"[^0-9a-zA-Z_-]+", "_", ts.strip())
    return safe.strip("_") or "timestamp"


def _check_ffmpeg_available() -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    return {
        "available": bool(ffmpeg),
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
    }


def _source_dir(video_id: str) -> Path:
    path = VIDEO_SOURCE_DIR / video_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _asset_dir(video_id: str, kind: str) -> Path:
    path = VIDEO_ASSET_DIR / video_id / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def _latest_mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    mtimes = [path.stat().st_mtime]
    if path.is_dir():
        mtimes.extend(child.stat().st_mtime for child in path.rglob("*") if child.exists())
    return datetime.fromtimestamp(max(mtimes)).isoformat()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _find_cached_source(video_id: str) -> Path | None:
    source_dir = _source_dir(video_id)
    for path in sorted(source_dir.glob("source.*")):
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _context_ref(video_id: str) -> str | None:
    path = _context_path(video_id)
    return str(path) if path.exists() else None


def _resolve_video_identity(video_id_or_url: str) -> tuple[str, str | None, SavedVideoContext | None]:
    ctx = _load_video_context(video_id_or_url)
    if ctx is not None:
        return ctx.video_id, ctx.url, ctx
    if "://" in video_id_or_url:
        return _video_id_from_url(video_id_or_url), video_id_or_url, None
    video_id = re.sub(r"[^a-zA-Z0-9_-]", "", video_id_or_url.strip())
    if not video_id:
        raise ValueError("video_id_or_url cannot be empty")
    return video_id, None, None


def _ensure_video_source(
    video_id_or_url: str,
    force_refresh: bool = False,
) -> tuple[Path, str, SavedVideoContext | None]:
    """Ensure a durable local source file exists for frame/clip extraction."""
    video_id, url, ctx = _resolve_video_identity(video_id_or_url)
    cached = _find_cached_source(video_id)
    if cached is not None and not force_refresh:
        try:
            _validate_media_file_for_processing(cached, url, require_decodable=True)
            return cached, video_id, ctx
        except VideoAnalyzerError:
            if not url:
                raise
            logger.warning("Cached source failed validation; refreshing: %s", cached)
            try:
                cached.unlink()
            except OSError:
                pass

    if not url:
        raise RuntimeError(
            "source_not_available_for_asset_extraction: no saved source file "
            "exists and no URL is available for this video_id"
        )

    file_paths: list[str] = []
    try:
        file_paths = _download_video(url)
        video_paths = [
            path for path in file_paths
            if path.lower().endswith(
                (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv", ".3gp")
            )
        ]
        if not video_paths:
            raise RuntimeError(
                "source_not_available_for_asset_extraction: downloaded media "
                "does not include a video source"
            )
        source = Path(video_paths[0])
        ext = source.suffix.lower() or ".mp4"
        dest = _source_dir(video_id) / f"source{ext}"
        shutil.copy2(source, dest)
        _validate_media_file_for_processing(dest, url, require_decodable=True)
        logger.info("Cached video source to %s", dest)
        return dest, video_id, ctx
    finally:
        if file_paths:
            _cleanup(file_paths, None)


def _run_ffmpeg_extract_frame(
    source_path: Path | str,
    timestamp_seconds: float,
    output_path: Path,
) -> None:
    info = _check_ffmpeg_available()
    if not info["available"]:
        raise RuntimeError("ffmpeg_unavailable: ffmpeg is required for asset extraction")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        info["ffmpeg"] or "ffmpeg",
        "-y",
        "-ss",
        f"{timestamp_seconds:.3f}",
        "-i",
        str(source_path),
        "-frames:v",
        "1",
    ]
    if output_path.suffix.lower() in (".jpg", ".jpeg"):
        cmd.extend(["-q:v", "2"])
    cmd.append(str(output_path))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_ffmpeg_timeout(90),
    )
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            "ffmpeg_frame_extract_failed: "
            f"{(result.stderr or result.stdout)[-500:]}"
        )


def _run_youtube_fast_frame_extract(
    url: str,
    timestamp_seconds: float,
    output_path: Path,
) -> None:
    """Extract a YouTube frame without downloading the full video."""
    info = _check_ffmpeg_available()
    if not info["available"]:
        raise RuntimeError("ffmpeg_unavailable: ffmpeg is required for asset extraction")

    normalized = _normalize_youtube_url(url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ytdlp_base = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-warnings",
    ]

    direct_cmd = [
        *ytdlp_base,
        "-f",
        "bv*[ext=mp4][protocol^=http]/bv*[protocol^=http]/bv*[ext=mp4]/bv*",
        "-g",
        normalized,
    ]
    direct_error = ""
    try:
        direct = subprocess.run(
            direct_cmd,
            capture_output=True,
            text=True,
            timeout=_download_timeout(60),
        )
        if direct.returncode == 0:
            stream_url = next(
                (line.strip() for line in direct.stdout.splitlines() if line.strip()),
                "",
            )
            if stream_url:
                _run_ffmpeg_extract_frame(
                    stream_url,
                    timestamp_seconds,
                    output_path,
                )
                return
        direct_error = (direct.stderr or direct.stdout)[-500:]
    except Exception as e:
        direct_error = str(e)

    if output_path.exists():
        try:
            output_path.unlink()
        except OSError:
            pass

    tmp_dir = tempfile.mkdtemp(prefix="video_frame_section_")
    start_seconds = max(0.0, timestamp_seconds - 1.0)
    end_seconds = timestamp_seconds + 2.0
    section = (
        f"*{_format_seconds_to_timestamp(start_seconds)}-"
        f"{_format_seconds_to_timestamp(end_seconds)}"
    )
    partial_error = ""
    try:
        section_cmd = [
            *ytdlp_base,
            "-f",
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "--download-sections",
            section,
            "--merge-output-format",
            "mp4",
            "-o",
            str(Path(tmp_dir) / "section.%(ext)s"),
            normalized,
        ]
        partial = subprocess.run(
            section_cmd,
            capture_output=True,
            text=True,
            timeout=_download_timeout(120),
        )
        if partial.returncode == 0:
            candidates = [
                path for path in Path(tmp_dir).glob("section.*")
                if path.is_file() and path.stat().st_size > 0
            ]
            if candidates:
                relative_ts = max(0.0, timestamp_seconds - start_seconds)
                _run_ffmpeg_extract_frame(candidates[0], relative_ts, output_path)
                return
        partial_error = (partial.stderr or partial.stdout)[-500:]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    raise RuntimeError(
        "youtube_frame_extract_failed_without_full_download: "
        f"direct_stream={direct_error} partial_section={partial_error}"
    )


def _run_ffmpeg_extract_clip(
    source_path: Path | str,
    start_seconds: float,
    duration_seconds: float,
    output_path: Path,
) -> None:
    info = _check_ffmpeg_available()
    if not info["available"]:
        raise RuntimeError("ffmpeg_unavailable: ffmpeg is required for asset extraction")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stream_copy_cmd = [
        info["ffmpeg"] or "ffmpeg",
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{duration_seconds:.3f}",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(output_path),
    ]
    copy_result = subprocess.run(
        stream_copy_cmd,
        capture_output=True,
        text=True,
        timeout=_ffmpeg_timeout(45),
    )
    if (
        copy_result.returncode == 0
        and output_path.exists()
        and output_path.stat().st_size > 0
    ):
        return

    if output_path.exists():
        try:
            output_path.unlink()
        except OSError:
            pass

    cmd = [
        info["ffmpeg"] or "ffmpeg",
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{duration_seconds:.3f}",
    ]
    if output_path.suffix.lower() == ".webm":
        cmd.extend([
            "-c:v", "libvpx-vp9",
            "-deadline", "realtime",
            "-cpu-used", "8",
            "-b:v", "1M",
            "-c:a", "libopus",
        ])
    else:
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-c:a", "aac",
            "-movflags", "+faststart",
        ])
    cmd.append(str(output_path))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_ffmpeg_timeout(120),
    )
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError(
            "ffmpeg_clip_extract_failed: "
            f"stream_copy={(copy_result.stderr or copy_result.stdout)[-250:]} "
            f"fallback={(result.stderr or result.stdout)[-500:]}"
        )


def _run_youtube_fast_clip_extract(
    url: str,
    start_seconds: float,
    duration_seconds: float,
    output_path: Path,
) -> None:
    """Extract a short YouTube clip without downloading the full video."""
    info = _check_ffmpeg_available()
    if not info["available"]:
        raise RuntimeError("ffmpeg_unavailable: ffmpeg is required for asset extraction")

    normalized = _normalize_youtube_url(url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ytdlp_base = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-warnings",
    ]

    direct_cmd = [
        *ytdlp_base,
        "-f",
        "b[ext=mp4][protocol^=http]/b[protocol^=http]/bv*[ext=mp4][protocol^=http]/bv*[protocol^=http]",
        "-g",
        normalized,
    ]
    direct_error = ""
    try:
        direct = subprocess.run(
            direct_cmd,
            capture_output=True,
            text=True,
            timeout=_download_timeout(60),
        )
        if direct.returncode == 0:
            stream_url = next(
                (line.strip() for line in direct.stdout.splitlines() if line.strip()),
                "",
            )
            if stream_url:
                _run_ffmpeg_extract_clip(
                    stream_url,
                    start_seconds,
                    duration_seconds,
                    output_path,
                )
                return
        direct_error = (direct.stderr or direct.stdout)[-500:]
    except Exception as e:
        direct_error = str(e)

    if output_path.exists():
        try:
            output_path.unlink()
        except OSError:
            pass

    tmp_dir = tempfile.mkdtemp(prefix="video_clip_section_")
    section = (
        f"*{_format_seconds_to_timestamp(start_seconds)}-"
        f"{_format_seconds_to_timestamp(start_seconds + duration_seconds)}"
    )
    partial_error = ""
    try:
        section_cmd = [
            *ytdlp_base,
            "-f",
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "--download-sections",
            section,
            "--merge-output-format",
            "mp4",
            "--concurrent-fragments",
            "4",
            "-o",
            str(Path(tmp_dir) / "section.%(ext)s"),
            normalized,
        ]
        partial = subprocess.run(
            section_cmd,
            capture_output=True,
            text=True,
            timeout=_download_timeout(140),
        )
        if partial.returncode == 0:
            candidates = [
                path for path in Path(tmp_dir).glob("section.*")
                if path.is_file() and path.stat().st_size > 0
            ]
            if candidates:
                _run_ffmpeg_extract_clip(
                    candidates[0],
                    0.0,
                    duration_seconds,
                    output_path,
                )
                return
        partial_error = (partial.stderr or partial.stdout)[-500:]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    raise RuntimeError(
        "youtube_clip_extract_failed_without_full_download: "
        f"direct_stream={direct_error} partial_section={partial_error}"
    )


def _asset_id_for(video_id: str, kind: str, locator: str, fmt: str) -> str:
    raw = f"{video_id}:{kind}:{locator}:{fmt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


_asset_manifest_locks: dict[str, threading.Lock] = {}
_asset_manifest_locks_lock = threading.Lock()


def _asset_manifest_lock(video_id: str) -> threading.Lock:
    with _asset_manifest_locks_lock:
        if video_id not in _asset_manifest_locks:
            _asset_manifest_locks[video_id] = threading.Lock()
        return _asset_manifest_locks[video_id]


def _asset_manifest_path(video_id: str) -> Path:
    return VIDEO_ASSET_DIR / video_id / "asset_manifest.json"


def _load_asset_manifest(video_id: str) -> dict:
    path = _asset_manifest_path(video_id)
    if not path.exists():
        return {
            "schema_version": "1.4.0",
            "video_id": video_id,
            "assets": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "schema_version": "1.4.0",
            "video_id": video_id,
            "assets": {},
            "warnings": ["Existing asset manifest could not be parsed; rebuilt in memory."],
        }
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema_version", "1.4.0")
    data.setdefault("video_id", video_id)
    data.setdefault("assets", {})
    return data


def _write_asset_manifest(video_id: str, manifest: dict) -> None:
    path = _asset_manifest_path(video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _manifest_key(kind: str, key: str, fmt: str) -> str:
    return f"{kind}:{key}:{fmt}"


def _record_asset(
    video_id: str,
    kind: str,
    key: str,
    fmt: str,
    asset_ref: str,
    meta: dict,
) -> dict:
    with _asset_manifest_lock(video_id):
        manifest = _load_asset_manifest(video_id)
        entry = {
            **meta,
            "video_id": video_id,
            "kind": kind,
            "asset_ref": asset_ref,
            "file_size_bytes": (
                Path(asset_ref).stat().st_size if Path(asset_ref).exists() else 0
            ),
        }
        manifest.setdefault("assets", {})[_manifest_key(kind, key, fmt)] = entry
        manifest["updated_at"] = datetime.now().isoformat()
        _write_asset_manifest(video_id, manifest)
        return entry


def _lookup_asset(video_id: str, kind: str, key: str, fmt: str) -> dict | None:
    with _asset_manifest_lock(video_id):
        entry = _load_asset_manifest(video_id).get("assets", {}).get(
            _manifest_key(kind, key, fmt)
        )
    if not isinstance(entry, dict):
        return None
    asset_ref = entry.get("asset_ref")
    if not asset_ref or not Path(asset_ref).exists():
        return None
    return entry


def _asset_error(
    video_id_or_url: str,
    request: str | None,
    message: str,
    context_missing: bool = False,
    insufficient_context: bool = False,
    tool: str = "video_asset",
    code: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    retryable: bool = False,
) -> str:
    try:
        video_id, _, _ = _resolve_video_identity(video_id_or_url)
    except Exception:
        video_id = ""
    return json.dumps(
        {
            "status": "error",
            "tool": tool,
            "code": code or "runtime_error",
            "video_id": video_id,
            "request": request,
            "message": message,
            "retryable": retryable,
            "details": details or {},
            "context_missing": context_missing,
            "insufficient_context": insufficient_context,
            "gemini_called": False,
            "reused_existing": False,
        },
        indent=2,
        ensure_ascii=False,
    )


def _best_context_match(
    ctx: SavedVideoContext,
    request: str,
) -> tuple[VideoContextSegment | None, VideoContextEvidence | None, str, float]:
    segments = [*ctx.key_moments, *ctx.timeline]
    if not segments:
        return None, None, "No timeline or key moments are saved.", 0.0

    lowered = request.lower()
    terms = _question_terms(request)
    scored = _score_context_segments(ctx, request)
    if terms and not scored:
        return None, None, "No saved evidence matched the requested terms.", 0.0
    candidate_segments = [item[1] for item in scored] if scored else segments

    if "first appearance" in lowered or "first " in lowered:
        segment = min(candidate_segments, key=_segment_start_seconds)
        evidence = max(segment.evidence, key=lambda item: item.confidence, default=None)
        return segment, evidence, "Matched the earliest saved segment for this request.", segment.confidence

    if "end" in lowered or "last" in lowered:
        segment = max(candidate_segments, key=_segment_end_seconds)
        evidence = segment.evidence[-1] if segment.evidence else None
        return segment, evidence, "Matched the latest saved segment for this request.", segment.confidence

    if "strongest" in lowered:
        segment = max(
            candidate_segments,
            key=lambda item: max([ev.confidence for ev in item.evidence] + [item.confidence]),
        )
        evidence = max(segment.evidence, key=lambda item: item.confidence, default=None)
        return segment, evidence, "Matched the strongest saved evidence.", segment.confidence

    if scored:
        _, segment, matches = scored[0]
        visual_evidence = [
            item for item in segment.evidence
            if item.type in ("visual", "mixed", "text_on_screen")
        ]
        evidence = max(
            visual_evidence or segment.evidence,
            key=lambda item: item.confidence,
            default=None,
        )
        reason = (
            f"Matched saved context terms: {', '.join(matches[:6])}."
            if matches
            else "Matched the highest-scoring saved segment."
        )
        return segment, evidence, reason, min(1.0, scored[0][0] / 5.0)

    if terms:
        return None, None, "No saved evidence matched the requested terms.", 0.0

    segment = max(segments, key=lambda item: item.confidence)
    evidence = max(segment.evidence, key=lambda item: item.confidence, default=None)
    return segment, evidence, "Used highest-confidence saved segment.", segment.confidence


def _build_asset_result(
    asset_id: str,
    video_id: str,
    asset_type: Literal["image", "video_clip"],
    mime_type: str,
    asset_ref: str,
    request: str | None = None,
    timestamp: str | None = None,
    start: str | None = None,
    end: str | None = None,
    duration_seconds: float | None = None,
    source_context_ref: str | None = None,
    matched_reason: str | None = None,
    confidence: float = 1.0,
    reused_existing: bool = False,
    warnings: list[str] | None = None,
    limitations: list[str] | None = None,
) -> str:
    result = VideoAssetResult(
        asset_id=asset_id,
        video_id=video_id,
        type=asset_type,
        request=request,
        timestamp=timestamp,
        start=start,
        end=end,
        duration_seconds=duration_seconds,
        mime_type=mime_type,
        asset_ref=asset_ref,
        source_context_ref=source_context_ref,
        matched_reason=matched_reason,
        confidence=confidence,
        gemini_called=False,
        reused_existing=reused_existing,
        created_at=datetime.now().isoformat(),
        warnings=warnings or [],
        limitations=limitations or [],
    )
    return json.dumps(result.model_dump(), indent=2, ensure_ascii=False)


def _answer_from_saved_context(
    ctx: SavedVideoContext,
    question: str,
    detail: str,
) -> AskVideoContextResult:
    if detail not in ("compact", "standard"):
        raise ValueError("detail must be 'compact' or 'standard'")

    scored = _score_context_segments(ctx, question)
    evidence_limit = 3 if detail == "compact" else 8
    terms = _question_terms(question)
    summary_mode = _is_summary_question(question)
    evidence_mode = _is_evidence_question(question)
    existence_mode = _is_existence_question(question)
    quality = _ensure_context_quality(ctx)
    low_quality = quality.evidence_count == 0 or quality.coverage_score < 0.3

    warnings = list(ctx.warnings[:5])
    limitations = list(ctx.limitations[:5])
    if low_quality:
        limitations.append(
            "Saved context quality is low; evidence is limited and answers may be incomplete."
        )
    relevant_segments: list[str] = []
    evidence: list[VideoContextAnswerEvidence] = []

    if summary_mode:
        top_segments = (
            [item[1] for item in scored[:2]]
            if scored
            else ctx.key_moments[:2] or ctx.timeline[:2]
        )
        for segment in top_segments:
            relevant_segments.append(f"{segment.start}-{segment.end}")
            evidence.extend(_segment_evidence(segment, evidence_limit - len(evidence)))
            if len(evidence) >= evidence_limit:
                break
        answer = f"Based on the saved context, {ctx.summary}"
        return AskVideoContextResult(
            video_id=ctx.video_id,
            question=question,
            answer=answer[:1200 if detail == "compact" else 2400],
            source="saved_video_context",
            gemini_called=False,
            context_missing=False,
            insufficient_context=low_quality,
            evidence=evidence[:evidence_limit],
            relevant_segments=relevant_segments,
            confidence=min(0.8 if evidence else 0.7, max(0.25, quality.confidence)),
            warnings=warnings,
            limitations=limitations,
            context_ref=str(_context_path(ctx.video_id)),
        )

    if evidence_mode:
        top_segments = (
            [item[1] for item in scored[:3]]
            if scored
            else sorted(
                [*ctx.key_moments, *ctx.timeline],
                key=lambda s: s.confidence,
                reverse=True,
            )[:3]
        )
        for segment in top_segments:
            relevant_segments.append(f"{segment.start}-{segment.end}")
            evidence.extend(_segment_evidence(segment, evidence_limit - len(evidence)))
            if len(evidence) >= evidence_limit:
                break
        if evidence:
            best = max(evidence, key=lambda item: item.confidence)
            answer = (
                "Based on the saved context, the strongest timestamp evidence "
                f"is at {best.timestamp}: {best.detail}"
            )
            confidence = best.confidence
            insufficient = False
        else:
            answer = "Based on the saved context, I did not find timestamp evidence."
            confidence = 0.25
            insufficient = True
        return AskVideoContextResult(
            video_id=ctx.video_id,
            question=question,
            answer=answer,
            source="saved_video_context",
            gemini_called=False,
            context_missing=False,
            insufficient_context=insufficient or low_quality,
            evidence=evidence[:evidence_limit],
            relevant_segments=relevant_segments,
            confidence=min(confidence, max(0.25, quality.confidence)),
            warnings=warnings,
            limitations=limitations,
            context_ref=str(_context_path(ctx.video_id)),
        )

    top = scored[0] if scored else None
    if not top:
        subject = ", ".join(terms[:6]) if terms else question
        answer = (
            "Based on the saved context, I did not find evidence of "
            f"{subject}."
        )
        return AskVideoContextResult(
            video_id=ctx.video_id,
            question=question,
            answer=answer,
            source="saved_video_context",
            gemini_called=False,
            context_missing=False,
            insufficient_context=True,
            evidence=[],
            relevant_segments=[],
            confidence=0.2,
            warnings=warnings,
            limitations=limitations + [
                "Saved context may be too compact for this question; use force_refresh or use_gemini=True if needed."
            ],
            context_ref=str(_context_path(ctx.video_id)),
        )

    score, segment, matches = top
    relevant_segments = [f"{item[1].start}-{item[1].end}" for item in scored[:3]]
    for _, segment_item, _ in scored[:3]:
        evidence.extend(_segment_evidence(segment_item, evidence_limit - len(evidence)))
        if len(evidence) >= evidence_limit:
            break

    weak = score < 1.5
    if low_quality:
        weak = True
    if existence_mode and matches:
        answer = (
            "Based on the saved context, yes. I found evidence related to "
            f"{', '.join(matches[:6])}: {segment.summary}"
        )
    elif existence_mode:
        subject = ", ".join(terms[:6]) if terms else question
        answer = (
            "Based on the saved context, I did not find evidence of "
            f"{subject}."
        )
        weak = True
    else:
        answer = (
            "Based on the saved context, the most relevant segment is "
            f"{segment.start}-{segment.end}: {segment.summary}"
        )

    confidence = min(0.95, max(0.25, score / (len(terms) + 2 or 2)))
    confidence = min(confidence, max(0.25, quality.confidence))
    return AskVideoContextResult(
        video_id=ctx.video_id,
        question=question,
        answer=answer[:1200 if detail == "compact" else 2400],
        source="saved_video_context",
        gemini_called=False,
        context_missing=False,
        insufficient_context=weak,
        evidence=evidence[:evidence_limit],
        relevant_segments=relevant_segments,
        confidence=confidence,
        warnings=warnings,
        limitations=limitations,
        context_ref=str(_context_path(ctx.video_id)),
    )


def _detect_post_type_for_routing(url: str) -> str:
    """Best-effort slideshow detection that never blocks the legacy video path."""
    try:
        post_type = detect_post_type(url)
        logger.info("Slideshow detection result: %s", post_type)
        return post_type
    except Exception as exc:
        logger.warning(
            "Slideshow detection failed; continuing with existing video flow: %s",
            exc,
        )
        return "unknown"


def _make_slideshow_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="slideshow_", dir=str(ANALYSES_DIR)))


def _validate_slideshow_download(slideshow: dict[str, Any], url: str) -> None:
    paths = [str(path) for path in slideshow.get("images") or []]
    if slideshow.get("audio"):
        paths.append(str(slideshow["audio"]))
    if not paths:
        raise SlideshowError("Slideshow download produced no media files.")
    _check_download_sizes(paths)
    _validate_media_paths_for_processing(paths, url)


def _download_slideshow_for_url(url: str, post_type: str) -> tuple[dict[str, Any], Path]:
    temp_dir = _make_slideshow_temp_dir()
    try:
        slideshow = download_slideshow(url, temp_dir, post_type)
        _validate_slideshow_download(slideshow, url)
        return slideshow, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _analyze_slideshow_post(
    url: str,
    prompt: str,
    model: str,
    lang: str | None = None,
    post_type: str | None = None,
) -> str:
    resolved_type = post_type or _detect_post_type_for_routing(url)
    slideshow, temp_dir = _download_slideshow_for_url(url, resolved_type)
    try:
        result = analyze_slideshow(
            _get_client(),
            model,
            slideshow,
            prompt,
            lang,
            upload_file=_upload_to_gemini,
            generate_content=_generate_content_with_retry,
            config=_build_analysis_config(model),
        )
        return _truncate_response(result)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _transcribe_slideshow_audio(
    url: str,
    transcript_prompt: str,
    model: str,
    post_type: str,
) -> str:
    uploaded_file = None
    slideshow, temp_dir = _download_slideshow_for_url(url, post_type)
    try:
        metadata = slideshow.get("metadata") or {}
        audio = slideshow.get("audio")
        if not audio:
            return json.dumps({
                "type": "slideshow_no_audio",
                "message": (
                    f"This post contains {metadata.get('image_count', 0)} images "
                    "but no audio track."
                ),
                "image_count": metadata.get("image_count", 0),
                "platform": metadata.get("platform"),
            }, indent=2, ensure_ascii=False)

        uploaded_file = _upload_to_gemini(str(audio))
        response = _generate_content_with_retry(
            model=model,
            contents=[
                "Transcribe the audio/music track from this slideshow post. "
                "If there is music without intelligible speech, describe that clearly.",
                uploaded_file,
                transcript_prompt,
            ],
            config=_build_analysis_config(model),
        )
        return _truncate_response(_require_response_text(response))
    finally:
        _cleanup(uploaded_file=uploaded_file)
        shutil.rmtree(temp_dir, ignore_errors=True)


def do_prepare_slideshow_assets(
    url: str,
    include_audio: bool = False,
) -> list[Any]:
    """Return slideshow images as MCP content blocks so the client model can inspect them."""
    validate_url(url)
    post_type = _detect_post_type_for_routing(url)
    if post_type not in {"slideshow", "youtube_community"}:
        raise VideoAnalyzerError(
            "This URL was not detected as a photo/slideshow post. "
            "Use analyze_video for regular videos.",
            code="not_slideshow",
            platform=detect_platform(url),
        )

    slideshow, temp_dir = _download_slideshow_for_url(url, post_type)
    try:
        metadata = slideshow.get("metadata") or {}
        images = [Path(path) for path in slideshow.get("images") or []]
        audio = Path(slideshow["audio"]) if slideshow.get("audio") else None
        platform = metadata.get("platform") or detect_platform(url)
        caption = metadata.get("caption")

        content: list[Any] = [
            (
                f"Analyze these {len(images)} slideshow images in order. "
                f"Platform: {platform}. Source URL: {url}. "
                "Each image is preceded by its 1-based image_index label; "
                "preserve that order in your answer."
            )
        ]
        if caption:
            content.append(f"Caption: {caption}")

        for index, image_path in enumerate(images, start=1):
            content.append(f"image_index={index}; image_count={len(images)}")
            content.append(Image(path=image_path).to_image_content())

        if audio:
            if include_audio:
                content.append("audio_track=background music/audio for this slideshow")
                content.append(Audio(path=audio).to_audio_content())
            else:
                content.append(
                    "audio_track_available=true; call get_transcript on this URL "
                    "to transcribe or describe the audio track."
                )
        else:
            content.append("audio_track_available=false")

        return content
    except Exception as exc:
        if isinstance(exc, VideoAnalyzerError):
            raise
        raise VideoAnalyzerError(
            f"Could not prepare slideshow assets: {exc}",
            code="prepare_slideshow_assets_failed",
            platform=detect_platform(url),
        ) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _analyze_youtube(url: str, prompt: str, model: str) -> str:
    """Analyze a YouTube video directly via Gemini (no download)."""
    normalized_url = _normalize_youtube_url(url)
    logger.info("Analyzing YouTube video directly: %s (model: %s)", normalized_url, model)

    response = _generate_content_with_retry(
        model=model,
        contents=types.Content(
            parts=[
                types.Part(
                    file_data=types.FileData(file_uri=normalized_url)
                ),
                types.Part(text=prompt),
            ]
        ),
        config=_build_analysis_config(model),
    )
    return _truncate_response(_require_response_text(response))


def _analyze_downloaded(url: str, prompt: str, model: str) -> str:
    """Download media (video or image carousel), upload to Gemini, analyze."""
    post_type = _detect_post_type_for_routing(url)
    if post_type in {"slideshow", "youtube_community"}:
        return _analyze_slideshow_post(url, prompt, model, post_type=post_type)

    file_paths: list[str] = []
    uploaded_files: list = []
    try:
        file_paths = _download_video(url)
        is_images = bool(file_paths) and all(
            p.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
            for p in file_paths
        )

        if is_images and len(file_paths) > 1:
            logger.info(
                "Analyzing %d images as a carousel (model: %s)...",
                len(file_paths), model,
            )
            image_prompt = (
                f"The following {len(file_paths)} images are slides from a single "
                f"social-media carousel post (Instagram/TikTok photo post), in order. "
                f"Analyze them collectively as one piece of content.\n\n{prompt}"
            )
        else:
            image_prompt = prompt

        for p in file_paths:
            uploaded_files.append(_upload_to_gemini(p))

        logger.info("Analyzing uploaded media (%d file(s), model: %s)...",
                    len(uploaded_files), model)
        contents = list(uploaded_files) + [image_prompt]
        response = _generate_content_with_retry(
            model=model,
            contents=contents,
            config=_build_analysis_config(model),
        )
        return _truncate_response(_require_response_text(response))
    finally:
        _cleanup(file_paths, uploaded_files)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

DEFAULT_ANALYSIS_PROMPT = """Analyze this video comprehensively. Include:

1. **Overview**: What is the video about? Main topic and purpose.
2. **Visual Content**: Describe what is shown visually — scenes, people, text on screen, graphics, transitions.
3. **Audio Content**: What is said (speech), background music, sound effects.
4. **Key Points**: Main messages, arguments, or information conveyed.
5. **Transcript Summary**: Summarize the spoken content with approximate timestamps.
6. **Mood & Tone**: Overall mood, style, and tone of the video.
7. **Technical Quality**: Video quality, editing, production value.
8. **Target Audience**: Who is this video aimed at?

Provide a thorough, detailed analysis."""


# ---------------------------------------------------------------------------
# Core functions (callable directly for testing)
# ---------------------------------------------------------------------------

def do_analyze_video(
    url: str,
    prompt: str = DEFAULT_ANALYSIS_PROMPT,
    model: str = DEFAULT_MODEL,
) -> str:
    """Analyze a video or photo/slideshow post from YouTube, TikTok, or Instagram."""
    validate_url(url)
    post_type = _detect_post_type_for_routing(url)
    platform = detect_platform(url)
    logger.info("analyze_video: platform=%s, url=%s", platform, url)

    try:
        if post_type in {"slideshow", "youtube_community"}:
            return _analyze_slideshow_post(url, prompt, model, post_type=post_type)
        if platform == "youtube":
            return _analyze_youtube(url, prompt, model)
        return _analyze_downloaded(url, prompt, model)
    except Exception as e:
        logger.error("Error analyzing video: %s", e, exc_info=True)
        details = _exception_details(e)
        return _error_response(
            "analyze_video",
            details["message"],
            code=details["code"],
            details=details["details"],
            retryable=details["retryable"],
            platform=details["platform"] or platform,
        )


def do_get_transcript(
    url: str,
    lang: str = "auto",
    model: str = DEFAULT_MODEL,
) -> str:
    """Extract speech transcript from a video with timestamps."""
    validate_url(url)
    post_type = _detect_post_type_for_routing(url)
    lang_instruction = ""
    if lang and lang != "auto":
        lang_instruction = f" The video is in {lang}. Transcribe in that language."

    transcript_prompt = (
        "Extract a detailed transcript of ALL spoken words in this video. "
        "Include timestamps in [MM:SS] format for each segment. "
        "If there are multiple speakers, identify them (Speaker 1, Speaker 2, etc.). "
        "Include any on-screen text as well, marked as [ON-SCREEN TEXT]. "
        "Be thorough — do not skip any spoken content."
        f"{lang_instruction}"
    )

    platform = detect_platform(url)
    logger.info("get_transcript: platform=%s, url=%s, lang=%s", platform, url, lang)

    try:
        if post_type in {"slideshow", "youtube_community"}:
            return _transcribe_slideshow_audio(url, transcript_prompt, model, post_type)
        if platform == "youtube":
            return _analyze_youtube(url, transcript_prompt, model)
        return _analyze_downloaded(url, transcript_prompt, model)
    except Exception as e:
        logger.error("Error extracting transcript: %s", e, exc_info=True)
        details = _exception_details(e)
        return _error_response(
            "get_transcript",
            details["message"],
            code=details["code"],
            details=details["details"],
            retryable=details["retryable"],
            platform=details["platform"] or platform,
        )


def do_ask_about_video(
    url: str,
    question: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """Ask a specific question about a video or photo/slideshow post."""
    validate_url(url)
    post_type = _detect_post_type_for_routing(url)
    question_prompt = (
        f"Watch this video carefully, then answer the following question:\n\n"
        f"Question: {question}\n\n"
        f"Provide a detailed, accurate answer based on the video content."
    )

    platform = detect_platform(url)
    logger.info("ask_about_video: platform=%s, url=%s", platform, url)

    try:
        if post_type in {"slideshow", "youtube_community"}:
            return _analyze_slideshow_post(url, question_prompt, model, post_type=post_type)
        if platform == "youtube":
            return _analyze_youtube(url, question_prompt, model)
        return _analyze_downloaded(url, question_prompt, model)
    except Exception as e:
        logger.error("Error answering question: %s", e, exc_info=True)
        details = _exception_details(e)
        return _error_response(
            "ask_about_video",
            details["message"],
            code=details["code"],
            details=details["details"],
            retryable=details["retryable"],
            platform=details["platform"] or platform,
        )


def do_find_video_moments(
    url: str,
    query: str,
    max_results: int,
    context_seconds: int,
    detail: str,
    model: Optional[str],
    return_full_text: bool,
) -> str:
    """Find semantic video moments with Gemini structured output."""
    try:
        validate_url(url)
        settings = _detail_settings(detail)
        max_results = min(max(1, int(max_results)), settings["max_moments"])
        context_seconds = max(0, int(context_seconds))
        chosen_model = _resolve_model(model, detail)
        prompt = _build_moment_prompt(query, max_results, context_seconds, detail)
        config = _build_structured_config(MomentSearchResult, detail, chosen_model)
        platform = detect_platform(url)
        logger.info(
            "find_video_moments: platform=%s detail=%s model=%s",
            platform,
            detail,
            chosen_model,
        )

        if platform == "youtube":
            normalized = _normalize_youtube_url(url)
            response = _generate_content_with_retry(
                model=chosen_model,
                contents=types.Content(
                    parts=[
                        types.Part(
                            file_data=types.FileData(file_uri=normalized)
                        ),
                        types.Part(text=prompt),
                    ]
                ),
                config=config,
            )
        else:
            file_paths: list[str] = []
            uploaded_files: list = []
            try:
                file_paths = _download_video(url)
                for path in file_paths:
                    uploaded_files.append(_upload_to_gemini(path))
                response = _generate_content_with_retry(
                    model=chosen_model,
                    contents=list(uploaded_files) + [prompt],
                    config=config,
                )
            finally:
                _cleanup(file_paths, uploaded_files)

        parsed = MomentSearchResult.model_validate_json(
            _require_response_text(response)
        )
        parsed.query = query
        parsed.analyzed_by = "gemini"
        parsed.model_used = chosen_model
        parsed.detail = cast(DetailLevel, detail)
        parsed.moments = parsed.moments[:max_results]
        for moment in parsed.moments:
            moment.audio_evidence = moment.audio_evidence[
                :settings["max_audio_evidence"]
            ]
            moment.visual_evidence = moment.visual_evidence[
                :settings["max_visual_evidence"]
            ]
        _apply_strict_moment_filter(parsed, query, detail)

        if detail == "full" and not return_full_text:
            parsed.full_result_ref = _save_full_result(
                url,
                {
                    "tool": "find_video_moments",
                    "query": query,
                    "detail": detail,
                    "model_used": chosen_model,
                    "result": parsed.model_dump(),
                },
            )

        visible = parsed.model_dump()
        visible["gemini_called"] = True
        return json.dumps(visible, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("find_video_moments failed: %s", e, exc_info=True)
        details = _exception_details(e)
        payload = json.loads(
            _error_response(
                "find_video_moments",
                details["message"],
                code=details["code"],
                details=details["details"],
                retryable=details["retryable"],
                platform=details["platform"],
            )
        )
        payload["gemini_called"] = True
        return json.dumps(payload, indent=2, ensure_ascii=False)


def _is_uploaded_clip_rejection(error: Exception) -> bool:
    """Return True when uploaded-file VideoMetadata clipping was rejected."""
    text = str(error).lower()
    markers = (
        "video_metadata",
        "start_offset",
        "end_offset",
        "invalid_argument",
        "unsupported",
        "file_data",
    )
    return any(marker in text for marker in markers)


def do_analyze_video_segment(
    url: str,
    start: str,
    end: str,
    prompt: str,
    detail: str,
    model: Optional[str],
    return_full_text: bool,
) -> str:
    """Analyze a selected video segment with Gemini structured output."""
    fallback_warning = (
        "Server-side video_metadata clipping was not available for this "
        "uploaded file; used prompt-based segment focus fallback."
    )
    segment_warnings: list[str] = []

    try:
        validate_url(url)
        start_offset = _parse_timestamp(start)
        end_offset = _parse_timestamp(end)
        chosen_model = _resolve_model(model, detail)
        segment_prompt = _build_segment_prompt(start, end, prompt, detail)
        config = _build_structured_config(SegmentAnalysisResult, detail, chosen_model)
        settings = _detail_settings(detail)
        platform = detect_platform(url)
        video_metadata = types.VideoMetadata(
            start_offset=start_offset,
            end_offset=end_offset,
        )
        logger.info(
            "analyze_video_segment: platform=%s detail=%s model=%s start=%s end=%s",
            platform,
            detail,
            chosen_model,
            start_offset,
            end_offset,
        )

        if platform == "youtube":
            normalized = _normalize_youtube_url(url)
            video_part = types.Part(
                file_data=types.FileData(file_uri=normalized),
                video_metadata=video_metadata,
            )
            response = _generate_content_with_retry(
                model=chosen_model,
                contents=types.Content(
                    parts=[video_part, types.Part(text=segment_prompt)]
                ),
                config=config,
            )
        else:
            file_paths: list[str] = []
            uploaded_files: list = []
            try:
                file_paths = _download_video(url)
                for path in file_paths:
                    uploaded_files.append(_upload_to_gemini(path))
                if not uploaded_files:
                    raise RuntimeError("No uploaded media file is available.")

                primary = uploaded_files[0]
                uploaded_part = types.Part(
                    file_data=types.FileData(
                        file_uri=primary.uri,
                        mime_type=getattr(primary, "mime_type", None),
                    ),
                    video_metadata=video_metadata,
                )
                try:
                    response = _generate_content_with_retry(
                        model=chosen_model,
                        contents=types.Content(
                            parts=[uploaded_part, types.Part(text=segment_prompt)]
                        ),
                        config=config,
                    )
                except Exception as clip_error:
                    if not _is_uploaded_clip_rejection(clip_error):
                        raise
                    logger.warning(
                        "video_metadata clipping rejected for uploaded file; "
                        "falling back to prompt-based segment focus."
                    )
                    segment_warnings.append(fallback_warning)
                    fallback_prompt = (
                        f"{segment_prompt}\n\n"
                        f"Analyze ONLY the segment from {start} to {end}. "
                        "Treat content outside this time as out-of-scope."
                    )
                    response = _generate_content_with_retry(
                        model=chosen_model,
                        contents=list(uploaded_files) + [fallback_prompt],
                        config=config,
                    )
            finally:
                _cleanup(file_paths, uploaded_files)

        parsed = SegmentAnalysisResult.model_validate_json(
            _require_response_text(response)
        )
        parsed.analyzed_by = "gemini"
        parsed.model_used = chosen_model
        parsed.detail = cast(DetailLevel, detail)
        parsed.segment_start = start
        parsed.segment_end = end
        parsed.key_points = parsed.key_points[:settings["max_key_points"]]
        parsed.evidence = parsed.evidence[:settings["max_evidence_items"]]
        if segment_warnings:
            parsed.warnings.extend(segment_warnings)

        if detail != "full":
            parsed.extended_analysis = None

        if detail == "full" and not return_full_text:
            parsed.full_result_ref = _save_full_result(
                url,
                {
                    "tool": "analyze_video_segment",
                    "segment_start": start,
                    "segment_end": end,
                    "prompt": prompt,
                    "detail": detail,
                    "model_used": chosen_model,
                    "result": parsed.model_dump(),
                },
            )
            parsed.extended_analysis = None
            visible = parsed.model_dump(exclude={"extended_analysis"})
            visible["gemini_called"] = True
            return json.dumps(visible, indent=2, ensure_ascii=False)

        visible = parsed.model_dump()
        visible["gemini_called"] = True
        return json.dumps(visible, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("analyze_video_segment failed: %s", e, exc_info=True)
        details = _exception_details(e)
        payload = json.loads(
            _error_response(
                "analyze_video_segment",
                details["message"],
                code=details["code"],
                details=details["details"],
                retryable=details["retryable"],
                platform=details["platform"],
            )
        )
        payload["gemini_called"] = True
        return json.dumps(payload, indent=2, ensure_ascii=False)


def do_prepare_video_context(
    url: str,
    detail: str,
    force_refresh: bool,
    model: Optional[str],
    chunk_seconds: int,
) -> str:
    """Analyze a whole video once and save a structured local context."""
    gemini_called = False
    try:
        validate_url(url)
        _detail_settings(detail)
        chunk_seconds = min(max(5, int(chunk_seconds)), 300)
        normalized = _normalize_video_url(url)
        video_id = _video_id_from_url(url)
        existing = _load_video_context(video_id)
        if existing is not None and not force_refresh:
            return json.dumps(
                _compact_prepare_result(
                    existing,
                    reused_existing=True,
                    gemini_called=False,
                ),
                indent=2,
                ensure_ascii=False,
            )

        chosen_model = _resolve_model(model, detail)
        prompt = _build_video_context_prompt(normalized, detail, chunk_seconds)
        config = _build_video_context_config(detail, SavedVideoContext, chosen_model)
        platform = detect_platform(url)
        logger.info(
            "prepare_video_context: platform=%s detail=%s model=%s video_id=%s",
            platform,
            detail,
            chosen_model,
            video_id,
        )

        if platform == "youtube":
            gemini_called = True
            response = _generate_content_with_retry(
                model=chosen_model,
                contents=types.Content(
                    parts=[
                        types.Part(file_data=types.FileData(file_uri=normalized)),
                        types.Part(text=prompt),
                    ]
                ),
                config=config,
            )
        else:
            file_paths: list[str] = []
            uploaded_files: list = []
            try:
                file_paths = _download_video(url)
                for path in file_paths:
                    uploaded_files.append(_upload_to_gemini(path))
                    gemini_called = True
                gemini_called = True
                response = _generate_content_with_retry(
                    model=chosen_model,
                    contents=list(uploaded_files) + [prompt],
                    config=config,
                )
            finally:
                _cleanup(file_paths, uploaded_files)

        now = datetime.now().isoformat()
        parsed = SavedVideoContext.model_validate_json(
            _require_response_text(response)
        )
        parsed.schema_version = "1.3.0"
        parsed.video_id = video_id
        parsed.url = url
        parsed.normalized_url = normalized
        parsed.created_at = existing.created_at if existing else now
        parsed.updated_at = now
        parsed.analyzed_by = "gemini"
        parsed.model_used = chosen_model
        parsed.detail = cast(DetailLevel, detail)
        parsed.timeline = parsed.timeline[:40]
        parsed.key_moments = parsed.key_moments[:20]
        parsed.context_quality = _compute_context_quality(parsed)
        _save_video_context(parsed)

        return json.dumps(
            _compact_prepare_result(
                parsed,
                reused_existing=False,
                gemini_called=True,
            ),
            indent=2,
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error("prepare_video_context failed: %s", e, exc_info=True)
        details = _exception_details(e)
        payload = json.loads(
            _error_response(
                "prepare_video_context",
                details["message"],
                code=details["code"],
                details=details["details"],
                retryable=details["retryable"],
                platform=details["platform"],
            )
        )
        payload["gemini_called"] = gemini_called
        return json.dumps(payload, indent=2, ensure_ascii=False)


def _missing_context_result(video_id_or_url: str, question: str) -> str:
    video_id = ""
    if "://" in video_id_or_url:
        try:
            video_id = _video_id_from_url(video_id_or_url)
        except Exception:
            video_id = ""
    else:
        video_id = re.sub(r"[^a-zA-Z0-9_-]", "", video_id_or_url.strip())

    result = AskVideoContextResult(
        video_id=video_id,
        question=question,
        answer=(
            "No saved video context was found. Call prepare_video_context for "
            "this video first, or explicitly set use_gemini=True to allow a "
            "new Gemini analysis."
        ),
        source="context_missing",
        gemini_called=False,
        context_missing=True,
        insufficient_context=True,
        evidence=[],
        relevant_segments=[],
        confidence=0.0,
        warnings=[],
        limitations=["Saved-context Q&A cannot answer without a saved context."],
        context_ref=None,
    )
    return json.dumps(result.model_dump(), indent=2, ensure_ascii=False)


def _build_context_followup_prompt(
    ctx: SavedVideoContext,
    question: str,
    detail: str,
) -> str:
    compact_context = ctx.model_dump(
        exclude={"gemini_followups", "raw_full_result_ref"}
    )
    return (
        "Answer the user's question from this saved structured video context. "
        "Return structured JSON only. Do not include a transcript, base64, "
        "binary data, or long free-form analysis. Use timestamp evidence from "
        "the saved context where possible. If the context is insufficient, set "
        "insufficient_context=true and say so clearly.\n\n"
        f"Detail: {detail}\n"
        f"Question: {question}\n"
        f"Saved context JSON: {json.dumps(compact_context, ensure_ascii=False)[:30000]}"
    )


def _ask_gemini_about_saved_context(
    ctx: SavedVideoContext,
    question: str,
    detail: str,
) -> AskVideoContextResult:
    chosen_model = _resolve_model(None, "standard")
    prompt = _build_context_followup_prompt(ctx, question, detail)
    config = _build_structured_config(AskVideoContextResult, "standard", chosen_model)
    response = _generate_content_with_retry(
        model=chosen_model,
        contents=prompt,
        config=config,
    )
    answer = AskVideoContextResult.model_validate_json(
        _require_response_text(response)
    )
    answer.video_id = ctx.video_id
    answer.question = question
    answer.source = "gemini_reanalysis"
    answer.gemini_called = True
    answer.context_missing = False
    answer.context_ref = str(_context_path(ctx.video_id))
    return answer


def _persist_gemini_followup(
    ctx: SavedVideoContext,
    answer: AskVideoContextResult,
) -> None:
    followup = FollowupAnswer(
        asked_at=datetime.now().isoformat(),
        question=answer.question,
        answer=answer.answer[:2400],
        evidence=answer.evidence[:8],
        model_used=ctx.model_used,
    )
    ctx.gemini_followups.append(followup)
    ctx.updated_at = datetime.now().isoformat()
    _save_video_context(ctx)


def do_ask_video_context(
    video_id_or_url: str,
    question: str,
    reanalyze_if_needed: bool,
    use_gemini: bool,
    detail: str,
) -> str:
    """Answer from saved video context by default; Gemini only when allowed."""
    try:
        if detail not in ("compact", "standard"):
            raise ValueError("detail must be 'compact' or 'standard'")

        ctx = _load_video_context(video_id_or_url)
        can_prepare = "://" in video_id_or_url

        if ctx is None:
            if (use_gemini or reanalyze_if_needed) and can_prepare:
                prepare_raw = do_prepare_video_context(
                    video_id_or_url,
                    "standard",
                    True,
                    None,
                    30,
                )
                prepare_data = json.loads(prepare_raw)
                if prepare_data.get("status") == "error":
                    return _missing_context_result(video_id_or_url, question)
                ctx = _load_video_context(video_id_or_url)
                if ctx is None:
                    return _missing_context_result(video_id_or_url, question)
                answer = _ask_gemini_about_saved_context(ctx, question, detail)
                _persist_gemini_followup(ctx, answer)
                return json.dumps(answer.model_dump(), indent=2, ensure_ascii=False)
            return _missing_context_result(video_id_or_url, question)

        if use_gemini:
            answer = _ask_gemini_about_saved_context(ctx, question, detail)
            _persist_gemini_followup(ctx, answer)
            return json.dumps(answer.model_dump(), indent=2, ensure_ascii=False)

        answer = _answer_from_saved_context(ctx, question, detail)
        if answer.insufficient_context and reanalyze_if_needed:
            answer = _ask_gemini_about_saved_context(ctx, question, detail)
            _persist_gemini_followup(ctx, answer)
            return json.dumps(
                answer.model_dump(),
                indent=2,
                ensure_ascii=False,
            )
        return json.dumps(answer.model_dump(), indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("ask_video_context failed: %s", e, exc_info=True)
        return json.dumps(
            {
                "status": "error",
                "tool": "ask_video_context",
                "gemini_called": False,
                "message": str(e),
            },
            indent=2,
            ensure_ascii=False,
        )


def do_list_video_contexts(
    filter_text: Optional[str],
    limit: int,
) -> str:
    """List saved local video contexts."""
    limit = min(max(1, int(limit)), 200)
    needle = (filter_text or "").strip().lower()
    rows = []
    VIDEO_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

    for path in sorted(
        VIDEO_CONTEXT_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            ctx = SavedVideoContext.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception as e:
            logger.warning("Skipping malformed video context %s: %s", path, e)
            continue
        searchable = _flatten_text(
            [
                ctx.video_id,
                ctx.url,
                ctx.title_guess,
                ctx.summary,
                ctx.topics,
                ctx.entities,
                ctx.objects,
            ]
        ).lower()
        if needle and needle not in searchable:
            continue
        rows.append(
            {
                "video_id": ctx.video_id,
                "url": ctx.url,
                "created_at": ctx.created_at,
                "model_used": ctx.model_used,
                "title_guess": ctx.title_guess,
                "summary_short": ctx.summary[:300],
                "context_ref": str(path),
            }
        )
        if len(rows) >= limit:
            break

    return json.dumps(
        {
            "count": len(rows),
            "contexts": rows,
            "gemini_called": False,
        },
        indent=2,
        ensure_ascii=False,
    )


def _resolve_context_path_loose(video_id_or_url: str) -> tuple[str, Path | None]:
    raw = video_id_or_url.strip()
    if not raw:
        return "", None
    video_id = ""
    if "://" in raw:
        try:
            normalized = _normalize_video_url(raw)
            video_id = _video_id_from_url(raw)
        except Exception:
            normalized = raw
    else:
        normalized = raw
        video_id = re.sub(r"[^a-zA-Z0-9_-]", "", raw)

    if video_id:
        path = _context_path(video_id)
        if path.exists() and _is_within(path, VIDEO_CONTEXT_DIR):
            return video_id, path

    if "://" in raw:
        for path in VIDEO_CONTEXT_DIR.glob("*.json"):
            if not _is_within(path, VIDEO_CONTEXT_DIR):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("url") == raw or data.get("normalized_url") == normalized:
                return str(data.get("video_id") or path.stem), path

    return video_id, None


def do_delete_video_context(video_id_or_url: str) -> str:
    """Delete one saved local video context."""
    video_id, path = _resolve_context_path_loose(video_id_or_url)
    context_ref = str(path or _context_path(video_id)) if video_id else None
    deleted = False
    if path is not None and path.exists():
        if not _is_within(path, VIDEO_CONTEXT_DIR):
            raise RuntimeError("Refusing to delete a context path outside video_contexts.")
        path.unlink()
        deleted = True

    return json.dumps(
        {
            "deleted": deleted,
            "video_id": video_id,
            "context_ref": context_ref,
            "gemini_called": False,
            "message": (
                "Deleted saved video context."
                if deleted
                else "No saved video context found to delete."
            ),
        },
        indent=2,
        ensure_ascii=False,
    )


def _known_video_ids() -> set[str]:
    ids: set[str] = set()
    ids.update(path.stem for path in VIDEO_CONTEXT_DIR.glob("*.json") if path.is_file())
    ids.update(path.name for path in VIDEO_SOURCE_DIR.iterdir() if path.is_dir())
    ids.update(path.name for path in VIDEO_ASSET_DIR.iterdir() if path.is_dir())
    return {re.sub(r"[^a-zA-Z0-9_-]", "", item) for item in ids if item}


def do_list_video_sources(filter_text: Optional[str], limit: int) -> str:
    """List cached sources, assets, and contexts per video_id."""
    limit = min(max(1, int(limit)), 500)
    needle = (filter_text or "").strip().lower()
    items = []
    totals = {
        "sources_bytes": 0,
        "assets_bytes": 0,
        "contexts_bytes": 0,
    }

    for video_id in sorted(_known_video_ids()):
        context_path = _context_path(video_id)
        source_dir = VIDEO_SOURCE_DIR / video_id
        asset_dir = VIDEO_ASSET_DIR / video_id
        ctx = _load_video_context(video_id)
        source_url = ctx.url if ctx else None
        cached_source = _find_cached_source(video_id)
        source_info = _source_validation_summary(cached_source, source_url)
        title_guess = ctx.title_guess if ctx else None
        searchable = _flatten_text([video_id, source_url, title_guess]).lower()
        if needle and needle not in searchable:
            continue

        source_bytes = _path_size_bytes(source_dir)
        assets_bytes = _path_size_bytes(asset_dir)
        context_bytes = _path_size_bytes(context_path)
        totals["sources_bytes"] += source_bytes
        totals["assets_bytes"] += assets_bytes
        totals["contexts_bytes"] += context_bytes
        last_updated = max(
            [
                value
                for value in (
                    _latest_mtime_iso(context_path),
                    _latest_mtime_iso(source_dir),
                    _latest_mtime_iso(asset_dir),
                )
                if value
            ],
            default=None,
        )
        items.append(
            {
                "video_id": video_id,
                "source_url": source_url,
                "title_guess": title_guess,
                "source_ref": str(cached_source) if cached_source else None,
                "source_bytes": source_bytes,
                **source_info,
                "assets_bytes": assets_bytes,
                "context_bytes": context_bytes,
                "context_ref": str(context_path) if context_path.exists() else None,
                "last_updated": last_updated,
            }
        )
        if len(items) >= limit:
            break

    return json.dumps(
        {
            "items": items,
            "totals": totals,
            "gemini_called": False,
        },
        indent=2,
        ensure_ascii=False,
    )


def _approved_cleanup_roots() -> set[Path]:
    """Approved cache roots that ``cleanup_video_cache`` is allowed to touch.

    These are the configured/default cache directories the server itself
    creates and manages. Validation against this set replaces the older
    PROJECT_ROOT containment check, which broke under packaged/uvx installs
    where the package lives in site-packages but cache dirs resolve under
    the process working directory.
    """
    return {
        VIDEO_SOURCE_DIR.resolve(),
        VIDEO_ASSET_DIR.resolve(),
        VIDEO_CONTEXT_DIR.resolve(),
    }


def _assert_safe_cleanup_root(root: Path) -> Path:
    """Ensure ``root`` is one of the approved cache roots and not a system root.

    Raises ``RuntimeError`` for filesystem root, drive root, or the user's
    home directory itself; and for any path that is not in the approved set.
    Returns the resolved path on success.
    """
    resolved = root.resolve()
    home = Path.home().resolve()
    if resolved == Path(resolved.anchor) or resolved == home:
        raise RuntimeError(f"Refusing cleanup of system root: {root}")
    if resolved not in _approved_cleanup_roots():
        raise RuntimeError(f"Refusing cleanup root outside managed cache: {root}")
    return resolved


def _cache_cleanup_candidates(scope: str, video_id: str | None) -> list[tuple[str, Path, Path]]:
    scopes = ("sources", "assets", "contexts", "all")
    if scope not in scopes:
        raise ValueError("scope must be one of: sources, assets, contexts, all")
    ids = [video_id] if video_id else sorted(_known_video_ids())
    roots: list[tuple[str, Path]] = []
    if scope in ("sources", "all"):
        roots.append(("sources", VIDEO_SOURCE_DIR))
    if scope in ("assets", "all"):
        roots.append(("assets", VIDEO_ASSET_DIR))
    if scope in ("contexts", "all"):
        roots.append(("contexts", VIDEO_CONTEXT_DIR))

    candidates: list[tuple[str, Path, Path]] = []
    for kind, root in roots:
        resolved_root = _assert_safe_cleanup_root(root)
        root.mkdir(parents=True, exist_ok=True)
        for vid in ids:
            if not vid:
                continue
            safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", vid)
            path = root / f"{safe_id}.json" if kind == "contexts" else root / safe_id
            if path.exists():
                if not _is_within(path, resolved_root):
                    raise RuntimeError(f"Refusing cleanup path outside {root}: {path}")
                candidates.append((kind, path, resolved_root))
    return candidates


def do_cleanup_video_cache(
    scope: str,
    dry_run: bool,
    video_id: Optional[str],
) -> str:
    """Inspect or clean managed video cache directories; safe by default."""
    normalized_video_id: str | None = None
    if video_id:
        normalized_video_id = (
            _video_id_from_url(video_id)
            if "://" in video_id
            else re.sub(r"[^a-zA-Z0-9_-]", "", video_id.strip())
        )
        if not normalized_video_id:
            raise ValueError("video_id cannot be empty")

    candidates = _cache_cleanup_candidates(scope, normalized_video_id)
    rows = [
        {
            "kind": kind,
            "path": str(path),
            "bytes": _path_size_bytes(path),
        }
        for kind, path, _ in candidates
    ]
    freed_bytes = sum(row["bytes"] for row in rows)
    removed: list[dict] = []
    if not dry_run:
        for row, (_, path, root) in zip(rows, candidates):
            _assert_safe_cleanup_root(root)
            if not _is_within(path, root):
                raise RuntimeError(f"Refusing cleanup path outside managed cache: {path}")
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(row)

    return json.dumps(
        {
            "scope": scope,
            "video_id": normalized_video_id,
            "dry_run": bool(dry_run),
            "would_remove": rows if dry_run else [],
            "removed": removed,
            "freed_bytes": freed_bytes,
            "gemini_called": False,
        },
        indent=2,
        ensure_ascii=False,
    )


def do_get_video_frame(
    video_id_or_url: str,
    timestamp: str,
    reason: Optional[str],
    output_format: str,
    force_refresh: bool,
) -> str:
    """Extract one local still frame and return compact asset metadata."""
    try:
        fmt = output_format.lower().strip()
        if fmt not in ("jpg", "png"):
            raise ValueError("output_format must be 'jpg' or 'png'")
        timestamp_seconds = _parse_timestamp_to_seconds(timestamp)
        timestamp_label = _format_seconds_to_timestamp(timestamp_seconds)
        video_id, _, _ = _resolve_video_identity(video_id_or_url)
        safe_ts = _safe_timestamp_for_filename(timestamp_label)
        asset_id = _asset_id_for(video_id, "frame", timestamp_label, fmt)
        output_path = _asset_dir(video_id, "frames") / f"{safe_ts}.{fmt}"
        mime_type = "image/png" if fmt == "png" else "image/jpeg"

        existing = None if force_refresh else _lookup_asset(video_id, "frame", safe_ts, fmt)
        if existing is not None:
            return _build_asset_result(
                asset_id=existing.get("asset_id", asset_id),
                video_id=video_id,
                asset_type="image",
                mime_type=existing.get("mime_type", mime_type),
                asset_ref=existing["asset_ref"],
                request=reason,
                timestamp=existing.get("timestamp", timestamp_label),
                source_context_ref=existing.get("source_context_ref") or _context_ref(video_id),
                matched_reason=reason,
                reused_existing=True,
                warnings=existing.get("warnings", []),
                limitations=existing.get("limitations", []),
            )

        if not force_refresh and output_path.exists():
            _record_asset(
                video_id,
                "frame",
                safe_ts,
                fmt,
                str(output_path),
                {
                    "asset_id": asset_id,
                    "type": "frame",
                    "timestamp": timestamp_label,
                    "mime_type": mime_type,
                    "created_at": datetime.now().isoformat(),
                    "request": reason,
                    "source_context_ref": _context_ref(video_id),
                    "warnings": [],
                    "limitations": [],
                },
            )
            return _build_asset_result(
                asset_id=asset_id,
                video_id=video_id,
                asset_type="image",
                mime_type=mime_type,
                asset_ref=str(output_path),
                request=reason,
                timestamp=timestamp_label,
                source_context_ref=_context_ref(video_id),
                matched_reason=reason,
                reused_existing=True,
            )

        cached_source = _find_cached_source(video_id)
        if cached_source is not None and not force_refresh:
            _validate_media_file_for_processing(
                cached_source,
                video_id_or_url if "://" in video_id_or_url else None,
                require_decodable=True,
            )
            _run_ffmpeg_extract_frame(cached_source, timestamp_seconds, output_path)
        elif "://" in video_id_or_url and detect_platform(video_id_or_url) == "youtube":
            _run_youtube_fast_frame_extract(
                video_id_or_url,
                timestamp_seconds,
                output_path,
            )
        else:
            source_path, _, _ = _ensure_video_source(video_id_or_url, force_refresh)
            _run_ffmpeg_extract_frame(source_path, timestamp_seconds, output_path)

        _record_asset(
            video_id,
            "frame",
            safe_ts,
            fmt,
            str(output_path),
            {
                "asset_id": asset_id,
                "type": "frame",
                "timestamp": timestamp_label,
                "mime_type": mime_type,
                "created_at": datetime.now().isoformat(),
                "request": reason,
                "source_context_ref": _context_ref(video_id),
                "warnings": [],
                "limitations": [],
            },
        )
        return _build_asset_result(
            asset_id=asset_id,
            video_id=video_id,
            asset_type="image",
            mime_type=mime_type,
            asset_ref=str(output_path),
            request=reason,
            timestamp=timestamp_label,
            source_context_ref=_context_ref(video_id),
            matched_reason=reason,
            reused_existing=False,
        )
    except Exception as e:
        logger.error("get_video_frame failed: %s", e, exc_info=True)
        details = _exception_details(e)
        return _asset_error(
            video_id_or_url,
            reason,
            details["message"],
            tool="get_video_frame",
            code=details["code"],
            details=details["details"],
            retryable=details["retryable"],
        )


def do_get_video_clip(
    video_id_or_url: str,
    start: str,
    end: str,
    reason: Optional[str],
    max_duration_seconds: int,
    output_format: str,
    force_refresh: bool,
) -> str:
    """Extract a local short video clip and return compact asset metadata."""
    try:
        fmt = output_format.lower().strip()
        if fmt not in ("mp4", "webm"):
            raise ValueError("output_format must be 'mp4' or 'webm'")
        hard_cap = min(max(1, int(max_duration_seconds)), 30)
        start_seconds = _parse_timestamp_to_seconds(start)
        end_seconds = _parse_timestamp_to_seconds(end)
        duration = end_seconds - start_seconds
        if duration <= 0:
            raise ValueError("end must be greater than start")
        if duration > hard_cap:
            raise ValueError(
                f"clip duration is {duration:.1f}s, which exceeds the "
                f"{hard_cap}s limit. Choose a shorter range."
            )

        start_label = _format_seconds_to_timestamp(start_seconds)
        end_label = _format_seconds_to_timestamp(end_seconds)
        video_id, source_url, _ = _resolve_video_identity(video_id_or_url)
        safe_range = (
            f"{_safe_timestamp_for_filename(start_label)}_"
            f"{_safe_timestamp_for_filename(end_label)}"
        )
        asset_id = _asset_id_for(video_id, "clip", f"{start_label}-{end_label}", fmt)
        output_path = _asset_dir(video_id, "clips") / f"{safe_range}.{fmt}"
        mime_type = "video/webm" if fmt == "webm" else "video/mp4"

        existing = None if force_refresh else _lookup_asset(video_id, "clip", safe_range, fmt)
        if existing is not None:
            return _build_asset_result(
                asset_id=existing.get("asset_id", asset_id),
                video_id=video_id,
                asset_type="video_clip",
                mime_type=existing.get("mime_type", mime_type),
                asset_ref=existing["asset_ref"],
                request=reason,
                start=existing.get("start", start_label),
                end=existing.get("end", end_label),
                duration_seconds=existing.get("duration_seconds", round(duration, 3)),
                source_context_ref=existing.get("source_context_ref") or _context_ref(video_id),
                matched_reason=reason,
                reused_existing=True,
                warnings=existing.get("warnings", []),
                limitations=existing.get("limitations", []),
            )

        if not force_refresh and output_path.exists():
            _record_asset(
                video_id,
                "clip",
                safe_range,
                fmt,
                str(output_path),
                {
                    "asset_id": asset_id,
                    "type": "clip",
                    "start": start_label,
                    "end": end_label,
                    "duration_seconds": round(duration, 3),
                    "mime_type": mime_type,
                    "created_at": datetime.now().isoformat(),
                    "request": reason,
                    "source_context_ref": _context_ref(video_id),
                    "warnings": [],
                    "limitations": [],
                },
            )
            return _build_asset_result(
                asset_id=asset_id,
                video_id=video_id,
                asset_type="video_clip",
                mime_type=mime_type,
                asset_ref=str(output_path),
                request=reason,
                start=start_label,
                end=end_label,
                duration_seconds=round(duration, 3),
                source_context_ref=_context_ref(video_id),
                matched_reason=reason,
                reused_existing=True,
            )

        cached_source = _find_cached_source(video_id)
        if cached_source is not None and not force_refresh:
            _validate_media_file_for_processing(
                cached_source,
                source_url,
                require_decodable=True,
            )
            _run_ffmpeg_extract_clip(cached_source, start_seconds, duration, output_path)
        elif source_url and detect_platform(source_url) == "youtube":
            _run_youtube_fast_clip_extract(
                source_url,
                start_seconds,
                duration,
                output_path,
            )
        else:
            source_path, _, _ = _ensure_video_source(video_id_or_url, force_refresh)
            _run_ffmpeg_extract_clip(source_path, start_seconds, duration, output_path)

        _record_asset(
            video_id,
            "clip",
            safe_range,
            fmt,
            str(output_path),
            {
                "asset_id": asset_id,
                "type": "clip",
                "start": start_label,
                "end": end_label,
                "duration_seconds": round(duration, 3),
                "mime_type": mime_type,
                "created_at": datetime.now().isoformat(),
                "request": reason,
                "source_context_ref": _context_ref(video_id),
                "warnings": [],
                "limitations": [],
            },
        )
        return _build_asset_result(
            asset_id=asset_id,
            video_id=video_id,
            asset_type="video_clip",
            mime_type=mime_type,
            asset_ref=str(output_path),
            request=reason,
            start=start_label,
            end=end_label,
            duration_seconds=round(duration, 3),
            source_context_ref=_context_ref(video_id),
            matched_reason=reason,
            reused_existing=False,
        )
    except Exception as e:
        logger.error("get_video_clip failed: %s", e, exc_info=True)
        details = _exception_details(e)
        return _asset_error(
            video_id_or_url,
            reason,
            details["message"],
            tool="get_video_clip",
            code=details["code"],
            details=details["details"],
            retryable=details["retryable"],
        )


def _augment_evidence_asset_payload(
    raw: str,
    request: str,
    matched_reason: str,
    segment: VideoContextSegment,
    evidence: VideoContextEvidence | None,
    confidence: float,
) -> str:
    data = json.loads(raw)
    data["request"] = request
    data["matched_reason"] = matched_reason
    data["matched_segment"] = {
        "start": segment.start,
        "end": segment.end,
        "summary": segment.summary,
    }
    data["matched_evidence"] = evidence.model_dump() if evidence else None
    data["confidence"] = confidence
    data["gemini_called"] = False
    data.setdefault("reused_existing", False)
    if data.get("status") == "error":
        data.setdefault("insufficient_context", False)
    return json.dumps(data, indent=2, ensure_ascii=False)


def do_get_video_evidence_asset(
    video_id_or_url: str,
    request: str,
    asset_type: str,
    preferred_timestamp: Optional[str],
    preferred_start: Optional[str],
    preferred_end: Optional[str],
    max_duration_seconds: int,
) -> str:
    """Find saved visual evidence and extract a local frame or short clip."""
    try:
        if asset_type not in ("frame", "clip"):
            raise ValueError("asset_type must be 'frame' or 'clip'")
        ctx = _load_video_context(video_id_or_url)
        if ctx is None:
            return _asset_error(
                video_id_or_url,
                request,
                "context_missing: run prepare_video_context first or provide a saved video_id.",
                context_missing=True,
                insufficient_context=True,
            )

        segment, evidence, matched_reason, confidence = _best_context_match(ctx, request)
        if segment is None:
            return _asset_error(
                video_id_or_url,
                request,
                "insufficient_context: no saved timeline/evidence is available for this request.",
                insufficient_context=True,
            )

        if asset_type == "frame":
            timestamp = (
                preferred_timestamp
                or (evidence.timestamp if evidence else None)
                or segment.start
            )
            raw = do_get_video_frame(
                ctx.video_id,
                timestamp,
                reason=request,
                output_format="jpg",
                force_refresh=False,
            )
            return _augment_evidence_asset_payload(
                raw,
                request,
                matched_reason,
                segment,
                evidence,
                confidence,
            )

        cap = min(max(1, int(max_duration_seconds)), 30)
        if preferred_start and preferred_end:
            start = preferred_start
            end = preferred_end
        else:
            start_seconds = _parse_timestamp_to_seconds(segment.start)
            end_seconds = _parse_timestamp_to_seconds(segment.end)
            request_lower = request.lower()
            last_n = re.search(r"last\s+(\d{1,2})\s*(?:seconds|second|sec|s)\b", request_lower)
            if last_n:
                requested = min(int(last_n.group(1)), cap)
                end_seconds = max(end_seconds, start_seconds + requested)
                start_seconds = max(start_seconds, end_seconds - requested)
            if end_seconds <= start_seconds:
                end_seconds = start_seconds + min(5, cap)
            if end_seconds - start_seconds > cap:
                end_seconds = start_seconds + cap
            start = _format_seconds_to_timestamp(start_seconds)
            end = _format_seconds_to_timestamp(end_seconds)

        raw = do_get_video_clip(
            ctx.video_id,
            start,
            end,
            reason=request,
            max_duration_seconds=cap,
            output_format="mp4",
            force_refresh=False,
        )
        return _augment_evidence_asset_payload(
            raw,
            request,
            matched_reason,
            segment,
            evidence,
            confidence,
        )
    except Exception as e:
        logger.error("get_video_evidence_asset failed: %s", e, exc_info=True)
        details = _exception_details(e)
        return _asset_error(
            video_id_or_url,
            request,
            details["message"],
            tool="get_video_evidence_asset",
            code=details["code"],
            details=details["details"],
            retryable=details["retryable"],
        )


# ---------------------------------------------------------------------------
# MCP Tool wrappers
# ---------------------------------------------------------------------------

def _dispatch_or_background(tool_name: str, url: str, func, *args, **kwargs) -> str:
    """Run synchronously for YouTube, or dispatch to background for other platforms.

    Returns the result directly for YouTube, or a JSON job ticket for others.
    """
    platform = detect_platform(url)
    if platform == "youtube":
        return func(*args, **kwargs)

    # Non-YouTube: run in background to avoid Claude Desktop timeout
    job_id = _create_job(tool_name, url)
    _run_in_background(job_id, func, *args, **kwargs)
    return json.dumps({
        "status": "processing",
        "job_id": job_id,
        "gemini_called": False,
        "message": (
            f"Downloading and analyzing {platform} video in the background. "
            f"Use check_analysis_job(job_id=\"{job_id}\") to get the result."
        ),
    }, indent=2)


@mcp.tool()
def analyze_video(
    url: str,
    prompt: str = DEFAULT_ANALYSIS_PROMPT,
    model: str = DEFAULT_MODEL,
) -> str:
    """Analyze a video or photo/slideshow post from YouTube, TikTok, or Instagram.

    Provides comprehensive audio + visual analysis using Gemini AI.
    Works with videos AND photo/slideshow posts on TikTok, Instagram, and
    YouTube community posts.
    YouTube videos are analyzed directly and return the result immediately.
    TikTok and Instagram videos are processed in the background — the tool
    returns a job_id. Use check_analysis_job(job_id) to poll for the result.

    Args:
        url: The video URL (YouTube, TikTok, Instagram, or other).
        prompt: Custom analysis prompt. Defaults to comprehensive analysis.
        model: Gemini model to use. Defaults to gemini-3.5-flash.

    Returns:
        Analysis text (YouTube) or JSON with job_id (TikTok/Instagram).
    """
    return _dispatch_or_background(
        "analyze_video", url, do_analyze_video, url, prompt, model,
    )


@mcp.tool()
def get_transcript(
    url: str,
    lang: str = "auto",
    model: str = DEFAULT_MODEL,
) -> str:
    """Extract speech transcript from a video or slideshow audio track.

    YouTube returns the result immediately. TikTok/Instagram return a job_id —
    use check_analysis_job(job_id) to poll for the result.
    Slideshows without audio return a structured slideshow_no_audio response.

    Args:
        url: The video URL (YouTube, TikTok, Instagram, or other).
        lang: Language hint (e.g., 'en', 'ar', 'auto'). Defaults to auto-detect.
        model: Gemini model to use. Defaults to gemini-3.5-flash.

    Returns:
        Transcript text (YouTube) or JSON with job_id (TikTok/Instagram).
    """
    return _dispatch_or_background(
        "get_transcript", url, do_get_transcript, url, lang, model,
    )


@mcp.tool()
def ask_about_video(
    url: str,
    question: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """Ask a specific question about a video or photo/slideshow post.

    Works with videos AND photo/slideshow posts on TikTok, Instagram, and
    YouTube community posts.
    YouTube returns the answer immediately. TikTok/Instagram return a job_id —
    use check_analysis_job(job_id) to poll for the result.

    Args:
        url: The video URL (YouTube, TikTok, Instagram, or other).
        question: Your question about the video.
        model: Gemini model to use. Defaults to gemini-3.5-flash.

    Returns:
        Answer text (YouTube) or JSON with job_id (TikTok/Instagram).
    """
    return _dispatch_or_background(
        "ask_about_video", url, do_ask_about_video, url, question, model,
    )


@mcp.tool()
def prepare_slideshow_assets(
    url: str,
    include_audio: bool = False,
) -> list[Any]:
    """Return slideshow images as ordered MCP image blocks for client-side vision.

    This tool does not call Gemini. It downloads TikTok Photo Mode,
    Instagram photo/carousel posts, or YouTube community post images, then
    returns each image directly to the MCP client with an explicit
    image_index label. Use it when you want Claude/the client AI to inspect
    the images itself instead of receiving a Gemini-generated analysis.

    Args:
        url: TikTok Photo Mode, Instagram photo/carousel, or YouTube community URL.
        include_audio: If true and a slideshow audio track exists, include it
            as an MCP audio block. Defaults to false to keep responses lighter.

    Returns:
        Ordered text + image content blocks, optionally including audio.
    """
    return do_prepare_slideshow_assets(url, include_audio)


@mcp.tool()
def find_video_moments(
    url: str,
    query: str,
    max_results: int = 5,
    context_seconds: int = 15,
    detail: str = "compact",
    model: Optional[str] = None,
    return_full_text: bool = False,
) -> str:
    """Find moments in a video matching a semantic query.

    Gemini performs the video/audio/visual reasoning.
    detail controls model + max_output_tokens + thinking/media config.
    compact is default and returns concise structured JSON.
    """
    return _dispatch_or_background(
        "find_video_moments",
        url,
        do_find_video_moments,
        url,
        query,
        max_results,
        context_seconds,
        detail,
        model,
        return_full_text,
    )


@mcp.tool()
def analyze_video_segment(
    url: str,
    start: str,
    end: str,
    prompt: str = "Analyze this segment in detail.",
    detail: str = "compact",
    model: Optional[str] = None,
    return_full_text: bool = False,
) -> str:
    """Analyze only a selected video segment.

    Uses Gemini video_metadata clipping when available.
    Gemini performs video/audio/visual reasoning.
    detail controls model + max_output_tokens + thinking/media config.
    """
    return _dispatch_or_background(
        "analyze_video_segment",
        url,
        do_analyze_video_segment,
        url,
        start,
        end,
        prompt,
        detail,
        model,
        return_full_text,
    )


@mcp.tool()
def prepare_video_context(
    url: str,
    detail: Literal["compact", "standard", "full"] = "standard",
    force_refresh: bool = False,
    model: Optional[str] = None,
    chunk_seconds: int = 30,
) -> str:
    """Analyze a whole video once and save a local structured context."""
    validate_url(url)

    existing: SavedVideoContext | None = None
    try:
        existing = _load_video_context(url)
    except Exception as exc:
        # A malformed/corrupt cached context must not block new analysis or
        # bypass async dispatch. Treat lookup failure as "no reusable context".
        logger.warning(
            "prepare_video_context: cached context lookup failed, treating as missing: %s",
            exc,
        )
        existing = None

    if existing is not None and not force_refresh:
        return do_prepare_video_context(
            url,
            detail,
            force_refresh,
            model,
            chunk_seconds,
        )

    return _dispatch_or_background(
        "prepare_video_context",
        url,
        do_prepare_video_context,
        url,
        detail,
        force_refresh,
        model,
        chunk_seconds,
    )


@mcp.tool()
def ask_video_context(
    video_id_or_url: str,
    question: str,
    reanalyze_if_needed: bool = False,
    use_gemini: bool = False,
    detail: Literal["compact", "standard"] = "compact",
) -> str:
    """Answer a question from saved video context without Gemini by default."""
    return do_ask_video_context(
        video_id_or_url,
        question,
        reanalyze_if_needed,
        use_gemini,
        detail,
    )


@mcp.tool()
def list_video_contexts(
    filter_text: Optional[str] = None,
    limit: int = 20,
) -> str:
    """List saved local video context files."""
    return do_list_video_contexts(filter_text, limit)


@mcp.tool()
def delete_video_context(video_id_or_url: str) -> str:
    """Delete one saved local video context file."""
    return do_delete_video_context(video_id_or_url)


@mcp.tool()
def get_video_frame(
    video_id_or_url: str,
    timestamp: str,
    reason: Optional[str] = None,
    output_format: Literal["jpg", "png"] = "jpg",
    force_refresh: bool = False,
) -> str:
    """Extract a local still frame and return compact asset metadata."""
    return do_get_video_frame(
        video_id_or_url,
        timestamp,
        reason,
        output_format,
        force_refresh,
    )


@mcp.tool()
def get_video_clip(
    video_id_or_url: str,
    start: str,
    end: str,
    reason: Optional[str] = None,
    max_duration_seconds: int = 30,
    output_format: Literal["mp4", "webm"] = "mp4",
    force_refresh: bool = False,
) -> str:
    """Extract a local video clip under 30 seconds and return metadata."""
    return do_get_video_clip(
        video_id_or_url,
        start,
        end,
        reason,
        max_duration_seconds,
        output_format,
        force_refresh,
    )


@mcp.tool()
def get_video_evidence_asset(
    video_id_or_url: str,
    request: str,
    asset_type: Literal["frame", "clip"] = "frame",
    preferred_timestamp: Optional[str] = None,
    preferred_start: Optional[str] = None,
    preferred_end: Optional[str] = None,
    max_duration_seconds: int = 30,
) -> str:
    """Find saved visual evidence and return a local frame/clip reference."""
    return do_get_video_evidence_asset(
        video_id_or_url,
        request,
        asset_type,
        preferred_timestamp,
        preferred_start,
        preferred_end,
        max_duration_seconds,
    )


@mcp.tool()
def list_video_sources(
    filter_text: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List cached video sources, contexts, and assets. Never calls Gemini."""
    return do_list_video_sources(filter_text, limit)


@mcp.tool()
def cleanup_video_cache(
    scope: str = "sources",
    dry_run: bool = True,
    video_id: Optional[str] = None,
) -> str:
    """Inspect or clean managed video cache files. Safe dry-run by default."""
    return do_cleanup_video_cache(scope, dry_run, video_id)


# ---------------------------------------------------------------------------
# Watch-and-Build: Core functions
# ---------------------------------------------------------------------------

_BLOCKED_PATH_PATTERNS = re.compile(
    r"("
    r"\.\.[\\/]"              # path traversal
    r"|^~"                    # home shortcut
    r"|[\\/]\.ssh[\\/]"       # SSH keys
    r"|[\\/]\.gnupg[\\/]"     # GPG keys
    r"|[\\/]\.aws[\\/]"       # AWS credentials
    r"|[\\/]\.kube[\\/]"      # Kubernetes config
    r"|[\\/]\.docker[\\/]"    # Docker config
    r"|[\\/]\.git[\\/]"       # Git internals
    r"|[\\/]\.env"            # environment files
    r"|[\\/]\.bashrc"         # shell configs
    r"|[\\/]\.zshrc"
    r"|[\\/]\.profile"
    r"|[\\/]\.bash_profile"
    r"|/etc/"                 # Linux system
    r"|/root"
    r"|/var/"
    r"|/usr/"
    r"|/bin/"
    r"|/sbin/"
    r"|C:\\Windows"           # Windows system
    r"|C:\\Program Files"
    r"|C:\\ProgramData"
    r"|AppData\\Roaming"      # Windows user data
    r"|AppData\\Local"
    r")",
    re.IGNORECASE,
)


def _validate_file_path(fpath: str) -> bool:
    """Return True if a file path is safe for writing."""
    return not _BLOCKED_PATH_PATTERNS.search(fpath)


_BLOCKED_COMMANDS = re.compile(
    r"("
    r"rm\s+-[^\s]*[rf][^\s]*[rf]" # rm -rf / rm -fr / rm -rfi etc
    r"|mkfs\b"                   # format filesystem
    r"|dd\s+if="                 # raw disk write
    r"|format\s+[a-zA-Z]:"      # Windows format drive
    r"|del\s+/[sqf]"            # Windows force delete
    r"|shutdown\b"               # shutdown/reboot
    r"|reboot\b"
    r"|poweroff\b"
    r"|halt\b"
    r"|chmod\s+777\s+/"          # world-writable system dirs
    r"|chown\s+.*\s+/"           # change ownership of system dirs
    r"|\|\s*(?:sh|bash|zsh|cmd|powershell)"  # pipe to shell
    r"|curl\s+[^|]*\|\s*"       # curl | (download & pipe)
    r"|wget\s+[^|]*\|\s*"       # wget | (download & pipe)
    r"|nc\s+-[el]"              # netcat listen/exec (reverse shell)
    r"|ncat\s+-[el]"
    r"|python[3]?\s+-c\s"       # inline code execution
    r"|node\s+-e\s"
    r"|\beval\s"                # eval/exec
    r"|\bexec\s"
    r"|>\s*/dev/sd"             # overwrite disk device
    r"|Add-MpPreference"        # disable Windows Defender
    r"|Set-MpPreference"
    r")",
    re.IGNORECASE,
)


def _validate_command(cmd: str) -> bool:
    """Return True if a command is safe to execute."""
    return not _BLOCKED_COMMANDS.search(cmd)


def _parse_gemini_json(text: str) -> dict:
    """Parse JSON from Gemini response, stripping markdown fences if present.

    Also validates the schema:
      - is_tutorial=true  → requires title (str), steps (list), prerequisites (list)
      - is_tutorial=false → requires description (str), category (str)
      - file_paths are checked for path traversal patterns
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n")
        last_fence = cleaned.rfind("```")
        cleaned = cleaned[first_newline + 1:last_fence].strip()

    data = json.loads(cleaned)

    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object from Gemini")

    if data.get("post_type") == "slideshow":
        for key in ("title", "platform", "steps", "summary"):
            if key not in data:
                raise ValueError(f"Slideshow JSON missing required key: {key!r}")
        if not isinstance(data["steps"], list):
            raise ValueError("'steps' must be a list")
        for step in data["steps"]:
            if not isinstance(step, dict):
                raise ValueError("Each slideshow step must be an object")
            if "image_index" not in step:
                raise ValueError("Slideshow step missing image_index")
        return data

    if data.get("is_tutorial"):
        for key in ("title", "steps", "prerequisites"):
            if key not in data:
                raise ValueError(f"Tutorial JSON missing required key: {key!r}")
        if not isinstance(data["steps"], list):
            raise ValueError("'steps' must be a list")
        if not isinstance(data["prerequisites"], list):
            raise ValueError("'prerequisites' must be a list")
        # Validate file_paths inside steps
        for step in data["steps"]:
            for fpath in step.get("file_paths", []):
                if not _validate_file_path(fpath):
                    raise ValueError(f"Blocked dangerous file_path: {fpath!r}")
    else:
        for key in ("description", "category"):
            if key not in data:
                raise ValueError(f"Non-tutorial JSON missing required key: {key!r}")

    return data


_EXPECTED_TUTORIAL_STEPS_JSON = """Expected JSON structure:
{
  "analysis": {
    "is_tutorial": true,
    "title": "Tutorial title",
    "summary": "What the tutorial builds",
    "prerequisites": ["tool or account required"],
    "steps": [
      {
        "step_number": 1,
        "timestamp": "MM:SS",
        "description": "What this step does",
        "commands": ["command to review before execution"],
        "code_snippets": ["file contents, if any"],
        "file_paths": ["matching/path/for/snippet.ext"],
        "notes": "Warnings or context"
      }
    ],
    "final_result": "Expected outcome"
  }
}

The outer "analysis" wrapper is optional if you pass the analysis object
directly. Required fields: is_tutorial (boolean); when is_tutorial=true,
steps must be an array. Each step must be an object. commands,
code_snippets, and file_paths must be arrays when present.

Validation does not execute commands. Execution only happens after valid
JSON is reviewed and confirm=true is supplied."""


def _tutorial_steps_validation_error(reason: str) -> str:
    return f"Invalid tutorial steps JSON: {reason}\n\n{_EXPECTED_TUTORIAL_STEPS_JSON}"


def _validate_tutorial_steps_payload(
    data: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(data, dict):
        return None, "top-level value must be a JSON object"

    analysis = data.get("analysis", data)
    if not isinstance(analysis, dict):
        return None, '"analysis" must be a JSON object when provided'

    if analysis.get("post_type") == "slideshow":
        steps = analysis.get("steps")
        if not isinstance(steps, list):
            return None, 'when "post_type" is "slideshow", "steps" must be an array'
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                return None, f"steps[{idx}] must be an object"
            if "image_index" not in step:
                return None, f'steps[{idx}] missing "image_index"'
        return analysis, None

    if "is_tutorial" not in analysis:
        return None, 'missing required field "is_tutorial"'
    if not isinstance(analysis.get("is_tutorial"), bool):
        return None, '"is_tutorial" must be a boolean'

    if not analysis["is_tutorial"]:
        return analysis, None

    steps = analysis.get("steps")
    if not isinstance(steps, list):
        return None, 'when "is_tutorial" is true, "steps" must be an array'

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            return None, f"steps[{idx}] must be an object"
        for field in ("commands", "code_snippets", "file_paths"):
            if field in step and not isinstance(step[field], list):
                return None, f'steps[{idx}].{field} must be an array when present'

    return analysis, None


def _cleanup_old_analyses() -> None:
    """Remove oldest analyses if count exceeds MAX_ANALYSES_STORED."""
    files = sorted(ANALYSES_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    removed = 0
    while len(files) > MAX_ANALYSES_STORED:
        oldest = files.pop(0)
        try:
            oldest.unlink()
            removed += 1
        except OSError:
            pass
    if removed:
        logger.info("Cleaned up %d old analyses (cap: %d)", removed, MAX_ANALYSES_STORED)


def _save_analysis(url: str, analysis: dict) -> str:
    """Save analysis JSON to the analyses directory. Returns the file path."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]
    filename = f"{ts}_{url_hash}.json"
    filepath = ANALYSES_DIR / filename
    payload = {
        "url": url,
        "analyzed_at": datetime.now().isoformat(),
        "analysis": analysis,
    }
    filepath.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Analysis saved to %s", filepath)
    _cleanup_old_analyses()
    return str(filepath)


def do_watch_and_analyze(
    url: str,
    lang: str = "auto",
    model: str = DEFAULT_MODEL,
) -> str:
    """Download/stream a video, analyze it for technical tutorial content."""
    validate_url(url)
    post_type = _detect_post_type_for_routing(url)
    platform = detect_platform(url)
    logger.info("watch_and_analyze: platform=%s, url=%s", platform, url)

    lang_hint = ""
    if lang and lang != "auto":
        lang_hint = f"\n\nIMPORTANT: The video is in {lang}. Extract all content in that language."

    prompt = (
        SLIDESHOW_TUTORIAL_ANALYSIS_PROMPT
        if post_type in {"slideshow", "youtube_community"}
        else TUTORIAL_ANALYSIS_PROMPT
    ) + lang_hint

    raw_response = ""
    try:
        if post_type in {"slideshow", "youtube_community"}:
            raw_response = _analyze_slideshow_post(
                url,
                prompt,
                model,
                None if lang == "auto" else lang,
                post_type=post_type,
            )
        elif platform == "youtube":
            raw_response = _analyze_youtube(url, prompt, model)
        else:
            raw_response = _analyze_downloaded(url, prompt, model)

        analysis = _parse_gemini_json(raw_response)
        saved_path = _save_analysis(url, analysis)

        result = {
            "status": "success",
            "saved_to": saved_path,
            "analysis": analysis,
        }
        return json.dumps(result, indent=2, ensure_ascii=False)

    except json.JSONDecodeError:
        # Gemini returned non-JSON — save raw text and return it
        saved_path = _save_analysis(url, {"raw_response": raw_response})
        return json.dumps({
            "status": "parse_error",
            "message": "Gemini did not return valid JSON. Raw response saved.",
            "saved_to": saved_path,
            "raw_response": raw_response,
        }, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error("watch_and_analyze failed: %s", e, exc_info=True)
        details = _exception_details(e)
        return _error_response(
            "watch_and_analyze",
            details["message"],
            code=details["code"],
            details=details["details"],
            retryable=details["retryable"],
            platform=details["platform"] or platform,
        )


def do_execute_tutorial_steps(
    steps_json: str,
    confirm: bool = False,
) -> str:
    """Review or execute tutorial steps extracted by watch_and_analyze."""
    try:
        data = json.loads(steps_json) if isinstance(steps_json, str) else steps_json
    except json.JSONDecodeError as e:
        return _tutorial_steps_validation_error(f"invalid JSON syntax: {e}")

    # Handle nested structure from watch_and_analyze. This validation phase
    # never executes commands, even when confirm=true.
    analysis, validation_error = _validate_tutorial_steps_payload(data)
    if validation_error:
        return _tutorial_steps_validation_error(validation_error)
    assert analysis is not None

    if analysis.get("post_type") == "slideshow":
        return (
            "This analysis came from a slideshow/photo post. "
            "Slideshows can contain visual steps, but they do not include "
            "executable command steps, so execute_tutorial_steps will not run them."
        )

    if not analysis.get("is_tutorial", False):
        desc = analysis.get("description", "Unknown content")
        return f"This video is not a technical tutorial.\nContent: {desc}"

    steps = analysis.get("steps", [])
    if not steps:
        return "No executable steps found in the analysis."

    title = analysis.get("title", "Untitled Tutorial")
    prereqs = analysis.get("prerequisites", [])

    # --- Review mode (confirm=False): show summary ---
    if not confirm:
        lines = [
            f"=== Tutorial: {title} ===",
            f"Summary: {analysis.get('summary', 'N/A')}",
            f"Prerequisites: {', '.join(prereqs) if prereqs else 'None'}",
            f"Steps: {len(steps)}",
            "",
        ]
        total_commands = 0
        for step in steps:
            sn = step.get("step_number", "?")
            desc = step.get("description", "")
            cmds = step.get("commands", [])
            snippets = step.get("code_snippets", [])
            files = step.get("file_paths", [])
            total_commands += len(cmds)

            lines.append(f"Step {sn}: {desc}")
            if cmds:
                for c in cmds:
                    lines.append(f"  $ {c}")
            if snippets:
                lines.append(f"  Code snippets: {len(snippets)}")
            if files:
                lines.append(f"  Files: {', '.join(files)}")
            if step.get("notes"):
                lines.append(f"  Note: {step['notes']}")
            lines.append("")

        lines.append(f"Total commands to execute: {total_commands}")
        lines.append(f"Final result: {analysis.get('final_result', 'N/A')}")
        lines.append("")
        lines.append("To execute, call execute_tutorial_steps with confirm=true")
        return "\n".join(lines)

    # --- Execute mode (confirm=True) ---
    log_lines = [f"=== Executing: {title} ===", ""]
    success_count = 0
    fail_count = 0

    for step in steps:
        sn = step.get("step_number", "?")
        desc = step.get("description", "")
        cmds = step.get("commands", [])
        snippets = step.get("code_snippets", [])
        files = step.get("file_paths", [])

        log_lines.append(f"--- Step {sn}: {desc} ---")

        # Create files from code snippets if file paths are provided
        if snippets and files and len(snippets) == len(files):
            for fpath, snippet in zip(files, snippets):
                if not _validate_file_path(fpath):
                    log_lines.append(f"  BLOCKED dangerous path: {fpath}")
                    fail_count += 1
                    continue
                try:
                    p = Path(fpath)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(snippet, encoding="utf-8")
                    log_lines.append(f"  Created file: {fpath}")
                    success_count += 1
                except Exception as e:
                    log_lines.append(f"  FAILED to create {fpath}: {e}")
                    fail_count += 1

        # Execute commands
        for cmd in cmds:
            log_lines.append(f"  $ {cmd}")
            if not _validate_command(cmd):
                log_lines.append("  BLOCKED dangerous command")
                fail_count += 1
                continue
            try:
                result = subprocess.run(
                    shlex.split(cmd),
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(Path.home()),
                )
                if result.stdout.strip():
                    for line in result.stdout.strip().split("\n")[:20]:
                        log_lines.append(f"    {line}")
                if result.returncode != 0:
                    log_lines.append(f"  EXIT CODE: {result.returncode}")
                    if result.stderr.strip():
                        for line in result.stderr.strip().split("\n")[:10]:
                            log_lines.append(f"    ERR: {line}")
                    fail_count += 1
                else:
                    log_lines.append("  OK")
                    success_count += 1
            except subprocess.TimeoutExpired:
                log_lines.append("  TIMEOUT (120s)")
                fail_count += 1
            except Exception as e:
                log_lines.append(f"  ERROR: {e}")
                fail_count += 1

        log_lines.append("")

    log_lines.append(f"=== Done: {success_count} succeeded, {fail_count} failed ===")
    return "\n".join(log_lines)


# ---------------------------------------------------------------------------
# Watch-and-Build: MCP Tool wrappers
# ---------------------------------------------------------------------------

@mcp.tool()
def watch_and_analyze(
    url: str,
    lang: str = "auto",
    model: str = DEFAULT_MODEL,
) -> str:
    """Watch a video tutorial and extract all technical steps, commands, and code.

    Downloads the video, analyzes it with Gemini AI, and returns structured
    JSON with every command, code snippet, file path, and tool mentioned.
    YouTube returns the result immediately. TikTok/Instagram return a job_id —
    use check_analysis_job(job_id) to poll for the result.

    This tool ONLY analyzes — it does NOT execute anything.
    Use execute_tutorial_steps to run the extracted steps after review.

    Args:
        url: Video URL (YouTube, TikTok, Instagram).
        lang: Language hint (e.g., 'en', 'ar', 'auto'). Defaults to auto-detect.
        model: Gemini model to use. Defaults to gemini-3.5-flash.

    Returns:
        JSON with tutorial analysis (YouTube) or JSON with job_id (TikTok/Instagram).
    """
    return _dispatch_or_background(
        "watch_and_analyze", url, do_watch_and_analyze, url, lang, model,
    )


@mcp.tool()
def check_analysis_job(job_id: str) -> str:
    """Check the status of a background video analysis job.

    When analyze_video, get_transcript, ask_about_video, or watch_and_analyze
    returns a job_id (for TikTok/Instagram videos), use this tool to poll
    for the result. Keep calling until status is "completed" or "failed".

    Args:
        job_id: The job ID returned by the analysis tool.

    Returns:
        JSON with status ("processing", "completed", or "failed") and the result when done.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)

    if job is None:
        return json.dumps({
            "status": "not_found",
            "gemini_called": False,
            "error": f"No job found with ID: {job_id}",
        }, indent=2)

    response = {
        "status": job["status"],
        "job_id": job_id,
        "tool": job["tool"],
        "url": job["url"],
        "started_at": job["started_at"],
        "gemini_called": job["status"] == "completed",
    }

    if job["status"] == "completed":
        response["completed_at"] = job["completed_at"]
        response["result"] = job["result"]
    elif job["status"] == "failed":
        response["completed_at"] = job["completed_at"]
        response["error"] = job["error"]
    else:
        response["message"] = "Still processing. Please wait and check again."

    return json.dumps(response, indent=2, ensure_ascii=False)


@mcp.tool()
def execute_tutorial_steps(
    steps_json: str,
    confirm: bool = False,
) -> str:
    """Review or execute tutorial steps extracted by watch_and_analyze.

    SAFETY: By default (confirm=false), this only shows a summary of what
    WOULD be executed. Set confirm=true ONLY after reviewing the steps.

    Args:
        steps_json: The JSON output from watch_and_analyze (copy the full analysis field).
        confirm: If false (default), shows a review summary. If true, executes the steps.

    Returns:
        Review summary (confirm=false) or execution log with success/failure per step (confirm=true).
    """
    return do_execute_tutorial_steps(steps_json, confirm)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the ``video-url-analyzer-mcp`` console script."""
    logger.info("Starting Video Analyzer MCP Server...")
    mcp.run()


if __name__ == "__main__":
    main()
