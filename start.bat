@echo off
rem ---------------------------------------------------------------------------
rem video-url-analyzer-mcp launcher (Windows)
rem
rem Loads .env.keys.local if present, sets safe defaults, then runs the
rem packaged MCP server entry point.  No secrets are printed.
rem ---------------------------------------------------------------------------

setlocal EnableExtensions EnableDelayedExpansion

rem --- Load .env.keys.local (KEY=VALUE per line, no quoting required) -------
if exist "%~dp0.env.keys.local" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env.keys.local") do (
    set "_line=%%A"
    if not "!_line:~0,1!"=="#" if not "%%A"=="" (
      set "%%A=%%B"
    )
  )
)

rem --- Safe defaults (do not override values already in the environment) ---
if "%VIDEO_ANALYZER_MODE%"=="" set "VIDEO_ANALYZER_MODE=auto"

rem --- Resolve a Python interpreter -----------------------------------------
set "PYTHON_CMD="
where python >nul 2>nul && set "PYTHON_CMD=python"
if "%PYTHON_CMD%"=="" where py >nul 2>nul && set "PYTHON_CMD=py -3"
if "%PYTHON_CMD%"=="" (
  echo [start.bat] ERROR: Python 3.10+ was not found on PATH.
  echo [start.bat] Install Python from https://www.python.org/downloads/ and re-run.
  exit /b 1
)

rem --- Verify the package is importable -------------------------------------
%PYTHON_CMD% -c "import video_url_analyzer_mcp" >nul 2>nul
if errorlevel 1 (
  echo [start.bat] ERROR: 'video_url_analyzer_mcp' is not importable.
  echo [start.bat] Run scripts\install_windows.ps1, or:
  echo               pip install -e .
  echo               (alternatively) uvx video-url-analyzer-mcp
  exit /b 1
)

rem --- Run the MCP server ---------------------------------------------------
%PYTHON_CMD% -m video_url_analyzer_mcp
exit /b %ERRORLEVEL%
