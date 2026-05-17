<!-- mcp-name: io.github.u2n4/video-url-analyzer-mcp -->
<p align="center">
  <img src="assets/banner.png" alt="Video Analyzer MCP Server" width="100%">
</p>

<h1 align="center">Video Analyzer MCP Server</h1>

<p align="center">
  <strong>Analyze any video with AI — YouTube, TikTok & Instagram</strong><br>
  Powered by <a href="https://ai.google.dev/gemini-api">Google Gemini 2.5 Flash</a> &middot; <a href="https://github.com/jlowin/fastmcp">FastMCP</a> &middot; <a href="https://github.com/yt-dlp/yt-dlp">yt-dlp</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Gemini-2.5_Flash-4285F4?style=for-the-badge&logo=google&logoColor=white" alt="Gemini 2.5 Flash">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/MCP-stdio-00C853?style=for-the-badge" alt="MCP stdio">
  <img src="https://img.shields.io/badge/Security-Hardened-8E24AA?style=for-the-badge&logo=shieldsdotio&logoColor=white" alt="Security Hardened">
  <img src="https://img.shields.io/badge/License-MIT-F57C00?style=for-the-badge" alt="MIT License">
</p>

<p align="center">
  <a href="#features">Features</a> &middot;
  <a href="#architecture">Architecture</a> &middot;
  <a href="#quick-start">Quick Start</a> &middot;
  <a href="#integration">Integration</a> &middot;
  <a href="#security">Security</a> &middot;
  <a href="#%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9">العربية</a>
</p>

---

## Features

<p align="center">
  <img src="assets/features.png" alt="Features — Analyze, Transcript, Ask" width="80%">
</p>

v1.4 exposes **17 MCP tools**:

| Tool | What it does |
|------|-------------|
| `analyze_video` | Full audio + visual analysis with custom prompts. Uses Gemini multimodal understanding. |
| `get_transcript` | Extract timestamped transcript with speaker identification. Supports 100+ languages via auto-detection. |
| `ask_about_video` | Ask any question about the video content. |
| `find_video_moments` | Find matching moments with compact/standard/full structured output. |
| `analyze_video_segment` | Analyze only a selected time range. |
| `prepare_video_context` | Analyze once and save a reusable local context for follow-up questions and evidence lookup. |
| `ask_video_context` | Answer from saved context by default; Gemini reanalysis is explicit opt-in. |
| `list_video_contexts` | List saved local contexts. |
| `delete_video_context` | Delete one saved context. |
| `get_video_frame` | Extract a local still frame, using the YouTube fast path when possible. |
| `get_video_clip` | Extract a short local clip, with a 30s cap and YouTube fast path when possible. |
| `get_video_evidence_asset` | Select saved evidence and return a frame or clip reference. |
| `list_video_sources` | Inspect local source/context/asset cache metadata. |
| `cleanup_video_cache` | Dry-run or clean managed cache files; dry-run is the default. |
| `watch_and_analyze` | Extract tutorial steps, shell commands, code snippets, and file paths from technical videos. |
| `execute_tutorial_steps` | Validate and review extracted steps safely; executes only with confirmation. |
| `check_analysis_job` | Poll background job status for TikTok/Instagram async downloads. |

### Supported Platforms

| Platform | Method | Speed |
|----------|--------|-------|
| **YouTube** | Direct Gemini analysis — no download needed for analysis. Frame/clip extraction uses `yt-dlp -g` stream URLs + ffmpeg when available, with a small section-download fallback. | Instant / fast local extraction |
| **TikTok** | tikwm.com API (fast) &#8594; yt-dlp fallback with a safe MP4 H.264/H.265 selector that avoids unsupported ByteDance `bvc2`/`bytevc2` streams when possible | ~8s |
| **Instagram** | Page scrape via curl_cffi (fast) &#8594; yt-dlp fallback | ~10s |

#### Photo / Slideshow Posts

| Platform | Post type | Handling |
|----------|-----------|----------|
| **TikTok** | Photo Mode slideshow | Downloads all images, extracts the music/audio track when available, and sends one multimodal Gemini request. |
| **Instagram** | Carousel / single-photo posts | Downloads image slides in order and analyzes them as one post; mixed video slides are skipped with metadata. |
| **YouTube** | Community `/post/` image attachments | Scrapes `ytInitialData`, downloads image attachments, and analyzes them through Gemini Files API. |

> YouTube videos are analyzed directly through Gemini's native video understanding — zero download, zero upload, maximum speed.
> For YouTube evidence assets, `source.mp4` is not always required: saved contexts keep the original URL, so frame/clip tools can use the local stream fast path later.

---

## Architecture

<p align="center">
  <img src="assets/architecture.png" alt="Architecture Diagram" width="85%">
</p>

| Component | Role |
|-----------|------|
| **Gemini 2.5 Flash** | Latest multimodal model — full audio + visual understanding in a single pass |
| **FastMCP 3.x** | MCP protocol framework over stdio transport |
| **yt-dlp + curl_cffi** | Video download with Chrome browser impersonation to bypass anti-bot. TikTok downloads prefer locally decodable MP4 H.264/H.265 formats and reject ByteDance `bvc2`/`bytevc2` streams. |
| **tikwm.com API** | TikTok fast-path fallback when yt-dlp is WAF-blocked |
| **ffmpeg** | Local frame/clip extraction, including YouTube stream URL extraction without full-video downloads |
| **Background Jobs** | Async threading for TikTok/Instagram to prevent Claude Desktop timeouts. Failed inner results are reported as failed jobs, not completed jobs. |

### Operating Modes

These are behavior modes, not tool names:

| Mode | Behavior |
|------|----------|
| `auto` | Default adaptive behavior: YouTube direct analysis; TikTok/Instagram fast API/scrape then yt-dlp fallback; saved contexts reused when available. |
| `api` | Gemini-backed analysis or reanalysis. This is used by analysis tools and explicit Gemini opt-ins. |
| `client` | MCP client workflow mode: long TikTok/Instagram analysis returns a background `job_id` for polling. |
| `local` | Saved-context, cache, frame, and clip operations that avoid Gemini unless explicitly requested. |

---

## Quick Start

The packaged runtime lives at `src/video_url_analyzer_mcp/server.py`; the
console entry point is `video_url_analyzer_mcp:main`. Pick whichever
install path matches your workflow.

### Option A — `uvx` (no install)

```powershell
uvx video-url-analyzer-mcp
```

Works on Windows / macOS / Linux as long as [uv](https://docs.astral.sh/uv/) is on PATH.

### Option B — Windows wizard (recommended for new users)

```powershell
git clone https://github.com/u2n4/video-url-analyzer-mcp.git
cd video-url-analyzer-mcp
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

The wizard:

- Verifies Python 3.10+ and (optionally) `uv`.
- Installs the package as an editable checkout or via `uv tool install`.
- Lets you save `GEMINI_API_KEY` to a User environment variable **or** to a
  gitignored `.env.keys.local`. Keys are masked in logs (`AIza...abcd`),
  never printed in full.
- Lets you pick a default model via `VIDEO_ANALYZER_MODEL`.
- Runs an offline import smoke test.
- Hands off to `scripts/configure_mcp_clients.ps1` for client setup.

### Option C — Manual install (any OS)

```bash
git clone https://github.com/u2n4/video-url-analyzer-mcp.git
cd video-url-analyzer-mcp
pip install -e .
python -m video_url_analyzer_mcp
```

Get a free key at [Google AI Studio](https://aistudio.google.com/apikey),
then set it once in your shell environment (`setx GEMINI_API_KEY ...` on
Windows, or your `~/.zshrc` / `~/.bashrc` on macOS/Linux). For local dev
only, you can also drop it in a gitignored `.env.keys.local` next to the
repo.

### Windows launcher

`start.bat` loads `.env.keys.local` (if present), sets safe defaults
(`VIDEO_ANALYZER_MODE=auto`), and runs `python -m video_url_analyzer_mcp`.
Use it as a `command` value in any MCP client that prefers a single
executable.

---

## Integration

Run `scripts/configure_mcp_clients.ps1` for an interactive menu, or copy
the snippets below from `docs/mcp-config-examples.md`. The wizard backs up
existing client config, validates JSON/TOML, and never overwrites unrelated
MCP servers.

### Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "video-url-analyzer": {
      "command": "uvx",
      "args": ["video-url-analyzer-mcp"]
    }
  }
}
```

The wizard prefers leaving the API key in your OS environment. If you must
embed it, add `"env": { "GEMINI_API_KEY": "YOUR_KEY_HERE" }`.

### Claude Code (CLI)

```bash
claude mcp add video-url-analyzer --transport stdio -- uvx video-url-analyzer-mcp
```

Or, after `pip install -e .` in the repo:

```bash
claude mcp add video-url-analyzer --transport stdio -- python -m video_url_analyzer_mcp
```

### Codex CLI (`~/.codex/config.toml`)

```toml
[mcp_servers.video-url-analyzer]
command = "uvx"
args = ["video-url-analyzer-mcp"]
```

### VS Code / GitHub Copilot MCP

Workspace registration writes `.vscode/mcp.json`:

```json
{
  "servers": {
    "video-url-analyzer": {
      "command": "uvx",
      "args": ["video-url-analyzer-mcp"]
    }
  }
}
```

For all clients, restart the application after editing config so the new
MCP server is picked up. Full snippets — including Cursor, Windsurf, and
Antigravity — live in [`docs/mcp-config-examples.md`](docs/mcp-config-examples.md).

---

## Usage Examples

```python
# Full video analysis with Gemini 2.5 Flash
analyze_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

# Custom analysis prompt
analyze_video("https://www.tiktok.com/@user/video/123",
              prompt="List every product shown and estimate prices")

# Multilingual transcript extraction
get_transcript("https://www.instagram.com/reel/ABC123/", lang="ar")

# Ask specific questions about video content
ask_about_video("https://youtu.be/abc",
                question="What programming language is used in the tutorial?")

# Analyze once, then ask locally from the saved context
prepare_video_context("https://youtu.be/abc", detail="standard")
ask_video_context("https://youtu.be/abc",
                  question="What happens near the end?")

# Extract visual evidence without embedding binary data in the tool output
get_video_frame("https://youtu.be/abc", timestamp="00:30")
get_video_clip("https://youtu.be/abc", start="00:20", end="00:28")

# Watch & build — extract tutorial steps
watch_and_analyze("https://www.youtube.com/watch?v=tutorial123")
```

---

## Security

This server has been hardened against a comprehensive threat model audit:

| Layer | Protection |
|-------|-----------|
| **SSRF** | URL allowlist — only YouTube, TikTok, Instagram domains accepted. Private IPs, localhost, `file://` blocked. |
| **Command Injection** | `shell=False` + `shlex.split()`. Dangerous command blocklist (rm -rf, reverse shells, eval, pipe-to-shell). |
| **Path Traversal** | 25+ sensitive path patterns blocked (`.ssh`, `.aws`, `.env`, system dirs, AppData). |
| **TLS** | Full certificate validation on all downloads. |
| **Browser Cookies** | Opt-in only via `VIDEO_ANALYZER_COOKIES=true`. Disabled by default. |
| **Download Size** | Hard limit of 100 MB per video. |
| **DoS Protection** | Max 10 concurrent background jobs. Auto-expiry after 1 hour. Storage cap of 200 analyses. |
| **Schema Validation** | Gemini JSON responses validated before execution. Response size capped at 500K chars. |
| **Dependencies** | All versions pinned in `requirements.txt`. |

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Google Gemini API key | **Required** |
| `ANALYSES_DIR` | Directory for saved analyses | `./analyses` |
| `VIDEO_CONTEXT_DIR` | Directory for saved reusable video contexts | `video_contexts` |
| `VIDEO_SOURCE_DIR` | Directory for cached downloaded source media | `video_sources` |
| `VIDEO_ASSET_DIR` | Directory for extracted frames/clips | `video_assets` |
| `VIDEO_ANALYZER_COOKIES` | Enable browser cookie access (`true`/`false`) | `false` |
| `VIDEO_DOWNLOAD_TIMEOUT` | Override download and yt-dlp timeout values, in seconds | Existing per-operation defaults |
| `VIDEO_FFMPEG_TIMEOUT` | Override ffmpeg extraction timeout values, in seconds | Existing per-operation defaults |
| `VIDEO_GEMINI_TIMEOUT` | Optional Gemini HTTP timeout, in seconds | SDK default |
| `VIDEO_ANALYZER_MODEL` | Single-knob default model id used for both fast and deep model resolution. Per-call `model=` and `GEMINI_FAST_MODEL` / `GEMINI_DEEP_MODEL` still take precedence. | _unset_ |
| `GEMINI_FAST_MODEL` | Override for compact/standard detail mode | `gemini-3.1-flash-lite-preview` |
| `GEMINI_DEEP_MODEL` | Override for `full` detail mode | `gemini-3.1-pro-preview` |

> Default fast model: `gemini-3.1-flash-lite-preview`. Default deep model
> (used when `detail="full"`): `gemini-3.1-pro-preview`. Set
> `VIDEO_ANALYZER_MODEL` to override both at once. Model availability can
> vary by Google account, region, and API tier — fall back to
> `gemini-flash-latest` if the 3.1 preview ids are not yet enabled for your
> key.
| `VIDEO_ANALYZER_MODE` | Behavior preset (`auto` / `api` / `client` / `local`); read by `start.bat` | `auto` |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `GEMINI_API_KEY not set` | Create `.env` file or pass via environment variable |
| TikTok download fails | tikwm.com fallback activates automatically. Ensure `curl_cffi` is installed. |
| TikTok `bvc2` / `bytevc2` codec | v1.4 avoids these unsupported ByteDance streams when a compatible MP4 H.264/H.265 format exists. If no compatible stream is available, tools return a structured `unsupported_codec` or `no_compatible_format` error instead of uploading or pretending success. |
| Instagram download fails | `pip install curl_cffi` for browser impersonation support |
| `ENOENT` on Windows | Use `start.bat` as command in Claude Desktop config |
| Impersonate target not available | Reinstall: `pip install "yt-dlp[curl-cffi]"` |
| Claude Desktop timeout | TikTok/Instagram run in background — use `check_analysis_job(job_id)` to poll |
| Background job says failed | Expected when the worker result is an error, Gemini file processing fails, or media validation rejects the source. `check_analysis_job` no longer reports these inner failures as completed. |
| `prepare_video_context` repeats work | It reuses cached contexts unless `force_refresh=true` |
| YouTube `source.mp4` missing | Expected in some cases. Frame/clip tools can recover from the saved URL and use `yt-dlp -g` + ffmpeg fast extraction. |
| Transient Gemini 503/5xx | Gemini `generate_content` calls retry up to 3 times with exponential backoff. 4xx auth/schema/user errors are not retried. |

---

## Tech Stack

| Technology | Version | Purpose |
|-----------|---------|---------|
| [Google Gemini 2.5 Flash](https://ai.google.dev/gemini-api) | Latest | Multimodal video analysis engine |
| [FastMCP](https://github.com/jlowin/fastmcp) | 3.1.0 | MCP protocol framework |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 2026.3.3 | Video downloader |
| [curl_cffi](https://github.com/lexiforest/curl_cffi) | 0.14.0 | Browser impersonation (TLS fingerprint) |
| [google-genai](https://pypi.org/project/google-genai/) | 1.66.0 | Official Google GenAI SDK |

---

## License

MIT

---

<div dir="rtl">

## العربية

<p align="center">
  <img src="assets/banner.png" alt="خادم تحليل الفيديو" width="100%">
</p>

### خادم تحليل الفيديو بالذكاء الاصطناعي

خادم MCP لتحليل الفيديو باستخدام **Google Gemini 2.5 Flash** — احدث واقوى نموذج ذكاء اصطناعي متعدد الوسائط من جوجل.

### المميزات

| الاداة | الوصف |
|--------|-------|
| `analyze_video` | تحليل شامل للصوت والصورة مع دعم الاوامر المخصصة |
| `get_transcript` | استخراج النص المنطوق مع الطوابع الزمنية — يدعم +100 لغة |
| `ask_about_video` | اسال اي سؤال عن محتوى الفيديو |
| `prepare_video_context` | تحليل الفيديو مرة واحدة وحفظ سياق محلي قابل لاعادة الاستخدام |
| `ask_video_context` | الاجابة من السياق المحفوظ بدون Gemini افتراضيا |
| `get_video_frame` / `get_video_clip` | استخراج صورة او مقطع كدليل محلي، مع مسار سريع ليوتيوب عبر yt-dlp + ffmpeg |
| `watch_and_analyze` | استخراج خطوات الشروحات التقنية والاوامر والاكواد |
| `execute_tutorial_steps` | مراجعة وتنفيذ الخطوات المستخرجة بامان |

### المنصات المدعومة

| المنصة | السرعة |
|--------|--------|
| **يوتيوب** | فوري — تحليل مباشر بدون تحميل |
| **تيك توك** | ~8 ثواني — واجهة tikwm.com السريعة |
| **انستقرام** | ~10 ثواني — استخراج مباشر من الصفحة |

### التثبيت السريع (ويندوز)

استنسخ الريبو ثم شغل المعالج التفاعلي:

```powershell
git clone https://github.com/u2n4/video-url-analyzer-mcp.git
cd video-url-analyzer-mcp
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

البديل بدون تثبيت: `uvx video-url-analyzer-mcp`.

### الامان

الخادم محمي ضد:
- **SSRF** — قائمة بيضاء للنطاقات المسموحة فقط
- **حقن الاوامر** — حظر الاوامر الخطيرة + تنفيذ بدون shell
- **اختراق المسارات** — حظر 25+ مسار حساس
- **حماية من الحمل الزائد** — حد اقصى 10 مهام متزامنة

### الحصول على مفتاح API

1. اذهب الى [Google AI Studio](https://aistudio.google.com/apikey)
2. انشئ مفتاح API مجاني
3. ضعه في ملف `.env`:

```env
GEMINI_API_KEY=مفتاحك_هنا
```

</div>
