#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installeert Python 3.11+, Tesseract OCR (nld + deu) en de Python-pakketten
    die nodig zijn voor de Mendrix -> RouteXL app.
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Step { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "    >>  $msg" -ForegroundColor Yellow }
function Write-Err  { param($msg) Write-Host "    !!  $msg" -ForegroundColor Red }

function Get-FileFromWeb {
    param([string]$Url, [string]$Dest)
    Write-Warn "Downloaden: $([System.IO.Path]::GetFileName($Dest))"
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile($Url, $Dest)
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

# ============================================================
# 1. Python 3.11+
# ============================================================
Write-Step "Python 3.11+ controleren..."

$pythonCmd = $null
$pythonOk  = $false

foreach ($cmd in @("python", "python3", "py")) {
    try {
        $raw = & $cmd --version 2>&1
        if ($raw -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 11)) {
                $pythonCmd = $cmd
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
    Write-Warn "Python 3.11+ niet gevonden -- installeren..."

    $pyVersion = "3.11.9"
    $pyUrl     = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-amd64.exe"
    $pyInst    = "$env:TEMP\python-$pyVersion-amd64.exe"

    Get-FileFromWeb -Url $pyUrl -Dest $pyInst
    Write-Warn "Python installeren (even geduld)..."
    Start-Process -FilePath $pyInst -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_tcltk=1" -Wait

    Refresh-Path
    Write-Ok "Python $pyVersion geinstalleerd"
    $pythonCmd = "python"
}

try {
    $ver = & $pythonCmd --version 2>&1
    Write-Ok "Actief: $ver"
} catch {
    Write-Err "Python kon niet worden gestart. Herstart de pc en probeer opnieuw."
    exit 1
}

# ============================================================
# 2. Tesseract OCR
# ============================================================
Write-Step "Tesseract OCR controleren..."

$tessExe = $null
foreach ($p in @("C:\Program Files\Tesseract-OCR\tesseract.exe", "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")) {
    if (Test-Path $p) { $tessExe = $p; break }
}
if (-not $tessExe) {
    try { $tessExe = (Get-Command tesseract -ErrorAction Stop).Source } catch { }
}

if ($tessExe) {
    $tessVer = & $tessExe --version 2>&1 | Select-Object -First 1
    Write-Ok "Tesseract gevonden: $tessExe ($tessVer)"
} else {
    Write-Warn "Tesseract niet gevonden -- installeren..."

    try {
        $apiHeaders = @{ "User-Agent" = "MendrixRouteXL-Installer" }
        $release  = Invoke-RestMethod -Uri "https://api.github.com/repos/UB-Mannheim/tesseract/releases/latest" -Headers $apiHeaders
        $asset    = $release.assets | Where-Object { $_.name -match "w64-setup.*\.exe$" } | Select-Object -First 1
        $tessUrl  = $asset.browser_download_url
        Write-Warn "Laatste versie: $($release.tag_name)"
    } catch {
        Write-Warn "GitHub API niet bereikbaar, gebruik fallback-versie 5.4.0"
        $tessUrl = "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
    }

    $tessInst = "$env:TEMP\tesseract-setup.exe"
    Get-FileFromWeb -Url $tessUrl -Dest $tessInst
    Write-Warn "Tesseract installeren (even geduld)..."
    Start-Process -FilePath $tessInst -ArgumentList "/S" -Wait

    Refresh-Path
    $tessExe = "C:\Program Files\Tesseract-OCR\tesseract.exe"
    Write-Ok "Tesseract geinstalleerd"
}

# ============================================================
# 3. Tesseract taalpakketten: nld + deu
# ============================================================
Write-Step "Tesseract taalpakketten (nld + deu) controleren..."

$tessDir     = Split-Path -Parent $tessExe
$tessdataDir = Join-Path $tessDir "tessdata"

if (-not (Test-Path $tessdataDir)) {
    New-Item -ItemType Directory -Path $tessdataDir | Out-Null
}

foreach ($lang in @("nld", "deu")) {
    $dest = Join-Path $tessdataDir "$lang.traineddata"
    if (Test-Path $dest) {
        Write-Ok "Taalpakket '$lang' aanwezig"
    } else {
        Write-Warn "Taalpakket '$lang' ontbreekt -- downloaden (~10 MB)..."
        try {
            Get-FileFromWeb -Url "https://github.com/tesseract-ocr/tessdata/raw/main/$lang.traineddata" -Dest $dest
            Write-Ok "Taalpakket '$lang' gedownload"
        } catch {
            Write-Err "Download van '$lang.traineddata' mislukt: $_"
            Write-Err "Download handmatig van https://github.com/tesseract-ocr/tessdata en kopieer naar: $tessdataDir"
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
& $pythonCmd -m pip install -r $reqFile
if ($LASTEXITCODE -ne 0) {
    Write-Err "pip install mislukt. Controleer de foutmelding hierboven."
    exit 1
}
Write-Ok "Alle pakketten geinstalleerd"

# ============================================================
# 5. start.bat aanmaken
# ============================================================
Write-Step "Snelstartbestand aanmaken..."

$startBat = Join-Path $ScriptDir "start.bat"
if (-not (Test-Path $startBat)) {
    $batLines = '@echo off', 'cd /d "%~dp0"', 'python main.py', 'if %errorLevel% neq 0 pause'
    [System.IO.File]::WriteAllLines($startBat, $batLines, [System.Text.Encoding]::ASCII)
    Write-Ok "start.bat aangemaakt -- dubbelklik hierop om de app te starten"
} else {
    Write-Ok "start.bat bestaat al"
}

# ============================================================
# 6. RouteXL-inloggegevens instellen (optioneel)
# ============================================================
Write-Step "RouteXL-inloggegevens instellen..."
Write-Host ""
Write-Host "  Wil je de RouteXL-inloggegevens nu alvast invullen?" -ForegroundColor White
Write-Host "  (anders vraagt de app ze de eerste keer dat je hem opstart)" -ForegroundColor Gray
Write-Host ""
$antwoord = Read-Host "  Inloggegevens nu instellen? (j/n)"

if ($antwoord -match "^[jJyY]") {
    Write-Host ""
    $rxUser       = Read-Host "  RouteXL gebruikersnaam"
    $rxPassSecure = Read-Host "  RouteXL wachtwoord" -AsSecureString
    $rxPass       = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
                        [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($rxPassSecure))

    if ($rxUser -and $rxPass) {
        $pyLines = @(
            "import json, os, sys",
            "try:",
            "    import keyring",
            "except ImportError:",
            "    sys.exit('keyring niet beschikbaar')",
            "username = sys.argv[1]",
            "password = sys.argv[2]",
            "config_dir  = os.path.join(os.path.expanduser('~'), '.mendrix_routexl')",
            "config_file = os.path.join(config_dir, 'config.json')",
            "os.makedirs(config_dir, exist_ok=True)",
            "with open(config_file, 'w', encoding='utf-8') as f:",
            "    import json as _j; _j.dump({'username': username}, f)",
            "keyring.set_password('MendrixRouteXL', username, password)",
            "print('OK')"
        )
        $tmpPy = "$env:TEMP\save_creds.py"
        [System.IO.File]::WriteAllLines($tmpPy, $pyLines, [System.Text.Encoding]::UTF8)

        $result = & $pythonCmd $tmpPy $rxUser $rxPass 2>&1
        Remove-Item $tmpPy -ErrorAction SilentlyContinue

        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Inloggegevens opgeslagen (gebruikersnaam in config, wachtwoord in Credential Manager)"
        } else {
            Write-Warn "Opslaan mislukt: $result"
            Write-Warn "Je kunt de gegevens de eerste keer dat je de app opstart alsnog invullen."
        }
    } else {
        Write-Warn "Gebruikersnaam of wachtwoord leeg -- overgeslagen."
    }
} else {
    Write-Ok "Overgeslagen -- je vult de gegevens in bij het eerste opstarten van de app."
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
