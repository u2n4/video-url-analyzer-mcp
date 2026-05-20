@echo off
rem ---------------------------------------------------------------------------
rem video-url-analyzer-mcp launcher (Windows)
rem
rem Works in two modes:
rem   * Local repo checkout: prepends "%~dp0src" to PYTHONPATH so the package
rem     resolves without an editable install.
rem   * Installed package: relies on the active Python environment.
rem
rem Check mode (does NOT start the server, useful for double-click testers):
rem   start.bat --check
rem   set VIDEO_ANALYZER_START_CHECK=1 && start.bat
rem
rem Secrets are loaded from .env.keys.local but never echoed.
rem ---------------------------------------------------------------------------

setlocal EnableExtensions EnableDelayedExpansion

set "_CHECK_MODE=0"
if /I "%~1"=="--check" set "_CHECK_MODE=1"
if /I "%~1"=="-check" set "_CHECK_MODE=1"
if defined VIDEO_ANALYZER_START_CHECK if not "%VIDEO_ANALYZER_START_CHECK%"=="0" set "_CHECK_MODE=1"

set "_PAUSE_ON_FAIL=0"
if /I "%~2"=="--pause" set "_PAUSE_ON_FAIL=1"
rem Heuristic: when launched by a double-click the parent process is explorer.exe
rem and CMDCMDLINE contains "/c". Pause so the user can read the error.
echo %CMDCMDLINE% | findstr /I /C:"/c" >nul 2>nul && set "_PAUSE_ON_FAIL=1"

rem --- Load .env.keys.local (KEY=VALUE per line; values never echoed) -------
if exist "%~dp0.env.keys.local" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env.keys.local") do (
    set "_line=%%A"
    if not "!_line:~0,1!"=="#" if not "%%A"=="" (
      set "%%A=%%B"
    )
  )
)

rem --- Safe defaults (do not override values already in the environment) ----
if "%VIDEO_ANALYZER_MODE%"=="" set "VIDEO_ANALYZER_MODE=auto"
if "%VIDEO_ANALYZER_MODEL%"=="" set "VIDEO_ANALYZER_MODEL=gemini-3.5-flash"

rem --- Local checkout fallback: prepend src to PYTHONPATH if it exists ------
if exist "%~dp0src\video_url_analyzer_mcp\server.py" (
  if defined PYTHONPATH (
    set "PYTHONPATH=%~dp0src;!PYTHONPATH!"
  ) else (
    set "PYTHONPATH=%~dp0src"
  )
)

rem --- Resolve a Python interpreter -----------------------------------------
set "PYTHON_CMD="
where python >nul 2>nul && set "PYTHON_CMD=python"
if "%PYTHON_CMD%"=="" where py >nul 2>nul && set "PYTHON_CMD=py -3"
if "%PYTHON_CMD%"=="" (
  echo [start.bat] ERROR: Python 3.10+ was not found on PATH.
  echo [start.bat] Install Python from https://www.python.org/downloads/ and re-run.
  goto :fail
)

rem --- Lightweight import check ---------------------------------------------
%PYTHON_CMD% -c "from video_url_analyzer_mcp import main; print('IMPORT_OK', callable(main))" >"%TEMP%\vurla_import.log" 2>&1
if errorlevel 1 (
  echo [start.bat] ERROR: 'video_url_analyzer_mcp' could not be imported.
  echo [start.bat] Last error:
  type "%TEMP%\vurla_import.log"
  echo.
  echo [start.bat] Try one of:
  echo               powershell -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1"
  echo               %PYTHON_CMD% -m pip install -e "%~dp0."
  echo               uvx video-url-analyzer-mcp
  goto :fail
)
type "%TEMP%\vurla_import.log"
del "%TEMP%\vurla_import.log" >nul 2>nul

if "%_CHECK_MODE%"=="1" (
  echo [start.bat] check mode: VIDEO_ANALYZER_MODE=%VIDEO_ANALYZER_MODE%
  echo [start.bat] check mode: VIDEO_ANALYZER_MODEL=%VIDEO_ANALYZER_MODEL%
  if defined PYTHONPATH (
    echo [start.bat] check mode: PYTHONPATH=%PYTHONPATH%
  )
  if defined GEMINI_API_KEY (
    echo [start.bat] check mode: GEMINI_API_KEY is set ^(value hidden^)
  ) else (
    echo [start.bat] check mode: GEMINI_API_KEY is not set
  )
  echo [start.bat] check mode: OK -- not starting the MCP server.
  endlocal
  exit /b 0
)

rem --- Run the MCP server ---------------------------------------------------
%PYTHON_CMD% -m video_url_analyzer_mcp
set "_RC=%ERRORLEVEL%"
if not "%_RC%"=="0" goto :fail
endlocal & exit /b 0

:fail
if "%_PAUSE_ON_FAIL%"=="1" (
  echo.
  pause
)
endlocal & exit /b 1
