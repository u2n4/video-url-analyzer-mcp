# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] — 2026-04-17

### Added
- Instagram and TikTok **photo carousel** analysis — previously videos only.
  `analyze_video`, `get_transcript`, `ask_about_video`, and `watch_and_analyze`
  now work on `/p/<id>` carousels and TikTok photo slideshows.
- `_extract_instagram_carousel_block` helper — uses brace-balanced JSON
  scoping to isolate the main post's `carousel_media` / `edge_sidecar_to_children`
  array, avoiding thumbnails of suggested posts.
- `_download_media_url` — urllib-first (system DNS, 3 retries) with
  `curl_cffi` fallback. Fixes intermittent DNS failures on `*.fna.fbcdn.net`
  shards observed with curl_cffi's bundled resolver on Windows.
- `_build_analysis_config()` — sets `MEDIA_RESOLUTION_HIGH` and
  `ThinkingLevel.HIGH` on every Gemini call so small on-screen text
  (captions, benchmark tables in carousel slides) remains readable.

### Changed
- `DEFAULT_MODEL` → `gemini-flash-latest` (alias tracking the latest stable
  Flash; currently Gemini 3 Flash). Fast (~1.3s overhead) with strong
  multimodal accuracy and free-tier availability.
- `_download_video` now returns `list[str]` (one entry for a video, many for
  a carousel). `_analyze_downloaded` uploads every slide and prompts Gemini
  with carousel context.
- `_cleanup` and `_check_download_sizes` accept list inputs.

## [1.0.0] - 2026-03-07

### Added
- Full video analysis with audio + visual processing via Gemini API
- YouTube direct analysis (no download needed)
- TikTok and Instagram async job pattern with yt-dlp
- 6 MCP tools: analyze_video, get_transcript, ask_about_video, watch_and_analyze, execute_tutorial_steps, check_analysis_job
- Smart Windows launcher (start.bat) with auto Python detection
- One-click PowerShell installer
- Security features: SSRF protection, download limits, URL validation
- Bilingual support (Arabic + English)
