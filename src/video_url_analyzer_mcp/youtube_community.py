"""Minimal YouTube community post extraction helpers."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request as UrllibRequest
from urllib.request import urlopen as urllib_urlopen

logger = logging.getLogger("video-analyzer")

YOUTUBE_COMMUNITY_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
YOUTUBE_IMAGE_HOST_SUFFIXES = (
    "ytimg.com",
    "ggpht.com",
    "googleusercontent.com",
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class CommunityPostError(RuntimeError):
    """Raised when a YouTube community post cannot be extracted safely."""


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 16) -> int:
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
    return min(value, maximum)


def _bounded_worker_count(item_count: int) -> int:
    if item_count <= 1:
        return 1
    return max(1, min(item_count, _env_int("VIDEO_IMAGE_DOWNLOAD_CONCURRENCY", 6)))


def _validate_community_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_COMMUNITY_HOSTS:
        raise CommunityPostError("Unsupported YouTube community post URL.")
    if not (parsed.path.startswith("/post/") or "/community" in parsed.path or "community" in parsed.query):
        raise CommunityPostError("URL does not look like a YouTube community post.")


def _validate_image_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise CommunityPostError(f"Blocked non-HTTPS image URL: {url[:80]}")
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in YOUTUBE_IMAGE_HOST_SUFFIXES):
        raise CommunityPostError(f"Blocked non-YouTube CDN image host: {host}")
    return url


def _fetch_text(url: str, timeout: int = 30) -> str:
    req = UrllibRequest(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib_urlopen(req, timeout=timeout) as resp:
        if getattr(resp, "status", 200) == 429:
            raise CommunityPostError("YouTube returned HTTP 429 while loading the community post.")
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            raise CommunityPostError(f"Unexpected YouTube page content-type: {content_type}")
        return resp.read().decode("utf-8", errors="replace")


def _extract_json_object(text: str, open_brace_index: int) -> str:
    depth = 0
    in_string = False
    escape = False
    for index in range(open_brace_index, len(text)):
        char = text[index]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index:index + 1]
    raise CommunityPostError("Could not parse ytInitialData JSON from the page.")


def _extract_yt_initial_data(html: str) -> dict[str, Any]:
    for pattern in (
        r"var\s+ytInitialData\s*=\s*\{",
        r"window\[['\"]ytInitialData['\"]\]\s*=\s*\{",
        r"ytInitialData\s*=\s*\{",
    ):
        match = re.search(pattern, html)
        if not match:
            continue
        json_text = _extract_json_object(html, match.end() - 1)
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise CommunityPostError(f"Could not decode ytInitialData JSON: {exc}") from exc
        if isinstance(data, dict):
            return data
    raise CommunityPostError("YouTube page did not include ytInitialData.")


def _walk_key(value: Any, wanted_key: str):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == wanted_key:
                yield child
            yield from _walk_key(child, wanted_key)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_key(child, wanted_key)


def _text_from_runs(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("simpleText"), str):
        return value["simpleText"]
    runs = value.get("runs")
    if isinstance(runs, list):
        text = "".join(str(run.get("text") or "") for run in runs if isinstance(run, dict)).strip()
        return text or None
    return None


def _highest_thumbnail(thumbnails: Any) -> str | None:
    if not isinstance(thumbnails, list):
        return None
    candidates = [item for item in thumbnails if isinstance(item, dict) and item.get("url")]
    if not candidates:
        return None
    best = max(candidates, key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0))
    url = str(best["url"]).replace("\\u0026", "&")
    if url.startswith("//"):
        url = f"https:{url}"
    return url


def _extract_image_urls(renderer: dict[str, Any]) -> list[str]:
    search_roots = list(_walk_key(renderer, "backstageAttachment")) or [renderer]
    urls: list[str] = []
    seen: set[str] = set()
    for root in search_roots:
        for image_renderer in _walk_key(root, "backstageImageRenderer"):
            if not isinstance(image_renderer, dict):
                continue
            image = image_renderer.get("image")
            if not isinstance(image, dict):
                continue
            url = _highest_thumbnail(image.get("thumbnails"))
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def extract_community_post(url: str) -> dict[str, Any]:
    """Extract metadata and image URLs from a YouTube community post page."""
    _validate_community_url(url)
    logger.info("YouTube community: fetching post page")
    html = _fetch_text(url)
    if "consent.youtube.com" in html or "Before you continue to YouTube" in html:
        raise CommunityPostError("YouTube returned a consent wall; cannot extract community post.")

    data = _extract_yt_initial_data(html)
    renderers = [item for item in _walk_key(data, "backstagePostRenderer") if isinstance(item, dict)]
    if not renderers:
        raise CommunityPostError("Could not find backstagePostRenderer in YouTube community post.")

    renderer = renderers[0]
    image_urls = _extract_image_urls(renderer)
    if not image_urls:
        raise CommunityPostError("YouTube community post has no extractable image attachments.")

    caption = _text_from_runs(renderer.get("contentText")) or _text_from_runs(renderer.get("postText"))
    uploader = _text_from_runs(renderer.get("authorText"))
    title = caption[:90] if caption else "YouTube community post"
    logger.info("YouTube community: extracted %d image URL(s)", len(image_urls))
    return {
        "image_urls": image_urls,
        "metadata": {
            "title": title,
            "uploader": uploader,
            "caption": caption,
            "image_count": len(image_urls),
            "has_audio": False,
            "source_url": url,
            "platform": "youtube_community",
        },
    }


def _guess_image_suffix(url: str, content_type: str) -> str:
    lowered = content_type.lower().split(";", 1)[0].strip()
    if lowered == "image/png":
        return ".png"
    if lowered == "image/webp":
        return ".webp"
    if lowered in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"


def _download_image(url: str, final_path: Path, timeout: int = 60) -> Path:
    _validate_image_url(url)
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError as exc:
        raise CommunityPostError("curl_cffi is required to download YouTube community images.") from exc

    response = cffi_requests.get(
        url,
        impersonate="chrome",
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
    )
    if response.status_code != 200:
        raise CommunityPostError(f"Image download failed with HTTP {response.status_code}.")
    content_type = response.headers.get("content-type", "")
    if not content_type.lower().startswith("image/"):
        raise CommunityPostError(f"Blocked non-image response: {content_type or 'missing content-type'}")
    if not response.content:
        raise CommunityPostError("Image download returned an empty body.")

    suffix = _guess_image_suffix(url, content_type)
    final_path = final_path.with_suffix(suffix)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(final_path.parent), suffix=".tmp") as tmp:
            tmp.write(response.content)
            tmp_name = tmp.name
        Path(tmp_name).replace(final_path)
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return final_path


def download_community_images(image_urls: list[str], output_dir: Path) -> list[Path]:
    """Download YouTube community image URLs to ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    worker_count = _bounded_worker_count(len(image_urls))

    def download_one(item: tuple[int, str]) -> Path:
        index, image_url = item
        return _download_image(image_url, output_dir / f"community_{index:02d}.jpg")

    items = list(enumerate(image_urls, start=1))
    if worker_count == 1:
        paths = [download_one(item) for item in items]
    else:
        logger.info(
            "YouTube community: downloading %d image(s) with %d worker(s)",
            len(image_urls),
            worker_count,
        )
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="community-download") as executor:
            paths = list(executor.map(download_one, items))
    logger.info("YouTube community: downloaded %d image(s)", len(paths))
    return paths
