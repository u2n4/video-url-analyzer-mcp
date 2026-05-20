#requires -Version 5.1
<#
.SYNOPSIS
  One-line bootstrap installer for video-url-analyzer-mcp (Windows).

.DESCRIPTION
  Designed to be run directly from the web with no prior checkout:

      irm https://raw.githubusercontent.com/u2n4/video-url-analyzer-mcp/main/install.ps1 | iex

  What it does:
    1. Ensures `uv` (and `uvx`) is installed; installs it automatically via the
       official Astral installer if missing.
    2. Uses GEMINI_API_KEY from the current environment when present; otherwise
       optionally captures it with hidden input. Do not put API keys in the
       one-line PowerShell command; that leaks into shell history.
    3. Lets you pick which MCP client(s) to configure: Claude Code, Claude
       Desktop, Codex CLI, Cursor, Windsurf, VS Code, Antigravity, Cline -- or
       all of them.
    4. Registers the server to run via `uvx video-url-analyzer-mcp` (no clone,
       no virtualenv). Existing configs are backed up and unrelated MCP
       servers are never touched.

  The script never publishes to PyPI, never pushes to git, and never prints the
  full API key.

.PARAMETER Targets
  Comma/space separated list of clients to configure without prompting:
  claude-code, claude-desktop, codex, cursor, windsurf, vscode, antigravity,
  cline, all.

.PARAMETER NonInteractive
  Run without prompts. Requires -Targets (defaults to claude-code) and uses
  $env:GEMINI_API_KEY if available. Do not pass secrets on the command line.

.EXAMPLE
  irm https://raw.githubusercontent.com/u2n4/video-url-analyzer-mcp/main/install.ps1 | iex

.EXAMPLE
  # Non-interactive, all clients, key from env:
  # Set GEMINI_API_KEY from your CI/user secret store first.
  .\install.ps1 -NonInteractive -Targets all
#>

[CmdletBinding()]
param(
  [string[]]$Targets,
  [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'

# When piped through `irm | iex` there is no $PSScriptRoot and $args may carry
# junk; normalize interactive detection.
$script:Interactive = -not $NonInteractive

function Write-Info($m) { Write-Host "[install] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[install] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[install] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "[install] $m" -ForegroundColor Red }

function Get-MaskedKey([string]$key) {
  if ([string]::IsNullOrWhiteSpace($key)) { return '<empty>' }
  if ($key.Length -le 8) { return ('*' * $key.Length) }
  return ($key.Substring(0, 4) + '...' + $key.Substring($key.Length - 4))
}

function Confirm-Yes([string]$prompt, [bool]$default = $true) {
  if (-not $script:Interactive) { return $default }
  $hint = if ($default) { '[Y/n]' } else { '[y/N]' }
  $ans = Read-Host "$prompt $hint"
  if ([string]::IsNullOrWhiteSpace($ans)) { return $default }
  return ($ans -match '^(y|yes)$')
}

function Read-Secret([string]$prompt) {
  if (-not $script:Interactive) { return $null }
  $sec = Read-Host -AsSecureString $prompt
  if ($null -eq $sec) { return $null }
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Backup-File([string]$path) {
  if (-not (Test-Path $path)) { return }
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  Copy-Item -Path $path -Destination "$path.bak-$stamp" -Force
  Write-Ok "Backed up: $path -> $path.bak-$stamp"
}

# ---------------------------------------------------------------------------
# 1. Ensure uv / uvx
# ---------------------------------------------------------------------------
function Resolve-Uvx {
  $uvx = Get-Command uvx -ErrorAction SilentlyContinue
  if ($uvx) { return $uvx.Source }

  # uv may be installed but not yet on this session's PATH (e.g. just installed).
  $candidate = Join-Path $env:USERPROFILE '.local\bin\uvx.exe'
  if (Test-Path $candidate) { return $candidate }
  return $null
}

function Ensure-Uv {
  $uvxPath = Resolve-Uvx
  if ($uvxPath) {
    $ver = (& $uvxPath --version) 2>$null
    Write-Ok "uv detected: $ver"
    return $uvxPath
  }

  Write-Warn "uv was not found. Installing it via the official Astral installer..."
  if (-not (Confirm-Yes "Install uv now (downloads from https://astral.sh)?" $true)) {
    Write-Err "uv is required for uvx-based installs. Aborting."
    Write-Err "Install it manually: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
  }

  $prevPP = $env:UV_INSTALL_SCRIPT_DISABLE_MODIFY_PATH
  try {
    powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  } catch {
    Write-Err "uv install failed: $($_.Exception.Message)"
    exit 1
  } finally {
    $env:UV_INSTALL_SCRIPT_DISABLE_MODIFY_PATH = $prevPP
  }

  # Make the freshly installed uv visible to THIS session.
  $localBin = Join-Path $env:USERPROFILE '.local\bin'
  if ((Test-Path $localBin) -and ($env:PATH -notlike "*$localBin*")) {
    $env:PATH = "$localBin;$env:PATH"
  }

  $uvxPath = Resolve-Uvx
  if (-not $uvxPath) {
    Write-Err "uv was installed but uvx is still not resolvable. Open a NEW terminal and re-run."
    exit 1
  }
  $ver = (& $uvxPath --version) 2>$null
  Write-Ok "uv installed: $ver"
  return $uvxPath
}

# ---------------------------------------------------------------------------
# 2. Smoke test: fetch the package and import it (no API key needed)
# ---------------------------------------------------------------------------
function Test-Package([string]$uvxPath) {
  Write-Info "Fetching video-url-analyzer-mcp from PyPI and verifying import..."
  $probe = & $uvxPath --from video-url-analyzer-mcp python -c "from video_url_analyzer_mcp import main; print('IMPORT_OK', callable(main))" 2>&1
  if ($LASTEXITCODE -ne 0 -or ($probe -notmatch 'IMPORT_OK')) {
    Write-Err "Package smoke test failed:"
    Write-Host $probe
    exit 1
  }
  Write-Ok "Package import OK."
}

# ---------------------------------------------------------------------------
# 3. Client configuration
# ---------------------------------------------------------------------------
$ServerName = 'video-analyzer'
$PkgName    = 'video-url-analyzer-mcp'

function Build-EnvBlock([string]$key) {
  $env = [ordered]@{}
  if ($key) { $env['GEMINI_API_KEY'] = $key }
  return $env
}

function Configure-ClaudeCode([string]$key) {
  $claude = Get-Command claude -ErrorAction SilentlyContinue
  if (-not $claude) {
    Write-Warn "Claude Code CLI ('claude') not found on PATH. Skipping. Install it, then run:"
    Write-Host "  claude mcp add $ServerName -s user -- uvx $PkgName"
    Write-Warn "Set GEMINI_API_KEY with this installer prompt or your user environment; do not paste secrets into shared commands."
    return
  }
  # Remove any prior entry so we don't collide, then re-add cleanly.
  & $claude.Source mcp remove $ServerName -s user 2>$null | Out-Null
  $addArgs = @('mcp', 'add', $ServerName, '-s', 'user')
  if ($key) { $addArgs += @('-e', "GEMINI_API_KEY=$key") }
  $addArgs += @('--', 'uvx', $PkgName)
  & $claude.Source @addArgs
  if ($LASTEXITCODE -eq 0) {
    Write-Ok "Registered '$ServerName' with Claude Code (user scope)."
  } else {
    Write-Err "Claude Code registration failed."
  }
}

function Configure-ClaudeDesktop([string]$key) {
  $dir = Join-Path $env:APPDATA 'Claude'
  $cfg = Join-Path $dir 'claude_desktop_config.json'
  if (-not (Test-Path $dir)) {
    if (-not (Confirm-Yes "Claude Desktop config dir does not exist. Create it?" $true)) { return }
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }
  Backup-File $cfg

  $current = $null
  if (Test-Path $cfg) {
    try { $current = Get-Content $cfg -Raw | ConvertFrom-Json -ErrorAction Stop }
    catch { Write-Warn "Existing JSON invalid; starting fresh."; $current = $null }
  }
  if (-not $current) { $current = [pscustomobject]@{ mcpServers = [pscustomobject]@{} } }
  if (-not ($current.PSObject.Properties.Name -contains 'mcpServers')) {
    $current | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
  }

  $entry = [ordered]@{ command = 'uvx'; args = @($PkgName) }
  $envBlock = Build-EnvBlock $key
  if ($envBlock.Count -gt 0) { $entry['env'] = $envBlock }

  if ($current.mcpServers.PSObject.Properties.Name -contains $ServerName) {
    $current.mcpServers.PSObject.Properties.Remove($ServerName)
  }
  $current.mcpServers | Add-Member -NotePropertyName $ServerName -NotePropertyValue ([pscustomobject]$entry) -Force

  $json = $current | ConvertTo-Json -Depth 12
  try { $null = $json | ConvertFrom-Json } catch { Write-Err "Generated JSON invalid; refusing to write."; return }
  Set-Content -Path $cfg -Value $json -Encoding UTF8
  Write-Ok "Updated $cfg. Restart Claude Desktop to load the server."
}

function Configure-Codex([string]$key) {
  $dir = Join-Path $env:USERPROFILE '.codex'
  $cfg = Join-Path $dir 'config.toml'
  if (-not (Test-Path $dir)) {
    if (-not (Confirm-Yes "Codex config dir does not exist. Create it?" $true)) { return }
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }
  Backup-File $cfg

  $envToml = ''
  if ($key) {
    $escaped = $key -replace '"', '\"'
    $envToml = "`r`n[mcp_servers.$ServerName.env]`r`nGEMINI_API_KEY = `"$escaped`"`r`n"
  }
  $section = "`r`n[mcp_servers.$ServerName]`r`ncommand = `"uvx`"`r`nargs = [`"$PkgName`"]`r`n$envToml"

  $existing = ''
  if (Test-Path $cfg) { $existing = Get-Content $cfg -Raw }
  # Strip prior [mcp_servers.video-analyzer] and its .env subtable (idempotent).
  $pattern = '(?ms)^\s*\[mcp_servers\.' + [regex]::Escape($ServerName) + '(\.[^\]]+)?\].*?(?=^\s*\[|\Z)'
  $stripped = [regex]::Replace($existing, $pattern, '').TrimEnd()
  $newContent = ($stripped + "`r`n" + $section).TrimStart("`r`n")

  Set-Content -Path $cfg -Value $newContent -Encoding UTF8

  # Validate TOML if Python is available; otherwise warn but keep the backup.
  $py = Get-Command python -ErrorAction SilentlyContinue
  if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
  if ($py) {
    $check = & $py.Source -c "import tomllib,sys; tomllib.load(open(sys.argv[1],'rb')); print('OK')" $cfg 2>&1
    if ($check -notmatch 'OK') {
      Write-Err "Codex TOML failed validation after edit. Restore from the .bak file:"
      Write-Host $check
      return
    }
  }
  Write-Ok "Updated $cfg. Restart Codex CLI to load the server."
}

# ---------------------------------------------------------------------------
# Generic JSON-config client (Cursor, Windsurf, VS Code, Cline).
# Root key differs: most use 'mcpServers'; VS Code uses 'servers'.
# ---------------------------------------------------------------------------
function Configure-JsonClient {
  param(
    [string]$Label,
    [string]$Path,
    [string]$RootKey,
    [string]$Key,
    [switch]$IncludeType
  )
  $dir = Split-Path -Parent $Path
  if (-not (Test-Path $dir)) {
    if (-not (Confirm-Yes "$Label config dir does not exist ($dir). Create it?" $true)) { return }
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }
  Backup-File $Path

  $current = $null
  if (Test-Path $Path) {
    try { $current = Get-Content $Path -Raw | ConvertFrom-Json -ErrorAction Stop }
    catch { Write-Warn "$Label JSON invalid; starting fresh."; $current = $null }
  }
  if (-not $current) { $current = [pscustomobject]@{} }
  if (-not ($current.PSObject.Properties.Name -contains $RootKey)) {
    $current | Add-Member -NotePropertyName $RootKey -NotePropertyValue ([pscustomobject]@{})
  }

  $entry = [ordered]@{}
  if ($IncludeType) { $entry['type'] = 'stdio' }
  $entry['command'] = 'uvx'
  $entry['args'] = @($PkgName)
  $envBlock = Build-EnvBlock $Key
  if ($envBlock.Count -gt 0) { $entry['env'] = $envBlock }

  $root = $current.$RootKey
  if ($root.PSObject.Properties.Name -contains $ServerName) {
    $root.PSObject.Properties.Remove($ServerName)
  }
  $root | Add-Member -NotePropertyName $ServerName -NotePropertyValue ([pscustomobject]$entry) -Force

  $json = $current | ConvertTo-Json -Depth 12
  try { $null = $json | ConvertFrom-Json } catch { Write-Err "$Label JSON would be invalid; refusing to write."; return }
  Set-Content -Path $Path -Value $json -Encoding UTF8
  Write-Ok "Updated $Path. Restart $Label to load the server."
}

function Configure-Cursor([string]$key) {
  Configure-JsonClient -Label 'Cursor' -Path (Join-Path $env:USERPROFILE '.cursor\mcp.json') -RootKey 'mcpServers' -Key $key
}
function Configure-Windsurf([string]$key) {
  Configure-JsonClient -Label 'Windsurf' -Path (Join-Path $env:USERPROFILE '.codeium\windsurf\mcp_config.json') -RootKey 'mcpServers' -Key $key
}
function Configure-VSCode([string]$key) {
  $dir = Join-Path $env:APPDATA 'Code\User'
  $cfg = Join-Path $dir 'mcp.json'
  if (-not (Test-Path $dir)) {
    if (-not (Confirm-Yes "VS Code config dir does not exist ($dir). Create it?" $true)) { return }
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }
  Backup-File $cfg

  $current = $null
  if (Test-Path $cfg) {
    try { $current = Get-Content $cfg -Raw | ConvertFrom-Json -ErrorAction Stop }
    catch { Write-Warn "VS Code JSON invalid; starting fresh."; $current = $null }
  }
  if (-not $current) { $current = [pscustomobject]@{} }
  if (-not ($current.PSObject.Properties.Name -contains 'servers')) {
    $current | Add-Member -NotePropertyName servers -NotePropertyValue ([pscustomobject]@{})
  }
  if (-not ($current.PSObject.Properties.Name -contains 'inputs')) {
    $current | Add-Member -NotePropertyName inputs -NotePropertyValue @()
  }

  # VS Code supports secure input variables, so avoid hardcoding the key.
  $inputId = 'video-url-analyzer-gemini-api-key'
  $inputs = @($current.inputs | Where-Object { -not ($_.id -eq $inputId) })
  $inputs += [pscustomobject]@{
    type = 'promptString'
    id = $inputId
    description = 'Gemini API key for video-url-analyzer-mcp'
    password = $true
  }
  $current.inputs = $inputs

  $entry = [ordered]@{
    type = 'stdio'
    command = 'uvx'
    args = @($PkgName)
    env = [ordered]@{ GEMINI_API_KEY = '${input:video-url-analyzer-gemini-api-key}' }
  }
  if ($current.servers.PSObject.Properties.Name -contains $ServerName) {
    $current.servers.PSObject.Properties.Remove($ServerName)
  }
  $current.servers | Add-Member -NotePropertyName $ServerName -NotePropertyValue ([pscustomobject]$entry) -Force

  $json = $current | ConvertTo-Json -Depth 12
  try { $null = $json | ConvertFrom-Json } catch { Write-Err "VS Code JSON would be invalid; refusing to write."; return }
  Set-Content -Path $cfg -Value $json -Encoding UTF8
  Write-Ok "Updated $cfg. VS Code will prompt for the Gemini key when the server starts."
}
function Configure-Cline([string]$key) {
  $p = Join-Path $env:USERPROFILE '.cline\data\settings\cline_mcp_settings.json'
  Configure-JsonClient -Label 'Cline' -Path $p -RootKey 'mcpServers' -Key $key
}
function Configure-Antigravity([string]$key) {
  $p = Join-Path $env:USERPROFILE '.gemini\antigravity\mcp_config.json'
  Configure-JsonClient -Label 'Antigravity' -Path $p -RootKey 'mcpServers' -Key $key
}

# ---------------------------------------------------------------------------
# 4. Pick targets
# ---------------------------------------------------------------------------
$AllTargets = @('claude-code', 'claude-desktop', 'codex', 'cursor', 'windsurf', 'vscode', 'antigravity', 'cline')

function Resolve-Targets {
  if ($Targets -and $Targets.Count -gt 0) {
    $flat = ($Targets -join ',') -split '[,\s]+' | Where-Object { $_ }
    if ($flat -contains 'all') { return $AllTargets }
    return $flat
  }
  if (-not $script:Interactive) { return @('claude-code') }

  Write-Host ""
  Write-Host "Which MCP client(s) do you want to configure?" -ForegroundColor White
  Write-Host "  1. Claude Code (CLI)"
  Write-Host "  2. Claude Desktop"
  Write-Host "  3. Codex CLI"
  Write-Host "  4. Cursor"
  Write-Host "  5. Windsurf"
  Write-Host "  6. VS Code (Copilot)"
  Write-Host "  7. Antigravity"
  Write-Host "  8. Cline"
  Write-Host "  9. All of the above"
  Write-Host "(You can enter several numbers, e.g. '1 4 5'.)"
  while ($true) {
    $raw = Read-Host "Choose [1-9] (default 1)"
    if ([string]::IsNullOrWhiteSpace($raw)) { return @('claude-code') }
    $picks = $raw -split '[,\s]+' | Where-Object { $_ }
    $result = @()
    $bad = $false
    foreach ($p in $picks) {
      switch ($p.Trim()) {
        '1' { $result += 'claude-code' }
        '2' { $result += 'claude-desktop' }
        '3' { $result += 'codex' }
        '4' { $result += 'cursor' }
        '5' { $result += 'windsurf' }
        '6' { $result += 'vscode' }
        '7' { $result += 'antigravity' }
        '8' { $result += 'cline' }
        '9' { return $AllTargets }
        default { Write-Warn "Invalid choice '$p'. Try again."; $bad = $true }
      }
    }
    if ((-not $bad) -and ($result.Count -gt 0)) { return ($result | Select-Object -Unique) }
  }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Write-Host ""
Write-Info "video-url-analyzer-mcp installer"
Write-Host ""

$uvxPath = Ensure-Uv
Test-Package $uvxPath

# API key (env > hidden prompt). Optional everywhere.
$key = $env:GEMINI_API_KEY
$keySource = $null
if ($key) { $keySource = 'GEMINI_API_KEY environment variable' }
if (-not $key -and $script:Interactive) {
  Write-Host ""
  Write-Info "Get a free key at https://aistudio.google.com/apikey (or press Enter to skip)."
  Write-Warn "Do not paste API keys into the one-line install command. This prompt hides input."
  $key = Read-Secret "Paste GEMINI_API_KEY (hidden, Enter to skip)"
  if ($key) { $keySource = 'hidden prompt' }
}
if ($key) {
  Write-Ok "Using API key from ${keySource}: $(Get-MaskedKey $key)"
} else {
  Write-Warn "No API key provided. The server will install but you must set GEMINI_API_KEY before use."
}

$targets = Resolve-Targets
Write-Info ("Configuring: " + ($targets -join ', '))

foreach ($t in $targets) {
  switch ($t.ToLower()) {
    'claude-code'    { Configure-ClaudeCode $key }
    'claude-desktop' { Configure-ClaudeDesktop $key }
    'codex'          { Configure-Codex $key }
    'cursor'         { Configure-Cursor $key }
    'windsurf'       { Configure-Windsurf $key }
    'vscode'         { Configure-VSCode $key }
    'code'           { Configure-VSCode $key }
    'antigravity'    { Configure-Antigravity $key }
    'anti-gravity'   { Configure-Antigravity $key }
    'cline'          { Configure-Cline $key }
    default          { Write-Warn "Unknown target '$t' (use: claude-code, claude-desktop, codex, cursor, windsurf, vscode, antigravity, cline, or all)." }
  }
}

Write-Host ""
Write-Ok "Done."
Write-Host "  - Restart your MCP client (or run '/mcp' in Claude Code) to load the server."
if (-not $key) {
  Write-Host "  - Set your key later by re-running this installer and pasting it into the hidden prompt, or by setting GEMINI_API_KEY in your OS/user environment." -ForegroundColor Yellow
}
Write-Host "  - Try it: ask your assistant to analyze a YouTube/TikTok/Instagram URL."
