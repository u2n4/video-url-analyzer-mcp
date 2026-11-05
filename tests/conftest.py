"""Test isolation: no Gemini key, no Gemini model overrides, no network.

Gemini behaviour is exercised only through fake clients. The Gemini variables
are removed at import time, before ``video_url_analyzer_mcp.server`` reads its
module-level model defaults, so a developer's shell cannot change results.
Outbound connections to anything but loopback are refused.
"""

from __future__ import annotations

import os
import socket

import pytest

GEMINI_ENV_VARS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "VIDEO_ANALYZER_MODEL",
    "GEMINI_FAST_MODEL",
    "GEMINI_DEEP_MODEL",
)

for _name in GEMINI_ENV_VARS:
    os.environ.pop(_name, None)

_real_connect = socket.socket.connect
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else str(address)
    if host not in _LOOPBACK_HOSTS:
        raise RuntimeError(f"network access blocked in unit tests: {host!r}")
    return _real_connect(self, address)


@pytest.fixture(autouse=True)
def _isolate_gemini_and_network(monkeypatch):
    for name in GEMINI_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    yield
