"""Static checks for the Windows installer / wizard deliverables.

Verifies file presence, README modernization, gitignore coverage, and the
absence of accidentally-committed API key material in the new files.
"""

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
    print("ALL_PASS")


if __name__ == "__main__":
    main()
