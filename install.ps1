#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installeert Python 3.11+, Tesseract OCR (nld + deu) en de Python-pakketten
    die nodig zijn voor de Mendrix -> RouteXL app.
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Step  { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "    >>  $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "    !!  $msg" -ForegroundColor Red }

function Get-FileFromWeb {
    param([string]$Url, [string]$Dest, [hashtable]$Headers = @{})
    Write-Warn "Downloaden: $([System.IO.Path]::GetFileName($Dest))"
    $wc = New-Object System.Net.WebClient
    foreach ($k in $Headers.Keys) { $wc.Headers.Add($k, $Headers[$k]) }
    $wc.DownloadFile($Url, $Dest)
}

# ============================================================
# 1. Python 3.11+
# ============================================================
Write-Step "Python 3.11+ controleren..."

$pythonCmd  = $null
$pythonOk   = $false

foreach ($cmd in @("python", "python3", "py -3")) {
    try {
        $raw = Invoke-Expression "$cmd --version 2>&1"
        if ($raw -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 11)) {
                $pythonCmd = $cmd.Split()[0]   # "py" ipv "py -3"
                $pythonOk  = $true
                Write-Ok "Python $maj.$min gevonden ($cmd)"
                break
            } else {
                Write-Warn "Python $maj.$min gevonden maar te oud (minimaal 3.11 nodig)"
            }
        }
    } catch { }
}

if (-not $pythonOk) {
    Write-Warn "Python 3.11+ niet gevonden — installeren..."

    $pyVersion = "3.11.9"
    $pyUrl     = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-amd64.exe"
    $pyInst    = "$env:TEMP\python-$pyVersion-amd64.exe"

    Get-FileFromWeb -Url $pyUrl -Dest $pyInst
    Write-Warn "Python installeren (even geduld)..."
    Start-Process -FilePath $pyInst `
        -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_tcltk=1" `
        -Wait

    # PATH herladen in deze sessie
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") +
                ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")

    Write-Ok "Python $pyVersion geinstalleerd"
    $pythonCmd = "python"
}

# Verificatie
try {
    $ver = & $pythonCmd --version 2>&1
    Write-Ok "Actief: $ver"
} catch {
    Write-Err "Python kon niet worden gestart na installatie. Herstart de pc en probeer opnieuw."
    exit 1
}

# ============================================================
# 2. Tesseract OCR
# ============================================================
Write-Step "Tesseract OCR controleren..."

$tessExe = $null
$tessPaths = @(
    "C:\Program Files\Tesseract-OCR\tesseract.exe",
    "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
)
foreach ($p in $tessPaths) {
    if (Test-Path $p) { $tessExe = $p; break }
}
if (-not $tessExe) {
    try { $tessExe = (Get-Command tesseract -ErrorAction Stop).Source } catch { }
}

if ($tessExe) {
    $tessVer = & $tessExe --version 2>&1 | Select-Object -First 1
    Write-Ok "Tesseract gevonden: $tessExe  ($tessVer)"
} else {
    Write-Warn "Tesseract niet gevonden — installeren..."

    # Haal de laatste release-URL op via de GitHub API
    $apiHeaders = @{ "User-Agent" = "MendrixRouteXL-Installer" }
    try {
        $release = Invoke-RestMethod `
            -Uri "https://api.github.com/repos/UB-Mannheim/tesseract/releases/latest" `
            -Headers $apiHeaders
        $asset = $release.assets | Where-Object { $_.name -match "w64-setup.*\.exe$" } |
                 Select-Object -First 1
        $tessUrl = $asset.browser_download_url
        Write-Warn "Laatste versie: $($release.tag_name)"
    } catch {
        # Fallback naar bekende stabiele versie
        Write-Warn "GitHub API niet bereikbaar, gebruik fallback-versie 5.4.0"
        $tessUrl = "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
    }

    $tessInst = "$env:TEMP\tesseract-setup.exe"
    Get-FileFromWeb -Url $tessUrl -Dest $tessInst
    Write-Warn "Tesseract installeren (even geduld)..."
    Start-Process -FilePath $tessInst -ArgumentList "/S" -Wait

    # PATH herladen
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") +
                ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")

    $tessExe = "C:\Program Files\Tesseract-OCR\tesseract.exe"
    Write-Ok "Tesseract geinstalleerd"
}

# ============================================================
# 3. Tesseract taalpakketten: nld + deu
# ============================================================
Write-Step "Tesseract taalpakketten (nld + deu) controleren..."

$tessDir      = Split-Path -Parent $tessExe
$tessdataDir  = Join-Path $tessDir "tessdata"

if (-not (Test-Path $tessdataDir)) {
    New-Item -ItemType Directory -Path $tessdataDir | Out-Null
}

foreach ($lang in @("nld", "deu")) {
    $dest = Join-Path $tessdataDir "$lang.traineddata"
    if (Test-Path $dest) {
        Write-Ok "Taalpakket '$lang' aanwezig"
    } else {
        Write-Warn "Taalpakket '$lang' ontbreekt — downloaden (~10 MB)..."
        $url = "https://github.com/tesseract-ocr/tessdata/raw/main/$lang.traineddata"
        try {
            Get-FileFromWeb -Url $url -Dest $dest
            Write-Ok "Taalpakket '$lang' gedownload"
        } catch {
            Write-Err "Download van '$lang.traineddata' mislukt: $_"
            Write-Err "Download handmatig van: https://github.com/tesseract-ocr/tessdata"
            Write-Err "Kopieer het bestand naar: $tessdataDir"
        }
    }
}

# ============================================================
# 4. Python-pakketten installeren
# ============================================================
Write-Step "Python-pakketten installeren (requirements.txt)..."

$reqFile = Join-Path $ScriptDir "requirements.txt"
if (-not (Test-Path $reqFile)) {
    Write-Err "requirements.txt niet gevonden in $ScriptDir"
    exit 1
}

& $pythonCmd -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Write-Warn "pip upgrade mislukt, ga verder..." }

& $pythonCmd -m pip install -r $reqFile
if ($LASTEXITCODE -ne 0) {
    Write-Err "pip install mislukt. Controleer de foutmelding hierboven."
    exit 1
}
Write-Ok "Alle pakketten geinstalleerd"

# ============================================================
# 5. start.bat aanmaken (als die er nog niet is)
# ============================================================
Write-Step "Snelstartbestand aanmaken..."

$startBat = Join-Path $ScriptDir "start.bat"
if (-not (Test-Path $startBat)) {
    @"
@echo off
cd /d "%~dp0"
python main.py
if %errorLevel% neq 0 pause
"@ | Out-File -FilePath $startBat -Encoding ascii
    Write-Ok "start.bat aangemaakt — dubbelklik hierop om de app te starten"
} else {
    Write-Ok "start.bat bestaat al"
}

# ============================================================
# Klaar
# ============================================================
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor Green
Write-Host "   Installatie voltooid!" -ForegroundColor Green
Write-Host ""
Write-Host "   Start de app via:  start.bat  (dubbelklikken)" -ForegroundColor Green
Write-Host "  ================================================================" -ForegroundColor Green
Write-Host ""

Read-Host "Druk op Enter om dit venster te sluiten"
