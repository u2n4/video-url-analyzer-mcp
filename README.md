<!-- Banner image -->
<div align="center">
  <img src="assets/banner.png" alt="Video URL Analyzer MCP" width="100%">

  <h1>Video URL Analyzer MCP</h1>
  <p><strong>MCP server to analyze YouTube, TikTok &amp; Instagram videos from URL — transcripts, AI insights, tutorial extraction</strong></p>

  <p>
    <a href="#"><img src="https://img.shields.io/badge/MCP-Compatible-blue?style=for-the-badge" alt="MCP"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="MIT"></a>
    <a href="#"><img src="https://img.shields.io/badge/Gemini-Powered-4285F4?style=for-the-badge&logo=google&logoColor=white" alt="Gemini"></a>
    <a href="#"><img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=for-the-badge" alt="Platform"></a>
  </p>

  <p>
    <a href="#features">Features</a> •
    <a href="#quick-start">Quick Start</a> •
    <a href="#tools">Tools</a> •
    <a href="#usage-examples">Usage</a> •
    <a href="#configuration">Configuration</a> •
    <a href="#contributing">Contributing</a>
  </p>
</div>

---

## What is This?

Video URL Analyzer MCP is a Model Context Protocol (MCP) server that lets Claude (or any MCP-compatible AI) analyze videos from YouTube, TikTok, and Instagram — just paste a URL. Powered by Google's Gemini API with full audio + visual analysis, it extracts transcripts, provides AI-powered insights, and can even extract executable tutorial steps.

## Features

- **YouTube Analysis** — Direct analysis via Gemini API (no download needed)
- **TikTok & Instagram** — Async job pattern with yt-dlp download + Gemini Files API
- **Full Audio + Visual** — Analyzes both video frames AND audio/speech
- **6 Tools** — analyze, transcript, Q&A, watch & analyze, execute tutorials, check jobs
- **Bilingual** — Supports Arabic and English prompts and responses
- **Async Jobs** — Background processing prevents Claude Desktop timeout crashes
- **Security** — URL validation, download size limits, SSRF protection
- **One-Click Install** — PowerShell installer for Windows

## Quick Start

### One-Click Install (Windows)

```powershell
irm https://raw.githubusercontent.com/alihsh0/video-url-analyzer-mcp/main/install.ps1 | iex
```

### Manual Install

```bash
git clone https://github.com/alihsh0/video-url-analyzer-mcp.git
cd video-url-analyzer-mcp
pip install -r requirements.txt
```

Create a `.env` file:
```
GEMINI_API_KEY=your_gemini_api_key_here
```

### Claude Desktop Configuration

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "video-analyzer": {
      "command": "path/to/video-url-analyzer-mcp/start.bat",
      "args": [],
      "env": {
        "GEMINI_API_KEY": "your_gemini_api_key_here"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add video-analyzer -- path/to/video-url-analyzer-mcp/start.bat
```

### Cursor / Windsurf

Add to your MCP config:
```json
{
  "video-analyzer": {
    "command": "python",
    "args": ["path/to/video-url-analyzer-mcp/server.py"],
    "env": {
      "GEMINI_API_KEY": "your_gemini_api_key_here"
    }
  }
}
```

## Tools

| Tool | Description | Platform |
|------|-------------|----------|
| `analyze_video` | Full audio + visual analysis with optional custom prompt | All |
| `get_transcript` | Extract spoken transcript with timestamps | All |
| `ask_about_video` | Ask a specific question about video content | All |
| `watch_and_analyze` | Extract tutorial steps, commands, and code | All |
| `execute_tutorial_steps` | Review or execute extracted tutorial steps | N/A |
| `check_analysis_job` | Poll async job status for TikTok/Instagram | TikTok, Instagram |

### How It Works

**YouTube** — Synchronous: URL is sent directly to Gemini API for instant analysis (no download).

**TikTok & Instagram** — Asynchronous: Video is downloaded via yt-dlp, uploaded to Gemini Files API, analyzed, then cleaned up. Returns a `job_id` immediately — poll with `check_analysis_job`.

## Usage Examples

### Analyze a YouTube video
```
Analyze this video: https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

### Get a transcript
```
Get the transcript of https://youtu.be/abc123
```

### Ask about a TikTok
```
What cooking technique is shown in https://www.tiktok.com/@user/video/123
```

### Extract tutorial steps
```
Extract all the steps from this tutorial: https://www.youtube.com/watch?v=xyz
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Google Gemini API key (required) | — |
| `ANALYSES_DIR` | Directory to store analysis results | `./analyses` |
| `VIDEO_ANALYZER_COOKIES` | Enable browser cookies for yt-dlp | `false` |

Get your Gemini API key at: https://aistudio.google.com/apikey

## Architecture

```
video-url-analyzer-mcp/
├── server.py          # Main MCP server (all 6 tools)
├── start.bat          # Smart Windows launcher (auto-detects Python)
├── install.ps1        # One-click installer
├── requirements.txt   # Python dependencies
├── .env.example       # Environment variable template
└── mcp-config.example.json  # Claude Desktop config template
```

### Platform Detection

URLs are automatically routed to the correct pipeline:
- **YouTube**: `youtube.com`, `youtu.be`, `youtube.com/shorts/`
- **TikTok**: `tiktok.com`, `vm.tiktok.com`, `vt.tiktok.com`
- **Instagram**: `instagram.com/reels/`, `instagram.com/reel/`, `instagram.com/p/`

### Security Features

- URL validation with allowlisted domains
- SSRF protection (blocks private/internal IPs)
- Download size limit (100MB)
- Max concurrent jobs (10)
- Job expiry (1 hour)
- Temp file cleanup in `finally` blocks

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `GEMINI_API_KEY not set` | Create `.env` with your API key |
| TikTok download fails | Install/update yt-dlp: `pip install -U yt-dlp` |
| Instagram 403 errors | Install curl_cffi: `pip install curl_cffi` |
| Claude Desktop timeout | Use async tools — TikTok/IG return job_id, poll with check_analysis_job |
| Python not found | Install Python 3.10+ from python.org |

## Dependencies

- [google-genai](https://pypi.org/project/google-genai/) — Google Gemini API SDK
- [fastmcp](https://pypi.org/project/fastmcp/) — MCP protocol framework
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Video downloader
- [python-dotenv](https://pypi.org/project/python-dotenv/) — Environment variables
- [curl_cffi](https://pypi.org/project/curl_cffi/) — Instagram TLS fingerprint bypass

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT — see [LICENSE](LICENSE).

## Support

If you find this useful, please star this repository!
