@echo off
setlocal
cd /d "%~dp0"
python scripts\bootstrap.py
if errorlevel 1 goto failed
echo Setup complete.
pause
exit /b 0
:failed
echo Setup failed. Check Python 3.12 or newer and network access.
pause
exit /b 1
