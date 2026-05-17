from __future__ import annotations

from types import SimpleNamespace

from video_url_analyzer_mcp import server


class FakeFiles:
    def delete(self, name):
        return None


class FakeClient:
    files = FakeFiles()


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
