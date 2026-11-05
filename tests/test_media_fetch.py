"""Media fetch hardening: validated redirects and HTTP error handling. No network."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from urllib.error import HTTPError

import pytest

from video_url_analyzer_mcp import slideshow, youtube_community


class _Response:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None, content: bytes = b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content


def _install_fake_curl_cffi(monkeypatch, responses: dict[str, _Response], calls: list[str]):
    def fake_get(url, **kwargs):
        calls.append(url)
        assert kwargs.get("allow_redirects") is False
        return responses[url]

    package = types.ModuleType("curl_cffi")
    package.requests = types.SimpleNamespace(get=fake_get)
    monkeypatch.setitem(sys.modules, "curl_cffi", package)


def test_redirect_to_metadata_address_is_refused(monkeypatch, tmp_path: Path):
    calls: list[str] = []
    start = "https://p16-sign.tiktokcdn.com/redirect?to=x"
    responses = {
        start: _Response(302, {"location": "http://169.254.169.254/latest/meta-data/"}),
    }
    _install_fake_curl_cffi(monkeypatch, responses, calls)
    with pytest.raises(slideshow.SlideshowError):
        slideshow._download_image_atomic(start, tmp_path / "slide.jpg")
    assert calls == [start]


def test_allowlisted_redirect_is_followed_and_checked(monkeypatch, tmp_path: Path):
    calls: list[str] = []
    start = "https://p16-sign.tiktokcdn.com/first.jpg"
    final = "https://p16-sign.tiktokcdn.com/final.jpg"
    responses = {
        start: _Response(302, {"location": final}),
        final: _Response(200, {"content-type": "image/jpeg"}, b"\xff\xd8\xff data"),
    }
    _install_fake_curl_cffi(monkeypatch, responses, calls)
    slideshow._download_image_atomic(start, tmp_path / "slide.jpg")
    assert calls == [start, final]


def test_community_rate_limit_becomes_community_post_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(youtube_community, "urllib_urlopen", fake_urlopen)
    with pytest.raises(youtube_community.CommunityPostError, match="HTTP 429"):
        youtube_community._fetch_text("https://www.youtube.com/post/example")
