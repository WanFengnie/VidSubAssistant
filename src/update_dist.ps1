# Incremental update: copy Python sources into dist/.../hot/ (no full PyInstaller rebuild).
# Sources in src/; packed app under repo dist/.
# Usage:  powershell -ExecutionPolicy Bypass -File src\update_dist.ps1

$ErrorActionPreference = "Stop"
$SrcRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $SrcRoot
Set-Location $SrcRoot

$AppName = "VideoSubtitleHelper_fast"
$Dist = Join-Path $RepoRoot "dist\$AppName"
$Exe = Join-Path $Dist "$AppName.exe"
$Hot = Join-Path $Dist "hot"

if (-not (Test-Path $Exe)) {
    Write-Host "ERROR: $Exe not found. Run full pack once first (src\pack.bat or root pack.bat)."
    exit 1
}

Get-Process -Name $AppName -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path $Hot | Out-Null

$files = @("app.py", "pipeline.py", "translators.py")
foreach ($f in $files) {
    $src = Join-Path $SrcRoot $f
    if (-not (Test-Path $src)) {
        Write-Host "WARN: missing $f"
        continue
    }
    Copy-Item $src (Join-Path $Hot $f) -Force
    Write-Host "updated hot\$f"
}

$settingsSrc = Join-Path $SrcRoot "settings.json"
if (-not (Test-Path $settingsSrc)) {
    $settingsSrc = Join-Path $RepoRoot "settings.json"
}
if (Test-Path $settingsSrc) {
    Copy-Item $settingsSrc (Join-Path $Dist "settings.json") -Force
}

$Internal = Join-Path $Dist "_internal"
if (Test-Path $Internal) {
    foreach ($f in @("pipeline.py", "translators.py")) {
        $src = Join-Path $Hot $f
        if (Test-Path $src) {
            Copy-Item $src (Join-Path $Internal $f) -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host ""
Write-Host "DONE (incremental, no full rebuild)"
Write-Host "  $Exe"
Write-Host "  hot files: $Hot"
Write-Host "Restart the app to load changes."
exit 0
