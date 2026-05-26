#Requires -Version 5.1
<#
.SYNOPSIS
    Download en installeer de laatste versie van Mendrix -> RouteXL vanaf GitHub.
    Overschrijft alleen de Python-bestanden; instellingen blijven intact.
#>

$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ZipUrl = "https://github.com/jeroenk22/mendrix-to-routexl/archive/refs/heads/main.zip"
$TmpZip = "$env:TEMP\mendrix-update.zip"
$TmpDir = "$env:TEMP\mendrix-update"

function Write-Step { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "    >>  $msg" -ForegroundColor Yellow }
function Write-Err  { param($msg) Write-Host "    !!  $msg" -ForegroundColor Red }

# 1. Download
Write-Step "Laatste versie downloaden van GitHub..."

try {
    Invoke-WebRequest -Uri $ZipUrl -OutFile $TmpZip -UseBasicParsing
    Write-Ok "Gedownload"
} catch {
    Write-Err "Download mislukt: $_"
    Write-Err "Controleer je internetverbinding en probeer opnieuw."
    Read-Host "Druk op Enter om dit venster te sluiten"
    exit 1
}

# 2. Uitpakken
Write-Step "Uitpakken..."

if (Test-Path $TmpDir) { Remove-Item $TmpDir -Recurse -Force }
Expand-Archive -Path $TmpZip -DestinationPath $TmpDir
$SrcDir = Get-ChildItem -Path $TmpDir -Directory | Select-Object -First 1 -ExpandProperty FullName
Write-Ok "Uitgepakt"

# 3. Bestanden kopieren naar app-map
Write-Step "Bestanden bijwerken in: $AppDir"

$FilesToCopy = @(
    "main.py", "overlay.py", "ocr_parser.py", "geocoder.py",
    "routexl_api.py", "review_window.py", "config.py", "tomtom_api.py",
    "requirements.txt"
)

foreach ($file in $FilesToCopy) {
    $src = Join-Path $SrcDir $file
    $dst = Join-Path $AppDir $file
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $dst -Force
        Write-Ok $file
    }
}

# 4. Pip-pakketten bijwerken
Write-Step "Python-pakketten controleren..."

$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $v = & $cmd --version 2>&1
        if ($v -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 11) {
            $pythonCmd = $cmd; break
        }
    } catch { }
}

if ($pythonCmd) {
    $reqFile = Join-Path $AppDir "requirements.txt"
    & $pythonCmd -m pip install -r $reqFile --quiet
    Write-Ok "Pakketten up-to-date"
} else {
    Write-Warn "Python niet gevonden - pakketten niet bijgewerkt."
    Write-Warn "Draai install.bat als er problemen zijn bij het starten."
}

# Opruimen
Remove-Item $TmpZip -ErrorAction SilentlyContinue
Remove-Item $TmpDir -Recurse -Force -ErrorAction SilentlyContinue

# Klaar
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor Green
Write-Host "   Update voltooid! Start de app opnieuw via start.bat." -ForegroundColor Green
Write-Host "  ================================================================" -ForegroundColor Green
Write-Host ""
Read-Host "Druk op Enter om dit venster te sluiten"
