<#
.SYNOPSIS
    Video Analyzer MCP Server — One-Click Installer
.DESCRIPTION
    Clones the repo, installs dependencies, and configures Claude Desktop.
    Run: irm https://raw.githubusercontent.com/alihsh0/video-url-analyzer-mcp/main/install.ps1 | iex
#>

Write-Host ""
Write-Host "=== Video Analyzer MCP Server — Installer ===" -ForegroundColor Cyan
Write-Host ""

# Step 1: Find Python
$pythonExe = $null
$candidates = @(
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    (Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
)
# Check Windows Store Python
Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WindowsApps" -Filter "PythonSoftwareFoundation.Python.3.*" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $candidates += Join-Path $_.FullName "python.exe"
}
# Check standard paths
@("313","312","311") | ForEach-Object {
    $candidates += "$env:LOCALAPPDATA\Programs\Python\Python$_\python.exe"
}

foreach ($c in $candidates) {
    if ($c -and (Test-Path $c)) {
        $pythonExe = $c
        break
    }
}

if (-not $pythonExe) {
    Write-Host "ERROR: Python not found! Install from https://python.org" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Python: $pythonExe" -ForegroundColor Green

# Step 2: Clone or update repo
$installDir = Join-Path $env:USERPROFILE "video-url-analyzer-mcp"
if (Test-Path $installDir) {
    Write-Host "[..] Updating existing installation..." -ForegroundColor Yellow
    Push-Location $installDir
    git pull --quiet 2>$null
    Pop-Location
} else {
    Write-Host "[..] Cloning repository..." -ForegroundColor Yellow
    git clone https://github.com/alihsh0/video-url-analyzer-mcp.git $installDir --quiet
}
Write-Host "[OK] Installed to: $installDir" -ForegroundColor Green

# Step 3: Install dependencies
Write-Host "[..] Installing Python dependencies..." -ForegroundColor Yellow
& $pythonExe -m pip install -r (Join-Path $installDir "requirements.txt") --quiet --break-system-packages 2>$null
& $pythonExe -m pip install -r (Join-Path $installDir "requirements.txt") --quiet 2>$null
Write-Host "[OK] Dependencies installed" -ForegroundColor Green

# Step 4: Setup API key
$envFile = Join-Path $installDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Host ""
    $apiKey = Read-Host "Enter your Gemini API Key (from https://aistudio.google.com/apikey)"
    if ($apiKey) {
        "GEMINI_API_KEY=$apiKey" | Set-Content $envFile -Encoding ASCII
        Write-Host "[OK] API key saved to .env" -ForegroundColor Green
    }
} else {
    Write-Host "[OK] .env already exists" -ForegroundColor Green
}

# Step 5: Configure Claude Desktop
$configPath = "$env:APPDATA\Claude\claude_desktop_config.json"
$serverPath = Join-Path $installDir "start.bat"

Write-Host ""
Write-Host "=== Claude Desktop Configuration ===" -ForegroundColor Cyan
Write-Host "Add this to your Claude Desktop config ($configPath):" -ForegroundColor Yellow
Write-Host ""

$escapedPath = $serverPath.Replace('\', '\\\\')
Write-Host @"
{
  "mcpServers": {
    "video-analyzer": {
      "command": "$escapedPath",
      "args": [],
      "env": {
        "GEMINI_API_KEY": "your_api_key_here"
      }
    }
  }
}
"@

Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Green
Write-Host "Server location: $installDir" -ForegroundColor Cyan
Write-Host "To test: cd $installDir && python server.py" -ForegroundColor Cyan
Write-Host ""
