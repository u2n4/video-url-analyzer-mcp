# Video Analyzer MCP Server

## Project Purpose

MCP server for video/photo analysis using Google's Gemini API. Supports
YouTube (direct, no download), TikTok (videos + photo slideshows), and
Instagram (Reels, video posts, and photo carousels). Downloaded media is
uploaded to the Gemini Files API and analyzed with full visual (and, for
videos, audio) coverage.

## Architecture

- `server.py`: Main MCP server with 17 v1.4 tools: the original
  analysis/transcript/Q&A/tutorial tools, structured moment/segment tools,
  saved video context tools, local evidence asset tools, and cache tools.
- Pipelines:
  - **YouTube**: Direct analysis via `Part.from_uri()` (no download).
  - **TikTok/Instagram video**: Fast-path API/scrape → yt-dlp fallback →
    upload to Gemini Files API → analyze → cleanup.
  - **TikTok/Instagram photo carousel**: Scrape image URLs from page/API →
    download each image → upload ALL images to Gemini → analyze as a
    single carousel with ordered slides → cleanup.
- `_download_video(url)` returns `list[str]` (one path for a video, many
  for a carousel). `_analyze_downloaded` handles both transparently.
- YouTube frame/clip extraction has a fast local path using `yt-dlp -g`
  stream URLs + ffmpeg. If `source.mp4` is missing but a saved context
  has the original YouTube URL, asset tools can still extract frames/clips.
- **FastMCP** for MCP protocol (stdio transport).
- **google-genai** SDK for Gemini API interaction.

## Media download strategy

- TikTok photo posts: tikwm.com API returns `data.images[]` of image URLs.
- Instagram photo posts: page HTML is scraped; the `carousel_media` /
  `edge_sidecar_to_children` JSON block is isolated to avoid picking up
  thumbnails of suggested/related posts. Falls back to whole-page scan
  if the block isn't present. Hard cap at 20 unique images.
- Image downloads use `_download_media_url`: urllib (system DNS, 3
  retries) → curl_cffi impersonate fallback. urllib-first was added
  because curl_cffi's bundled resolver intermittently fails to resolve
  some `*.fna.fbcdn.net` CDN shards on Windows while system DNS works.
- All temp files deleted in a `finally` block; uploaded Gemini files are
  also deleted after the analysis call returns.

## Key Files

| File | Purpose |
|------|---------|
| `server.py` | Main server with all tools and platform logic |
| `requirements.txt` | Python dependencies (google-genai, fastmcp, python-dotenv, yt-dlp, curl_cffi) |
| `.env` | API key configuration (GEMINI_API_KEY) |
| `mcp-config.json` | Codex Desktop MCP server configuration |
| `test_urls.py` | Test script for video analysis |
| `test_photos.py` | Download-only test for TikTok/Instagram photo carousels |
| `test_analyze_photos.py` | Full end-to-end Gemini analysis for photo carousels (writes to `test_output/`) |
| `README.md` | Full documentation (bilingual Arabic/English) |

## Development Notes

- **SDK**: `google-genai` (NOT the older `google-generativeai` package).
- **Default model**: `gemini-flash-latest` — alias that tracks the latest
  stable Gemini Flash (currently Gemini 3 Flash). See `DEFAULT_MODEL` in
  server.py. Override per-call via the `model` kwarg.
- **Transport**: stdio (for Codex Desktop integration).
- **Temp files**: Always cleaned up after analysis in a `finally` block.
- **Platform detection**: Regex-based URL pattern matching for YouTube,
  TikTok, Instagram.
- **Error handling**: Bilingual error messages (Arabic + English).
- **Image carousel detection**: `_analyze_downloaded` inspects file
  extensions — if all paths end in jpg/jpeg/png/webp AND there is more
  than one file, the prompt is wrapped with carousel context.
- **Analysis config**: `_build_analysis_config()` enables
  `MEDIA_RESOLUTION_HIGH` + `ThinkingLevel.HIGH` on every Gemini call,
  closing most of the Flash↔Pro quality gap on small-text / fine-detail
  images (e.g. benchmark tables in carousel slides) without the Pro-tier
  billing requirement.
- **Timeout knobs**: `VIDEO_DOWNLOAD_TIMEOUT` and `VIDEO_FFMPEG_TIMEOUT`
  override existing per-operation defaults in seconds. `VIDEO_GEMINI_TIMEOUT`
  optionally sets the Gemini HTTP timeout in seconds.
- **Gemini retry**: Gemini `generate_content` calls retry transient 5xx/503 server
  errors up to 3 attempts with exponential backoff. 4xx auth/schema/user
  errors are not retried.
- **Operating modes**: `auto` is the default adaptive behavior; `api`
  means Gemini-backed analysis/reanalysis; `client` means MCP background
  job workflow for long client calls; `local` means saved context/cache/
  frame/clip operations without Gemini unless explicitly requested.

## Tool Signatures

### analyze_video(url, prompt=None, model=DEFAULT_MODEL) -> str
Full visual (+ audio for videos) analysis of a video OR a photo carousel.
Non-YouTube URLs return a `job_id` and run in the background; poll with
`check_analysis_job`.

### get_transcript(url, lang="auto", model=DEFAULT_MODEL) -> str
Transcript with timestamps. Background-jobbed for non-YouTube.

### ask_about_video(url, question, model=DEFAULT_MODEL) -> str
Custom question. Background-jobbed for non-YouTube.

### find_video_moments(url, query, max_results=5, context_seconds=15, detail="compact", model=None, return_full_text=False) -> str
Find semantic moments using Gemini structured output.

### analyze_video_segment(url, start, end, prompt="Analyze this segment in detail.", detail="compact", model=None, return_full_text=False) -> str
Analyze a selected time range using Gemini video metadata when available.

### prepare_video_context(url, detail="standard", force_refresh=False, model=None, chunk_seconds=30) -> str
Analyze once and save a reusable local context. Reuses cached context unless
`force_refresh=True`.

### ask_video_context(video_id_or_url, question, reanalyze_if_needed=False, use_gemini=False, detail="compact") -> str
Answer from saved context locally by default. Gemini is explicit opt-in.

### list_video_contexts(filter_text=None, limit=20) -> str
List saved local video contexts.

### delete_video_context(video_id_or_url) -> str
Delete one saved local context.

### get_video_frame(video_id_or_url, timestamp, reason=None, output_format="jpg", force_refresh=False) -> str
Extract a local still frame. YouTube can use the stream URL + ffmpeg fast path.

### get_video_clip(video_id_or_url, start, end, reason=None, max_duration_seconds=30, output_format="mp4", force_refresh=False) -> str
Extract a local short clip. YouTube can use the stream URL + ffmpeg fast path.

### get_video_evidence_asset(video_id_or_url, request, asset_type="frame", preferred_timestamp=None, preferred_start=None, preferred_end=None, max_duration_seconds=30) -> str
Find saved evidence and return a local frame/clip reference.

### list_video_sources(filter_text=None, limit=50) -> str
List local source/context/asset cache metadata.

### cleanup_video_cache(scope="sources", dry_run=True, video_id=None) -> str
Inspect or clean managed cache files. Dry-run is the default.

### watch_and_analyze(url, lang="auto", model=DEFAULT_MODEL) -> str
Structured tutorial JSON extraction (commands, file paths, snippets).

### execute_tutorial_steps(steps_json, confirm=False) -> str
Review (default) or execute the steps from `watch_and_analyze`.

### check_analysis_job(job_id) -> str
Poll background jobs created by the async tools above.

## Platform URL Patterns

- **YouTube**: `youtube.com`, `youtu.be`, `youtube.com/shorts/`
- **TikTok**: `tiktok.com`, `vm.tiktok.com`, `vt.tiktok.com` (videos AND
  photo slideshows)
- **Instagram**: `instagram.com/reels/`, `instagram.com/reel/`,
  `instagram.com/p/` (Reels, video posts, AND photo carousels)

## Dependencies

- `google-genai` — Google Generative AI SDK
- `fastmcp` — MCP protocol framework
- `python-dotenv` — Environment variable loading from .env
- `yt-dlp` — Video downloader fallback for TikTok and Instagram
- `curl_cffi` — TLS impersonation for page scraping + image-download fallback
