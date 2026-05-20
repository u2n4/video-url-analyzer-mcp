#requires -Version 5.1
<#
.SYNOPSIS
  Interactive MCP client configuration helper for video-url-analyzer-mcp.

.DESCRIPTION
  Generates safe stdio entries for Claude Desktop, Claude Code, Codex CLI,
  VS Code MCP, Cursor/Windsurf/Antigravity (generic), or just prints config
  snippets.  Always backs up existing config before writing, asks before
  modifying, validates JSON/TOML, and never overwrites unrelated MCP servers.

  This script never publishes to PyPI, never pushes to git, and never prints
  the GEMINI_API_KEY value.  The wizard prefers OS environment variables for
  secrets and only writes a placeholder env block when the user explicitly
  asks for it.
#>

[CmdletBinding()]
param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
  [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'

function Write-Info($m) { Write-Host "[mcp-wizard] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[mcp-wizard] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[mcp-wizard] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "[mcp-wizard] $m" -ForegroundColor Red }

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

function Confirm-Yes([string]$prompt, [bool]$default = $true) {
  if ($NonInteractive) { return $default }
  $hint = if ($default) { '[Y/n]' } else { '[y/N]' }
  $ans = Read-Host "$prompt $hint"
  if ([string]::IsNullOrWhiteSpace($ans)) { return $default }
  return ($ans -match '^(y|yes)$')
}

function Backup-File([string]$path) {
  if (-not (Test-Path $path)) { return $null }
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $backup = "$path.bak-$stamp"
  Copy-Item -Path $path -Destination $backup -Force
  Write-Ok "Backed up: $path -> $backup"
  return $backup
}

# ---------------------------------------------------------------------------
# Resolve a launch command
# ---------------------------------------------------------------------------
function Get-LaunchCommand {
  $opts = @(
    "uvx (recommended): command='uvx', args=['video-url-analyzer-mcp']",
    "python module: command='python', args=['-m', 'video_url_analyzer_mcp']",
    "Local launcher: command='<repo>\\start.bat'"
  )
  $pick = Read-Choice -prompt "Which launch command should the client use?" -options $opts -default 2
  switch ($pick) {
    1 { return @{ command = 'uvx'; args = @('video-url-analyzer-mcp') } }
    2 { return @{ command = 'python'; args = @('-m', 'video_url_analyzer_mcp') } }
    3 {
      $bat = Join-Path $RepoRoot 'start.bat'
      return @{ command = $bat; args = @() }
    }
  }
}

# ---------------------------------------------------------------------------
# Should we embed an env block in the client config?
# Defaults: NO -- user OS environment is preferred for secrets.
# ---------------------------------------------------------------------------
function Get-EnvBlock {
  $useEnv = Confirm-Yes -prompt "Embed GEMINI_API_KEY placeholder in the client config? (Recommended: No, use OS env)" -default $false
  if (-not $useEnv) { return @{} }
  return @{ GEMINI_API_KEY = 'set-in-environment-not-here' }
}

# ---------------------------------------------------------------------------
# Generic MCP entry shape
# ---------------------------------------------------------------------------
function New-McpEntry($launch, $envBlock) {
  $entry = [ordered]@{
    command = $launch.command
    args    = $launch.args
  }
  if ($envBlock.Count -gt 0) { $entry.env = $envBlock }
  return $entry
}

# ---------------------------------------------------------------------------
# Claude Desktop  (%APPDATA%\Claude\claude_desktop_config.json)
# ---------------------------------------------------------------------------
function Configure-ClaudeDesktop($launch, $envBlock) {
  $configDir = Join-Path $env:APPDATA 'Claude'
  $configPath = Join-Path $configDir 'claude_desktop_config.json'
  Write-Info "Claude Desktop config: $configPath"

  if (-not (Test-Path $configDir)) {
    if (Confirm-Yes "Claude config directory does not exist. Create it?" $true) {
      New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    } else { return }
  }

  if (-not (Confirm-Yes "Write/update video-analyzer entry in Claude Desktop config?" $true)) { return }

  Backup-File $configPath | Out-Null

  $current = $null
  if (Test-Path $configPath) {
    try { $current = Get-Content $configPath -Raw | ConvertFrom-Json -ErrorAction Stop }
    catch { Write-Warn "Existing JSON is invalid; starting fresh."; $current = $null }
  }
  if (-not $current) { $current = [pscustomobject]@{ mcpServers = [pscustomobject]@{} } }
  if (-not ($current.PSObject.Properties.Name -contains 'mcpServers')) {
    $current | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
  }

  $entry = New-McpEntry $launch $envBlock
  if ($current.mcpServers.PSObject.Properties.Name -contains 'video-analyzer') {
    $current.mcpServers.PSObject.Properties.Remove('video-analyzer')
  }
  $current.mcpServers | Add-Member -NotePropertyName 'video-analyzer' -NotePropertyValue ([pscustomobject]$entry) -Force

  $json = $current | ConvertTo-Json -Depth 10
  try { $null = $json | ConvertFrom-Json } catch { Write-Err "Generated JSON is invalid; refusing to write."; return }

  Set-Content -Path $configPath -Value $json -Encoding UTF8
  Write-Ok "Updated $configPath. Restart Claude Desktop to load the new server."
}

# ---------------------------------------------------------------------------
# Claude Code (CLI) -- prints the official command, no file edits
# ---------------------------------------------------------------------------
function Configure-ClaudeCode($launch, $envBlock) {
  $envFlags = ''
  if ($envBlock.Count -gt 0) {
    $envFlags = ($envBlock.GetEnumerator() | ForEach-Object { "-e $($_.Key)=$($_.Value)" }) -join ' '
  }
  $cmd = $launch.command
  $argString = ($launch.args | ForEach-Object { '"' + $_ + '"' }) -join ' '
  Write-Info "Run this from any shell to register the MCP server with Claude Code:"
  Write-Host ""
  Write-Host "  claude mcp add video-analyzer --transport stdio $envFlags -- $cmd $argString"
  Write-Host ""
}

# ---------------------------------------------------------------------------
# Codex CLI  (~/.codex/config.toml)
# ---------------------------------------------------------------------------
function Configure-Codex($launch, $envBlock) {
  $configDir = Join-Path $env:USERPROFILE '.codex'
  $configPath = Join-Path $configDir 'config.toml'
  Write-Info "Codex config: $configPath"

  if (-not (Test-Path $configDir)) {
    if (Confirm-Yes "Codex config directory does not exist. Create it?" $true) {
      New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    } else { return }
  }

  if (-not (Confirm-Yes "Append/update [mcp_servers.video-analyzer] in Codex config?" $true)) { return }

  Backup-File $configPath | Out-Null

  $argsToml = ($launch.args | ForEach-Object { '"' + $_ + '"' }) -join ', '
  $envToml = ''
  if ($envBlock.Count -gt 0) {
    $pairs = $envBlock.GetEnumerator() | ForEach-Object { '"' + $_.Key + '" = "' + $_.Value + '"' }
    $envToml = "env = { " + ($pairs -join ', ') + " }`r`n"
  }

  $section = @"

[mcp_servers.video-analyzer]
command = "$($launch.command -replace '\\','\\\\')"
args = [$argsToml]
$envToml
"@

  $existing = ''
  if (Test-Path $configPath) { $existing = Get-Content $configPath -Raw }
  # Strip any prior block to avoid duplicates (idempotent)
  $stripped = [regex]::Replace($existing, '(?ms)^\s*\[mcp_servers\.video-analyzer\].*?(?=^\s*\[|\Z)', '').TrimEnd()
  $newContent = ($stripped + "`r`n" + $section).TrimStart("`r`n")

  Set-Content -Path $configPath -Value $newContent -Encoding UTF8
  Write-Ok "Updated $configPath. Restart Codex CLI to load the new server."
}

# ---------------------------------------------------------------------------
# VS Code  -- prefers workspace .vscode/mcp.json, falls back to user-level guidance
# ---------------------------------------------------------------------------
function Configure-VSCode($launch, $envBlock) {
  $scope = Read-Choice -prompt "VS Code MCP scope:" -options @(
    "Workspace: write .vscode/mcp.json in this repo",
    "User: print the snippet to add to your VS Code user MCP config"
  ) -default 1

  $entry = New-McpEntry $launch $envBlock
  $config = [ordered]@{
    servers = [ordered]@{
      'video-analyzer' = $entry
    }
  }
  $json = ($config | ConvertTo-Json -Depth 10)

  if ($scope -eq 1) {
    $vscodeDir = Join-Path $RepoRoot '.vscode'
    $configPath = Join-Path $vscodeDir 'mcp.json'
    if (-not (Confirm-Yes "Write $configPath?" $true)) { return }
    if (-not (Test-Path $vscodeDir)) { New-Item -ItemType Directory -Path $vscodeDir -Force | Out-Null }
    Backup-File $configPath | Out-Null

    # Merge with existing servers if present
    if (Test-Path $configPath) {
      try {
        $existing = Get-Content $configPath -Raw | ConvertFrom-Json -ErrorAction Stop
        if ($existing.PSObject.Properties.Name -contains 'servers') {
          $existing.servers | Add-Member -NotePropertyName 'video-analyzer' -NotePropertyValue ([pscustomobject]$entry) -Force
          $json = $existing | ConvertTo-Json -Depth 10
        }
      } catch { Write-Warn "Could not parse existing $configPath; replacing with a fresh config." }
    }

    try { $null = $json | ConvertFrom-Json } catch { Write-Err "Generated JSON is invalid."; return }
    Set-Content -Path $configPath -Value $json -Encoding UTF8
    Write-Ok "Wrote $configPath. Reload VS Code window to pick it up."
  } else {
    Write-Info "Add this to your VS Code user MCP config (Command Palette: 'MCP: Open User Configuration'):"
    Write-Host ""
    Write-Host $json
    Write-Host ""
  }
}

# ---------------------------------------------------------------------------
# Generic snippet only
# ---------------------------------------------------------------------------
function Show-GenericSnippets($launch, $envBlock) {
  $entry = New-McpEntry $launch $envBlock
  $generic = [ordered]@{ mcpServers = [ordered]@{ 'video-analyzer' = $entry } }
  Write-Info "Generic stdio JSON snippet:"
  Write-Host ""
  Write-Host (($generic | ConvertTo-Json -Depth 10))
  Write-Host ""
  Write-Info "Python module alternative:"
  $alt = @{ command = 'python'; args = @('-m', 'video_url_analyzer_mcp') }
  $altEntry = New-McpEntry $alt $envBlock
  $altCfg = [ordered]@{ mcpServers = [ordered]@{ 'video-analyzer' = $altEntry } }
  Write-Host (($altCfg | ConvertTo-Json -Depth 10))
}

# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------
$client = Read-Choice -prompt "Which MCP client do you want to configure?" -options @(
  "Claude Desktop",
  "Claude Code (CLI)",
  "Codex CLI",
  "VS Code / GitHub Copilot MCP",
  "Cursor / Windsurf / Antigravity / Generic MCP client",
  "Generate config snippets only",
  "Skip client setup"
) -default 1

if ($client -eq 7) { Write-Info "Skipping client setup."; return }

$launch = Get-LaunchCommand
$envBlock = Get-EnvBlock

switch ($client) {
  1 { Configure-ClaudeDesktop -launch $launch -envBlock $envBlock }
  2 { Configure-ClaudeCode    -launch $launch -envBlock $envBlock }
  3 { Configure-Codex         -launch $launch -envBlock $envBlock }
  4 { Configure-VSCode        -launch $launch -envBlock $envBlock }
  5 { Show-GenericSnippets    -launch $launch -envBlock $envBlock }
  6 { Show-GenericSnippets    -launch $launch -envBlock $envBlock }
}

Write-Ok "Client setup complete. Restart your MCP client to load the server."
