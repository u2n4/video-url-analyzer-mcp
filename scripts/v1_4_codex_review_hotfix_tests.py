"""Regression tests for the Codex Review post-merge hotfix.

P1 — cleanup_video_cache must work when configured cache roots resolve outside
the package PROJECT_ROOT (e.g. uvx/site-packages installs).
P2 — prepare_video_context must keep async/background dispatch when the cached
context lookup raises (e.g. malformed JSON), instead of running a synchronous
download+analysis inline.

The tests run without a GEMINI_API_KEY by monkeypatching the few internal
helpers that would otherwise touch the network or Gemini.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(tempfile.mkdtemp(prefix="v1_4_codex_hotfix_"))

# Configure cache roots OUTSIDE the package PROJECT_ROOT to mirror a uvx install
CACHE_ROOT = TEST_ROOT / "caches"
CACHE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["VIDEO_CONTEXT_DIR"] = str(CACHE_ROOT / "contexts")
os.environ["VIDEO_SOURCE_DIR"] = str(CACHE_ROOT / "sources")
os.environ["VIDEO_ASSET_DIR"] = str(CACHE_ROOT / "assets")
os.environ["ANALYSES_DIR"] = str(CACHE_ROOT / "analyses")

sys.path.insert(0, str(ROOT / "src"))

from video_url_analyzer_mcp import server  # noqa: E402


TIKTOK_URL = (
    "https://www.tiktok.com/@marwan7_q/video/7623847107283160327"
    "?is_from_webapp=1&sender_device=pc"
)


def _ok(name, detail=""):
    print(f"PASS  {name}  {detail}".rstrip())


def _fail(name, detail):
    print(f"FAIL  {name}  {detail}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# P1 — cleanup_video_cache with cache roots outside PROJECT_ROOT
# ---------------------------------------------------------------------------

def test_cleanup_dry_run_outside_project_root():
    # Verify the configured roots really are outside the package PROJECT_ROOT
    # (otherwise this regression check is meaningless).
    if server.VIDEO_SOURCE_DIR.resolve().is_relative_to(server.PROJECT_ROOT.resolve()):
        _fail(
            "cleanup_dry_run_outside_project_root",
            "VIDEO_SOURCE_DIR resolved INSIDE PROJECT_ROOT; test setup is wrong",
        )

    raw = server.do_cleanup_video_cache("all", dry_run=True, video_id=None)
    payload = json.loads(raw)
    if payload.get("dry_run") is not True:
        _fail("cleanup_dry_run_outside_project_root", f"dry_run flag missing: {payload}")
    if "removed" not in payload or payload["removed"]:
        _fail("cleanup_dry_run_outside_project_root", f"unexpected removals: {payload}")
    _ok("cleanup_dry_run_outside_project_root")


def test_cleanup_real_delete_inside_approved_root():
    # Seed a fake source directory under VIDEO_SOURCE_DIR
    fake_id = "abc123def456"
    target = server.VIDEO_SOURCE_DIR / fake_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "source.mp4").write_bytes(b"fake")

    raw = server.do_cleanup_video_cache("sources", dry_run=False, video_id=fake_id)
    payload = json.loads(raw)
    if not payload.get("removed"):
        _fail("cleanup_real_delete_inside_approved_root", f"nothing removed: {payload}")
    if target.exists():
        _fail("cleanup_real_delete_inside_approved_root", f"target still exists: {target}")
    _ok("cleanup_real_delete_inside_approved_root")


def test_cleanup_rejects_unapproved_root():
    # Sanity-check the helper directly. Pointing the helper at an arbitrary
    # path (not VIDEO_SOURCE_DIR/ASSET/CONTEXT) must raise.
    rogue = TEST_ROOT / "rogue"
    rogue.mkdir(parents=True, exist_ok=True)
    try:
        server._assert_safe_cleanup_root(rogue)
    except RuntimeError:
        _ok("cleanup_rejects_unapproved_root")
        return
    _fail("cleanup_rejects_unapproved_root", "expected RuntimeError")


def test_cleanup_rejects_drive_or_home_root():
    home = Path.home()
    drive_root = Path(home.anchor) if home.anchor else home
    for danger in (home, drive_root):
        try:
            server._assert_safe_cleanup_root(danger)
        except RuntimeError:
            continue
        _fail(
            "cleanup_rejects_drive_or_home_root",
            f"helper accepted dangerous root: {danger}",
        )
    _ok("cleanup_rejects_drive_or_home_root")


def test_cleanup_candidates_inside_root():
    fake_id = "fakecandidateid"
    target = server.VIDEO_SOURCE_DIR / fake_id
    target.mkdir(parents=True, exist_ok=True)
    candidates = server._cache_cleanup_candidates("sources", fake_id)
    if not candidates:
        _fail("cleanup_candidates_inside_root", "no candidates returned")
    for kind, path, root in candidates:
        if not server._is_within(path, root):
            _fail(
                "cleanup_candidates_inside_root",
                f"path {path} not inside approved root {root}",
            )
    shutil.rmtree(target, ignore_errors=True)
    _ok("cleanup_candidates_inside_root")


# ---------------------------------------------------------------------------
# P2 — prepare_video_context must keep async dispatch when cache lookup fails
# ---------------------------------------------------------------------------

def _seed_corrupt_context_for(url):
    video_id = server._video_id_from_url(url)
    path = server._context_path(video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid JSON ;;;", encoding="utf-8")
    return path


def _patch_dispatch_to_record(call_log):
    original = server._dispatch_or_background

    def fake_dispatch(tool_name, url, func, *args, **kwargs):
        call_log.append(("dispatch", tool_name, url))
        return json.dumps({"status": "processing", "job_id": "FAKE_JOB", "gemini_called": False})

    server._dispatch_or_background = fake_dispatch
    return original


def _patch_do_prepare_to_record(call_log):
    original = server.do_prepare_video_context

    def fake_do_prepare(*args, **kwargs):
        call_log.append(("do_prepare", args[0] if args else None))
        return json.dumps({"status": "success", "video_id": "called_sync"})

    server.do_prepare_video_context = fake_do_prepare
    return original


def test_corrupt_context_routes_through_dispatch_for_tiktok():
    _seed_corrupt_context_for(TIKTOK_URL)
    call_log = []
    orig_dispatch = _patch_dispatch_to_record(call_log)
    orig_do_prepare = _patch_do_prepare_to_record(call_log)
    try:
        result = server.prepare_video_context(TIKTOK_URL)
    finally:
        server._dispatch_or_background = orig_dispatch
        server.do_prepare_video_context = orig_do_prepare

    payload = json.loads(result)
    sync_calls = [c for c in call_log if c[0] == "do_prepare"]
    dispatch_calls = [c for c in call_log if c[0] == "dispatch"]
    if sync_calls:
        _fail(
            "corrupt_context_routes_through_dispatch_for_tiktok",
            f"do_prepare_video_context was called synchronously: {sync_calls}",
        )
    if not dispatch_calls:
        _fail(
            "corrupt_context_routes_through_dispatch_for_tiktok",
            f"_dispatch_or_background was not called: {call_log}",
        )
    if payload.get("status") != "processing":
        _fail(
            "corrupt_context_routes_through_dispatch_for_tiktok",
            f"unexpected payload: {payload}",
        )
    _ok("corrupt_context_routes_through_dispatch_for_tiktok")


def test_corrupt_context_does_not_block_new_analysis():
    # Same scenario, but we assert the function returned a usable JSON ticket
    # (i.e. the corrupt context did not propagate or return an error).
    _seed_corrupt_context_for(TIKTOK_URL)
    call_log = []
    orig_dispatch = _patch_dispatch_to_record(call_log)
    orig_do_prepare = _patch_do_prepare_to_record(call_log)
    try:
        result = server.prepare_video_context(TIKTOK_URL)
    finally:
        server._dispatch_or_background = orig_dispatch
        server.do_prepare_video_context = orig_do_prepare

    payload = json.loads(result)
    if payload.get("status") == "error":
        _fail(
            "corrupt_context_does_not_block_new_analysis",
            f"corrupt cache caused error response: {payload}",
        )
    _ok("corrupt_context_does_not_block_new_analysis")


def test_existing_cached_context_still_reused():
    # Build a valid SavedVideoContext and persist it. The cached-hit fast path
    # must call do_prepare_video_context synchronously (so the cached JSON is
    # returned immediately without a download).
    valid_url = TIKTOK_URL
    video_id = server._video_id_from_url(valid_url)
    ctx = server.SavedVideoContext(
        schema_version="1.3.0",
        video_id=video_id,
        url=valid_url,
        normalized_url=valid_url,
        created_at="2025-01-01T00:00:00",
        updated_at="2025-01-01T00:00:00",
        analyzed_by="gemini",
        model_used="gemini-flash-latest",
        detail="standard",
        summary="cached fixture",
        visual_summary="visual",
        audio_summary="audio",
        spoken_content_summary="spoken",
        text_on_screen_summary="onscreen",
        topics=[],
        entities=[],
        objects=[],
        key_moments=[],
        timeline=[],
        warnings=[],
        limitations=[],
    )
    server._save_video_context(ctx)

    call_log = []
    orig_dispatch = _patch_dispatch_to_record(call_log)
    orig_do_prepare = _patch_do_prepare_to_record(call_log)
    try:
        result = server.prepare_video_context(valid_url)
    finally:
        server._dispatch_or_background = orig_dispatch
        server.do_prepare_video_context = orig_do_prepare
        # Clean up the cached JSON so other tests don't inherit it
        server._context_path(video_id).unlink(missing_ok=True)

    sync_calls = [c for c in call_log if c[0] == "do_prepare"]
    dispatch_calls = [c for c in call_log if c[0] == "dispatch"]
    if not sync_calls:
        _fail(
            "existing_cached_context_still_reused",
            f"cached hit did not call do_prepare_video_context: {call_log}",
        )
    if dispatch_calls:
        _fail(
            "existing_cached_context_still_reused",
            f"cached hit unexpectedly went through dispatch: {dispatch_calls}",
        )
    payload = json.loads(result)
    if payload.get("status") == "error":
        _fail("existing_cached_context_still_reused", f"unexpected error: {payload}")
    _ok("existing_cached_context_still_reused")


# ---------------------------------------------------------------------------
# Smoke checks shared with the v1.4 codec test suite
# ---------------------------------------------------------------------------

def test_tool_count_unchanged():
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    if len(names) != 17:
        _fail("tool_count_unchanged", f"expected 17 tools, got {len(names)}: {sorted(names)}")
    _ok("tool_count_unchanged", "17 tools")


def test_import_without_gemini_api_key():
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
    if proc.returncode != 0 or "IMPORT_NO_KEY_OK" not in proc.stdout:
        _fail(
            "import_without_gemini_api_key",
            f"exit={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
    _ok("import_without_gemini_api_key")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    test_cleanup_dry_run_outside_project_root()
    test_cleanup_real_delete_inside_approved_root()
    test_cleanup_rejects_unapproved_root()
    test_cleanup_rejects_drive_or_home_root()
    test_cleanup_candidates_inside_root()
    test_corrupt_context_routes_through_dispatch_for_tiktok()
    test_corrupt_context_does_not_block_new_analysis()
    test_existing_cached_context_still_reused()
    test_tool_count_unchanged()
    test_import_without_gemini_api_key()
    print("ALL_PASS")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
