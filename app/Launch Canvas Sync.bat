@echo off
REM Double-click to open the Canvas Sync setup app.
cd /d "%~dp0\.."
where pythonw >nul 2>nul && (start "" pythonw "app\app.py") || (python "app\app.py")
