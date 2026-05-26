@echo off
chcp 65001 >nul
echo.
echo  ================================================================
echo   Mendrix ^> RouteXL  --  Installatie
echo  ================================================================
echo.

:: Controleer beheerdersrechten (nodig voor Python- en Tesseract-installatie)
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo  Beheerdersrechten zijn nodig.
    echo  Klik op "Ja" in het venster dat zo verschijnt...
    echo.
    powershell -Command "Start-Process -FilePath '%ComSpec%' -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "install.ps1"
if %errorLevel% neq 0 (
    echo.
    echo  FOUT: Installatie mislukt. Lees de foutmelding hierboven.
    pause
    exit /b 1
)
