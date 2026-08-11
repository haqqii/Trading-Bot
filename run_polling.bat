@echo off
chcp 65001 >nul
title Ochobot - Polling Mode

echo ============================================
echo  Ochobot - Polling Mode
echo ============================================
echo.

REM Kill old instances
echo [INFO] Menghentikan instance lama...
taskkill /F /IM python.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul

REM Ensure WEBHOOK_URL is empty (comment it out)
echo [INFO] Cek WEBHOOK_URL di .env...
findstr /B "WEBHOOK_URL" .env | findstr /V "WEBHOOK_URL=" >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] WEBHOOK_URL aktif, menonaktifkan...
    powershell -Command "(Get-Content .env) -replace 'WEBHOOK_URL=.*', 'WEBHOOK_URL=' | Set-Content .env"
)

echo.
echo [INFO] Memulai bot...
echo.
python main.py

echo.
echo [INFO] Bot berhenti. Tekan tombol apapun untuk menutup window ini...
pause >nul
