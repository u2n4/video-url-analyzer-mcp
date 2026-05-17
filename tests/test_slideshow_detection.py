from __future__ import annotations

import pytest

from video_url_analyzer_mcp import slideshow


class FakeYoutubeDL:
    calls: list[str] = []
    responses: dict[str, dict] = {}
    errors: dict[str, Exception] = {}

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        self.calls.append(url)
        if url in self.errors:
            raise self.errors[url]
        return self.responses[url]


@pytest.fixture(autouse=True)
def fake_ytdlp(monkeypatch):
    FakeYoutubeDL.calls = []
    FakeYoutubeDL.responses = {}
    FakeYoutubeDL.errors = {}
    monkeypatch.setattr(slideshow.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    return FakeYoutubeDL


def test_detect_tiktok_video(fake_ytdlp):
    url = "https://www.tiktok.com/@user/video/123"
    fake_ytdlp.responses[url] = {"formats": [{"vcodec": "h264", "acodec": "aac"}]}

    assert slideshow.detect_post_type(url) == "video"


def test_detect_tiktok_photo_mode_from_images(fake_ytdlp):
    url = "https://www.tiktok.com/@user/photo/123"

    assert slideshow.detect_post_type(url) == "slideshow"
    assert fake_ytdlp.calls == []


def test_detect_tiktok_photo_mode_from_images_metadata(fake_ytdlp):
    url = "https://www.tiktok.com/@user/video/123"
    fake_ytdlp.responses[url] = {
        "images": ["https://p16-sign.tiktokcdn.com/image1.jpeg"],
        "formats": [{"vcodec": "none", "acodec": "aac"}],
    }

    assert slideshow.detect_post_type(url) == "slideshow"


def test_detect_tiktok_photo_mode_from_audio_only_formats(fake_ytdlp):
    url = "https://www.tiktok.com/@user/video/456"
    fake_ytdlp.responses[url] = {
        "formats": [{"vcodec": "none", "acodec": "aac"}],
        "thumbnails": [{"url": "https://p16-sign.tiktokcdn.com/1.jpeg"}, {"url": "https://p16-sign.tiktokcdn.com/2.jpeg"}],
    }

    assert slideshow.detect_post_type(url) == "slideshow"


def test_detect_instagram_reel(fake_ytdlp):
    url = "https://www.instagram.com/reel/abc/"
    fake_ytdlp.responses[url] = {"formats": [{"vcodec": "h264", "acodec": "aac"}], "media_type": "video"}

    assert slideshow.detect_post_type(url) == "video"


def test_detect_instagram_carousel(fake_ytdlp):
    url = "https://www.instagram.com/p/carousel/"
    fake_ytdlp.responses[url] = {
        "_type": "playlist",
        "entries": [{"ext": "jpg", "url": "https://scontent.cdninstagram.com/1.jpg"}],
    }

    assert slideshow.detect_post_type(url) == "slideshow"


def test_detect_instagram_single_photo(fake_ytdlp):
    url = "https://www.instagram.com/p/photo/"
    fake_ytdlp.responses[url] = {"media_type": "photo"}

    assert slideshow.detect_post_type(url) == "slideshow"


def test_detect_instagram_single_photo_from_no_video_error(fake_ytdlp):
    url = "https://www.instagram.com/p/photo-error/"
    fake_ytdlp.errors[url] = Exception("ERROR: [Instagram] photo-error: There is no video in this post")

    assert slideshow.detect_post_type(url) == "slideshow"


def test_detect_instagram_empty_playlist_without_video_as_slideshow(fake_ytdlp):
    url = "https://www.instagram.com/p/empty-playlist/"
    fake_ytdlp.responses[url] = {"_type": "playlist", "entries": [], "formats": []}

    assert slideshow.detect_post_type(url) == "slideshow"


def test_detect_youtube_video(fake_ytdlp):
    url = "https://www.youtube.com/watch?v=abc123"
    fake_ytdlp.responses[url] = {"formats": [{"vcodec": "vp9", "acodec": "opus"}]}

    assert slideshow.detect_post_type(url) == "video"


def test_detect_youtube_community_short_circuits(fake_ytdlp):
    url = "https://www.youtube.com/post/UgkxExample"

    assert slideshow.detect_post_type(url) == "youtube_community"
    assert fake_ytdlp.calls == []


def test_detect_malformed_url_returns_unknown(fake_ytdlp):
    assert slideshow.detect_post_type("not a url") == "unknown"
    assert fake_ytdlp.calls == []
