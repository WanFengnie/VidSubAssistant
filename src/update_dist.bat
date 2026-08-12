@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo   Incremental update (hot reload)
echo   Updates dist\ and code\VideoSubtitleHelper_fast
echo   Does NOT full-pack. For release use pack.bat
echo ========================================
echo.

where powershell >nul 2>&1
if errorlevel 1 (
  echo [ERROR] powershell not found.
  goto :fail
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_dist.ps1"
if errorlevel 1 (
  echo.
  echo [FAIL] update_dist.ps1 failed.
  goto :fail
)

echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
