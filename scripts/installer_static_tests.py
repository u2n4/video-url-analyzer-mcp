"""Static checks for the Windows installer / wizard deliverables.

Verifies file presence, README modernization, gitignore coverage, the
absence of accidentally-committed API key material in the new files,
and the v1.2.0 model/version contract.
"""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "1.2.0"
DEFAULT_FAST_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_DEEP_MODEL = "gemini-3.1-pro-preview"


def _ok(name, detail=""):
    print(f"PASS  {name}  {detail}".rstrip())


def _fail(name, detail):
    print(f"FAIL  {name}  {detail}")
    sys.exit(1)


def _read(path: Path) -> str:
    if not path.exists():
        _fail(f"file_present:{path.relative_to(ROOT)}", "file is missing")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Required files exist
# ---------------------------------------------------------------------------

REQUIRED_FILES = [
    "start.bat",
    "scripts/install_windows.ps1",
    "scripts/configure_mcp_clients.ps1",
    "docs/mcp-config-examples.md",
]


def test_required_files_exist():
    for rel in REQUIRED_FILES:
        path = ROOT / rel
        if not path.exists():
            _fail("required_files_exist", f"missing: {rel}")
    _ok("required_files_exist", f"{len(REQUIRED_FILES)} files")


# ---------------------------------------------------------------------------
# 2. README modernization
# ---------------------------------------------------------------------------

def test_readme_uses_correct_repo():
    body = _read(ROOT / "README.md")
    if "u2n4/video-url-analyzer-mcp" not in body:
        _fail("readme_uses_correct_repo", "expected 'u2n4/video-url-analyzer-mcp'")
    _ok("readme_uses_correct_repo")


def test_readme_drops_stale_owner():
    body = _read(ROOT / "README.md")
    if "alihsh0" in body:
        _fail("readme_drops_stale_owner", "stale 'alihsh0' reference still present")
    _ok("readme_drops_stale_owner")


def test_readme_drops_stale_install_commands():
    body = _read(ROOT / "README.md")
    if re.search(r"^\s*python\s+server\.py\s*$", body, flags=re.MULTILINE):
        _fail("readme_drops_stale_install_commands", "still recommends 'python server.py'")
    if re.search(r"pip\s+install\s+-r\s+requirements\.txt", body):
        _fail("readme_drops_stale_install_commands", "still recommends 'pip install -r requirements.txt'")
    _ok("readme_drops_stale_install_commands")


def test_readme_documents_uvx_and_module():
    body = _read(ROOT / "README.md")
    if "uvx video-url-analyzer-mcp" not in body:
        _fail("readme_documents_uvx_and_module", "missing 'uvx video-url-analyzer-mcp'")
    if "python -m video_url_analyzer_mcp" not in body:
        _fail("readme_documents_uvx_and_module", "missing 'python -m video_url_analyzer_mcp'")
    _ok("readme_documents_uvx_and_module")


# ---------------------------------------------------------------------------
# 3. No real API keys leaked into installer / docs / scripts
# ---------------------------------------------------------------------------

REAL_KEY_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ya29\.[0-9A-Za-z_\-]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.=]{20,}"),
]

PLACEHOLDER_OK = {
    "your_key_here",
    "YOUR_KEY_HERE",
    "set-in-environment-not-here",
    "<empty>",
    "AIza...abcd",
}

SCAN_TARGETS = [
    "start.bat",
    "scripts/install_windows.ps1",
    "scripts/configure_mcp_clients.ps1",
    "docs/mcp-config-examples.md",
    "README.md",
]


def test_no_hardcoded_api_keys():
    bad = []
    for rel in SCAN_TARGETS:
        path = ROOT / rel
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8")
        for rx in REAL_KEY_PATTERNS:
            for match in rx.finditer(body):
                # Ignore short/placeholder examples already on the allow-list
                literal = match.group(0)
                if any(p in literal for p in PLACEHOLDER_OK):
                    continue
                lineno = body.count("\n", 0, match.start()) + 1
                bad.append(f"{rel}:{lineno}: matches {rx.pattern}")
    if bad:
        _fail("no_hardcoded_api_keys", "; ".join(bad))
    _ok("no_hardcoded_api_keys")


def test_examples_use_placeholders_only():
    body = _read(ROOT / "docs/mcp-config-examples.md")
    if "AIza" in body and "AIza...abcd" not in body:
        _fail("examples_use_placeholders_only", "real-looking AIza... value in docs")
    _ok("examples_use_placeholders_only")


# ---------------------------------------------------------------------------
# 4. .gitignore coverage
# ---------------------------------------------------------------------------

REQUIRED_IGNORE_PATTERNS = [
    ".env.keys.local",   # via *.local or explicit
    "mcp-config.json",
    "cookies.txt",
    "video_contexts/",
    "video_sources/",
    "video_assets/",
    "*.mp4",
    "*.png",
]


def test_gitignore_covers_sensitive_paths():
    body = _read(ROOT / ".gitignore")
    missing = [pat for pat in REQUIRED_IGNORE_PATTERNS if pat not in body]
    if missing:
        _fail("gitignore_covers_sensitive_paths", f"missing patterns: {missing}")
    _ok("gitignore_covers_sensitive_paths")


# ---------------------------------------------------------------------------
# 5. start.bat sanity
# ---------------------------------------------------------------------------

def test_start_bat_uses_module_entrypoint():
    body = _read(ROOT / "start.bat")
    if "python -m video_url_analyzer_mcp" not in body and "%PYTHON_CMD% -m video_url_analyzer_mcp" not in body:
        _fail("start_bat_uses_module_entrypoint", "no 'python -m video_url_analyzer_mcp' invocation")
    if re.search(r"python\s+server\.py", body):
        _fail("start_bat_uses_module_entrypoint", "still launches root server.py")
    _ok("start_bat_uses_module_entrypoint")


# ---------------------------------------------------------------------------
# 6. installer scripts mention required safety behavior
# ---------------------------------------------------------------------------

def test_installer_handles_key_securely():
    body = _read(ROOT / "scripts/install_windows.ps1")
    if "AsSecureString" not in body:
        _fail("installer_handles_key_securely", "API key is not read as a SecureString")
    if ".env.keys.local" not in body:
        _fail("installer_handles_key_securely", "no .env.keys.local handling")
    if "Get-MaskedKey" not in body:
        _fail("installer_handles_key_securely", "key masking helper missing")
    _ok("installer_handles_key_securely")


def test_client_wizard_backs_up_and_validates():
    body = _read(ROOT / "scripts/configure_mcp_clients.ps1")
    for needle in ("Backup-File", "ConvertFrom-Json", "claude_desktop_config.json", "config.toml", ".vscode"):
        if needle not in body:
            _fail("client_wizard_backs_up_and_validates", f"missing: {needle}")
    _ok("client_wizard_backs_up_and_validates")


# ---------------------------------------------------------------------------
# 7. v1.2.0 version + model contract
# ---------------------------------------------------------------------------

def test_pyproject_version():
    body = _read(ROOT / "pyproject.toml")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', body)
    if not m:
        _fail("pyproject_version", "no version field found")
    if m.group(1) != EXPECTED_VERSION:
        _fail("pyproject_version", f"expected {EXPECTED_VERSION}, got {m.group(1)}")
    _ok("pyproject_version", EXPECTED_VERSION)


def test_package_dunder_version():
    body = _read(ROOT / "src/video_url_analyzer_mcp/__init__.py")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', body)
    if not m or m.group(1) != EXPECTED_VERSION:
        _fail("package_dunder_version", f"expected {EXPECTED_VERSION}, got {m.group(1) if m else 'missing'}")
    _ok("package_dunder_version", EXPECTED_VERSION)


def test_default_models_in_server():
    body = _read(ROOT / "src/video_url_analyzer_mcp/server.py")
    if f'FAST_MODEL_FALLBACK = "{DEFAULT_FAST_MODEL}"' not in body:
        _fail("default_models_in_server", f"FAST_MODEL_FALLBACK is not {DEFAULT_FAST_MODEL}")
    if f'DEEP_MODEL_FALLBACK = "{DEFAULT_DEEP_MODEL}"' not in body:
        _fail("default_models_in_server", f"DEEP_MODEL_FALLBACK is not {DEFAULT_DEEP_MODEL}")
    # Deprecated id must not appear as a default anywhere in source
    if re.search(r'(?<![\w-])gemini-3-pro-preview(?![\w-])', body):
        _fail("default_models_in_server", "deprecated 'gemini-3-pro-preview' present in server.py")
    _ok("default_models_in_server")


def test_docs_mention_new_default_models():
    for rel in ("README.md", "docs/mcp-config-examples.md"):
        body = _read(ROOT / rel)
        if DEFAULT_FAST_MODEL not in body:
            _fail("docs_mention_new_default_models", f"{rel} missing {DEFAULT_FAST_MODEL}")
        if DEFAULT_DEEP_MODEL not in body:
            _fail("docs_mention_new_default_models", f"{rel} missing {DEFAULT_DEEP_MODEL}")
        if re.search(r'(?<![\w-])gemini-3-pro-preview(?![\w-])', body):
            _fail("docs_mention_new_default_models", f"{rel} still uses deprecated 'gemini-3-pro-preview'")
    _ok("docs_mention_new_default_models")


# ---------------------------------------------------------------------------
# 8. start.bat hardening
# ---------------------------------------------------------------------------

def test_start_bat_pythonpath_fallback():
    body = _read(ROOT / "start.bat")
    if "src\\video_url_analyzer_mcp" not in body or "PYTHONPATH" not in body:
        _fail("start_bat_pythonpath_fallback", "no PYTHONPATH src fallback")
    _ok("start_bat_pythonpath_fallback")


def test_start_bat_does_not_echo_api_key():
    body = _read(ROOT / "start.bat")
    # Look for any line that echoes the value of GEMINI_API_KEY (a literal
    # %GEMINI_API_KEY% expansion next to an echo would leak it).
    for lineno, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if stripped.lower().startswith("rem"):
            continue
        if "echo" in stripped.lower() and "%GEMINI_API_KEY%" in stripped:
            _fail("start_bat_does_not_echo_api_key", f"line {lineno} echoes GEMINI_API_KEY value")
    _ok("start_bat_does_not_echo_api_key")


def test_start_bat_avoids_start_command():
    body = _read(ROOT / "start.bat")
    for lineno, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if stripped.lower().startswith("rem") or stripped.lower().startswith(":"):
            continue
        # Catch a literal `start ...` invocation; allow `start.bat`, `:start`,
        # filenames, and the labels we use for error handling.
        if re.match(r'(?i)^start\s+(?!\.bat\b)', stripped):
            _fail("start_bat_avoids_start_command", f"line {lineno} uses Windows 'start' command")
    _ok("start_bat_avoids_start_command")


def test_start_bat_check_mode_runs():
    """Run the launcher in check mode and confirm it reports correctly without
    starting the MCP server."""
    bat = ROOT / "start.bat"
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = ""
    proc = subprocess.run(
        ["cmd", "/c", str(bat), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        _fail("start_bat_check_mode_runs", f"exit={proc.returncode}\n{out}")
    if "IMPORT_OK" not in out or "check mode: OK" not in out:
        _fail("start_bat_check_mode_runs", f"missing markers in output:\n{out}")
    if DEFAULT_FAST_MODEL not in out:
        _fail("start_bat_check_mode_runs", f"default model not surfaced:\n{out}")
    _ok("start_bat_check_mode_runs")


# ---------------------------------------------------------------------------
# 9. Runtime contract: tool count + import without GEMINI_API_KEY
# ---------------------------------------------------------------------------

def test_tool_count_remains_17():
    src_path = str((ROOT / "src").resolve())
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    # Import via importlib to avoid leaking module state between tests
    from video_url_analyzer_mcp import server as _server
    names = {tool.name for tool in asyncio.run(_server.mcp.list_tools())}
    if len(names) != 17:
        _fail("tool_count_remains_17", f"expected 17 tools, got {len(names)}: {sorted(names)}")
    _ok("tool_count_remains_17", "17 tools")


def test_import_without_api_key_subprocess():
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = ""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(ROOT / 'src')!r}); "
                "from video_url_analyzer_mcp import main; "
                "print('IMPORT_OK', callable(main))"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    if proc.returncode != 0 or "IMPORT_OK" not in proc.stdout:
        _fail(
            "import_without_api_key_subprocess",
            f"exit={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
    _ok("import_without_api_key_subprocess")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    test_required_files_exist()
    test_readme_uses_correct_repo()
    test_readme_drops_stale_owner()
    test_readme_drops_stale_install_commands()
    test_readme_documents_uvx_and_module()
    test_no_hardcoded_api_keys()
    test_examples_use_placeholders_only()
    test_gitignore_covers_sensitive_paths()
    test_start_bat_uses_module_entrypoint()
    test_installer_handles_key_securely()
    test_client_wizard_backs_up_and_validates()
    test_pyproject_version()
    test_package_dunder_version()
    test_default_models_in_server()
    test_docs_mention_new_default_models()
    test_start_bat_pythonpath_fallback()
    test_start_bat_does_not_echo_api_key()
    test_start_bat_avoids_start_command()
    test_start_bat_check_mode_runs()
    test_tool_count_remains_17()
    test_import_without_api_key_subprocess()
    print("ALL_PASS")


if __name__ == "__main__":
    main()
