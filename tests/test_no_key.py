"""No-key contract.

Without GEMINI_API_KEY the server must start, list every tool, and run the
local tools. Gemini-backed tools must fail with a structured error that names
the missing key. Gemini behaviour is exercised only through fake clients.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from video_url_analyzer_mcp import server

SRC = Path(__file__).resolve().parents[1] / "src"
GEMINI_ENV = {
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "VIDEO_ANALYZER_MODEL",
    "GEMINI_FAST_MODEL",
    "GEMINI_DEEP_MODEL",
}
YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
EXPECTED_TOOLS = {
    "analyze_video",
    "analyze_video_segment",
    "ask_about_video",
    "ask_video_context",
    "check_analysis_job",
    "cleanup_video_cache",
    "delete_video_context",
    "execute_tutorial_steps",
    "find_video_moments",
    "get_transcript",
    "get_video_clip",
    "get_video_evidence_asset",
    "get_video_frame",
    "list_video_contexts",
    "list_video_sources",
    "prepare_slideshow_assets",
    "prepare_video_context",
    "watch_and_analyze",
}


def test_importing_server_does_not_load_google_genai():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "import video_url_analyzer_mcp.server\n"
        "print('google.genai' in sys.modules)\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in GEMINI_ENV}
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_all_tools_are_listed_without_gemini_key():
    tools = asyncio.run(server.mcp.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


GEMINI_CALLS = {
    "analyze_video": lambda: server.do_analyze_video(YOUTUBE_URL),
    "get_transcript": lambda: server.do_get_transcript(YOUTUBE_URL),
    "ask_about_video": lambda: server.do_ask_about_video(YOUTUBE_URL, "What is shown?"),
    "find_video_moments": lambda: server.do_find_video_moments(
        YOUTUBE_URL, "intro", 3, 10, "compact", None, False
    ),
    "analyze_video_segment": lambda: server.do_analyze_video_segment(
        YOUTUBE_URL, "0:00", "0:10", "Describe.", "compact", None, False
    ),
    "prepare_video_context": lambda: server.do_prepare_video_context(
        YOUTUBE_URL, "standard", False, None, 30
    ),
}


@pytest.mark.parametrize("tool", sorted(GEMINI_CALLS))
def test_gemini_tool_without_key_returns_named_structured_error(tool, monkeypatch):
    # Post-type detection would call yt-dlp (network); the key check does not depend on it.
    monkeypatch.setattr(server, "_detect_post_type_for_routing", lambda url: "video")
    text = GEMINI_CALLS[tool]()
    assert "Traceback" not in text
    payload = json.loads(text)
    assert payload["status"] == "error"
    assert payload["code"] == "gemini_api_key_missing"
    assert "GEMINI_API_KEY" in payload["message"]


def test_local_tools_run_without_key(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VIDEO_CONTEXT_DIR", tmp_path / "video_contexts")
    monkeypatch.setattr(server, "VIDEO_SOURCE_DIR", tmp_path / "video_sources")
    monkeypatch.setattr(server, "VIDEO_ASSET_DIR", tmp_path / "video_assets")
    for text in (
        server.do_list_video_contexts(None, 5),
        server.do_list_video_sources(None, 5),
        server.do_cleanup_video_cache("sources", True, None),
    ):
        assert json.loads(text).get("status") != "error"


def test_client_is_created_from_key_without_network(monkeypatch):
    from google import genai

    monkeypatch.setenv("GEMINI_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.setattr(server, "_client", None)
    assert isinstance(server._get_client(), genai.Client)


class _FakeResponse:
    text = "fake analysis"


class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse()


class _FakeGenAIClient:
    def __init__(self) -> None:
        self.models = _FakeModels()


def test_youtube_analysis_runs_only_against_fake_client(monkeypatch):
    fake = _FakeGenAIClient()
    monkeypatch.setattr(server, "_get_client", lambda: fake)
    assert server.do_analyze_video(YOUTUBE_URL) == "fake analysis"
    assert [call["model"] for call in fake.models.calls] == [server.DEFAULT_MODEL]
