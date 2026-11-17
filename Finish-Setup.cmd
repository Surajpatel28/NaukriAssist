@echo off
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if not exist ".venv\Scripts\python.exe" goto failed
set "PATH=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;%PATH%"
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed
".venv\Scripts\python.exe" apply_agent.py --help
if errorlevel 1 goto failed
echo.
echo Dependencies verified. Opening account setup only.
".venv\Scripts\python.exe" apply_agent.py --setup
if errorlevel 1 goto failed
echo Setup closed. No application run has been started.
pause
exit /b 0
:failed
echo Setup stopped. Please share the error shown above.
pause
exit /b 1
