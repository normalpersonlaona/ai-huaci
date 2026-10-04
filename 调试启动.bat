@echo off
rem Debug launch - keeps a console open so you can see what it is doing.
rem Uses python from PATH. If Python is not on PATH, edit this file and put
rem the full path to your python.exe between the quotes below.

chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   python not found on PATH.
    echo   Install Python 3.8+ from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

python "%~dp0main.py" --debug
echo.
echo ---- exited ----
pause
