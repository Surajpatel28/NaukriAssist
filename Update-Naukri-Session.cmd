@echo off
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" goto missing
".venv\Scripts\python.exe" desktop_launcher.py --setup
set "setupResult=%ERRORLEVEL%"
pause
exit /b %setupResult%
:missing
echo Run Finish-Setup.cmd first.
pause
exit /b 1
