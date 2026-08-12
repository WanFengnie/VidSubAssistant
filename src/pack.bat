@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo   Video Subtitle Helper - full pack
echo   Source: this folder (src)
echo   Output: ..\dist\VideoSubtitleHelper_fast
echo   No bundled FFmpeg / NVIDIA (system PATH)
echo   Runs pack.ps1 (PyInstaller)
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] python not found. Install Python and add to PATH.
  goto :fail
)

where powershell >nul 2>&1
if errorlevel 1 (
  echo [ERROR] powershell not found.
  goto :fail
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo [INFO] Installing PyInstaller...
  python -m pip install -U pyinstaller
  if errorlevel 1 (
    echo [ERROR] PyInstaller install failed.
    goto :fail
  )
)

echo [START] Packing, please wait...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack.ps1"
if errorlevel 1 (
  echo.
  echo [FAIL] pack.ps1 failed. See errors above.
  goto :fail
)

echo.
echo ========================================
echo   DONE
echo   dist: ..\dist\VideoSubtitleHelper_fast
echo   exe:  ..\dist\VideoSubtitleHelper_fast\VideoSubtitleHelper_fast.exe
echo ========================================
echo.
echo For code-only updates without full pack, run update_dist.bat
echo.
exit /b 0

:fail
echo.
exit /b 1
