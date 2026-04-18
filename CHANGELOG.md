# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-04-17

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
- `.editorconfig` for consistent cross-editor formatting.
- `.gitattributes` enforcing LF line endings.
- `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1).
- `SECURITY.md` disclosure policy (GitHub private advisories).
- GitHub Actions CI workflow (ruff + pyright + uv build).
- PyPI Trusted Publisher publish workflow (OIDC, native `uv publish`).
- Dependabot weekly updates for pip + GitHub Actions.
- `<!-- mcp-name: io.github.u2n4/video-url-analyzer-mcp -->` marker in README for MCP registry discovery.
- Expanded `.env.example` with `ANALYSES_DIR` + cookies flag documentation.

### Changed
- `DEFAULT_MODEL` → `gemini-flash-latest` (alias tracking the latest stable
  Flash; currently Gemini 3 Flash). Fast (~1.3s overhead) with strong
  multimodal accuracy and free-tier availability.
- `_download_video` now returns `list[str]` (one entry for a video, many for
  a carousel). `_analyze_downloaded` uploads every slide and prompts Gemini
  with carousel context.
- `_cleanup` and `_check_download_sizes` accept list inputs;
  `_cleanup` signature widened to `str | list[str] | None` with `-> None` annotation.
- GitHub username migrated from `alihsh0` to `u2n4` across all URLs, author metadata, and clone commands.
- `GEMINI_API_KEY` check moved from module-import time to `main()` — no more import-time traceback for users without a key.
- Version bumped from 1.0.0 to 1.1.0.

### Fixed
- `ValueError: GEMINI_API_KEY environment variable is required` raised at package import — now a friendly error at runtime only.

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
