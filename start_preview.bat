@echo off
setlocal
cd /d "%~dp0"
python scripts\bootstrap.py
if errorlevel 1 goto failed
start "PSD Progress" "%~dp0.venv\Scripts\pythonw.exe" -m anime_layer_agent.viewer %*
exit /b 0
:failed
echo Setup failed. See the error above.
pause
exit /b 1
