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
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv
from fastmcp import FastMCP
from google import genai
from google.genai import types

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("video-analyzer")

# Initialize Gemini client
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY environment variable is not set!")
    raise ValueError("GEMINI_API_KEY environment variable is required")

client = genai.Client(api_key=GEMINI_API_KEY)

# Default model
DEFAULT_MODEL = "gemini-2.5-flash"

# MCP Server
mcp = FastMCP("video-analyzer")

# Analyses output directory
ANALYSES_DIR = Path(os.environ.get("ANALYSES_DIR", "./analyses"))
ANALYSES_DIR.mkdir(parents=True, exist_ok=True)

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


def _complete_job(job_id: str, result: str):
    """Mark a job as completed with its result."""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "completed"
            _jobs[job_id]["result"] = result
            _jobs[job_id]["completed_at"] = datetime.now().isoformat()


def _fail_job(job_id: str, error: str):
    """Mark a job as failed with an error message."""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = error
            _jobs[job_id]["completed_at"] = datetime.now().isoformat()


def _run_in_background(job_id: str, func, *args, **kwargs):
    """Run a function in a background thread and update the job on completion."""
    def _worker():
        try:
            result = func(*args, **kwargs)
            _complete_job(job_id, result)
        except Exception as e:
            logger.error("Background job %s failed: %s", job_id, e, exc_info=True)
            _fail_job(job_id, str(e))

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


def _normalize_youtube_url(url: str) -> str:
    """Normalize YouTube URL to standard format."""
    parsed = urlparse(url)
    if "youtu.be" in parsed.hostname:
        video_id = parsed.path.lstrip("/")
    else:
        qs = parse_qs(parsed.query)
        video_id = qs.get("v", [None])[0]
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return url


def _download_tiktok_api(url: str, tmp_dir: str) -> str | None:
    """Download a TikTok video using the tikwm.com API as a fallback.

    Returns the local file path on success, or None on failure.
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
            data={"url": url, "hd": 1},
            impersonate="chrome",
            timeout=20,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("tikwm API error: %s", data.get("msg", "unknown"))
            return None

        video_data = data.get("data", {})
        download_url = video_data.get("hdplay") or video_data.get("play")
        if not download_url:
            logger.warning("tikwm API returned no video URL")
            return None

        logger.info("Downloading TikTok video from tikwm API...")
        vid_resp = cffi_requests.get(download_url, impersonate="chrome", timeout=120)
        fpath = os.path.join(tmp_dir, "video.mp4")
        with open(fpath, "wb") as f:
            f.write(vid_resp.content)

        size_mb = os.path.getsize(fpath) / 1e6
        logger.info("TikTok API download OK: %s (%.1f MB)", fpath, size_mb)
        return fpath
    except Exception as e:
        logger.warning("TikTok API fallback failed: %s", e)
        return None


def _download_instagram_scrape(url: str, tmp_dir: str) -> str | None:
    """Download an Instagram video by scraping the page with curl_cffi.

    Extracts the video URL from video_versions JSON embedded in the page HTML.
    Returns the local file path on success, or None on failure.
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.warning("curl_cffi not installed — skipping Instagram scrape fallback")
        return None

    logger.info("Trying Instagram scrape fallback...")
    try:
        resp = cffi_requests.get(url, impersonate="chrome", timeout=20)
        if resp.status_code != 200:
            logger.warning("Instagram page returned status %d", resp.status_code)
            return None

        # Try video_versions first (Reels), then video_url (posts)
        m = re.search(r'"video_versions":\[\{[^}]*"url":"([^"]+)"', resp.text)
        if not m:
            m = re.search(r'"video_url":"([^"]+)"', resp.text)
        if not m:
            logger.warning("No video URL found in Instagram page HTML")
            return None

        video_url = m.group(1).replace("\\/", "/").replace("\\u0026", "&")
        logger.info("Found Instagram video URL, downloading...")

        vid_resp = cffi_requests.get(video_url, impersonate="chrome", timeout=120)
        if vid_resp.status_code != 200:
            logger.warning("Instagram video download returned status %d", vid_resp.status_code)
            return None

        fpath = os.path.join(tmp_dir, "video.mp4")
        with open(fpath, "wb") as f:
            f.write(vid_resp.content)

        size_mb = os.path.getsize(fpath) / 1e6
        logger.info("Instagram scrape download OK: %s (%.1f MB)", fpath, size_mb)
        return fpath
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


def _download_video(url: str) -> str:
    """Download a video and return the local file path.

    Strategy (fast-first):
      - TikTok:    tikwm.com API first → yt-dlp fallback
      - Instagram: page scrape first   → yt-dlp fallback
      - YouTube/other: yt-dlp only
    yt-dlp timeout is 30s for TikTok/Instagram (fast-fail), 180s for others.
    """
    tmp_dir = tempfile.mkdtemp(prefix="video_analyzer_")
    output_template = os.path.join(tmp_dir, "video.%(ext)s")
    platform = detect_platform(url)

    logger.info("Downloading video from %s (platform: %s)", url, platform)

    # --- Fast path: try API/scrape methods first for TikTok/Instagram ---
    if platform == "tiktok":
        api_path = _download_tiktok_api(url, tmp_dir)
        if api_path:
            return _check_download_size(api_path)
        logger.info("TikTok API failed, falling back to yt-dlp...")

    if platform == "instagram":
        scrape_path = _download_instagram_scrape(url, tmp_dir)
        if scrape_path:
            return _check_download_size(scrape_path)
        logger.info("Instagram scrape failed, falling back to yt-dlp...")

    # --- yt-dlp path ---
    ytdlp_cmd = [sys.executable, "-m", "yt_dlp"]

    base_args = [
        "--no-playlist",
        "--force-overwrites",
        "-o", output_template,
    ]

    if platform == "instagram":
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
    ytdlp_timeout = 30 if platform in ("tiktok", "instagram") else 180

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
        cmd = ytdlp_cmd + ["-f", fmt_selector] + extra_args + base_args + [url]

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
            return _check_download_size(fpath)

    raise RuntimeError("Download completed but no file was found.")


def _upload_to_gemini(file_path: str):
    """Upload a file to Gemini Files API and wait until it's processed."""
    logger.info("Uploading %s to Gemini Files API...", file_path)
    uploaded = client.files.upload(file=file_path)
    logger.info("Uploaded file: %s, state: %s", uploaded.name, uploaded.state)

    # Poll until processing is complete (state becomes ACTIVE)
    retries = 0
    max_retries = 60
    while retries < max_retries:
        if uploaded.state and uploaded.state.name == "ACTIVE":
            break
        if uploaded.state and uploaded.state.name == "FAILED":
            raise RuntimeError(f"File processing failed: {uploaded.state}")
        time.sleep(2)
        uploaded = client.files.get(name=uploaded.name)
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


def _cleanup(file_path: str = None, uploaded_file=None):
    """Clean up temporary files and uploaded Gemini files."""
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            parent = os.path.dirname(file_path)
            if parent and os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
            logger.info("Cleaned up local file: %s", file_path)
        except OSError as e:
            logger.warning("Failed to clean up %s: %s", file_path, e)
    if uploaded_file:
        try:
            client.files.delete(name=uploaded_file.name)
            logger.info("Deleted uploaded file: %s", uploaded_file.name)
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


def _analyze_youtube(url: str, prompt: str, model: str) -> str:
    """Analyze a YouTube video directly via Gemini (no download)."""
    normalized_url = _normalize_youtube_url(url)
    logger.info("Analyzing YouTube video directly: %s (model: %s)", normalized_url, model)

    response = client.models.generate_content(
        model=model,
        contents=types.Content(
            parts=[
                types.Part(
                    file_data=types.FileData(file_uri=normalized_url)
                ),
                types.Part(text=prompt),
            ]
        ),
    )
    return _truncate_response(response.text)


def _analyze_downloaded(url: str, prompt: str, model: str) -> str:
    """Download a video, upload to Gemini, and analyze it."""
    file_path = None
    uploaded_file = None
    try:
        file_path = _download_video(url)
        uploaded_file = _upload_to_gemini(file_path)

        logger.info("Analyzing uploaded video (model: %s)...", model)
        response = client.models.generate_content(
            model=model,
            contents=[uploaded_file, prompt],
        )
        return _truncate_response(response.text)
    finally:
        _cleanup(file_path, uploaded_file)


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
    """Analyze a video from YouTube, TikTok, or Instagram."""
    validate_url(url)
    platform = detect_platform(url)
    logger.info("analyze_video: platform=%s, url=%s", platform, url)

    try:
        if platform == "youtube":
            return _analyze_youtube(url, prompt, model)
        return _analyze_downloaded(url, prompt, model)
    except Exception as e:
        logger.error("Error analyzing video: %s", e, exc_info=True)
        error_msg = str(e)
        if "yt-dlp" in error_msg.lower() or "download" in error_msg.lower():
            return (
                f"Error downloading video / خطأ في تحميل الفيديو:\n{error_msg}\n\n"
                "Make sure the URL is public and accessible.\n"
                "تأكد أن الرابط عام ويمكن الوصول إليه."
            )
        return f"Error analyzing video / خطأ في تحليل الفيديو:\n{error_msg}"


def do_get_transcript(
    url: str,
    lang: str = "auto",
    model: str = DEFAULT_MODEL,
) -> str:
    """Extract speech transcript from a video with timestamps."""
    validate_url(url)
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
        if platform == "youtube":
            return _analyze_youtube(url, transcript_prompt, model)
        return _analyze_downloaded(url, transcript_prompt, model)
    except Exception as e:
        logger.error("Error extracting transcript: %s", e, exc_info=True)
        return f"Error extracting transcript / خطأ في استخراج النص:\n{e}"


def do_ask_about_video(
    url: str,
    question: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """Ask a specific question about a video."""
    validate_url(url)
    question_prompt = (
        f"Watch this video carefully, then answer the following question:\n\n"
        f"Question: {question}\n\n"
        f"Provide a detailed, accurate answer based on the video content."
    )

    platform = detect_platform(url)
    logger.info("ask_about_video: platform=%s, url=%s", platform, url)

    try:
        if platform == "youtube":
            return _analyze_youtube(url, question_prompt, model)
        return _analyze_downloaded(url, question_prompt, model)
    except Exception as e:
        logger.error("Error answering question: %s", e, exc_info=True)
        return f"Error answering question / خطأ في الإجابة على السؤال:\n{e}"


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
    """Analyze a video from YouTube, TikTok, or Instagram.

    Provides comprehensive audio + visual analysis using Gemini AI.
    YouTube videos are analyzed directly and return the result immediately.
    TikTok and Instagram videos are processed in the background — the tool
    returns a job_id. Use check_analysis_job(job_id) to poll for the result.

    Args:
        url: The video URL (YouTube, TikTok, Instagram, or other).
        prompt: Custom analysis prompt. Defaults to comprehensive analysis.
        model: Gemini model to use. Defaults to gemini-2.5-flash.

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
    """Extract speech transcript from a video with timestamps.

    YouTube returns the result immediately. TikTok/Instagram return a job_id —
    use check_analysis_job(job_id) to poll for the result.

    Args:
        url: The video URL (YouTube, TikTok, Instagram, or other).
        lang: Language hint (e.g., 'en', 'ar', 'auto'). Defaults to auto-detect.
        model: Gemini model to use. Defaults to gemini-2.5-flash.

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
    """Ask a specific question about a video.

    YouTube returns the answer immediately. TikTok/Instagram return a job_id —
    use check_analysis_job(job_id) to poll for the result.

    Args:
        url: The video URL (YouTube, TikTok, Instagram, or other).
        question: Your question about the video.
        model: Gemini model to use. Defaults to gemini-2.5-flash.

    Returns:
        Answer text (YouTube) or JSON with job_id (TikTok/Instagram).
    """
    return _dispatch_or_background(
        "ask_about_video", url, do_ask_about_video, url, question, model,
    )


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
    platform = detect_platform(url)
    logger.info("watch_and_analyze: platform=%s, url=%s", platform, url)

    lang_hint = ""
    if lang and lang != "auto":
        lang_hint = f"\n\nIMPORTANT: The video is in {lang}. Extract all content in that language."

    prompt = TUTORIAL_ANALYSIS_PROMPT + lang_hint

    try:
        if platform == "youtube":
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
        return json.dumps({
            "status": "error",
            "message": str(e),
        }, indent=2, ensure_ascii=False)


def do_execute_tutorial_steps(
    steps_json: str,
    confirm: bool = False,
) -> str:
    """Review or execute tutorial steps extracted by watch_and_analyze."""
    try:
        data = json.loads(steps_json) if isinstance(steps_json, str) else steps_json
    except json.JSONDecodeError as e:
        return f"Invalid JSON input: {e}"

    # Handle nested structure from watch_and_analyze
    analysis = data.get("analysis", data)

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
                log_lines.append(f"  BLOCKED dangerous command")
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
                    log_lines.append(f"  OK")
                    success_count += 1
            except subprocess.TimeoutExpired:
                log_lines.append(f"  TIMEOUT (120s)")
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
        model: Gemini model to use. Defaults to gemini-2.5-flash.

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
            "error": f"No job found with ID: {job_id}",
        }, indent=2)

    response = {
        "status": job["status"],
        "job_id": job_id,
        "tool": job["tool"],
        "url": job["url"],
        "started_at": job["started_at"],
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

if __name__ == "__main__":
    logger.info("Starting Video Analyzer MCP Server...")
    mcp.run()
