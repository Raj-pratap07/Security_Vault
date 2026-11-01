@echo off
cd /d "%~dp0"
echo Installing the two packages PC1 needs...
python -m pip install -r requirements.txt
echo.
echo Checking the connection to PC2 (edit SETTINGS.bat first if this fails)...
call "%~dp0SETTINGS.bat"
curl %PC2_URL%/health
echo.
echo If you see {"status":"ok"} above, PC1 is connected to PC2.
pause
