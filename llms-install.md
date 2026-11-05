# Installation Guide for AI Agents

## Recommended: uvx (zero-config)

Add this to the user's Claude Desktop config:
```json
{
  "mcpServers": {
    "video-analyzer": {
      "command": "uvx",
      "args": ["video-url-analyzer-mcp"]
    }
  }
}
```

Config file locations:
- Windows: %APPDATA%\Claude\claude_desktop_config.json
- macOS: ~/Library/Application Support/Claude/claude_desktop_config.json
- Linux: ~/.config/claude/claude_desktop_config.json

Prerequisites:
- uv installed: curl -LsSf https://astral.sh/uv/install.sh | sh (or: winget install astral-sh.uv)
- GEMINI_API_KEY is optional. Without it the server still starts, and these tools
  work: prepare_slideshow_assets, get_video_frame, get_video_clip,
  list_video_contexts, list_video_sources, cleanup_video_cache,
  delete_video_context, and ask_video_context (use_gemini=false). Gemini-backed
  tools return an error that names the key. To add a key, get one at
  https://aistudio.google.com/apikey and put the real value in an env block.
  Claude Desktop does not expand ${VAR} placeholders.
- Supports video URLs plus TikTok Photo Mode, Instagram Carousel/photo posts, and YouTube Community image posts. Use `prepare_slideshow_assets` when the client AI should inspect ordered images itself.

For Claude Code (the -e key is optional):
```bash
claude mcp add video-analyzer -s user -e GEMINI_API_KEY=your_key -- uvx video-url-analyzer-mcp
```
