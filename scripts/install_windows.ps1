#requires -Version 5.1
<#
.SYNOPSIS
  Interactive Windows installer / configuration wizard for video-url-analyzer-mcp.

.DESCRIPTION
  - Verifies Python 3.10+ and (optionally) uv are available.
  - Lets the user pick between a published-package install (uvx/pip) or a local
    editable checkout for development.
  - Optionally captures GEMINI_API_KEY into a user environment variable OR a
    local .env.keys.local file.  Never prints the full key.
  - Optionally chooses a default model via VIDEO_ANALYZER_MODEL.
  - Runs an offline import smoke test and prints the next steps.
  - Hands off to scripts/configure_mcp_clients.ps1 for client setup.

  This script never publishes to PyPI, never pushes to git, never prints
  secrets, and never overwrites unrelated MCP servers.
#>

[CmdletBinding()]
param(
  [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'

function Write-Info($msg)    { Write-Host "[install] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)      { Write-Host "[install] $msg" -ForegroundColor Green }
function Write-Warn($msg)    { Write-Host "[install] $msg" -ForegroundColor Yellow }
function Write-Err($msg)     { Write-Host "[install] $msg" -ForegroundColor Red }

function Get-MaskedKey([string]$key) {
  if ([string]::IsNullOrWhiteSpace($key)) { return '<empty>' }
  if ($key.Length -le 8) { return ('*' * $key.Length) }
  return ($key.Substring(0, 4) + '...' + $key.Substring($key.Length - 4))
}

function Read-Choice([string]$prompt, [string[]]$options, [int]$default = 1) {
  if ($NonInteractive) { return $default }
  Write-Host ""
  Write-Host $prompt -ForegroundColor White
  for ($i = 0; $i -lt $options.Length; $i++) {
    Write-Host ("  {0}. {1}" -f ($i + 1), $options[$i])
  }
  while ($true) {
    $raw = Read-Host ("Choose [1-{0}] (default {1})" -f $options.Length, $default)
    if ([string]::IsNullOrWhiteSpace($raw)) { return $default }
    $n = 0
    if ([int]::TryParse($raw, [ref]$n) -and $n -ge 1 -and $n -le $options.Length) { return $n }
    Write-Warn "Invalid choice. Try again."
  }
}

function Read-Secret([string]$prompt) {
  if ($NonInteractive) { return $null }
  $sec = Read-Host -AsSecureString $prompt
  if ($null -eq $sec) { return $null }
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

# ---------------------------------------------------------------------------
# 1. Locate repo root and verify package layout
# ---------------------------------------------------------------------------
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Write-Info "Repo root: $RepoRoot"
if (-not (Test-Path (Join-Path $RepoRoot 'src/video_url_analyzer_mcp/server.py'))) {
  Write-Err "src/video_url_analyzer_mcp/server.py not found. Are you running this from the repo?"
  exit 1
}

# ---------------------------------------------------------------------------
# 2. Tooling checks (Python + optional uv)
# ---------------------------------------------------------------------------
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) {
  Write-Err "Python 3.10+ is required but was not found on PATH."
  Write-Err "Install Python from https://www.python.org/downloads/ then re-run."
  exit 1
}
$pyVer = & $python.Source -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Write-Ok "Python detected: $pyVer ($($python.Source))"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
  $uvVer = (& $uv.Source --version) 2>$null
  Write-Ok "uv detected: $uvVer"
} else {
  Write-Warn "uv was not found on PATH. The installer will not auto-install it."
  Write-Warn "If you want uvx-based installs, follow https://docs.astral.sh/uv/getting-started/installation/ and re-run."
}

# ---------------------------------------------------------------------------
# 3. Install mode
# ---------------------------------------------------------------------------
$installMode = Read-Choice -prompt "How should the MCP server be installed?" -options @(
  "Local editable checkout (recommended for development) - runs 'pip install -e .'",
  "Published package via uv tool (requires uv on PATH) - 'uv tool install video-url-analyzer-mcp'",
  "Skip install (I'll handle it myself)"
) -default 1

switch ($installMode) {
  1 {
    Write-Info "Installing local checkout in editable mode..."
    & $python.Source -m pip install --upgrade pip | Out-Null
    & $python.Source -m pip install -e $RepoRoot
    if ($LASTEXITCODE -ne 0) { Write-Err "pip install -e . failed."; exit 1 }
    Write-Ok "Editable install complete."
  }
  2 {
    if (-not $uv) { Write-Err "uv not available; cannot install published package."; exit 1 }
    Write-Info "Installing published package via uv tool..."
    & $uv.Source tool install video-url-analyzer-mcp
    if ($LASTEXITCODE -ne 0) { Write-Err "uv tool install failed."; exit 1 }
    Write-Ok "Package installed via uv tool."
  }
  3 { Write-Warn "Skipping install step." }
}

# ---------------------------------------------------------------------------
# 4. API key handling
# ---------------------------------------------------------------------------
$keyMode = Read-Choice -prompt "Where should GEMINI_API_KEY live?" -options @(
  "Set a User environment variable (recommended)",
  "Save to .env.keys.local in this repo (gitignored)",
  "Skip for now"
) -default 1

$apiKey = $null
if ($keyMode -ne 3) {
  $apiKey = Read-Secret -prompt "Paste GEMINI_API_KEY (input is hidden, press Enter to skip)"
}

if ($apiKey) {
  $masked = Get-MaskedKey $apiKey
  Write-Info "Captured API key: $masked"

  if ($keyMode -eq 1) {
    [Environment]::SetEnvironmentVariable('GEMINI_API_KEY', $apiKey, 'User')
    Write-Ok "Set User environment variable GEMINI_API_KEY (open a new terminal to see it)."
  } elseif ($keyMode -eq 2) {
    $envFile = Join-Path $RepoRoot '.env.keys.local'
    # Confirm gitignore covers this file before writing
    $ignoreCheck = & git -C $RepoRoot check-ignore -v -- $envFile 2>$null
    if (-not $ignoreCheck) {
      Write-Err ".env.keys.local is NOT gitignored. Aborting key write."
      exit 1
    }
    $existing = ''
    if (Test-Path $envFile) { $existing = Get-Content $envFile -Raw }
    $rebuilt = ($existing -split "`r?`n" |
      Where-Object { $_ -and ($_ -notmatch '^\s*GEMINI_API_KEY\s*=') }) -join "`r`n"
    if ($rebuilt -and -not $rebuilt.EndsWith("`r`n")) { $rebuilt += "`r`n" }
    $rebuilt += "GEMINI_API_KEY=$apiKey`r`n"
    Set-Content -Path $envFile -Value $rebuilt -NoNewline -Encoding UTF8
    Write-Ok "Wrote $envFile (gitignored). Masked key: $masked"
  }
} else {
  Write-Warn "No API key captured. You can set GEMINI_API_KEY later via the OS or .env.keys.local."
}

# ---------------------------------------------------------------------------
# 5. Default model (VIDEO_ANALYZER_MODEL)
# ---------------------------------------------------------------------------
$modelChoice = Read-Choice -prompt "Pick a default model (sets VIDEO_ANALYZER_MODEL):" -options @(
  "Recommended balanced: gemini-3.1-pro-preview (if your account has Pro access)",
  "Fast/cheap: gemini-3.1-flash-lite-preview",
  "Stable fallback: gemini-flash-latest",
  "Custom model string",
  "Skip (leave existing default behavior)"
) -default 3

$selectedModel = $null
switch ($modelChoice) {
  1 { $selectedModel = 'gemini-3.1-pro-preview' }
  2 { $selectedModel = 'gemini-3.1-flash-lite-preview' }
  3 { $selectedModel = 'gemini-flash-latest' }
  4 {
    if (-not $NonInteractive) {
      $custom = Read-Host "Enter custom model id"
      if ($custom) { $selectedModel = $custom.Trim() }
    }
  }
  5 { Write-Warn "Leaving model selection unchanged." }
}

if ($selectedModel) {
  [Environment]::SetEnvironmentVariable('VIDEO_ANALYZER_MODEL', $selectedModel, 'User')
  Write-Ok "Set VIDEO_ANALYZER_MODEL=$selectedModel (User scope). Open a new terminal to use it."
  Write-Info "Model availability is not verified offline; you can change VIDEO_ANALYZER_MODEL at any time."
}

# ---------------------------------------------------------------------------
# 6. Smoke test (offline; no Gemini call)
# ---------------------------------------------------------------------------
Write-Info "Running offline import smoke test..."
$smoke = & $python.Source -c "import os; os.environ.pop('GEMINI_API_KEY', None); from video_url_analyzer_mcp import main; print('IMPORT_OK', callable(main))"
if ($LASTEXITCODE -ne 0) {
  Write-Err "Smoke test failed: package not importable."
  Write-Err $smoke
  exit 1
}
Write-Ok ("Smoke test: " + $smoke)

$toolCount = & $python.Source -c "import asyncio; from video_url_analyzer_mcp import server; n = len({t.name for t in asyncio.run(server.mcp.list_tools())}); print(n)"
if ($LASTEXITCODE -eq 0) {
  Write-Ok "MCP tool count: $toolCount (expected 17)"
} else {
  Write-Warn "Could not enumerate tools; this is non-fatal."
}

# ---------------------------------------------------------------------------
# 7. Hand off to client wizard
# ---------------------------------------------------------------------------
$runWizard = Read-Choice -prompt "Configure an MCP client now?" -options @(
  "Yes, run scripts/configure_mcp_clients.ps1",
  "No, just show next steps"
) -default 1

if ($runWizard -eq 1) {
  $wizard = Join-Path $PSScriptRoot 'configure_mcp_clients.ps1'
  if (Test-Path $wizard) {
    & $wizard -RepoRoot $RepoRoot
  } else {
    Write-Warn "Client wizard not found at $wizard"
  }
}

Write-Host ""
Write-Ok "Done. Next steps:"
Write-Host "  - Open a NEW terminal so environment variables are picked up."
Write-Host "  - Test the launcher:  start.bat"
Write-Host "  - See docs/mcp-config-examples.md for client config snippets."
Write-Host "  - Get a key at https://aistudio.google.com/apikey if you skipped that step."
