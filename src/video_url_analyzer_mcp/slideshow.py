"""Slideshow/photo-post detection, download, and Gemini analysis helpers."""

from __future__ import annotations

import ipaddress
import html as html_lib
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import yt_dlp
from google.genai import errors, types

from .youtube_community import (
    CommunityPostError,
    download_community_images,
    extract_community_post,
)

logger = logging.getLogger("video-analyzer")

PostType = Literal["video", "slideshow", "youtube_community", "unknown"]
Platform = Literal["tiktok", "instagram", "youtube_community"]

YOUTUBE_COMMUNITY_RE = re.compile(
    r"https?://(?:www\.|m\.)?youtube\.com/(?:post/|.*/community\?)",
    re.IGNORECASE,
)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
AUDIO_EXT_BY_CONTENT_TYPE = {
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/m4a": ".m4a",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
    "video/mp4": ".m4a",
}
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav"}
MEDIA_CDN_HOST_SUFFIXES = (
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "tiktokv.com",
    "byteoversea.com",
    "ibytedtos.com",
    "muscdn.com",
    "akamaized.net",
    "cdninstagram.com",
    "fbcdn.net",
    "fna.fbcdn.net",
    "ytimg.com",
    "ggpht.com",
    "googleusercontent.com",
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class SlideshowError(RuntimeError):
    """Raised when slideshow detection or processing fails."""


def _looks_like_youtube_community(url: str) -> bool:
    return bool(YOUTUBE_COMMUNITY_RE.search(url))


def _platform_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "tiktok.com" in host:
        return "tiktok"
    if "instagram.com" in host:
        return "instagram"
    if "youtube.com" in host:
        return "youtube"
    if "youtu.be" in host:
        return "youtube"
    return "unknown"


def _is_malformed_public_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme not in {"http", "https"} or not parsed.hostname


def _extract_info(url: str) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "download": False,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(cast(Any, opts)) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise SlideshowError("yt-dlp returned no metadata for the URL.")
    return cast(dict[str, Any], info)


def _is_image_ext(ext: Any) -> bool:
    return str(ext or "").lower().lstrip(".") in {"jpg", "jpeg", "png", "webp"}


def _has_video_format(info: dict[str, Any]) -> bool:
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict):
            continue
        vcodec = fmt.get("vcodec")
        if vcodec not in (None, "none"):
            return True
    for entry in info.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("ext") or "").lower() in {"mp4", "mov", "mkv", "webm"}:
            return True
        for fmt in entry.get("formats") or []:
            if isinstance(fmt, dict) and fmt.get("vcodec") not in (None, "none"):
                return True
    ext = str(info.get("ext") or "").lower()
    if ext in {"mp4", "mov", "mkv", "webm"}:
        return True
    media_type = str(info.get("media_type") or "").lower()
    return media_type in {"video", "2"}


def _has_tiktok_slideshow_signal(info: dict[str, Any]) -> bool:
    images = _extract_image_urls(info.get("images"))
    if images:
        return True
    formats = [fmt for fmt in info.get("formats") or [] if isinstance(fmt, dict)]
    thumbnails = info.get("thumbnails") or []
    if formats and all(fmt.get("vcodec") in ("none", None) for fmt in formats) and len(thumbnails) > 1:
        return True
    return False


def _has_instagram_slideshow_signal(info: dict[str, Any]) -> bool:
    media_type = str(info.get("media_type") or "").lower()
    if media_type in {"photo", "image", "1"}:
        return True
    entries = [entry for entry in info.get("entries") or [] if isinstance(entry, dict)]
    if info.get("_type") == "playlist" and any(_is_image_ext(entry.get("ext")) for entry in entries):
        return True
    if info.get("_type") == "playlist" and not entries and not _has_video_format(info):
        return True
    return False


def _is_instagram_photo_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "instagram" in message and "no video in this post" in message


def detect_post_type(url: str) -> PostType:
    """Classify a URL as a video, slideshow/photo post, YouTube community post, or unknown."""
    if _looks_like_youtube_community(url):
        return "youtube_community"
    if _is_malformed_public_url(url):
        return "unknown"
    parsed = urlparse(url)
    if _platform_from_url(url) == "tiktok" and "/photo/" in parsed.path:
        return "slideshow"

    platform = _platform_from_url(url)
    logger.info("Slideshow detection: inspecting URL with yt-dlp")
    try:
        info = _extract_info(url)
    except Exception as exc:
        if platform == "instagram" and _is_instagram_photo_error(exc):
            return "slideshow"
        raise

    if platform == "tiktok":
        if _has_tiktok_slideshow_signal(info):
            return "slideshow"
        if _has_video_format(info):
            return "video"
        return "unknown"

    if platform == "instagram":
        if _has_instagram_slideshow_signal(info):
            return "slideshow"
        if _has_video_format(info):
            return "video"
        return "unknown"

    if platform == "youtube" and _has_video_format(info):
        return "video"

    return "unknown"


def _normalize_media_url(url: str) -> str:
    normalized = url.replace("\\/", "/").replace("\\u0026", "&")
    if normalized.startswith("//"):
        normalized = f"https:{normalized}"
    return normalized


def _iter_url_values(value: Any):
    if isinstance(value, str):
        if value.startswith("http") or value.startswith("//"):
            yield _normalize_media_url(value)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_url_values(item)
        return
    if isinstance(value, dict):
        priority_keys = (
            "url",
            "display_url",
            "thumbnail",
            "src",
            "uri",
            "url_list",
            "urlList",
            "urls",
            "image_url",
            "image_urls",
            "candidates",
            "images",
        )
        for key in priority_keys:
            if key in value:
                yield from _iter_url_values(value[key])


def _extract_image_urls(value: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for url in _iter_url_values(value):
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _thumbnail_urls(info: dict[str, Any]) -> list[str]:
    thumbnails = info.get("thumbnails")
    if not isinstance(thumbnails, list):
        return []
    ordered = sorted(
        [item for item in thumbnails if isinstance(item, dict) and item.get("url")],
        key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0),
        reverse=True,
    )
    return _extract_image_urls(ordered)


def _validate_download_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise SlideshowError(f"Blocked non-HTTPS media URL: {url[:80]}")
    if not host:
        raise SlideshowError("Blocked media URL without a hostname.")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr and (addr.is_private or addr.is_loopback or addr.is_reserved):
        raise SlideshowError(f"Blocked internal media IP: {host}")
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in MEDIA_CDN_HOST_SUFFIXES):
        raise SlideshowError(f"Blocked media host outside allowlist: {host}")
    return url


def _image_suffix(url: str, content_type: str | None = None) -> str:
    if content_type:
        suffix = IMAGE_EXT_BY_CONTENT_TYPE.get(content_type.lower().split(";", 1)[0].strip())
        if suffix:
            return suffix
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in IMAGE_EXTENSIONS else ".jpg"


def _audio_suffix(url: str, content_type: str | None = None) -> str:
    if content_type:
        suffix = AUDIO_EXT_BY_CONTENT_TYPE.get(content_type.lower().split(";", 1)[0].strip())
        if suffix:
            return suffix
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in AUDIO_EXTENSIONS else ".mp3"


def _download_image_atomic(url: str, final_path: Path, timeout: int = 60) -> Path:
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError as exc:
        raise SlideshowError("curl_cffi is required to download slideshow images.") from exc

    media_url = _validate_download_url(_normalize_media_url(url))
    response = cffi_requests.get(
        media_url,
        impersonate="chrome",
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
    )
    if response.status_code != 200:
        raise SlideshowError(f"Image download failed with HTTP {response.status_code}.")
    if not response.content:
        raise SlideshowError("Image download returned an empty body.")
    content_type = response.headers.get("content-type", "")
    if content_type and not content_type.lower().startswith("image/"):
        raise SlideshowError(f"Blocked non-image response: {content_type}")

    final_path = final_path.with_suffix(_image_suffix(media_url, content_type))
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


def _download_audio_atomic(url: str, final_path: Path, timeout: int = 120) -> Path:
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError as exc:
        raise SlideshowError("curl_cffi is required to download slideshow audio.") from exc

    media_url = _validate_download_url(_normalize_media_url(url))
    response = cffi_requests.get(
        media_url,
        impersonate="chrome",
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "audio/*,video/mp4,*/*;q=0.8"},
    )
    if response.status_code != 200:
        raise SlideshowError(f"Audio download failed with HTTP {response.status_code}.")
    if not response.content:
        raise SlideshowError("Audio download returned an empty body.")
    content_type = response.headers.get("content-type", "")
    if content_type.lower().startswith(("image/", "text/html", "application/json")):
        raise SlideshowError(f"Blocked non-audio response: {content_type}")

    final_path = final_path.with_suffix(_audio_suffix(media_url, content_type))
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


def _download_images(urls: list[str], output_dir: Path, prefix: str) -> list[Path]:
    paths: list[Path] = []
    for index, image_url in enumerate(urls, start=1):
        path = _download_image_atomic(image_url, output_dir / f"{prefix}_{index:02d}.jpg")
        paths.append(path)
    return paths


def _pick_audio_format(info: dict[str, Any]) -> dict[str, Any] | None:
    formats = [fmt for fmt in info.get("formats") or [] if isinstance(fmt, dict)]
    for fmt in formats:
        if fmt.get("format_id") == "play_addr":
            return fmt
    for fmt in formats:
        if fmt.get("format_id") in {"audio", "music", "play_addr"}:
            return fmt
    for fmt in formats:
        if fmt.get("acodec") not in (None, "none") and fmt.get("vcodec") in (None, "none"):
            return fmt
    return None


def _download_audio_with_ytdlp(url: str, output_dir: Path, format_id: str | None) -> Path | None:
    logger.info("Slideshow download: extracting audio track with yt-dlp")
    outtmpl = str(output_dir / "audio.%(ext)s")
    opts: dict[str, Any] = {
        "format": format_id or "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "force_overwrites": True,
    }
    if shutil.which("ffmpeg"):
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    try:
        with yt_dlp.YoutubeDL(cast(Any, opts)) as ydl:
            ydl.download([url])
    except Exception as exc:
        logger.warning("Slideshow audio download failed: %s", exc)
        return None

    candidates = sorted(
        [
            path for path in output_dir.iterdir()
            if path.is_file() and path.name.startswith("audio.") and path.suffix.lower() != ".tmp"
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if candidate.stat().st_size > 1024:
            return candidate
    return None


def _metadata(
    info: dict[str, Any],
    url: str,
    platform: Platform,
    image_count: int,
    has_audio: bool,
) -> dict[str, Any]:
    caption = info.get("description") or info.get("caption") or info.get("title")
    return {
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "caption": caption,
        "image_count": image_count,
        "has_audio": has_audio,
        "source_url": url,
        "platform": platform,
    }


def _fetch_tikwm_data(url: str) -> dict[str, Any]:
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError as exc:
        raise SlideshowError("curl_cffi is required for the TikTok slideshow fallback.") from exc

    response = cffi_requests.post(
        "https://www.tikwm.com/api/",
        data={"url": url, "hd": "1"},
        impersonate="chrome",
        timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    if response.status_code != 200:
        raise SlideshowError(f"TikTok API fallback failed with HTTP {response.status_code}.")
    payload = response.json()
    if payload.get("code") != 0:
        raise SlideshowError(f"TikTok API fallback failed: {payload.get('msg') or 'unknown error'}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SlideshowError("TikTok API fallback returned no data object.")
    return data


def _first_tikwm_audio_url(data: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        data.get("music"),
        data.get("music_play"),
        data.get("play_url"),
        data.get("music_info", {}).get("play") if isinstance(data.get("music_info"), dict) else None,
        data.get("music_info", {}).get("play_url") if isinstance(data.get("music_info"), dict) else None,
        data.get("music_info", {}).get("playUrl") if isinstance(data.get("music_info"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith(("https://", "//")):
            return _normalize_media_url(candidate)
    return None


def _download_tiktok_api_slideshow(url: str, output_dir: Path) -> dict[str, Any]:
    logger.info("Slideshow download: trying TikTok API fallback")
    data = _fetch_tikwm_data(url)
    image_urls = _extract_image_urls(data.get("images"))
    if not image_urls:
        raise SlideshowError("TikTok API fallback did not return slideshow images.")

    images = _download_images(image_urls, output_dir, "tiktok")
    audio: Path | None = None
    audio_url = _first_tikwm_audio_url(data)
    if audio_url:
        try:
            audio = _download_audio_atomic(audio_url, output_dir / "audio.mp3")
        except Exception as exc:
            logger.warning("TikTok API audio download failed: %s", exc)

    info = {
        "title": data.get("title"),
        "uploader": data.get("author", {}).get("unique_id") if isinstance(data.get("author"), dict) else None,
        "description": data.get("title"),
    }
    return {
        "images": images,
        "audio": audio,
        "metadata": _metadata(info, url, "tiktok", len(images), audio is not None),
    }


def _download_tiktok_slideshow(url: str, output_dir: Path, info: dict[str, Any]) -> dict[str, Any]:
    image_urls = _extract_image_urls(info.get("images"))
    if not image_urls:
        image_urls = _thumbnail_urls(info)
    if not image_urls:
        return _download_tiktok_api_slideshow(url, output_dir)

    logger.info("Slideshow download: TikTok image_count=%d", len(image_urls))
    images = _download_images(image_urls, output_dir, "tiktok")
    audio_format = _pick_audio_format(info)
    audio = _download_audio_with_ytdlp(url, output_dir, str(audio_format.get("format_id")) if audio_format else None)
    return {
        "images": images,
        "audio": audio,
        "metadata": _metadata(info, url, "tiktok", len(images), audio is not None),
    }


def _entry_image_url(entry: dict[str, Any]) -> str | None:
    direct = _extract_image_urls(entry.get("url"))
    if direct:
        return direct[0]
    direct = _extract_image_urls(entry.get("thumbnail"))
    if direct:
        return direct[0]
    thumbnails = entry.get("thumbnails")
    if isinstance(thumbnails, list):
        urls = _extract_image_urls(
            sorted(
                [item for item in thumbnails if isinstance(item, dict)],
                key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0),
                reverse=True,
            )
        )
        if urls:
            return urls[0]
    return None


def _extract_instagram_carousel_block(html: str) -> str | None:
    for key in ('"carousel_media":[', '"edge_sidecar_to_children":{"edges":['):
        idx = html.find(key)
        if idx < 0:
            continue
        start = idx + len(key)
        depth = 1
        in_string = False
        escape = False
        for index in range(start, len(html)):
            char = html[index]
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = not in_string
            elif not in_string:
                if char in "[{":
                    depth += 1
                elif char in "]}":
                    depth -= 1
                    if depth == 0:
                        return html[start:index]
    return None


def _instagram_image_urls_from_html(html: str) -> list[str]:
    carousel_block = _extract_instagram_carousel_block(html)
    if not carousel_block:
        og_match = re.search(
            r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
            html,
            flags=re.IGNORECASE,
        )
        if not og_match:
            og_match = re.search(
                r'<meta\s+content=["\']([^"\']+)["\']\s+(?:property|name)=["\']og:image["\']',
                html,
                flags=re.IGNORECASE,
            )
        if og_match:
            return [_normalize_media_url(html_lib.unescape(og_match.group(1)))]

    search_text = carousel_block or html
    urls = re.findall(
        r'"image_versions2":\{"candidates":\[\{[^}]*?"url":"([^"]+)"',
        search_text,
    )
    if not urls:
        urls = re.findall(r'"display_url":"([^"]+)"', search_text)
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = _normalize_media_url(url)
        if normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
    return deduped[:20]


def _download_instagram_page_slideshow(url: str, output_dir: Path) -> dict[str, Any]:
    logger.info("Slideshow download: trying Instagram page scrape fallback")
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError as exc:
        raise SlideshowError("curl_cffi is required for the Instagram slideshow fallback.") from exc

    response = cffi_requests.get(
        url,
        impersonate="chrome",
        timeout=30,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    if response.status_code != 200:
        raise SlideshowError(f"Instagram page scrape failed with HTTP {response.status_code}.")
    image_urls = _instagram_image_urls_from_html(response.text)
    if not image_urls:
        raise SlideshowError("Instagram page scrape did not find image URLs.")

    images = _download_images(image_urls, output_dir, "instagram")
    info = {"title": "Instagram photo post", "description": None, "uploader": None}
    return {
        "images": images,
        "audio": None,
        "metadata": _metadata(info, url, "instagram", len(images), False),
    }


def _download_instagram_slideshow(url: str, output_dir: Path, info: dict[str, Any]) -> dict[str, Any]:
    entries = [entry for entry in info.get("entries") or [] if isinstance(entry, dict)]
    image_urls: list[str] = []
    skipped_video_count = 0
    if entries:
        for entry in entries:
            if _is_image_ext(entry.get("ext")) or str(entry.get("media_type") or "").lower() in {"photo", "image", "1"}:
                image_url = _entry_image_url(entry)
                if image_url:
                    image_urls.append(image_url)
            else:
                skipped_video_count += 1
    else:
        image_urls = _extract_image_urls(info.get("url")) or _thumbnail_urls(info)

    if not image_urls:
        return _download_instagram_page_slideshow(url, output_dir)

    deduped: list[str] = []
    seen: set[str] = set()
    for image_url in image_urls:
        if image_url not in seen:
            seen.add(image_url)
            deduped.append(image_url)

    logger.info(
        "Slideshow download: Instagram image_count=%d skipped_video_count=%d",
        len(deduped),
        skipped_video_count,
    )
    images = _download_images(deduped, output_dir, "instagram")
    metadata = _metadata(info, url, "instagram", len(images), False)
    metadata["skipped_video_count"] = skipped_video_count
    return {"images": images, "audio": None, "metadata": metadata}


def download_slideshow(url: str, output_dir: Path, post_type: str) -> dict[str, Any]:
    """Download all slideshow images and optional audio to ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Slideshow download: post_type=%s", post_type)

    if post_type == "youtube_community":
        try:
            post = extract_community_post(url)
            images = download_community_images(list(post["image_urls"]), output_dir)
        except CommunityPostError as exc:
            raise SlideshowError(str(exc)) from exc
        metadata = dict(post["metadata"])
        metadata["image_count"] = len(images)
        metadata["has_audio"] = False
        return {"images": images, "audio": None, "metadata": metadata}

    platform = _platform_from_url(url)
    try:
        info = _extract_info(url)
    except Exception as exc:
        if platform == "tiktok":
            return _download_tiktok_api_slideshow(url, output_dir)
        if platform == "instagram" and _is_instagram_photo_error(exc):
            return _download_instagram_page_slideshow(url, output_dir)
        raise

    if platform == "tiktok":
        return _download_tiktok_slideshow(url, output_dir, info)
    if platform == "instagram":
        return _download_instagram_slideshow(url, output_dir, info)
    raise SlideshowError(f"Unsupported slideshow platform for URL: {platform}")


def _default_upload_file(client: Any, file_path: str) -> Any:
    uploaded = client.files.upload(file=file_path)
    file_name = getattr(uploaded, "name", None)
    if not file_name:
        raise SlideshowError("Gemini upload returned a file without a name.")
    retries = 0
    while retries < 60:
        state = getattr(getattr(uploaded, "state", None), "name", None)
        if state == "ACTIVE":
            return uploaded
        if state == "FAILED":
            raise SlideshowError(f"Gemini File processing failed: {uploaded.state}")
        time.sleep(2)
        uploaded = client.files.get(name=file_name)
        retries += 1
    raise SlideshowError(f"File processing timed out for {file_name}.")


def _is_retryable_gemini_error(exc: Exception) -> bool:
    if isinstance(exc, errors.ClientError):
        return False
    code = getattr(exc, "code", None)
    try:
        return code is not None and 500 <= int(code) < 600
    except (TypeError, ValueError):
        return False


def _default_generate_content(client: Any, **kwargs):
    for attempt in range(1, 4):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as exc:
            if attempt == 3 or not _is_retryable_gemini_error(exc):
                raise
            time.sleep(2 ** (attempt - 1))
    raise SlideshowError("Gemini generate_content retry loop exited unexpectedly.")


def _model_id(model: str) -> str:
    return model.strip().removeprefix("models/").lower()


def _thinking_config_for_model(model: str) -> types.ThinkingConfig | None:
    normalized = _model_id(model)
    if normalized.startswith("gemini-3"):
        return types.ThinkingConfig(thinking_level="high")
    if normalized.startswith("gemini-2.5"):
        return types.ThinkingConfig(thinking_budget=-1)
    return None


def _default_config(model: str) -> types.GenerateContentConfig:
    kwargs: dict[str, Any] = {
        "media_resolution": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
    }
    thinking_config = _thinking_config_for_model(model)
    if thinking_config is not None:
        kwargs["thinking_config"] = thinking_config
    return types.GenerateContentConfig(**kwargs)


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if not text:
        raise SlideshowError("Gemini returned an empty slideshow analysis response.")
    return str(text)


def _cleanup_uploaded(client: Any, uploaded_files: list[Any]) -> None:
    for uploaded in uploaded_files:
        name = getattr(uploaded, "name", None)
        if not name:
            continue
        try:
            client.files.delete(name=name)
        except Exception as exc:
            logger.warning("Failed to delete uploaded slideshow file %s: %s", name, exc)


def _cleanup_local(slideshow: dict[str, Any]) -> None:
    paths = [Path(path) for path in slideshow.get("images") or []]
    audio = slideshow.get("audio")
    if audio:
        paths.append(Path(audio))
    parents = {path.parent for path in paths}
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning("Failed to clean up slideshow temp file %s: %s", path, exc)
    for parent in parents:
        try:
            if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass


def analyze_slideshow(
    client: Any,
    model: str,
    slideshow: dict[str, Any],
    prompt: str,
    lang: str | None,
    *,
    upload_file: Callable[[str], Any] | None = None,
    generate_content: Callable[..., Any] | None = None,
    config: types.GenerateContentConfig | None = None,
) -> str:
    """Upload a slideshow as one multimodal Gemini request and return text."""
    metadata = slideshow.get("metadata") or {}
    images = [Path(path) for path in slideshow.get("images") or []]
    audio = Path(slideshow["audio"]) if slideshow.get("audio") else None
    platform = str(metadata.get("platform") or "social-media")
    caption = metadata.get("caption")
    uploaded_files: list[Any] = []

    try:
        logger.info("Slideshow upload: uploading %d image(s)", len(images))
        upload = upload_file or (lambda file_path: _default_upload_file(client, file_path))
        image_files: list[Any] = []
        for image_path in images:
            image_files.append(upload(str(image_path)))
        uploaded_files.extend(image_files)
        audio_file = None
        if audio:
            logger.info("Slideshow upload: uploading audio track")
            audio_file = upload(str(audio))
            uploaded_files.append(audio_file)

        intro = f"You are analyzing a {platform} photo post containing {len(images)} images"
        if audio:
            intro += ", with a background audio/music track"
        if caption:
            intro += f". Caption: {caption!r}"
        if lang:
            intro += f". Respond in {lang}."
        else:
            intro += "."

        contents: list[Any] = [intro]
        for index, image_file in enumerate(image_files, start=1):
            contents.append(f"Image {index} of {len(images)}. Analyze this image before moving to the next one.")
            contents.append(image_file)
        if audio_file:
            contents.append("Background audio/music track for the slideshow.")
            contents.append(audio_file)
        contents.append(prompt)

        logger.info("Slideshow analysis: model=%s files=%d", model, len(uploaded_files))
        generator = generate_content or (lambda **kwargs: _default_generate_content(client, **kwargs))
        response = generator(model=model, contents=contents, config=config or _default_config(model))
        return _response_text(response)
    finally:
        logger.info("Slideshow cleanup: uploaded=%d local_files=%d", len(uploaded_files), len(images) + int(audio is not None))
        _cleanup_uploaded(client, uploaded_files)
        _cleanup_local(slideshow)
