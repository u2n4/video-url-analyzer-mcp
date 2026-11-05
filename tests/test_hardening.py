"""Regression tests for the hardening fixes in server.py."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from video_url_analyzer_mcp import server


def test_windows_command_reaches_the_program_as_typed():
    # Quoting in the middle of an argument (--title="My Video") was split apart on whitespace.
    cmd = r'python "C:\Program Files\tool\run.py" --title="My Video" C:\Users\alice\in.txt'
    assert server._command_for_subprocess(cmd, windows=True) == cmd


def test_posix_command_is_split_with_shlex():
    assert server._command_for_subprocess("ls -la /tmp", windows=False) == ["ls", "-la", "/tmp"]


def test_tutorial_command_reaches_subprocess_without_a_shell(monkeypatch):
    cmd = r'python "C:\Program Files\tool\run.py" --title="My Video" C:\Users\alice\in.txt'
    steps = {
        "is_tutorial": True,
        "title": "Demo",
        "steps": [
            {
                "step_number": 1,
                "description": "Run the tool",
                "commands": [cmd],
                "code_snippets": [],
                "file_paths": [],
            }
        ],
    }
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    server.do_execute_tutorial_steps(json.dumps(steps), confirm=True)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == server._command_for_subprocess(cmd)
    assert kwargs["shell"] is False


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


def test_context_temp_paths_are_unique_and_outside_the_json_glob(tmp_path):
    target = tmp_path / "abc123xyz.json"
    first = server._unique_temp_path(target)
    second = server._unique_temp_path(target)
    assert first != second
    assert first.parent == tmp_path
    assert first.suffix == ".tmp"
    assert not first.match("*.json")
    assert not second.match("*.json")


def test_failed_context_save_leaves_no_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VIDEO_CONTEXT_DIR", tmp_path)
    monkeypatch.setattr(server, "_ensure_context_quality", lambda ctx: None)

    def refuse_replace(src, dst, attempts=40):
        raise OSError("replace refused")

    monkeypatch.setattr(server, "_replace_with_retry", refuse_replace)
    ctx = SimpleNamespace(video_id="abc123xyz", model_dump=lambda: {"video_id": "abc123xyz"})
    with pytest.raises(OSError, match="replace refused"):
        server._save_video_context(ctx)
    assert list(tmp_path.iterdir()) == []


def test_each_context_save_gets_its_own_temp_path(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VIDEO_CONTEXT_DIR", tmp_path)
    monkeypatch.setattr(server, "_ensure_context_quality", lambda ctx: None)
    real_replace = server._replace_with_retry
    sources = []

    def record_then_replace(src, dst, attempts=40):
        sources.append(src)
        real_replace(src, dst, attempts)

    monkeypatch.setattr(server, "_replace_with_retry", record_then_replace)
    ctx = SimpleNamespace(video_id="abc123xyz", model_dump=lambda: {"video_id": "abc123xyz"})
    server._save_video_context(ctx)
    server._save_video_context(ctx)
    assert len(sources) == 2
    assert sources[0] != sources[1]
    assert json.loads((tmp_path / "abc123xyz.json").read_text(encoding="utf-8")) == {"video_id": "abc123xyz"}


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
