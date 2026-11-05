"""Version and registry metadata must agree with the package."""

from __future__ import annotations

import json
import re
from pathlib import Path

from video_url_analyzer_mcp import __version__

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    assert match, "pyproject.toml has no version line"
    return match.group(1)


def _registry_manifest() -> dict:
    return json.loads((ROOT / ".mcp" / "server.json").read_text(encoding="utf-8"))


def test_package_versions_agree():
    assert __version__ == _pyproject_version()


def test_registry_manifest_matches_package_version():
    manifest = _registry_manifest()
    assert manifest["version"] == _pyproject_version()
    (package,) = manifest["packages"]
    assert package["identifier"] == "video-url-analyzer-mcp"
    assert package["version"] == _pyproject_version()


def test_registry_marks_gemini_key_optional():
    (package,) = _registry_manifest()["packages"]
    env = {item["name"]: item for item in package["environmentVariables"]}
    assert env["GEMINI_API_KEY"]["isRequired"] is False
