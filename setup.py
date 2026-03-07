#!/usr/bin/env python3
"""
Video Analyzer MCP Server -- Cross-Platform Setup Script

Detects OS, installs dependencies, configures .env, and registers the
MCP server with Claude Desktop automatically.

Requirements: Python 3.10+ (stdlib only -- no external deps needed).
"""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERVER_NAME = "video-analyzer"
SCRIPT_DIR = Path(__file__).resolve().parent
SERVER_PY = SCRIPT_DIR / "server.py"
START_BAT = SCRIPT_DIR / "start.bat"
REQUIREMENTS = SCRIPT_DIR / "requirements.txt"
ENV_FILE = SCRIPT_DIR / ".env"
ENV_EXAMPLE = SCRIPT_DIR / ".env.example"

SYSTEM = platform.system()  # "Windows", "Darwin", "Linux"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def heading(text: str) -> None:
    """Print a section heading."""
    width = 60
    print()
    print("=" * width)
    print(f"  {text}")
    print("=" * width)
    print()


def info(msg: str) -> None:
    print(f"  [INFO]  {msg}")


def ok(msg: str) -> None:
    print(f"  [ OK ]  {msg}")


def warn(msg: str) -> None:
    print(f"  [WARN]  {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL]  {msg}")


def detect_os_label() -> str:
    labels = {
        "Windows": "Windows",
        "Darwin": "macOS",
        "Linux": "Linux",
    }
    return labels.get(SYSTEM, SYSTEM)


def get_claude_desktop_config_path() -> Path:
    """Return the platform-specific path to claude_desktop_config.json."""
    if SYSTEM == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            fail("APPDATA environment variable is not set.")
            sys.exit(1)
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    elif SYSTEM == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:
        return Path.home() / ".config" / "claude" / "claude_desktop_config.json"


def find_python() -> str:
    """Return the python executable path (prefer python3 on non-Windows)."""
    if SYSTEM == "Windows":
        return sys.executable
    for candidate in ("python3", "python"):
        path = shutil.which(candidate)
        if path:
            return path
    return sys.executable


def read_env_key(env_path: Path, key: str) -> str:
    """Read a value from a simple KEY=VALUE .env file."""
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, v = stripped.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


# ---------------------------------------------------------------------------
# Step 1: Detect OS
# ---------------------------------------------------------------------------

def step_detect_os() -> None:
    heading("Step 1 / 6 -- Detect Operating System")
    label = detect_os_label()
    ok(f"Operating system: {label} ({platform.platform()})")
    ok(f"Python: {sys.version}")
    ok(f"Project directory: {SCRIPT_DIR}")


# ---------------------------------------------------------------------------
# Step 2: Install dependencies
# ---------------------------------------------------------------------------

def step_install_deps() -> None:
    heading("Step 2 / 6 -- Install Python Dependencies")

    if not REQUIREMENTS.exists():
        fail(f"requirements.txt not found at {REQUIREMENTS}")
        sys.exit(1)

    python = find_python()
    info(f"Using Python: {python}")
    info("Running pip install ...")

    cmd = [python, "-m", "pip", "install", "-r", str(REQUIREMENTS)]

    # On Linux, pip may refuse to install into a system Python without this.
    # It is harmless on other platforms / venvs.
    if SYSTEM == "Linux":
        cmd.append("--break-system-packages")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            # Retry without --break-system-packages in case the flag itself
            # is not supported on this pip version.
            cmd_retry = [python, "-m", "pip", "install", "-r", str(REQUIREMENTS)]
            result = subprocess.run(
                cmd_retry,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                fail("pip install failed. Output:")
                print(result.stdout)
                print(result.stderr)
                sys.exit(1)

        ok("All dependencies installed successfully.")
    except FileNotFoundError:
        fail("pip is not available. Please install pip and try again.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        fail("pip install timed out after 5 minutes.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Step 3: Create .env
# ---------------------------------------------------------------------------

def step_setup_env() -> None:
    heading("Step 3 / 6 -- Configure Environment (.env)")

    if ENV_FILE.exists():
        existing_key = read_env_key(ENV_FILE, "GEMINI_API_KEY")
        if existing_key and existing_key != "your_gemini_api_key_here":
            ok(".env already exists with a GEMINI_API_KEY. Skipping.")
            return
        else:
            warn(".env exists but GEMINI_API_KEY is not set.")
    else:
        info(".env not found. Creating from .env.example ...")

    print()
    print("  You need a Gemini API key to use this server.")
    print("  Get one free at: https://aistudio.google.com/apikey")
    print()

    api_key = input("  Enter your GEMINI_API_KEY (or press Enter to skip): ").strip()

    if not api_key:
        warn("No key entered. Copying .env.example as-is.")
        warn("You will need to edit .env manually before running the server.")
        if ENV_EXAMPLE.exists():
            ENV_FILE.write_text(
                ENV_EXAMPLE.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        else:
            ENV_FILE.write_text("GEMINI_API_KEY=your_gemini_api_key_here\n", encoding="utf-8")
    else:
        ENV_FILE.write_text(f"GEMINI_API_KEY={api_key}\n", encoding="utf-8")
        ok("GEMINI_API_KEY saved to .env")


# ---------------------------------------------------------------------------
# Step 4: Build the MCP server config block
# ---------------------------------------------------------------------------

def build_server_config() -> dict:
    """Build the MCP server entry for Claude Desktop config."""
    gemini_key = read_env_key(ENV_FILE, "GEMINI_API_KEY")

    env_block = {}
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        env_block["GEMINI_API_KEY"] = gemini_key

    if SYSTEM == "Windows":
        return {
            "command": str(START_BAT),
            "args": [],
            "env": env_block,
        }
    else:
        return {
            "command": find_python(),
            "args": [str(SERVER_PY)],
            "env": env_block,
        }


# ---------------------------------------------------------------------------
# Step 5: Configure Claude Desktop
# ---------------------------------------------------------------------------

def step_configure_claude_desktop() -> None:
    heading("Step 4 / 6 -- Configure Claude Desktop")

    config_path = get_claude_desktop_config_path()
    info(f"Config path: {config_path}")

    server_entry = build_server_config()

    # Check if Claude Desktop is installed (config dir exists or can be created)
    config_dir = config_path.parent
    if not config_dir.exists():
        warn(f"Claude Desktop config directory not found: {config_dir}")
        info("Claude Desktop may not be installed.")
        print()
        answer = input("  Create the directory and config anyway? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            warn("Skipping Claude Desktop configuration.")
            return
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            ok(f"Created directory: {config_dir}")
        except OSError as e:
            fail(f"Could not create directory: {e}")
            return

    # Load existing config or start fresh
    existing_config = {}
    if config_path.exists():
        try:
            raw = config_path.read_text(encoding="utf-8-sig")  # handles BOM if present
            existing_config = json.loads(raw) if raw.strip() else {}
            ok("Loaded existing Claude Desktop config.")
        except (json.JSONDecodeError, OSError) as e:
            warn(f"Could not parse existing config: {e}")
            warn("A backup will be created.")
            backup = config_path.with_suffix(".json.bak")
            try:
                shutil.copy2(config_path, backup)
                ok(f"Backup saved to: {backup}")
            except OSError:
                pass
            existing_config = {}

    # Merge -- preserve all existing servers
    if "mcpServers" not in existing_config:
        existing_config["mcpServers"] = {}

    existing_config["mcpServers"][SERVER_NAME] = server_entry

    # Write WITHOUT UTF-8 BOM (critical for Windows)
    try:
        json_text = json.dumps(existing_config, indent=2, ensure_ascii=False)
        config_path.write_bytes(json_text.encode("utf-8"))  # no BOM
        ok(f"Claude Desktop config updated: {config_path}")
        ok(f"Server \"{SERVER_NAME}\" registered successfully.")
    except OSError as e:
        fail(f"Could not write config: {e}")
        print()
        info("You can manually add this to your Claude Desktop config:")
        manual = {"mcpServers": {SERVER_NAME: server_entry}}
        print(json.dumps(manual, indent=2))


# ---------------------------------------------------------------------------
# Step 6: Show Claude Code command
# ---------------------------------------------------------------------------

def step_show_claude_code_command() -> None:
    heading("Step 5 / 6 -- Claude Code Setup (CLI Users)")

    gemini_key = read_env_key(ENV_FILE, "GEMINI_API_KEY")
    env_flag = ""
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        env_flag = f" -e GEMINI_API_KEY={gemini_key}"

    if SYSTEM == "Windows":
        command = str(START_BAT)
    else:
        command = f"{find_python()} {SERVER_PY}"

    print("  If you use Claude Code (CLI), run this command to add the server:")
    print()
    print(f"    claude mcp add {SERVER_NAME}{env_flag} -- {command}")
    print()


# ---------------------------------------------------------------------------
# Step 7: Success message
# ---------------------------------------------------------------------------

def step_success() -> None:
    heading("Step 6 / 6 -- Setup Complete")

    print("  The Video Analyzer MCP server has been set up successfully.")
    print()
    print("  What was configured:")
    print(f"    - Dependencies installed from requirements.txt")
    print(f"    - Environment file: {ENV_FILE}")
    print(f"    - Claude Desktop config: {get_claude_desktop_config_path()}")
    print(f"    - Server name: {SERVER_NAME}")
    print()
    print("  To test the server manually:")
    if SYSTEM == "Windows":
        print(f"    cd \"{SCRIPT_DIR}\" && start.bat")
    else:
        print(f"    cd \"{SCRIPT_DIR}\" && python server.py")
    print()
    print("  Restart Claude Desktop to load the new MCP server.")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    heading("Video Analyzer MCP Server -- Setup")
    print(f"  Project: {SCRIPT_DIR}")
    print(f"  Python:  {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    if sys.version_info < (3, 10):
        fail("Python 3.10 or newer is required.")
        sys.exit(1)

    step_detect_os()
    step_install_deps()
    step_setup_env()
    step_configure_claude_desktop()
    step_show_claude_code_command()
    step_success()


if __name__ == "__main__":
    main()
