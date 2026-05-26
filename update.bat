@echo off
chcp 65001 >nul
echo.
echo  ================================================================
echo   Mendrix ^> RouteXL  --  Update
echo  ================================================================
echo.

cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "update.ps1"
if %errorLevel% neq 0 (
    echo.
    echo  FOUT: Update mislukt. Lees de foutmelding hierboven.
    pause
    exit /b 1
)
