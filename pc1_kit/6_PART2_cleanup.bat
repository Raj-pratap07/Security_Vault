@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0SETTINGS.bat"
python share_holders.py cleanup --url %PC2_URL% --token %TOKEN%
pause
