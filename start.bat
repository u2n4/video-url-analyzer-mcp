@echo off
REM Video Analyzer MCP Server — Smart Windows Launcher
REM Prioritizes the Python with all dependencies (curl_cffi, yt-dlp)

cd /d "%~dp0"

REM Try Python 3.13 Windows Store FIRST (has curl_cffi + yt-dlp installed)
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13*") do (
    if exist "%%D\python.exe" (
        "%%D\python.exe" server.py
        exit /b
    )
)

REM Try Python 3.12 Windows Store
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12*") do (
    if exist "%%D\python.exe" (
        "%%D\python.exe" server.py
        exit /b
    )
)

REM Try standard install paths
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
) do (
    if exist %%P (
        %%P server.py
        exit /b
    )
)

REM Last resort: PATH python
where python >nul 2>&1 && (
    python server.py
    exit /b
)

echo ERROR: Python not found! Install Python 3.10+ from https://python.org
pause
