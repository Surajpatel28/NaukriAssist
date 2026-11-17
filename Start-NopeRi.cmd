@echo off
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" goto missing
".venv\Scripts\python.exe" desktop_launcher.py %*
set "runResult=%ERRORLEVEL%"
pause
exit /b %runResult%
:missing
echo Run Finish-Setup.cmd first to create the Python environment.
pause
exit /b 1
