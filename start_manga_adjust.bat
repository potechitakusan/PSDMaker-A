@echo off
setlocal
cd /d "%~dp0"
python scripts\bootstrap.py
if errorlevel 1 goto failed
start "Manga Adjust" "%~dp0.venv\Scripts\pythonw.exe" -m anime_layer_agent.manga_editor %*
exit /b 0
:failed
echo Setup failed. See the error above.
pause
exit /b 1
