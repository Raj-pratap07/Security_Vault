@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0SETTINGS.bat"
python two_pc_demo.py alice --url %PC2_URL% --token %TOKEN% %EXTRA%
pause
