@echo off
chcp 65001 >nul

echo ============================================
echo  Ochobot - Polling Mode
echo ============================================
echo.

REM Find WEBHOOK_URL line in .env and comment it out
findstr /B "WEBHOOK_URL" .env >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] Menonaktifkan WEBHOOK_URL di .env (mode polling)...
    powershell -Command "(Get-Content .env) -replace '^WEBHOOK_URL=.+', 'WEBHOOK_URL=' | Set-Content .env"
)

REM Kill old instances
taskkill /F /IM python.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul

py -3.13 -X utf8 main.py
