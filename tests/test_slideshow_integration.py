from __future__ import annotations

import os
from pathlib import Path

import pytest

from video_url_analyzer_mcp.slideshow import detect_post_type, download_slideshow


TEST_URLS = {
    # TODO: Fill with stable public posts before enabling network CI.
    "tiktok": None,
    "instagram": None,
    "youtube_community": None,
}

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("VIDEO_ANALYZER_RUN_NETWORK_TESTS") != "1",
        reason="network tests are skipped unless VIDEO_ANALYZER_RUN_NETWORK_TESTS=1",
    ),
]


@pytest.mark.parametrize("platform", ["tiktok", "instagram", "youtube_community"])
def test_download_slideshow_real_network(platform: str, tmp_path: Path):
    url = TEST_URLS[platform]
    if not url:
        pytest.skip(f"No stable public {platform} test URL configured yet.")

    before = {path.name for path in tmp_path.iterdir()}
    post_type = detect_post_type(url)
    slideshow = download_slideshow(url, tmp_path, post_type)

    assert slideshow["images"]
    assert all(Path(path).exists() for path in slideshow["images"])
    assert slideshow["audio"] is None or Path(slideshow["audio"]).exists()

    for path in [*slideshow["images"], slideshow["audio"]]:
        if path:
            Path(path).unlink(missing_ok=True)

    after = {path.name for path in tmp_path.iterdir()}
    assert after == before
