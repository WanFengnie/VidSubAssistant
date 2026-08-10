# Full PyInstaller pack. Sources live in src/; dist/build at repo root.
$ErrorActionPreference = "Stop"
$SrcRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $SrcRoot
Set-Location $SrcRoot

$APP = "VideoSubtitleHelper_fast"
$DistRoot = Join-Path $RepoRoot "dist"
$BuildRoot = Join-Path $RepoRoot "build"

Write-Host "=== clean old ==="
if (Test-Path $BuildRoot) { Remove-Item -Recurse -Force $BuildRoot }
if (Test-Path $DistRoot) { Remove-Item -Recurse -Force $DistRoot }
Get-ChildItem -Path $SrcRoot,$RepoRoot -Filter "*.spec" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "=== resolve faster_whisper assets ==="
$assets = (& python -c "import faster_whisper, pathlib; print(pathlib.Path(faster_whisper.__file__).resolve().parent / 'assets')").Trim()
if (-not $assets -or -not (Test-Path $assets)) {
  throw "Cannot locate faster_whisper assets via python import"
}

Write-Host "=== pyinstaller ==="
$pyArgs = @(
  "-m","PyInstaller","--noconfirm","--clean",
  "--name",$APP,"--windowed","--onedir",
  "--distpath",$DistRoot,
  "--workpath",$BuildRoot,
  "--specpath",$RepoRoot,
  "--collect-all","customtkinter",
  "--collect-all","faster_whisper",
  "--collect-all","ctranslate2",
  "--collect-all","av",
  "--collect-data","faster_whisper",
  "--collect-data","tokenizers",
  "--collect-binaries","onnxruntime",
  "--copy-metadata","faster_whisper",
  "--copy-metadata","onnxruntime",
  "--copy-metadata","tokenizers",
  "--copy-metadata","huggingface-hub",
  "--collect-all","nvidia",
  "--add-data","$assets;faster_whisper/assets",
  "--exclude-module","torch","--exclude-module","torchaudio","--exclude-module","torchvision",
  "--exclude-module","IPython","--exclude-module","jupyter","--exclude-module","notebook",
  "--exclude-module","matplotlib","--exclude-module","scipy","--exclude-module","pandas",
  "--exclude-module","tensorboard","--exclude-module","deep_translator",
  "--hidden-import","pipeline","--hidden-import","translators","--hidden-import","httpx",
  "--hidden-import","huggingface_hub","--hidden-import","tokenizers","--hidden-import","av",
  "--hidden-import","onnxruntime","--hidden-import","windnd",
  "app.py"
)
& python @pyArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }

$outDir = Join-Path $DistRoot $APP
Write-Host "=== ffmpeg ==="
$ff = (Get-Command ffmpeg -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
$fp = (Get-Command ffprobe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
if (-not $ff) {
  $cand = Join-Path $env:USERPROFILE "scoop\apps\ffmpeg\current\bin\ffmpeg.exe"
  if (Test-Path $cand) { $ff = $cand }
}
if (-not $fp -and $ff) {
  $fpCand = Join-Path (Split-Path $ff -Parent) "ffprobe.exe"
  if (Test-Path $fpCand) { $fp = $fpCand }
}
if ($ff -and (Test-Path $ff)) { Copy-Item $ff "$outDir\ffmpeg.exe" -Force; Write-Host "ffmpeg ok" }
else { Write-Host "WARN: ffmpeg not found in PATH" }
if ($fp -and (Test-Path $fp)) { Copy-Item $fp "$outDir\ffprobe.exe" -Force }

$vadDst = "$outDir\_internal\faster_whisper\assets"
if (Test-Path (Join-Path $assets "silero_vad_v6.onnx")) {
  New-Item -ItemType Directory -Force -Path $vadDst | Out-Null
  Copy-Item (Join-Path $assets "*") $vadDst -Force
}

$settingsSrc = Join-Path $SrcRoot "settings.json"
if (-not (Test-Path $settingsSrc)) {
  $settingsSrc = Join-Path $RepoRoot "settings.json"
}
if (Test-Path $settingsSrc) { Copy-Item $settingsSrc "$outDir\settings.json" -Force }

$Hot = Join-Path $outDir "hot"
New-Item -ItemType Directory -Force -Path $Hot | Out-Null
foreach ($f in @("app.py","pipeline.py","translators.py")) {
  $p = Join-Path $SrcRoot $f
  if (Test-Path $p) { Copy-Item $p (Join-Path $Hot $f) -Force }
}

$exe = Join-Path (Resolve-Path $outDir) "$APP.exe"
Write-Host "DONE: $exe"
exit 0
