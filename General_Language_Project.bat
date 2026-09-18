@echo off
cd /d "%~dp0"

if not exist "env\Scripts\pythonw.exe" (
    echo Cannot find env\Scripts\pythonw.exe
    pause
    exit /b 1
)

if not exist "General_Language_Project.py" (
    echo Cannot find General_Language_Project.py
    pause
    exit /b 1
)

start "" "env\Scripts\pythonw.exe" "General_Language_Project.py"
