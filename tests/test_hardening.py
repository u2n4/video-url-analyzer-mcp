"""Regression tests for the hardening fixes in server.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from video_url_analyzer_mcp import server


def test_windows_command_split_keeps_backslashes():
    tokens = server._split_command_line(
        r'python "C:\Program Files\tool\run.py" C:\Users\alice\in.txt',
        windows=True,
    )
    assert tokens == [
        "python",
        r"C:\Program Files\tool\run.py",
        r"C:\Users\alice\in.txt",
    ]


def test_posix_command_split_is_unchanged():
    assert server._split_command_line("ls -la /tmp", windows=False) == ["ls", "-la", "/tmp"]


@pytest.mark.parametrize(
    "path",
    [
        "C:/Users/alice/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/updater.cmd",
        "C:/Windows/System32/drivers/etc/hosts",
    ],
)
def test_forward_slash_spelling_of_protected_location_is_refused(path):
    assert server._validate_file_path(path) is False


def test_ordinary_project_path_is_allowed():
    assert server._validate_file_path("C:/Users/alice/projects/app/main.py") is True


def test_term_match_is_whole_word():
    assert server._term_in_text("auto", "the model is automatically tuned") is False
    assert server._term_in_text("auto", "auto mode is on") is True


def test_unreadable_saved_context_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VIDEO_CONTEXT_DIR", tmp_path)
    (tmp_path / "abc123xyz.json").write_text("{truncated", encoding="utf-8")
    assert server._load_video_context("abc123xyz") is None


def test_replace_with_retry_moves_file_over_destination(tmp_path):
    src = tmp_path / "ctx.json.tmp"
    dst = tmp_path / "ctx.json"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    server._replace_with_retry(src, dst)
    assert dst.read_text(encoding="utf-8") == "new"
    assert not src.exists()


def test_failed_gemini_upload_is_deleted(tmp_path, monkeypatch):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"not really a video")
    deleted: list[str] = []

    class FakeFiles:
        def upload(self, file):
            return SimpleNamespace(name="files/abc", state=SimpleNamespace(name="FAILED"))

        def get(self, name):
            raise AssertionError("a failed upload must not be polled")

        def delete(self, name):
            deleted.append(name)

    monkeypatch.setattr(server, "_validate_media_file_for_processing", lambda path, url=None: {})
    monkeypatch.setattr(server, "_get_client", lambda: SimpleNamespace(files=FakeFiles()))
    with pytest.raises(server.VideoAnalyzerError) as excinfo:
        server._upload_to_gemini(str(media))
    assert excinfo.value.code == "gemini_file_failed"
    assert deleted == ["files/abc"]


def test_oversized_download_raises_structured_error(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MAX_DOWNLOAD_SIZE_MB", 0.000001)
    media = tmp_path / "a.mp4"
    media.write_bytes(b"0123456789")
    with pytest.raises(server.VideoAnalyzerError) as excinfo:
        server._check_download_sizes([str(media)])
    assert excinfo.value.code == "download_too_large"


def test_cache_lookup_does_not_create_source_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VIDEO_SOURCE_DIR", tmp_path / "video_sources")
    assert server._find_cached_source("abc123xyz") is None
    assert not (tmp_path / "video_sources" / "abc123xyz").exists()
