@echo off
title Scorekeeper of the Fool
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo [%date% %time%] Starting bot...
python bot.py
echo.
echo [%date% %time%] Bot exited. Press any key to close.
pause >nul
