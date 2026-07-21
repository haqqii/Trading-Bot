@echo off
REM ============================================
REM Ochobot Windows Test Script (Webhook)
REM ============================================

echo ============================================
echo   Ochobot Webhook Test (Windows)
echo ============================================
echo.

REM Check if .env exists
if not exist .env (
    echo ERROR: .env file not found!
    echo Copy .env.example to .env first and configure it.
    pause
    exit /b 1
)

REM Load WEBHOOK_URL from .env
for /f "tokens=1,2 delims==" %%a in ('findstr /i "WEBHOOK_URL" .env') do (
    set WEBHOOK_URL=%%b
)

if "%WEBHOOK_URL%"=="" (
    echo ERROR: WEBHOOK_URL not set in .env
    echo Set WEBHOOK_URL first, e.g.: WEBHOOK_URL=https://abc.trycloudflare.com
    pause
    exit /b 1
)

REM Check if cloudflared is running
tasklist | findstr /i "cloudflared" >nul
if errorlevel 1 (
    echo WARNING: cloudflared not running!
    echo Run cloudflared tunnel --url http://localhost:8443 in another terminal
    pause
)

echo Starting bot with webhook mode...
echo WEBHOOK_URL: %WEBHOOK_URL%
echo.

REM Stop any running bot
tasklist | findstr /i "python.exe" >nul
if not errorlevel 1 (
    echo Stopping existing Python processes...
    taskkill /F /IM python.exe /T >nul 2>&1
    timeout /t 3 /nobreak >nul
)

REM Start bot in webhook mode
set WEBHOOK_URL=%WEBHOOK_URL%
python main.py

pause