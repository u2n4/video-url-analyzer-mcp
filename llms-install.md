# Installation Guide for AI Agents

## Recommended: uvx (zero-config)

Add this to the user's Claude Desktop config:
```json
{
  "mcpServers": {
    "video-analyzer": {
      "command": "uvx",
      "args": ["video-url-analyzer-mcp"],
      "env": {
        "GEMINI_API_KEY": "${GEMINI_API_KEY}"
      }
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
- GEMINI_API_KEY from https://aistudio.google.com/apikey

For Claude Code:
```bash
claude mcp add video-analyzer -s user -e GEMINI_API_KEY=your_key -- uvx video-url-analyzer-mcp
```
