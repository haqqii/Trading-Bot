@echo off
chcp 65001 >nul
title Ochobot Launcher

echo ============================================
echo  Ochobot - Webhook Mode Launcher
echo ============================================
echo.

REM Check cloudflared exists
where cloudflared >nul 2>&1
if %errorlevel% neq 0 (
    if not exist "%~dp0cloudflared.exe" (
        echo [ERROR] cloudflared.exe tidak ditemukan!
        echo.
        echo Download dari:
        echo https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
        echo.
        echo Simpan di folder ini: %~dp0
        echo.
        pause
        exit /b 1
    )
    set CF=%~dp0cloudflared.exe
) else (
    set CF=cloudflared
)

REM Kill old instances
echo [1/4] Menghentikan instance lama...
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM cloudflared.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul

REM Start cloudflared tunnel
echo.
echo [2/4] Memulai Cloudflare Tunnel...
echo    Biarkan window ini TERBUKA. Copy URL trycloudflare.com yang muncul.
echo.
start "Cloudflared Tunnel" %CF% tunnel --url http://localhost:8443

REM Wait for tunnel
echo.
echo [3/4] Menunggu tunnel stabil...
echo.
echo    BUKA window "Cloudflared Tunnel" di taskbar.
echo    COPY URL yang berakhiran trycloudflare.com
echo    PASTE ke .env file sebagai WEBHOOK_URL=...
echo.
echo    Contoh: WEBHOOK_URL=https://random-words.trycloudflare.com
echo.
echo    Setelah .env diupdate, tekan ENTER di window ini...
echo.
pause

REM Start bot
echo.
echo [4/4] Memulai bot...
echo.
py -3.13 -X utf8 main.py
