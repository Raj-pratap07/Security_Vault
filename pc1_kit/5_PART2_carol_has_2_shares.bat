@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0SETTINGS.bat"
python share_holders.py open carol %EXTRA%
pause
