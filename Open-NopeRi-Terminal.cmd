@echo off
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;%~dp0.venv\Scripts;%PATH%"
echo NopeRi environment ready. Account setup and application settings still need review.
cmd /k
