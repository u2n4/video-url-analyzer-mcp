# MCP Client Configuration Examples

> All examples below use placeholders. Never paste a real `GEMINI_API_KEY`
> into a tracked file, into a shared snippet, or into a screenshot. Prefer
> setting the key as a User environment variable on Windows, or in
> `~/.bashrc` / `~/.zshrc` on macOS/Linux. The wizard
> (`scripts/configure_mcp_clients.ps1`) writes config without an `env`
> block by default.

The package exposes the console script `video-url-analyzer-mcp` (entry
point `video_url_analyzer_mcp:main`). The same module also runs via
`python -m video_url_analyzer_mcp`.

---

## Launch commands

| Variant | Command | Notes |
|---|---|---|
| `uvx` (no install) | `uvx video-url-analyzer-mcp` | Recommended for end users. |
| Pip-installed package | `python -m video_url_analyzer_mcp` | Works after `pip install -e .` or `pip install video-url-analyzer-mcp`. |
| Local Windows launcher | `<repo>\start.bat` | Loads `.env.keys.local`, then launches `python -m video_url_analyzer_mcp`. |

---

## Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`)

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

If you must keep the key inside the config rather than the OS environment:

```json
{
  "mcpServers": {
    "video-url-analyzer": {
      "command": "uvx",
      "args": ["video-url-analyzer-mcp"],
      "env": {
        "GEMINI_API_KEY": "YOUR_KEY_HERE"
      }
    }
  }
}
```

Restart Claude Desktop after editing the file.

---

## Claude Code (CLI)

```bash
# uvx (recommended)
claude mcp add video-url-analyzer --transport stdio -- uvx video-url-analyzer-mcp

# Python module variant (after pip install -e . from the repo)
claude mcp add video-url-analyzer --transport stdio -- python -m video_url_analyzer_mcp
```

Add `-e GEMINI_API_KEY=YOUR_KEY_HERE` only if you cannot rely on your shell
environment (not recommended).

---

## Codex CLI (`~/.codex/config.toml`)

```toml
[mcp_servers.video-url-analyzer]
command = "uvx"
args = ["video-url-analyzer-mcp"]
# Optional, only if you don't set the key in your shell environment:
# env = { GEMINI_API_KEY = "YOUR_KEY_HERE" }
```

---

## VS Code / GitHub Copilot MCP (`.vscode/mcp.json` workspace, or User config)

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

Use the **MCP: Open User Configuration** command for a global registration
that survives across workspaces.

---

## Cursor / Windsurf / Antigravity / Generic MCP client

```json
{
  "mcpServers": {
    "video-url-analyzer": {
      "command": "uvx",
      "args": ["video-url-analyzer-mcp"],
      "env": {
        "GEMINI_API_KEY": "set-in-environment-not-here"
      }
    }
  }
}
```

Python-module alternative (after `pip install -e .` in this repo):

```json
{
  "mcpServers": {
    "video-url-analyzer": {
      "command": "python",
      "args": ["-m", "video_url_analyzer_mcp"]
    }
  }
}
```

---

## Local development command

From the repo root, with editable install:

```powershell
pip install -e .
python -m video_url_analyzer_mcp
```

Or use the launcher (Windows):

```powershell
.\start.bat
```

---

## Choosing a default model

Set one of these environment variables (User scope on Windows, shell rc on
macOS/Linux). Explicit per-call `model=` arguments still win over env vars.

| Variable | Effect |
|---|---|
| `VIDEO_ANALYZER_MODEL` | Single-knob default for both fast and deep model resolution. |
| `GEMINI_FAST_MODEL` | Overrides the model used for compact/standard detail modes. |
| `GEMINI_DEEP_MODEL` | Overrides the model used for the `full` detail mode. |

Suggested values: `gemini-flash-latest` (stable), `gemini-3.1-flash-lite-preview`
(fast/cheap), `gemini-3.1-pro-preview` (balanced, requires Pro access).
