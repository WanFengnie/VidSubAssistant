@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo   视频字幕助手 - 手动完整打包
echo   源码目录: src\  ^|  产物: 仓库根 dist\
echo   将调用 pack.ps1（PyInstaller 全量打包）
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 并加入 PATH
  goto :fail
)

where powershell >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 powershell
  goto :fail
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo [提示] 未安装 PyInstaller，正在安装…
  python -m pip install -U pyinstaller
  if errorlevel 1 (
    echo [错误] PyInstaller 安装失败
    goto :fail
  )
)

echo [开始] 打包中，请耐心等待…
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack.ps1"
if errorlevel 1 (
  echo.
  echo [失败] 打包未成功，请查看上方报错
  goto :fail
)

echo.
echo ========================================
echo   打包完成
echo   输出目录: ..\dist\VideoSubtitleHelper_fast\
echo   主程序:   ..\dist\VideoSubtitleHelper_fast\VideoSubtitleHelper_fast.exe
echo ========================================
echo.
echo 仅改了 Python 源码、不想全量重打时，可运行:
echo   update_dist.bat
echo   或: powershell -ExecutionPolicy Bypass -File update_dist.ps1
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
