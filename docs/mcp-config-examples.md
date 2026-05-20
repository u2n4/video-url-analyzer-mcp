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

Do not paste a real API key into a command you will share or screenshot. Use
the installer prompt, your user environment, or your client UI when available.

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
  "inputs": [
    {
      "type": "promptString",
      "id": "video-url-analyzer-gemini-api-key",
      "description": "Gemini API key for video-url-analyzer-mcp",
      "password": true
    }
  ],
  "servers": {
    "video-url-analyzer": {
      "type": "stdio",
      "command": "uvx",
      "args": ["video-url-analyzer-mcp"],
      "env": {
        "GEMINI_API_KEY": "${input:video-url-analyzer-gemini-api-key}"
      }
    }
  }
}
```

Use the **MCP: Open User Configuration** command for a global registration
that survives across workspaces.

---

## Cursor (`%USERPROFILE%\.cursor\mcp.json`)

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

## Windsurf (`%USERPROFILE%\.codeium\windsurf\mcp_config.json`)

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

## Google Antigravity (`%USERPROFILE%\.gemini\antigravity\mcp_config.json`)

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

## Cline (`%USERPROFILE%\.cline\data\settings\cline_mcp_settings.json`)

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

If your client does not inherit `GEMINI_API_KEY` from your user environment,
run `install.ps1` and paste the key into the hidden prompt; the installer will
write the local-only `env` block for the clients you select.

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

| Variable | Effect | Default |
|---|---|---|
| `VIDEO_ANALYZER_MODEL` | Single-knob default for both fast and deep model resolution. | _unset_ |
| `GEMINI_FAST_MODEL` | Overrides the model used for compact/standard detail modes. | `gemini-3.1-flash-lite-preview` |
| `GEMINI_DEEP_MODEL` | Overrides the model used for the `full` detail mode. | `gemini-3.1-pro-preview` |

Resolution order at call time:

1. explicit per-tool `model=` argument
2. `GEMINI_FAST_MODEL` / `GEMINI_DEEP_MODEL` (whichever applies)
3. `VIDEO_ANALYZER_MODEL` (umbrella override for both)
4. the hardcoded fallbacks above

Model availability may vary by Google account, region, and API tier. If
the Gemini 3.1 preview ids are not enabled for your key, set
`VIDEO_ANALYZER_MODEL=gemini-flash-latest` as a stable fallback.
