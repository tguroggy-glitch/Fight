@echo off
cd /d "%~dp0"
echo Starting the voice-chat bot...
.venv\Scripts\python main.py
echo.
echo Bot stopped. Press any key to close.
pause > nul
