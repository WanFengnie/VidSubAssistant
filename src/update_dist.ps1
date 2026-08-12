
$ErrorActionPreference = "Stop"
$SrcRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $SrcRoot
Set-Location $SrcRoot

$AppName = "VideoSubtitleHelper_fast"
$Dist = Join-Path $RepoRoot "dist\$AppName"
$Exe = Join-Path $Dist "$AppName.exe"
$ExtraOut = Join-Path (Split-Path -Parent $RepoRoot) $AppName

if (-not (Test-Path $Exe)) {
    Write-Host "ERROR: dist exe not found:"
    Write-Host "  $Exe"
    Write-Host "Run full pack first: pack.bat"
    exit 1
}

Get-Process -Name $AppName -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

function Update-AppTree([string]$Root) {
    if (-not (Test-Path $Root)) {
        Write-Host "SKIP (missing): $Root"
        return
    }
    $rootExe = Join-Path $Root "$AppName.exe"
    if (-not (Test-Path $rootExe)) {
        Write-Host "SKIP (no exe): $Root"
        Write-Host "  (run pack.bat to refresh this folder completely)"
        return
    }

    Write-Host "=== update $Root ==="

    $Hot = Join-Path $Root "hot"
    New-Item -ItemType Directory -Force -Path $Hot | Out-Null

    $files = @("app.py", "pipeline.py", "translators.py")
    foreach ($f in $files) {
        $src = Join-Path $SrcRoot $f
        if (-not (Test-Path $src)) {
            Write-Host "  WARN: missing src\$f"
            continue
        }
        Copy-Item $src (Join-Path $Hot $f) -Force
        Write-Host "  hot\$f"
    }

    $AssetsSrc = Join-Path $SrcRoot "assets"
    $InternalAssets = Join-Path $Root "_internal\assets"
    if ((Test-Path $AssetsSrc) -and (Test-Path (Join-Path $Root "_internal"))) {
        New-Item -ItemType Directory -Force -Path $InternalAssets | Out-Null
        foreach ($name in @("app.ico", "app_icon.png")) {
            $p = Join-Path $AssetsSrc $name
            if (Test-Path $p) {
                Copy-Item $p (Join-Path $InternalAssets $name) -Force
                Write-Host "  _internal\assets\$name"
            }
        }
    }

    foreach ($legacy in @(
        (Join-Path $Root "app.ico"),
        (Join-Path $Root "app_icon.png"),
        (Join-Path $Root "assets"),
        (Join-Path $Hot "assets")
    )) {
        if (Test-Path $legacy) {
            Remove-Item $legacy -Recurse -Force -ErrorAction SilentlyContinue
            Write-Host "  removed duplicate: $(Split-Path $legacy -Leaf)"
        }
    }


    $Internal = Join-Path $Root "_internal"
    if (Test-Path $Internal) {
        foreach ($f in @("pipeline.py", "translators.py", "app.py")) {
            $src = Join-Path $Hot $f
            if (Test-Path $src) {
                Copy-Item $src (Join-Path $Internal $f) -Force -ErrorAction SilentlyContinue
            }
        }
        Write-Host "  _internal\*.py (best-effort)"
    }

    Write-Host "  settings.json left as-is"
}

Update-AppTree $Dist
if (Test-Path $ExtraOut) {
    Update-AppTree $ExtraOut
} else {
    Write-Host "=== no hand-out folder yet: $ExtraOut ==="
    Write-Host "  (created on next full pack.ps1 mirror)"
}

Write-Host ""
Write-Host "DONE (incremental, no full rebuild)"
Write-Host "  dist: $Exe"
if (Test-Path (Join-Path $ExtraOut "$AppName.exe")) {
    Write-Host "  hand-out: $(Join-Path $ExtraOut "$AppName.exe")"
}
Write-Host "  Restart the app to load hot\ changes."
Write-Host "  Clean release without hot\: run pack.bat"
exit 0
