from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from video_url_analyzer_mcp import server


class FakeFiles:
    def delete(self, name):
        return None


class FakeClient:
    files = FakeFiles()


def test_default_fast_model_uses_latest_gemini_flash():
    assert server.DEFAULT_MODEL == "gemini-3.5-flash"
    assert server.FAST_MODEL_FALLBACK == "gemini-3.5-flash"


def test_analysis_config_uses_thinking_level_for_gemini_3():
    config = server._build_analysis_config("models/gemini-3.5-flash")

    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level.value == "HIGH"
    assert config.thinking_config.thinking_budget is None


def test_analysis_config_uses_thinking_budget_for_gemini_25():
    config = server._build_analysis_config("gemini-2.5-flash")

    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level is None
    assert config.thinking_config.thinking_budget == -1


def test_download_video_uses_ytdlp_python_api(monkeypatch, tmp_path):
    temp_dir = tmp_path / "download"
    temp_dir.mkdir()
    seen_opts = []
    seen_urls = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            seen_opts.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            seen_urls.append(urls)
            output = Path(seen_opts[-1]["outtmpl"].replace("%(ext)s", "mp4"))
            output.write_bytes(b"video bytes" * 200)

    monkeypatch.setattr(server.tempfile, "mkdtemp", lambda prefix: str(temp_dir))
    monkeypatch.setattr(server.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess should not run")),
    )
    monkeypatch.setattr(server, "_check_download_size", lambda path: path)
    monkeypatch.setattr(server, "_validate_media_file_for_processing", lambda path, url=None: {})

    result = server._download_video("https://www.youtube.com/watch?v=abc123")

    assert result == [str(temp_dir / "video.mp4")]
    assert seen_urls == [["https://www.youtube.com/watch?v=abc123"]]
    assert seen_opts[0]["noplaylist"] is True
    assert seen_opts[0]["overwrites"] is True
    assert seen_opts[0]["quiet"] is True
    assert seen_opts[0]["no_warnings"] is True


def test_download_video_falls_back_when_impersonate_unavailable(monkeypatch, tmp_path):
    temp_dir = tmp_path / "download"
    temp_dir.mkdir()
    attempts = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            attempts.append(opts)
            if opts.get("impersonate"):
                raise server.YtDlpError("impersonate target unavailable")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            output = Path(attempts[-1]["outtmpl"].replace("%(ext)s", "mp4"))
            output.write_bytes(b"video bytes" * 200)

    monkeypatch.setattr(server.tempfile, "mkdtemp", lambda prefix: str(temp_dir))
    monkeypatch.setattr(server, "_download_tiktok_api", lambda url, tmp_dir: None)
    monkeypatch.setattr(server.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(server, "_check_download_size", lambda path: path)
    monkeypatch.setattr(server, "_validate_media_file_for_processing", lambda path, url=None: {})

    result = server._download_video("https://www.tiktok.com/@user/video/123")

    assert result == [str(temp_dir / "video.mp4")]
    assert str(attempts[0]["impersonate"]) == "chrome"
    assert attempts[0]["format_sort"] == ["+codec:avc:m4a", "res", "ext:mp4:m4a"]
    assert "impersonate" not in attempts[-1]


def test_analyze_downloaded_routes_slideshow_branch(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(server, "detect_post_type", lambda url: "slideshow")
    monkeypatch.setattr(server, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(server, "_validate_slideshow_download", lambda slideshow, url: None)

    def fake_download_slideshow(url, output_dir, post_type):
        calls.append(f"slideshow:{post_type}")
        return {"images": [], "audio": None, "metadata": {"image_count": 0}}

    def fake_analyze_slideshow(client, model, slideshow, prompt, lang, **kwargs):
        calls.append("analyze_slideshow")
        return "slideshow result"

    monkeypatch.setattr(server, "download_slideshow", fake_download_slideshow)
    monkeypatch.setattr(server, "analyze_slideshow", fake_analyze_slideshow)
    monkeypatch.setattr(
        server,
        "_download_video",
        lambda url: (_ for _ in ()).throw(AssertionError("video branch should not run")),
    )

    result = server._analyze_downloaded("https://www.tiktok.com/@user/photo/1", "prompt", "model")

    assert result == "slideshow result"
    assert calls == ["slideshow:slideshow", "analyze_slideshow"]


def test_analyze_downloaded_routes_video_branch(monkeypatch, tmp_path):
    calls: list[str] = []
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video bytes" * 200)

    monkeypatch.setattr(server, "detect_post_type", lambda url: "video")
    monkeypatch.setattr(server, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(server, "download_slideshow", lambda *args, **kwargs: calls.append("slideshow"))

    def fake_download_video(url):
        calls.append("video")
        return [str(video)]

    def fake_upload(path):
        calls.append(f"upload:{path}")
        return SimpleNamespace(name="files/123")

    def fake_generate_content(**kwargs):
        calls.append("generate")
        assert kwargs["contents"][-1] == "prompt"
        return SimpleNamespace(text="video result")

    monkeypatch.setattr(server, "_download_video", fake_download_video)
    monkeypatch.setattr(server, "_upload_to_gemini", fake_upload)
    monkeypatch.setattr(server, "_generate_content_with_retry", fake_generate_content)

    result = server._analyze_downloaded("https://www.tiktok.com/@user/video/1", "prompt", "model")

    assert result == "video result"
    assert calls == ["video", f"upload:{video}", "generate"]


def test_analyze_downloaded_uploads_multiple_files_in_original_order(monkeypatch, tmp_path):
    videos = []
    for index in (2, 1, 3):
        video = tmp_path / f"video_{index}.mp4"
        video.write_bytes(b"video bytes" * 200)
        videos.append(video)

    monkeypatch.setenv("VIDEO_GEMINI_UPLOAD_CONCURRENCY", "3")
    monkeypatch.setattr(server, "_detect_post_type_for_routing", lambda url: "video")
    monkeypatch.setattr(server, "_download_video", lambda url: [str(video) for video in videos])
    monkeypatch.setattr(server, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(
        server,
        "_upload_to_gemini",
        lambda path: SimpleNamespace(name=f"files/{Path(path).stem}"),
    )

    captured = {}

    def fake_generate_content(**kwargs):
        captured["contents"] = kwargs["contents"]
        return SimpleNamespace(text="multi video result")

    monkeypatch.setattr(server, "_generate_content_with_retry", fake_generate_content)

    result = server._analyze_downloaded("https://www.instagram.com/reel/example/", "prompt", "model")

    assert result == "multi video result"
    uploaded_names = [item.name for item in captured["contents"][:-1]]
    assert uploaded_names == ["files/video_2", "files/video_1", "files/video_3"]


def test_prepare_slideshow_assets_returns_ordered_image_blocks(monkeypatch, tmp_path):
    image_paths = []
    for index in range(1, 3):
        image = tmp_path / f"slide_{index}.jpg"
        image.write_bytes(b"\xff\xd8" + bytes([index]) * 2048)
        image_paths.append(image)

    monkeypatch.setattr(server, "detect_post_type", lambda url: "slideshow")
    monkeypatch.setattr(server, "_validate_slideshow_download", lambda slideshow, url: None)
    monkeypatch.setattr(
        server,
        "download_slideshow",
        lambda url, output_dir, post_type: {
            "images": image_paths,
            "audio": None,
            "metadata": {
                "platform": "instagram",
                "caption": "caption text",
                "image_count": 2,
            },
        },
    )

    result = server.do_prepare_slideshow_assets("https://www.instagram.com/p/example/")

    assert result[0].startswith("Analyze these 2 slideshow images in order.")
    assert "Caption: caption text" in result
    assert "image_index=1; image_count=2" in result
    assert "image_index=2; image_count=2" in result
    image_blocks = [item for item in result if getattr(item, "type", None) == "image"]
    assert len(image_blocks) == 2


def test_analyze_slideshow_labels_images_before_gemini(monkeypatch, tmp_path):
    from video_url_analyzer_mcp import slideshow

    image_paths = []
    for index in range(1, 3):
        image = tmp_path / f"slide_{index}.jpg"
        image.write_bytes(b"\xff\xd8" + bytes([index]) * 2048)
        image_paths.append(image)

    captured = {}
    fake_files = [SimpleNamespace(name="files/1"), SimpleNamespace(name="files/2")]

    def fake_generate_content(**kwargs):
        captured["contents"] = kwargs["contents"]
        return SimpleNamespace(text="ok")

    result = slideshow.analyze_slideshow(
        FakeClient(),
        "model",
        {
            "images": image_paths,
            "audio": None,
            "metadata": {"platform": "instagram", "caption": None},
        },
        "prompt",
        "English",
        upload_file=lambda path: fake_files.pop(0),
        generate_content=fake_generate_content,
    )

    assert result == "ok"
    assert captured["contents"][1] == "Image 1 of 2. Analyze this image before moving to the next one."
    assert captured["contents"][3] == "Image 2 of 2. Analyze this image before moving to the next one."
    assert captured["contents"][-1] == "prompt"


def test_analyze_slideshow_uploads_images_concurrently_in_original_order(monkeypatch, tmp_path):
    from video_url_analyzer_mcp import slideshow

    image_paths = []
    for index in range(1, 5):
        image = tmp_path / f"slide_{index}.jpg"
        image.write_bytes(b"\xff\xd8" + bytes([index]) * 2048)
        image_paths.append(image)

    monkeypatch.setenv("VIDEO_GEMINI_UPLOAD_CONCURRENCY", "4")
    captured = {}

    def fake_upload(path: str):
        return SimpleNamespace(name=f"files/{Path(path).stem}")

    def fake_generate_content(**kwargs):
        captured["contents"] = kwargs["contents"]
        return SimpleNamespace(text="ok")

    result = slideshow.analyze_slideshow(
        FakeClient(),
        "model",
        {
            "images": image_paths,
            "audio": None,
            "metadata": {"platform": "instagram", "caption": None},
        },
        "prompt",
        None,
        upload_file=fake_upload,
        generate_content=fake_generate_content,
    )

    assert result == "ok"
    uploaded_names = [
        item.name
        for item in captured["contents"]
        if hasattr(item, "name")
    ]
    assert uploaded_names == ["files/slide_1", "files/slide_2", "files/slide_3", "files/slide_4"]


def test_download_images_keeps_order_with_parallel_workers(monkeypatch, tmp_path):
    from video_url_analyzer_mcp import slideshow

    monkeypatch.setenv("VIDEO_IMAGE_DOWNLOAD_CONCURRENCY", "3")

    def fake_download(url: str, final_path: Path, timeout: int = 60):
        final_path.write_text(url, encoding="utf-8")
        return final_path

    monkeypatch.setattr(slideshow, "_download_image_atomic", fake_download)

    paths = slideshow._download_images(
        ["https://example.com/3.jpg", "https://example.com/1.jpg", "https://example.com/2.jpg"],
        tmp_path,
        "slide",
    )

    assert [path.name for path in paths] == ["slide_01.jpg", "slide_02.jpg", "slide_03.jpg"]
    assert [path.read_text(encoding="utf-8") for path in paths] == [
        "https://example.com/3.jpg",
        "https://example.com/1.jpg",
        "https://example.com/2.jpg",
    ]
