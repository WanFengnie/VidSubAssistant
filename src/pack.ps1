
$ErrorActionPreference = "Stop"
$SrcRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $SrcRoot
Set-Location $SrcRoot

$APP = "VideoSubtitleHelper_fast"
$DistRoot = Join-Path $RepoRoot "dist"
$BuildRoot = Join-Path $RepoRoot "build"
$OldOut = Join-Path $DistRoot $APP
$ExtraOut = $null


$SettingsStash = Join-Path $env:TEMP "VideoSubtitleHelper_pack_settings.json"
if (Test-Path $SettingsStash) {
  Remove-Item $SettingsStash -Force -ErrorAction SilentlyContinue
}

function Get-SettingsScore([string]$Path) {
  if (-not (Test-Path $Path)) { return -1 }
  try {
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $j = $raw | ConvertFrom-Json
    $score = [int64](Get-Item -LiteralPath $Path).Length
    if ($null -ne $j.prompt_history) {
      $score += 10000 * @($j.prompt_history).Count
    }
    foreach ($k in @("ollama_model", "export_dir", "model_cache_dir", "openai_api_key", "deepl_api_key")) {
      $v = $j.$k
      if ($v -and ([string]$v).Trim().Length -gt 0) { $score += 500 }
    }
    return $score
  } catch {
    return 0
  }
}

$settingsCandidates = @(
  (Join-Path $OldOut "settings.json"),
  (Join-Path $env:LOCALAPPDATA "VideoSubtitleHelper\settings.json")
)
$bestSettings = $null
$bestScore = -1
foreach ($c in $settingsCandidates) {
  $s = Get-SettingsScore $c
  Write-Host ("  settings candidate score={0}: {1}" -f $s, $c)
  if ($s -gt $bestScore) {
    $bestScore = $s
    $bestSettings = $c
  }
}
if ($bestSettings -and $bestScore -ge 0) {
  Copy-Item -LiteralPath $bestSettings -Destination $SettingsStash -Force
  Write-Host "=== stashed settings (best score $bestScore) <- $bestSettings ==="
} else {
  Write-Host "=== no previous settings to stash ==="
}

Write-Host "=== clean old ==="
if (Test-Path $BuildRoot) { Remove-Item -Recurse -Force $BuildRoot }
if (Test-Path $DistRoot) { Remove-Item -Recurse -Force $DistRoot }
Get-ChildItem -Path $SrcRoot, $RepoRoot -Filter "*.spec" -ErrorAction SilentlyContinue |
  Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "=== resolve faster_whisper assets ==="
$assets = (& python -c "import faster_whisper, pathlib; print(pathlib.Path(faster_whisper.__file__).resolve().parent / 'assets')").Trim()
if (-not $assets -or -not (Test-Path $assets)) {
  throw "Cannot locate faster_whisper assets via python import"
}

$IconIco = Join-Path $SrcRoot "assets\app.ico"
if (-not (Test-Path $IconIco)) {
  throw "Missing app icon: $IconIco"
}

Write-Host "=== pyinstaller (no nvidia / no ffmpeg bundle) ==="
$pyArgs = @(
  "-m", "PyInstaller", "--noconfirm", "--clean",
  "--name", $APP, "--windowed", "--onedir",
  "--icon", $IconIco,
  "--distpath", $DistRoot,
  "--workpath", $BuildRoot,
  "--specpath", $RepoRoot,
  "--collect-all", "customtkinter",
  "--collect-all", "faster_whisper",
  "--collect-all", "ctranslate2",
  "--collect-all", "av",
  "--collect-data", "faster_whisper",
  "--collect-data", "tokenizers",
  "--collect-binaries", "onnxruntime",
  "--copy-metadata", "faster_whisper",
  "--copy-metadata", "onnxruntime",
  "--copy-metadata", "tokenizers",
  "--copy-metadata", "huggingface-hub",
  "--exclude-module", "nvidia",
  "--exclude-module", "nvidia.cublas",
  "--exclude-module", "nvidia.cuda_runtime",
  "--exclude-module", "nvidia.cudnn",
  "--exclude-module", "nvidia.cuda_nvrtc",
  "--exclude-module", "nvidia.cuda_cupti",
  "--exclude-module", "nvidia.cufft",
  "--exclude-module", "nvidia.curand",
  "--exclude-module", "nvidia.cusolver",
  "--exclude-module", "nvidia.cusparse",
  "--exclude-module", "nvidia.nccl",
  "--exclude-module", "nvidia.nvjitlink",
  "--exclude-module", "nvidia.nvtx",
  "--add-data", "$assets;faster_whisper/assets",
  "--add-data", "$(Join-Path $SrcRoot 'assets');assets",
  "--exclude-module", "torch", "--exclude-module", "torchaudio", "--exclude-module", "torchvision",
  "--exclude-module", "IPython", "--exclude-module", "jupyter", "--exclude-module", "notebook",
  "--exclude-module", "matplotlib", "--exclude-module", "scipy", "--exclude-module", "pandas",
  "--exclude-module", "tensorboard", "--exclude-module", "deep_translator",
  "--hidden-import", "pipeline", "--hidden-import", "translators",
  "--hidden-import", "huggingface_hub", "--hidden-import", "tokenizers", "--hidden-import", "av",
  "--hidden-import", "onnxruntime",
  "app.py"
)
& python @pyArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }

$outDir = Join-Path $DistRoot $APP
if (-not (Test-Path $outDir)) { throw "Missing output dir: $outDir" }

Write-Host "=== strip bundled ffmpeg / nvidia (if any) ==="
foreach ($name in @("ffmpeg.exe", "ffprobe.exe")) {
  $p = Join-Path $outDir $name
  if (Test-Path $p) {
    Remove-Item $p -Force
    Write-Host "removed $name"
  }
}
$nvidiaDir = Join-Path $outDir "_internal\nvidia"
if (Test-Path $nvidiaDir) {
  Remove-Item $nvidiaDir -Recurse -Force
  Write-Host "removed _internal\nvidia"
}

$internal = Join-Path $outDir "_internal"
if (Test-Path $internal) {
  Get-ChildItem $internal -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(nvidia|nvidia_.*|cublas|cudnn)' } |
    ForEach-Object {
      Write-Host "removed $($_.Name)"
      Remove-Item $_.FullName -Recurse -Force
    }
  Get-ChildItem $internal -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(cublas|cudnn|cudart|nvrtc|nvinfer).*' } |
    ForEach-Object {
      Write-Host "removed file $($_.Name)"
      Remove-Item $_.FullName -Force
    }
}

$vadDst = "$outDir\_internal\faster_whisper\assets"
if (Test-Path (Join-Path $assets "silero_vad_v6.onnx")) {
  New-Item -ItemType Directory -Force -Path $vadDst | Out-Null
  Copy-Item (Join-Path $assets "*") $vadDst -Force
}

if (Test-Path $SettingsStash) {
  $appDataDir = Join-Path $env:LOCALAPPDATA "VideoSubtitleHelper"
  try {
    New-Item -ItemType Directory -Force -Path $appDataDir | Out-Null
    Copy-Item -LiteralPath $SettingsStash -Destination (Join-Path $appDataDir "settings.json") -Force
    Write-Host "settings.json restored to AppData"
  } catch {
    Write-Host "AppData settings sync skipped: $_"
  }
  Remove-Item $SettingsStash -Force -ErrorAction SilentlyContinue
}

foreach ($doc in @("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")) {
  $srcDoc = Join-Path $RepoRoot $doc
  if (Test-Path $srcDoc) {
    Copy-Item $srcDoc (Join-Path $outDir $doc) -Force
    Write-Host "copied $doc"
  }
}


$exe = Join-Path (Resolve-Path $outDir) "$APP.exe"

if ($ExtraOut) {
  Write-Host "=== mirror to $ExtraOut ==="
  if (Test-Path $ExtraOut) { Remove-Item -Recurse -Force $ExtraOut }
  $parent = Split-Path -Parent $ExtraOut
  if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
  Copy-Item $outDir $ExtraOut -Recurse -Force
  Write-Host "mirrored OK"
}

if (Test-Path $BuildRoot) {
  Remove-Item -Recurse -Force $BuildRoot
  Write-Host "=== removed build\ ==="
}

Get-ChildItem -Path $SrcRoot, $RepoRoot -Filter "*.spec" -ErrorAction SilentlyContinue |
  Remove-Item -Force -ErrorAction SilentlyContinue
Write-Host "=== removed *.spec ==="

try {
  $total = (Get-ChildItem $outDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
  Write-Host ("=== package size: {0:N1} MB ===" -f ($total / 1MB))
} catch {}

Write-Host "DONE: $exe"
Write-Host "Note: FFmpeg/CUDA are NOT bundled — install on the machine; app auto-detects."
exit 0
