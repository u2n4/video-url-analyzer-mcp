# Video URL Analyzer MCP — Installation Guide

## For AI Agents / Automated Installation

### Prerequisites
- Python 3.10 or higher
- pip (Python package manager)
- Git
- A Gemini API key from https://aistudio.google.com/apikey

### Step 1: Clone Repository
```bash
git clone https://github.com/alihsh0/video-url-analyzer-mcp.git
cd video-url-analyzer-mcp
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment
```bash
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

### Step 4: Add to Claude Desktop
Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "video-analyzer": {
      "command": "/path/to/video-url-analyzer-mcp/start.bat",
      "args": [],
      "env": {
        "GEMINI_API_KEY": "YOUR_API_KEY"
      }
    }
  }
}
```

### Step 5: Restart Claude Desktop
Restart Claude Desktop to load the new MCP server.

### Verification
The server exposes 6 tools: analyze_video, get_transcript, ask_about_video, watch_and_analyze, execute_tutorial_steps, check_analysis_job.
