@echo off
rem Silent launch - no console window.
rem Uses pythonw from PATH. If Python is not on PATH, edit this file and put
rem the full path to your pythonw.exe between the quotes below.

cd /d "%~dp0"

where pythonw >nul 2>nul
if errorlevel 1 (
    echo.
    echo   pythonw not found on PATH.
    echo   Install Python 3.8+ from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

start "" pythonw "%~dp0main.py"
