import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(tempfile.mkdtemp(prefix="v1_4_tiktok_codec_"))

os.environ["VIDEO_CONTEXT_DIR"] = str(TEST_ROOT / "contexts")
os.environ["VIDEO_SOURCE_DIR"] = str(TEST_ROOT / "sources")
os.environ["VIDEO_ASSET_DIR"] = str(TEST_ROOT / "assets")
os.environ["ANALYSES_DIR"] = str(TEST_ROOT / "analyses")

sys.path.insert(0, str(ROOT / "src"))

from video_url_analyzer_mcp import server


TIKTOK_URL = (
    "https://www.tiktok.com/@marwan7_q/video/7623847107283160327"
    "?is_from_webapp=1&sender_device=pc"
)
YOUTUBE_ME_AT_THE_ZOO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
UNSUPPORTED_CODEC_MARKERS = ("bvc2", "bytevc2")
STRUCTURED_CODEC_ERROR_CODES = {
    "unsupported_codec",
    "no_compatible_format",
    "invalid_media",
    "media_not_decodable",
    "missing_media_file",
    "no_video_stream",
}


def run_cmd(args, timeout=120):
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result


def assert_success(result, label):
    assert result.returncode == 0, (
        f"{label} failed with exit {result.returncode}\n"
        f"stdout:\n{result.stdout[-1000:]}\n"
        f"stderr:\n{result.stderr[-1000:]}"
    )


def load_payload(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Expected JSON payload, got: {raw[:1000]}") from exc


def assert_no_unsupported_codec(text, label):
    lowered = (text or "").lower()
    assert not any(marker in lowered for marker in UNSUPPORTED_CODEC_MARKERS), (
        f"{label} contains an unsupported codec marker"
    )


def find_downloaded_media(directory: Path) -> Path:
    files = [path for path in directory.iterdir() if path.is_file()]
    assert files, f"No downloaded media in {directory}"
    return max(files, key=lambda path: path.stat().st_size)


def wait_for_job(job_id: str, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = load_payload(server.check_analysis_job(job_id))
        if payload["status"] != "processing":
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for job {job_id}")


def assert_tool_success_or_structured_codec_error(payload, label):
    if payload.get("status") == "error":
        assert payload.get("code") in STRUCTURED_CODEC_ERROR_CODES, payload
        return False
    assert payload.get("gemini_called") is False, payload
    assert payload.get("asset_ref"), payload
    assert Path(payload["asset_ref"]).exists(), payload
    return True


def test_ytdlp_selector(results):
    selector = server._safe_yt_dlp_format_selector_for_url(TIKTOK_URL)
    sort = server._safe_yt_dlp_sort_for_url(TIKTOK_URL)
    assert selector, "TikTok selector missing"
    assert sort, "TikTok sort preference missing"

    listing = run_cmd([sys.executable, "-m", "yt_dlp", "-F", TIKTOK_URL], timeout=120)
    assert_success(listing, "yt-dlp -F")
    assert "h264" in (listing.stdout + listing.stderr).lower(), "No h264 format shown"
    results.append(("yt_dlp_format_listing", "PASS", "formats listed"))

    simulate = run_cmd(
        [sys.executable, "-m", "yt_dlp", "--simulate", "-f", selector, "-S", sort, TIKTOK_URL],
        timeout=120,
    )
    assert_success(simulate, "yt-dlp selector simulate")
    selected_lines = "\n".join(
        line
        for line in (simulate.stdout + simulate.stderr).splitlines()
        if "format" in line.lower() or "download" in line.lower()
    )
    assert_no_unsupported_codec(selected_lines, "simulate selected format")
    results.append(("yt_dlp_selector_simulate", "PASS", selector))

    direct = run_cmd(
        [sys.executable, "-m", "yt_dlp", "-g", "-f", selector, "-S", sort, TIKTOK_URL],
        timeout=120,
    )
    assert_success(direct, "yt-dlp -g")
    direct_urls = [line for line in direct.stdout.splitlines() if line.strip()]
    assert direct_urls, "yt-dlp -g returned no stream URL"
    results.append(("yt_dlp_direct_url", "PASS", f"{len(direct_urls)} URL(s)"))

    download_dir = TEST_ROOT / "selected_download"
    download_dir.mkdir(parents=True, exist_ok=True)
    download = run_cmd(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--force-overwrites",
            "--impersonate",
            "chrome",
            "-f",
            selector,
            "-S",
            sort,
            "--merge-output-format",
            "mp4",
            "-o",
            str(download_dir / "selected.%(ext)s"),
            TIKTOK_URL,
        ],
        timeout=180,
    )
    assert_success(download, "yt-dlp selected download")
    media = find_downloaded_media(download_dir)
    info = server._validate_media_file_for_processing(
        media,
        TIKTOK_URL,
        require_decodable=True,
    )
    codec_text = " ".join(str(info.get(key) or "") for key in ("codec", "codec_tag"))
    assert_no_unsupported_codec(codec_text, "ffprobe codec")
    results.append(("ffprobe_selected_codec", "PASS", codec_text or "unknown"))


def test_tiktok_tool_paths(results):
    frame_payload = load_payload(
        server.get_video_frame(
            TIKTOK_URL,
            "00:01",
            reason="v1.4 TikTok codec smoke frame",
            force_refresh=True,
        )
    )
    frame_ok = assert_tool_success_or_structured_codec_error(frame_payload, "get_video_frame")
    results.append(
        (
            "get_video_frame_tiktok",
            "PASS",
            "asset created" if frame_ok else frame_payload.get("code"),
        )
    )

    clip_payload = load_payload(
        server.get_video_clip(
            TIKTOK_URL,
            "00:00",
            "00:02",
            reason="v1.4 TikTok codec smoke clip",
            max_duration_seconds=5,
            force_refresh=False,
        )
    )
    clip_ok = assert_tool_success_or_structured_codec_error(clip_payload, "get_video_clip")
    results.append(
        (
            "get_video_clip_tiktok",
            "PASS",
            "asset created" if clip_ok else clip_payload.get("code"),
        )
    )

    listed = load_payload(server.list_video_sources(limit=20))
    tiktok_id = server._video_id_from_url(TIKTOK_URL)
    matches = [item for item in listed.get("items", []) if item.get("video_id") == tiktok_id]
    if frame_ok or clip_ok:
        assert matches, listed
        source = matches[0]
        assert "source_downloaded" in source, source
        assert "source_valid" in source, source
        assert "codec" in source, source
        assert source["source_valid"] is True, source
        assert_no_unsupported_codec(str(source.get("codec")), "listed source codec")
        results.append(("list_video_sources_codec", "PASS", str(source.get("codec"))))
    else:
        results.append(("list_video_sources_codec", "PASS", "no source after structured error"))


def test_async_failed_result_mapping(results):
    job_ticket = load_payload(
        server._dispatch_or_background(
            "analyze_video",
            TIKTOK_URL,
            lambda: server._error_response(
                "analyze_video",
                "synthetic unsupported codec failure",
                code="unsupported_codec",
                details={"codec": "bytevc2"},
                platform="tiktok",
            ),
        )
    )
    assert job_ticket["status"] == "processing", job_ticket
    checked = wait_for_job(job_ticket["job_id"])
    assert checked["status"] == "failed", checked
    assert checked["error"]["error_code"] == "unsupported_codec", checked
    results.append(("async_inner_error_maps_failed", "PASS", checked["error"]["error_code"]))

    direct_job = server._create_job("analyze_video", TIKTOK_URL)
    server._complete_job(
        direct_job,
        '{"status":"failed","message":"FileState.FAILED","code":"gemini_file_failed"}',
    )
    direct_checked = load_payload(server.check_analysis_job(direct_job))
    assert direct_checked["status"] == "failed", direct_checked
    assert direct_checked["error"]["error_code"] == "gemini_file_failed", direct_checked
    results.append(("filestate_failed_maps_failed", "PASS", direct_checked["error"]["error_code"]))


def test_failed_prepare_does_not_save_context(results):
    old_download = server._download_video
    try:
        corrupt = TEST_ROOT / "corrupt.mp4"
        corrupt.write_bytes(b"bad")
        server._download_video = lambda _url: [str(corrupt)]
        payload = load_payload(
            server.do_prepare_video_context(
                TIKTOK_URL,
                "compact",
                True,
                None,
                30,
            )
        )
        assert payload["status"] == "error", payload
        assert payload["code"] == "invalid_media", payload
        assert payload["gemini_called"] is False, payload
        assert server._load_video_context(TIKTOK_URL) is None
        contexts = load_payload(server.list_video_contexts(limit=20))
        tiktok_id = server._video_id_from_url(TIKTOK_URL)
        assert all(item.get("video_id") != tiktok_id for item in contexts.get("items", [])), contexts
        results.append(("failed_prepare_no_context", "PASS", payload["code"]))
    finally:
        server._download_video = old_download


def test_youtube_regressions(results):
    old_download = server._download_video
    try:
        server._download_video = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("YouTube frame test must use the fast stream path")
        )
        payload = load_payload(
            server.get_video_frame(
                YOUTUBE_ME_AT_THE_ZOO,
                "00:01",
                reason="v1.4 YouTube fast path regression",
                force_refresh=True,
            )
        )
        assert payload.get("status") != "error", payload
        assert Path(payload["asset_ref"]).exists(), payload
        results.append(("youtube_frame_fast_path", "PASS", "asset created without download path"))
    finally:
        server._download_video = old_download

    if os.environ.get("GEMINI_API_KEY", "").strip():
        payload = load_payload(
            server.prepare_video_context(
                YOUTUBE_ME_AT_THE_ZOO,
                detail="compact",
                force_refresh=True,
                chunk_seconds=30,
            )
        )
        assert payload.get("status") != "error", payload
        assert payload.get("gemini_called") is True, payload
        results.append(("youtube_prepare_context", "PASS", payload.get("video_id", "context saved")))
    else:
        results.append(("youtube_prepare_context", "SKIP", "GEMINI_API_KEY not set"))


def test_retry_import_and_tool_count(results):
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = ""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(ROOT / 'src')!r}); "
                "from video_url_analyzer_mcp import server; "
                "print('IMPORT_NO_KEY_OK')"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert_success(proc, "import without GEMINI_API_KEY")
    assert "IMPORT_NO_KEY_OK" in proc.stdout
    results.append(("import_without_key", "PASS", "server imports"))

    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert len(names) == 18, sorted(names)
    results.append(("tool_count", "PASS", "18 tools"))

    calls = {"count": 0}

    class TransientError(Exception):
        code = 503

    old_sleep = server.time.sleep
    try:
        server.time.sleep = lambda _seconds: None

        def flaky():
            calls["count"] += 1
            if calls["count"] < 3:
                raise TransientError("temporary 503")
            return "ok"

        assert server._call_gemini_with_retry("test", flaky) == "ok"
        assert calls["count"] == 3, calls

        calls["count"] = 0

        class PermanentError(Exception):
            code = 400

        try:
            server._call_gemini_with_retry(
                "test",
                lambda: calls.__setitem__("count", calls["count"] + 1) or (_ for _ in ()).throw(
                    PermanentError("schema/auth/user error")
                ),
            )
        except PermanentError:
            pass
        else:
            raise AssertionError("Permanent 4xx error was not raised")
        assert calls["count"] == 1, calls
    finally:
        server.time.sleep = old_sleep
    results.append(("gemini_retry_policy", "PASS", "5xx retried, 4xx not retried"))


def main():
    results = []
    try:
        test_ytdlp_selector(results)
        test_tiktok_tool_paths(results)
        test_async_failed_result_mapping(results)
        test_failed_prepare_does_not_save_context(results)
        test_youtube_regressions(results)
        test_retry_import_and_tool_count(results)
        print(json.dumps({"status": "PASS", "results": results}, indent=2))
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
