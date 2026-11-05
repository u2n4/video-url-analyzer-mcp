# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Default models: fast `gemini-3.8-flash` (was `gemini-3.5-flash`) and deep (`detail="full"`) `gemini-4-argon` (was the preview `gemini-3.1-pro-preview`). Ids were checked against https://aistudio.google.com/docs/models on 2026-11-05. `start.bat` no longer sets a model id, so it overrides neither the deep default nor `VIDEO_ANALYZER_MODEL` from the environment or `.env`.
- `google-genai` is imported when a Gemini call is made. Importing the server no longer loads the SDK.
- A missing `GEMINI_API_KEY` returns a structured error (`gemini_api_key_missing`) that names the variable. The local tools run without a key.
- Dependencies: `fastmcp>=4.1.6,<5` (the version verified), `google-genai>=2.5.0,<3`, and `pydantic>=2.7,<3`, which the server imports directly. `uv.lock` is committed, and CI installs with `uv sync --locked`.
- The CI lint gate runs ruff's correctness rules (E4, E7, E9, F). Ruff's default set reports 107 style and broad-except findings on this code, and those kept the lint step red before pyright and the tests ran.
- The PyPI publish workflow runs the test job on the tagged commit, and it publishes only after that job passes. The workflow stays disabled in the repository settings.
- `.mcp/server.json` matches version 1.5.4, and `GEMINI_API_KEY` is marked optional there.
- Reused bounded parallel Gemini Files uploads in structured non-YouTube analysis paths (`find_video_moments`, `analyze_video_segment`, and `prepare_video_context`) so multi-file media keeps the v1.5.4 speedup outside the basic analysis tool too.

### Fixed
- The `execute_tutorial_steps` blocklist also checks the command with quotes removed, and blocks more inline-code forms: versioned and suffixed interpreters, options before `-c`, `perl -e`, `ruby -e`, `php -r`, `node -e`/`--eval`/`-p`, PowerShell `-Command` and `-EncodedCommand` prefixes, `cmd /c` and `/k`, and `sh`/`bash`/`zsh -c`. It is a speed bump, not a security boundary; review before `confirm=true` is the real gate.
- The pyright errors in `server.py` and `slideshow.py` are fixed: the ThinkingLevel enum is passed instead of a string, and the download strategies list is annotated.
- `execute_tutorial_steps` passes Windows commands to the program as typed, so backslashes and quoted arguments such as `--title="My Video"` survive. No shell is introduced.
- Tutorial file writes refuse forward-slash spellings of protected Windows locations.
- An unreadable saved context no longer breaks lookups. Saved contexts are written through a temp file that is unique to each save and replaced atomically, and a failed save removes its temp file.
- `list_video_sources` works on a fresh install, when the source and asset folders do not exist yet.
- Gemini uploads that end FAILED or never become ACTIVE are deleted.
- Oversized downloads raise a structured `download_too_large` error, so the yt-dlp fallback runs.
- Cache lookups no longer create empty source folders.
- `get_video_frame` and `get_video_clip` with `force_refresh` regenerate the asset without re-downloading the whole source.
- Synonym matching is whole-word, so `auto` no longer matches `automatically`.
- The TikTok `no_compatible_format` message includes the last yt-dlp error.
- Slideshow image and audio downloads check every redirect hop against the host allowlist.
- YouTube community fetches turn HTTP errors, including 429, into a `CommunityPostError`.
- Windows wizard: Codex TOML command values escape backslashes correctly. A stale `[mcp_servers.video-analyzer.env]` table is removed on re-run. Invalid Claude Desktop JSON stops the run instead of replacing the file. Config files are written without a BOM. The Microsoft Store config path is used when present. The printed `claude mcp add` command quotes the path. Python below 3.10 stops the installer. The key mask shows no characters of the key.
- `start.bat --check` reports the fast model it will use: the `GEMINI_FAST_MODEL` value when set, otherwise `VIDEO_ANALYZER_MODEL` or `gemini-3.8-flash`.
- README, CONTRIBUTING and llms-install.md no longer describe settings the server does not read, and the README data-directory defaults match the code.

### Testing
- The suite removes `GEMINI_API_KEY`, `GOOGLE_API_KEY` and the model override variables before the server is imported, and it refuses non-loopback network connections.
- Gemini paths are tested only through a fake client. Nothing in this release has been run against the live Gemini API. The model ids come from the official model page, and the thinking-level settings for `gemini-4-argon` are unverified against live calls.

## [1.5.4] - 2026-05-21

### Changed
- Replaced the Windows-heavy `python -m yt_dlp` subprocess path with the in-process `yt_dlp.YoutubeDL` Python API while preserving the existing download strategies.
- Added bounded parallel Gemini Files uploads for downloaded media and slideshow images while preserving original slide/file order.
- Added bounded parallel image downloads for TikTok, Instagram, and YouTube Community photo posts.
- Tightened runtime dependency bounds for the current yt-dlp/curl_cffi impersonation stack.

## [1.5.3] - 2026-05-20

### Fixed
- Server no longer crashes on startup with `PermissionError: [WinError 5]` when launched by GUI MCP clients (Claude Desktop, Cursor, etc.). Data directories now default to the platform user-data location via `platformdirs` instead of relative paths resolved against a protected working directory. Each path is still overridable via `ANALYSES_DIR`, `VIDEO_CONTEXT_DIR`, `VIDEO_SOURCE_DIR`, `VIDEO_ASSET_DIR`, or the new `VIDEO_ANALYZER_DATA_DIR` root.
- `install.ps1` now writes the absolute path to `uvx` for GUI clients (Claude Desktop, Codex, Cursor, Windsurf, VS Code, Cline, Antigravity) so the server resolves even when the client does not inherit the user `PATH`. Claude Code (CLI) keeps bare `uvx` since it inherits `PATH`.
- `install.ps1` now detects the Microsoft Store / MSIX build of Claude Desktop and writes to its packaged config path (`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude`) instead of only the classic `%APPDATA%\Claude`.

### Added
- `platformdirs` dependency for cross-platform user-data directory resolution.

## [1.5.2] - 2026-05-17

### Added
- `prepare_slideshow_assets` tool returns ordered slideshow images directly as MCP image blocks for client-side vision analysis.

### Fixed
- Instagram empty playlist photo posts now route to the slideshow fallback instead of returning `unknown`.
- Gemini slideshow prompts now label each uploaded image with its 1-based image index to reduce slide-order confusion.

## [1.5.1] - 2026-05-17

### Fixed
- Added mcp-name marker to README for MCP Registry ownership validation.

### Added
- Slideshow / Photo Post support: analyze_video and ask_about_video now handle TikTok Photo Mode, Instagram Carousel, and YouTube Community Posts.
- Multimodal analysis: all images + audio track sent to Gemini in a single request.
- get_transcript transcribes the audio of slideshow posts when present.
- watch_and_analyze returns image_index-keyed steps for slideshow posts.

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
