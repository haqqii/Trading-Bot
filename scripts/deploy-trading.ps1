#Requires -Version 5.1
<#
.SYNOPSIS
    Deploy bot trading ke container ochobot-trading.

.DESCRIPTION
    Bot trading (Telegram) jalan di container "ochobot-trading", terpisah
    dari bot Aimapps ("ochobot") tapi satu project compose. Kode trading
    disalin ke image lewat build context kedua, jadi setiap perubahan kode
    butuh rebuild image, bukan cuma restart.

    Script ini:
      1. Cek token Telegram ada di .env repo ini.
      2. docker compose up -d --build di folder repo Ochobot.
      3. Tampilkan status dua bot.

    .env yang dipakai compose ada di repo ini (D:/Haqqi/Trading-Bot/.env),
    jadi token cukup diisi di satu tempat.

.PARAMETER BotDir
    Folder repo Ochobot (yang punya docker-compose.yml).

.PARAMETER NoBuild
    Lewati build. Hanya berguna kalau kodenya tidak berubah.

.EXAMPLE
    .\scripts\deploy-trading.ps1

.EXAMPLE
    .\scripts\deploy-trading.ps1 -NoBuild
#>
[CmdletBinding()]
param(
    [string]$BotDir = 'D:\Automation\Ochobot',
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'

$RepoDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$EnvFile = Join-Path $RepoDir '.env'
$ComposeFile = Join-Path $BotDir 'docker-compose.yml'

if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "docker-compose.yml tidak ditemukan di $BotDir. Set -BotDir ke folder repo Ochobot."
}

# Token wajib ada, kalau tidak bot akan restart terus dengan "NO_TOKEN".
$token = ''
if (Test-Path -LiteralPath $EnvFile) {
    $line = Get-Content -LiteralPath $EnvFile |
        Where-Object { $_ -match '^\s*TELEGRAM_BOT_TOKEN\s*=' } |
        Select-Object -First 1
    if ($line) {
        $token = ($line -split '=', 2)[1].Trim()
    }
}
if (-not $token) {
    Write-Warning "TELEGRAM_BOT_TOKEN kosong di $EnvFile — bot trading akan restart terus dengan NO_TOKEN."
}
elseif ($token -eq 'your_telegram_bot_token_here') {
    Write-Warning "TELEGRAM_BOT_TOKEN masih placeholder di $EnvFile."
}

Push-Location $BotDir
try {
    $composeArgs = @('compose', 'up', '-d')
    if (-not $NoBuild) { $composeArgs += '--build' }
    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) { throw "docker compose up gagal (exit $LASTEXITCODE)." }
}
finally {
    Pop-Location
}

Start-Sleep -Seconds 10

Write-Host ''
Write-Host 'Status container:'
& docker ps --filter name=ochobot --format 'table {{.Names}}\t{{.Status}}'

Write-Host ''
Write-Host 'Bot Aimapps (Zulip) - container ochobot:'
& docker logs ochobot --tail 5

Write-Host ''
Write-Host 'Bot trading (Telegram) - container ochobot-trading:'
& docker logs ochobot-trading --tail 15
