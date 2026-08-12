from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

ProgressCb = Optional[Callable[[str, float], None]]

_path_cfg: dict = {}
_USER_HF_HOME = (os.environ.get("HF_HOME") or "").strip()
_USER_EXPORTS = (os.environ.get("SUBTITLE_HELPER_EXPORTS") or "").strip()
_USER_MODEL_CACHE = (os.environ.get("SUBTITLE_HELPER_MODEL_CACHE") or "").strip()


def app_base_dir() -> Path:
    """App install / portable root (exe folder when frozen; src when developing)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def user_config_dir() -> Path:
    r"""User configuration root: %LOCALAPPDATA%\VideoSubtitleHelper."""
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        base = Path(local) / "VideoSubtitleHelper"
    else:
        base = Path.home() / ".videosubtitlehelper"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path.home() / ".videosubtitlehelper"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return base


def _read_json_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_path_config() -> dict:
    """Merge path-related keys from settings.json + optional local_paths.json."""
    cfg = user_config_dir()
    merged: dict = {}
    merged.update(_read_json_file(cfg / "settings.json"))
    merged.update(_read_json_file(cfg / "local_paths.json"))
    if _path_cfg:
        merged.update({k: v for k, v in _path_cfg.items() if v not in (None, "")})
    return merged


def set_path_overrides(**kwargs: str) -> None:
    """Runtime overrides from GUI (export_dir / model_cache_dir). Empty string clears key."""
    global _path_cfg
    for k, v in kwargs.items():
        if v is None:
            continue
        s = str(v).strip()
        if s:
            _path_cfg[k] = s
        else:
            _path_cfg.pop(k, None)


def apply_path_config(cfg: Optional[dict] = None) -> dict:
    """Apply model cache env from config. Returns resolved paths."""
    if cfg:
        set_path_overrides(
            export_dir=str(cfg.get("export_dir") or ""),
            model_cache_dir=str(cfg.get("model_cache_dir") or ""),
        )
    cache = resolve_model_cache_dir()
    exports = resolve_export_dir()
    ensure_model_cache_env()
    try:
        exports.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return {"model_cache_dir": str(cache), "export_dir": str(exports), "app_dir": str(app_base_dir())}


def resolve_model_cache_dir() -> Path:
    cfg = load_path_config()
    custom = str(cfg.get("model_cache_dir") or "").strip()
    if custom:
        return Path(custom)
    if _USER_MODEL_CACHE:
        return Path(_USER_MODEL_CACHE)
    if _USER_HF_HOME:
        return Path(_USER_HF_HOME)
    return app_base_dir() / "cache" / "huggingface"


def resolve_export_dir() -> Path:
    cfg = load_path_config()
    custom = str(cfg.get("export_dir") or "").strip()
    if custom:
        return Path(custom)
    env_now = (os.environ.get("SUBTITLE_HELPER_EXPORTS") or "").strip()
    if env_now:
        return Path(env_now)
    if _USER_EXPORTS:
        return Path(_USER_EXPORTS)
    return app_base_dir() / "exports"


def _default_hf_home() -> Path:
    return resolve_model_cache_dir()


class PipelineCancelled(RuntimeError):
    pass

_cancel_flag = False
_active_proc: Optional[subprocess.Popen] = None

def request_cancel() -> None:
    global _cancel_flag, _active_proc
    _cancel_flag = True
    proc = _active_proc
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

def clear_cancel() -> None:
    global _cancel_flag
    _cancel_flag = False

def is_cancelled() -> bool:
    return bool(_cancel_flag)

def check_cancelled() -> None:
    if _cancel_flag:
        raise PipelineCancelled("已停止")

def _subprocess_hide_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    kw: dict = {}

    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    kw["creationflags"] = create_no_window
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
    except Exception:
        pass
    return kw

def _run_hidden(cmd: List[str], **kwargs):
    merged = {**_subprocess_hide_kwargs(), **kwargs}
    return subprocess.run(cmd, **merged)

def _popen_hidden(cmd: List[str], **kwargs) -> subprocess.Popen:
    global _active_proc
    check_cancelled()
    merged = {**_subprocess_hide_kwargs(), **kwargs}
    proc = subprocess.Popen(cmd, **merged)
    _active_proc = proc
    return proc

def ensure_model_cache_env() -> str:
    home = resolve_model_cache_dir()
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError:
        home = app_base_dir() / "cache" / "huggingface"
        try:
            home.mkdir(parents=True, exist_ok=True)
        except OSError:
            home = Path.home() / ".cache" / "huggingface"
            home.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(home)

    hub = home / "hub"
    xformers = home / "transformers"
    try:
        hub.mkdir(parents=True, exist_ok=True)
        xformers.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(hub)
    os.environ["TRANSFORMERS_CACHE"] = str(xformers)
    os.environ["HF_HUB_CACHE"] = str(hub)
    return str(home)


try:
    ensure_model_cache_env()
except Exception:
    pass

def _progress(cb: ProgressCb, message: str, percent: float) -> None:
    check_cancelled()
    if cb:
        cb(message, max(0.0, min(100.0, percent)))
    check_cancelled()

def _looks_like_scoop_shim(path: Path) -> bool:
    try:
        name = path.name.lower()
        parent = path.parent.name.lower()
        if parent == "shims" and "scoop" in str(path).lower():
            return True

        if name.startswith("ffmpeg") and path.is_file() and path.stat().st_size < 2_000_000:

            return True
    except OSError:
        pass
    return False

def _resolve_ffmpeg_candidate(path: Path) -> Optional[str]:
    if not path.is_file():
        return None

    real_scoop = Path.home() / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"
    if _looks_like_scoop_shim(path):
        if real_scoop.is_file():
            return str(real_scoop)
        return None

    try:
        if path.stat().st_size < 2_000_000 and real_scoop.is_file():

            return str(real_scoop)
    except OSError:
        pass

    return str(path)

def find_ffmpeg() -> str:
    """Locate a real ffmpeg binary. Prefer system install (PATH/scoop/winget); optional next to app."""
    candidates: List[Path] = []

    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        candidates.append(app_dir / "ffmpeg.exe")
        candidates.append(app_dir / "_internal" / "ffmpeg.exe")
    else:
        candidates.append(Path(__file__).resolve().parent / "ffmpeg.exe")

    which = shutil.which("ffmpeg")
    if which:
        candidates.append(Path(which))

    home = Path.home()
    candidates.append(home / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe")
    candidates.append(home / "scoop" / "shims" / "ffmpeg.exe")

    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        candidates.append(Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")
        wg = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if wg.is_dir():
            try:
                for p in wg.glob("**/ffmpeg.exe"):
                    candidates.append(p)
                    if len(candidates) > 40:
                        break
            except OSError:
                pass

    for base in (
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "chocolatey" / "bin" / "ffmpeg.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "ffmpeg" / "bin" / "ffmpeg.exe",
    ):
        candidates.append(base)

    seen: set[str] = set()
    for p in candidates:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        resolved = _resolve_ffmpeg_candidate(p)
        if resolved:
            return resolved

    raise FileNotFoundError(
        "未找到可用的 ffmpeg（需本机已安装的真实程序，非 Scoop 空壳）。\n"
        "本程序不内置、也不自动下载 FFmpeg（多为 GPL/LGPL，须自行安装并遵守许可）。\n"
        "安装后一般无需拷贝到软件目录，加入 PATH 即可被自动发现，例如：\n"
        "  winget install Gyan.FFmpeg\n"
        "  scoop install ffmpeg\n"
        "装好后可在命令行执行 ffmpeg -version 验证。\n"
        "也可将完整 ffmpeg.exe（通常 >50MB）放在本程序同目录作为备选。"
    )

def find_ffprobe(ffmpeg_path: str) -> str:
    p = Path(ffmpeg_path)
    probe = p.with_name("ffprobe.exe" if p.suffix.lower() == ".exe" else "ffprobe")
    if probe.is_file() and not _looks_like_scoop_shim(probe):
        try:
            if probe.stat().st_size >= 2_000_000 or not _looks_like_scoop_shim(probe):
                return str(probe)
        except OSError:
            return str(probe)
    which = shutil.which("ffprobe")
    if which:
        wp = Path(which)
        if not _looks_like_scoop_shim(wp):
            return which
        scoop_probe = Path.home() / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffprobe.exe"
        if scoop_probe.is_file():
            return str(scoop_probe)
    scoop_probe = Path.home() / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffprobe.exe"
    if scoop_probe.is_file():
        return str(scoop_probe)
    if probe.is_file():
        return str(probe)
    return str(probe)

def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

@dataclass
class Segment:
    start: float
    end: float
    text: str

@dataclass
class PipelineConfig:
    video_path: str
    output_path: str = ""

    whisper_model: str = "large-v3-turbo"
    source_language: str = "auto"
    device: str = "auto"
    compute_type: str = "auto"

    quality: str = "high"
    vad_threshold: float = 0.35

    glossary: str = ""

    translate: bool = True
    target_language: str = "zh-CN"
    translator: str = "ollama"
    ollama_model: str = ""
    ollama_base_url: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    deepl_api_key: str = ""
    deepl_use_free: bool = True
    baidu_app_id: str = ""
    baidu_app_key: str = ""

    embed_mode: str = "hard"
    font_name: str = "Microsoft YaHei"
    font_size: int = 22
    keep_temp: bool = False
    cleanup_cache: bool = True
    clear_ai_context: bool = True
    resume: bool = True
    max_retries: int = 3

@dataclass
class PipelineResult:
    srt_path: str
    output_video: str = ""
    source_language: str = ""
    segments: List[Segment] = field(default_factory=list)
    resumed_from: str = ""
    job_id: str = ""

def _run_hidden_cancellable(cmd: List[str], *, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    check_cancelled()
    proc = _popen_hidden(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None and proc.stderr is not None
    out_chunks: List[str] = []
    err_chunks: List[str] = []

    def _pump(stream, buf: List[str]) -> None:
        try:
            for line in stream:
                buf.append(line)
        except Exception:
            pass

    import threading

    t_out = threading.Thread(target=_pump, args=(proc.stdout, out_chunks), daemon=True)
    t_err = threading.Thread(target=_pump, args=(proc.stderr, err_chunks), daemon=True)
    t_out.start()
    t_err.start()
    import time

    t0 = time.time()
    while proc.poll() is None:
        if is_cancelled():
            try:
                proc.kill()
            except Exception:
                pass
            t_out.join(timeout=1)
            t_err.join(timeout=1)
            raise PipelineCancelled("已停止")
        if timeout is not None and (time.time() - t0) > timeout:
            try:
                proc.kill()
            except Exception:
                pass
            break
        time.sleep(0.15)
    t_out.join(timeout=2)
    t_err.join(timeout=2)
    global _active_proc
    if _active_proc is proc:
        _active_proc = None
    if is_cancelled():
        raise PipelineCancelled("已停止")
    return subprocess.CompletedProcess(
        cmd,
        proc.returncode if proc.returncode is not None else -1,
        "".join(out_chunks),
        "".join(err_chunks),
    )

def extract_audio(video_path: str, audio_path: str, ffmpeg: str, cb: ProgressCb = None) -> None:
    _progress(cb, "正在提取音频（人声增强）…", 5)

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        video_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-af",
        "highpass=f=70,lowpass=f=7800,loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a",
        "pcm_s16le",
        audio_path,
    ]
    run = _run_hidden_cancellable(cmd)
    if run.returncode != 0:

        cmd2 = [
            ffmpeg,
            "-y",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            audio_path,
        ]
        run2 = _run_hidden_cancellable(cmd2)
        if run2.returncode != 0:
            raise RuntimeError(f"提取音频失败:\n{(run.stderr or '')[-1500:]}\n{(run2.stderr or '')[-1500:]}")

_CUBLAS_DLL_NAMES = (
    "cublas64_13.dll",
    "cublas64_12.dll",
    "cublas64_11.dll",
)


def _add_cuda_layout_dirs(add_fn, root: Path) -> None:
    """Add bin/lib layouts used by CUDA Toolkit (incl. 12/13 bin\\x64) and pip wheels."""
    if not root:
        return
    for cand in (
        root / "bin" / "x64",
        root / "bin",
        root / "lib" / "x64",
        root / "lib",
        root,
    ):
        add_fn(cand)


def _iter_cuda_bin_dirs() -> List[Path]:
    """Directories that may contain CUDA runtime DLLs (12 / 13 / pip nvidia wheels)."""
    dirs: List[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key not in seen and p.is_dir():
            seen.add(key)
            dirs.append(p)

    try:
        import site

        site_dirs = list(site.getsitepackages())
        if hasattr(site, "getusersitepackages"):
            site_dirs.append(site.getusersitepackages())
        for sp in site_dirs:
            nvidia_root = Path(sp) / "nvidia"
            if not nvidia_root.is_dir():
                continue
            for sub in nvidia_root.iterdir():
                _add_cuda_layout_dirs(_add, sub)
    except Exception:
        pass

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        nvidia_root = Path(meipass) / "nvidia"
        if nvidia_root.is_dir():
            for sub in nvidia_root.iterdir():
                _add_cuda_layout_dirs(_add, sub)
        _add(Path(meipass))

    toolkit_roots: List[Path] = []
    for key, val in os.environ.items():
        if not val:
            continue
        ku = key.upper()
        if ku == "CUDA_PATH" or ku.startswith("CUDA_PATH_V"):
            toolkit_roots.append(Path(val.strip()))

    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    for base in (
        pf / "NVIDIA GPU Computing Toolkit" / "CUDA",
        pf / "NVIDIA Corporation" / "CUDA",
    ):
        if base.is_dir():
            try:
                for d in sorted(base.iterdir(), key=lambda x: x.name, reverse=True):
                    if d.is_dir():
                        toolkit_roots.append(d)
            except OSError:
                pass

    uniq_roots: List[Path] = []
    seen_root: set[str] = set()
    for r in toolkit_roots:
        try:
            k = str(r.resolve()).lower()
        except OSError:
            k = str(r).lower()
        if k in seen_root:
            continue
        seen_root.add(k)
        uniq_roots.append(r)
    uniq_roots.sort(key=lambda p: p.name, reverse=True)
    for root in uniq_roots:
        _add_cuda_layout_dirs(_add, root)

    app_dir = (
        Path(getattr(sys, "executable", "")).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    _add_cuda_layout_dirs(_add, app_dir / "cuda_bin")
    _add(app_dir)

    return dirs


_iter_cuda12_bin_dirs = _iter_cuda_bin_dirs


def _setup_cuda13_compat_alias() -> None:
    """If system only has CUDA 13 (cublas64_13.dll), alias/link it as cublas64_12.dll to satisfy CTranslate2."""
    if os.name != "nt":
        return
    try:
        app_data = os.environ.get("LOCALAPPDATA", "")
        if not app_data:
            return
        compat_dir = Path(app_data) / "VideoSubtitleHelper" / "cuda_compat"
        compat_dir.mkdir(parents=True, exist_ok=True)

        cublas13_path = None
        for d in _iter_cuda_bin_dirs():
            candidate = d / "cublas64_13.dll"
            if candidate.is_file():
                cublas13_path = candidate
                break

        if cublas13_path and cublas13_path.is_file():
            target_dll12 = compat_dir / "cublas64_12.dll"
            if not target_dll12.is_file():
                try:
                    import ctypes
                    res = ctypes.windll.kernel32.CreateHardLinkW(str(target_dll12), str(cublas13_path), None)
                    if not res:
                        import shutil
                        shutil.copy2(str(cublas13_path), str(target_dll12))
                except Exception:
                    import shutil
                    shutil.copy2(str(cublas13_path), str(target_dll12))

            cublasLt13 = cublas13_path.parent / "cublasLt64_13.dll"
            if cublasLt13.is_file():
                target_Lt12 = compat_dir / "cublasLt64_12.dll"
                if not target_Lt12.is_file():
                    try:
                        import ctypes
                        ctypes.windll.kernel32.CreateHardLinkW(str(target_Lt12), str(cublasLt13), None)
                    except Exception:
                        pass

            s = str(compat_dir)
            path_parts = os.environ.get("PATH", "").split(os.pathsep)
            if s.lower() not in [p.lower() for p in path_parts if p]:
                path_parts.insert(0, s)
                os.environ["PATH"] = os.pathsep.join(path_parts)
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(s)
                except Exception:
                    pass
    except Exception:
        pass

def ensure_cuda_dll_path() -> List[str]:
    _setup_cuda13_compat_alias()
    added: List[str] = []
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    path_lower = {p.lower() for p in path_parts if p}

    for d in _iter_cuda_bin_dirs():
        s = str(d)
        if s.lower() not in path_lower:
            path_parts.insert(0, s)
            path_lower.add(s.lower())
            added.append(s)

        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(s)
            except (OSError, FileNotFoundError):
                pass

    if added:
        os.environ["PATH"] = os.pathsep.join(path_parts)
    return added


def find_cublas_dll() -> Optional[Path]:
    """Find cuBLAS DLL for CUDA 13 / 12 / 11 (toolkit or pip wheels)."""
    ensure_cuda_dll_path()
    search_dirs = list(_iter_cuda_bin_dirs())
    for part in os.environ.get("PATH", "").split(os.pathsep):
        if part:
            search_dirs.append(Path(part))

    seen: set[str] = set()
    for d in search_dirs:
        try:
            key = str(d.resolve()).lower()
        except OSError:
            key = str(d).lower()
        if key in seen:
            continue
        seen.add(key)
        for name in _CUBLAS_DLL_NAMES:
            p = d / name
            if p.is_file():
                return p
    return None

def _cublas_available() -> bool:
    return find_cublas_dll() is not None

def resolve_device(device: str) -> tuple[str, str]:
    ensure_cuda_dll_path()
    if device == "cpu":
        return "cpu", "int8"
    if device == "cuda":

        return "cuda", "float16"

    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0 and _cublas_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"

def _hf_model_cache_dir(model_size: str) -> Optional[Path]:
    ensure_model_cache_env()
    hub_path = os.environ.get("HUGGINGFACE_HUB_CACHE")
    if not hub_path:
        hf_home = os.environ.get("HF_HOME", "")
        hub_path = str(Path(hf_home) / "hub") if hf_home else ""

    if not hub_path:
        return None

    hub = Path(hub_path)
    if not hub.is_dir():
        return None

    for p in hub.glob(f"*faster-whisper*{model_size}*"):
        if p.is_dir():
            snapshots = p / "snapshots"
            if snapshots.is_dir() and any(snapshots.iterdir()):
                return p
            if any(p.glob("*.bin")) or any(p.glob("*.json")) or any(p.glob("*.onnx")):
                return p
    return None

def _purge_whisper_model_cache(model_size: str, cb: ProgressCb = None) -> None:
    d = _hf_model_cache_dir(model_size)
    if not d:
        return
    _progress(cb, f"正在删除损坏的模型缓存: {d.name} …", 13)
    shutil.rmtree(d, ignore_errors=True)

def _is_corrupt_model_error(err: BaseException) -> bool:
    s = str(err).lower()
    return any(
        k in s
        for k in (
            "parse_error",
            "parse error",
            "unexpected end of input",
            "json.exception",
            "config.json",
            "unable to open file",
            "no such file",
            "failed to open",
            "invalid model",
        )
    )

def _load_whisper_model(model_size: str, device: str, compute_type: str, cb: ProgressCb = None):
    from faster_whisper import WhisperModel

    ensure_model_cache_env()
    dev, ct_hint = resolve_device(device)
    ct = ct_hint if compute_type == "auto" else compute_type

    cached = _hf_model_cache_dir(model_size)
    if cached:
        _progress(cb, f"[模型] 在本地找到已缓存权重，正在快速加载「{model_size}」…", 12)
    else:
        _progress(
            cb,
            f"[模型] 未找到本地缓存，首次使用「{model_size}」将从 Hugging Face 在线下载权重…\n"
            f"（下载后自动保存在本地，后续运行无需重复下载）",
            12,
        )

    def _try(dev_name: str, ct_name: str):
        return WhisperModel(model_size, device=dev_name, compute_type=ct_name), dev_name

    def _load_with_fallback(dev_name: str, ct_name: str):
        try:
            return _try(dev_name, ct_name)
        except Exception as e:
            if not _is_corrupt_model_error(e):
                raise
            _progress(
                cb,
                f"模型文件损坏或为空，清除缓存并按原仓库许可重新下载（{model_size}）…\n{e}",
                13,
            )
            _purge_whisper_model_cache(model_size, cb=cb)
            return _try(dev_name, ct_name)

    try:
        model, dev = _load_with_fallback(dev, ct)
        return model, dev
    except Exception as e:

        if _is_corrupt_model_error(e):

            raise RuntimeError(
                f"Whisper 模型「{model_size}」加载失败（缓存可能已损坏）。\n"
                f"已尝试清除缓存目录: {ensure_model_cache_env()}\n"
                f"请检查网络后重试，或手动删除该模型文件夹后再开。\n原始错误: {e}"
            ) from e
        if dev != "cpu":
            _progress(cb, f"GPU 加载失败，改用 CPU（{e}）…", 14)
            try:
                model, dev = _load_with_fallback("cpu", "int8")
                return model, dev
            except Exception as e2:
                if _is_corrupt_model_error(e2):
                    _purge_whisper_model_cache(model_size, cb=cb)
                    model, dev = _try("cpu", "int8")
                    return model, dev
                raise
        raise

def _normalize_cue_text(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[\"'「」『』【】\[\]()（）…·•、，,。.!！?？:：;；\-—_]+", "", t)
    return t.strip()

_PROMPT_META_MARKERS = (
    "transcribe",
    "transcription",
    "index|translation",
    "do not re-translate",
    "system prompt",
    "user prompt",
    "output format",
    "line count",
    "batch input",
)

def _phrase_chunks(text: str, min_len: int = 6) -> List[str]:
    if not text:
        return []
    parts = re.split(r"[\n。；;！!？?，,、/|]+", text)
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if len(p) >= min_len:
            out.append(p)

        if len(p) >= 16:
            for i in range(0, len(p) - min_len + 1, 4):
                chunk = p[i : i + max(min_len, min(18, len(p) - i))]
                if len(chunk) >= min_len:
                    out.append(chunk)
    return out

def build_prompt_leak_corpus(lang: Optional[str], special: str = "") -> List[str]:
    bases = [
        "影视对白字幕，简体中文口语转写。",
        "映画・ドラマの会話。自然な日本語で書き起こす。",
        "Film or drama dialogue. Transcribe spoken English accurately.",
        "영화·드라마 대화. 자연스러운 한국어로 받아적기.",
        "Dialogue de film. Transcrire le français parlé.",
        "Filmdialog. Gesprochenes Deutsch genau transkribieren.",
        "Diálogo de película. Transcribir español hablado.",
        "Кинодиалог. Точная транскрипция разговорного русского.",
        "Dialogue transcription.",
        "嗯。好的。我们走吧。",
        "Yeah. Okay. Let's go.",
        "うん。わかった。行こう。",
    ]
    corpus: List[str] = list(bases)
    if special and special.strip():
        corpus.append(special.strip())
        corpus.extend(_phrase_chunks(special.strip(), min_len=5))

    seen: set[str] = set()
    out: List[str] = []
    for c in corpus:
        k = c.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out

def looks_like_prompt_leak(text: str, corpus: Optional[Sequence[str]] = None) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    low = t.lower()

    hit_meta = sum(1 for m in _PROMPT_META_MARKERS if m.lower() in low or m in t)
    if hit_meta >= 1 and len(t) <= 80:
        return True
    if hit_meta >= 2:
        return True

    if t.count("；") + t.count(";") >= 2 and len(t) >= 12:
        return True
    if t.count("：") + t.count(":") >= 3 and len(t) <= 80:
        return True

    corpus = list(corpus or [])
    nt = _normalize_cue_text(t)
    for c in corpus:
        if not c or len(c.strip()) < 5:
            continue
        if c in t or t in c:

            if len(t) <= len(c) + 8 or len(c) <= len(t) + 8:
                return True
        nc = _normalize_cue_text(c)
        if nc and len(nc) >= 6 and (nc in nt or nt in nc):
            if min(len(nt), len(nc)) >= 6:
                return True
        for ph in _phrase_chunks(c, min_len=8):
            if ph in t and len(ph) >= 8:
                return True
            nph = _normalize_cue_text(ph)
            if nph and len(nph) >= 8 and nph in nt:
                return True
    return False

def strip_prompt_leak(text: str, corpus: Optional[Sequence[str]] = None) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if looks_like_prompt_leak(t, corpus):

        cleaned = t
        for c in corpus or []:
            for ph in sorted(_phrase_chunks(c, min_len=5), key=len, reverse=True):
                if ph and ph in cleaned:
                    cleaned = cleaned.replace(ph, " ")
            if c and c in cleaned:
                cleaned = cleaned.replace(c, " ")
        for m in _PROMPT_META_MARKERS:
            if m and m in cleaned:
                cleaned = cleaned.replace(m, " ")
            ml = m.lower()

            cleaned = re.sub(re.escape(m), " ", cleaned, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" 　-—|;；:：,，.")
        if not cleaned or looks_like_prompt_leak(cleaned, corpus) or _is_hallucination_text(cleaned):
            return ""
        return cleaned
    return t

def _bare_subtitle_text(text: str) -> str:
    """Strip punctuation/spaces for spam / mono-token checks."""
    t = (text or "").strip()
    t = re.sub(r"[\s\u3000。、．，,．.!！？?…・·\-—–~～「」『』【】\[\]()（）\"'“”‘’]", "", t)
    return t


def _is_hallucination_text(text: str) -> bool:
    t = text.strip()
    if not t:
        return True

    compact = re.sub(r"\s+", "", t)
    bare = _bare_subtitle_text(t)

    if bare and re.fullmatch(r"[\d０-９]+", bare):
        return True
    if bare in {"二", "２", "2", "ニ", "に"} and len(compact) <= 2:
        return True

    if len(bare) >= 3 and len(set(bare)) == 1:
        return True
    if len(compact) >= 6 and len(set(compact)) <= 2:
        return True

    words = re.findall(r"\S+", t)
    if len(words) >= 4:
        uniq = set(w.lower() for w in words)
        if len(uniq) <= 2:
            return True

    if re.search(r"(.{2,24}?)\1{2,}", compact):
        return True
    if re.search(r"(\S{1,20})(?:\s+\1){3,}", t, flags=re.I):
        return True

    junk = (
        "thank you for watching",
        "thanks for watching",
        "please subscribe",
        "subscribe",
        "see you next time",
        "to be continued",
        "ご視聴ありがとうございました",
        "視聴ありがとうございました",
        "チャンネル登録",
        "おやすみなさい",
        "字幕by",
        "www.",
        "amara.org",
        "mbc",
        "tvn",
        "music",
        "♪",
        "♫",
        "【音乐】",
        "[音乐]",
        "(音乐)",
        "（音乐）",
        "字幕志愿者",
        "translated by",
        "subtitles by",
        "www",
        ".com",
        "感谢收看",
        "謝謝收看",
        "晚安",
    )
    low = t.lower()
    if any(j in low or j in t for j in junk):
        if len(words) <= 12 or len(compact) <= 40:
            return True

    if not re.search(r"[\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", t):
        return True
    return False

def _texts_near_duplicate(a: str, b: str) -> bool:
    na, nb = _normalize_cue_text(a), _normalize_cue_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True

    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 2 and shorter in longer and len(longer) <= len(shorter) * 1.35 + 2:
        return True
    return False

def dedupe_repeat_segments(segments: List[Segment]) -> List[Segment]:
    """Collapse stutters and kill mono-token / credit loops without wiping real dialogue.

    Real JP backchannels (はい/なに) may repeat a few times far apart — keep those.
    Whisper loops like hundreds of \"2\"/\"二\" or おやすみなさい must die.
    """
    if not segments:
        return []

    collapsed: List[Segment] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text or _is_hallucination_text(text):
            continue
        if collapsed and _texts_near_duplicate(collapsed[-1].text, text):
            prev = collapsed[-1]
            gap = float(seg.start) - float(prev.end)
            if gap <= 0.8:
                collapsed[-1] = Segment(
                    start=prev.start,
                    end=max(float(prev.end), float(seg.end)),
                    text=prev.text,
                )
                continue
        collapsed.append(Segment(start=float(seg.start), end=float(seg.end), text=text))

    if not collapsed:
        return []

    norms = [_normalize_cue_text(s.text) for s in collapsed]
    counts = Counter(n for n in norms if n)
    total = len(collapsed)

    spam_drop_all: set[str] = set()
    spam_keep_one: set[str] = set()
    for k, c in counts.items():
        if not k:
            continue
        bare = _bare_subtitle_text(k)
        short = len(bare) <= 3
        if bare and (re.fullmatch(r"[\d０-９]+", bare) or bare in {"二", "２", "2", "ニ"}):
            if c >= 2:
                spam_drop_all.add(k)
            continue
        if short and c >= 8:
            spam_drop_all.add(k)
            continue
        if short and c >= 5 and c / max(total, 1) >= 0.05:
            spam_drop_all.add(k)
            continue
        if len(k) >= 6 and c >= 6 and (c / max(total, 1) >= 0.06 or c >= 10):
            spam_keep_one.add(k)

    out: List[Segment] = []
    seen_once: set[str] = set()
    for seg, n in zip(collapsed, norms):
        if n in spam_drop_all:
            continue
        if n in spam_keep_one:
            if n in seen_once:
                continue
            seen_once.add(n)
        out.append(seg)

    final: List[Segment] = []
    run_n = ""
    run_len = 0
    for seg in out:
        n = _normalize_cue_text(seg.text)
        bare = _bare_subtitle_text(n)
        is_short = len(bare) <= 3
        if final and n == run_n and is_short:
            run_len += 1
            if run_len >= 2:
                continue
        else:
            run_n = n
            run_len = 1

        drop = False
        for prev in final[-3:]:
            if not _texts_near_duplicate(prev.text, seg.text):
                continue
            gap = float(seg.start) - float(prev.end)
            if gap < 1.2 and n == _normalize_cue_text(prev.text):
                drop = True
                break
            if gap < 0.6:
                drop = True
                break
        if not drop:
            final.append(seg)
    return final

def _is_cjk_heavy(text: str) -> bool:
    if not text:
        return False
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af")
    return cjk >= max(1, len(text.replace(" ", "")) // 3)

def _split_subtitle_text(text: str, max_chars: int) -> List[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []

    parts = re.split(r"(?<=[。！？.!?…])\s*", text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        parts = re.split(r"(?<=[，,；;、])\s*", text)
        parts = [p.strip() for p in parts if p.strip()]
    refined: List[str] = []
    for p in parts:
        if len(p) <= max_chars:
            refined.append(p)
            continue
        if _is_cjk_heavy(p):

            buf = ""
            for ch in p:
                buf += ch
                if len(buf) >= max_chars and ch in "，,、；; ":
                    refined.append(buf.strip())
                    buf = ""
                elif len(buf) >= max_chars + 4:
                    refined.append(buf.strip())
                    buf = ""
            if buf.strip():
                refined.append(buf.strip())
        else:
            words = p.split()
            if len(words) <= 1:
                refined.append(p)
                continue
            buf_w: List[str] = []
            for w in words:
                trial = (" ".join(buf_w + [w])).strip()
                if buf_w and len(trial) > max_chars:
                    refined.append(" ".join(buf_w))
                    buf_w = [w]
                else:
                    buf_w.append(w)
            if buf_w:
                refined.append(" ".join(buf_w))

    merged: List[str] = []
    for p in refined:
        if merged and len(p) <= 2 and _is_cjk_heavy(merged[-1]):
            merged[-1] = merged[-1] + p
        elif merged and len(p) <= 4 and not _is_cjk_heavy(p) and len(merged[-1]) + len(p) < max_chars:
            merged[-1] = (merged[-1] + " " + p).strip()
        else:
            merged.append(p)
    return merged or [text]

def refine_segments(
    segments: List[Segment],
    max_duration: float = 6.5,
    max_chars: int = 0,
) -> List[Segment]:

    raw: List[Segment] = []
    for seg in segments:
        text = re.sub(r"\s+", " ", (seg.text or "").strip())
        if not text or _is_hallucination_text(text):
            continue
        start, end = float(seg.start), float(seg.end)
        if end <= start:
            end = start + 1.5
        dur = end - start
        limit = max_chars or (18 if _is_cjk_heavy(text) else 42)
        need_split = dur > max_duration * 1.35 or len(text) > limit
        if need_split:
            parts = _split_subtitle_text(text, limit)
            if len(parts) <= 1:
                if dur > 20:
                    end = start + min(dur, 12.0)
                raw.append(Segment(start=start, end=end, text=text))
                continue
            n = len(parts)
            weights = [max(1, len(p)) for p in parts]
            total_w = sum(weights) or n
            cursor = start
            for i, (p, w) in enumerate(zip(parts, weights)):
                if i == n - 1:
                    seg_end = end
                else:
                    seg_end = cursor + dur * (w / total_w)
                    seg_end = min(seg_end, end)
                if seg_end - cursor < 0.35:
                    seg_end = min(end, cursor + 0.55)
                if not _is_hallucination_text(p):
                    raw.append(Segment(start=cursor, end=seg_end, text=p))
                cursor = seg_end
        else:
            if dur > 18:
                end = start + min(dur, 11.0)
            raw.append(Segment(start=start, end=end, text=text))

    if not raw:
        return []

    out: List[Segment] = [raw[0]]
    for seg in raw[1:]:
        prev = out[-1]
        gap = seg.start - prev.end
        prev_cjk = _is_cjk_heavy(prev.text)
        limit = 18 if prev_cjk else 42
        if (
            gap >= 0
            and gap <= 0.35
            and (seg.end - prev.start) <= max_duration
            and len(prev.text) + len(seg.text) + 1 <= limit
            and not prev.text.endswith(("。", "！", "？", ".", "!", "?"))
        ):
            joiner = "" if prev_cjk and _is_cjk_heavy(seg.text) else " "
            out[-1] = Segment(
                start=prev.start,
                end=seg.end,
                text=(prev.text + joiner + seg.text).strip(),
            )
        else:

            if seg.start < prev.end:
                seg = Segment(start=prev.end, end=max(prev.end + 0.3, seg.end), text=seg.text)
            out.append(seg)
    return out

def _quality_asr_params(quality: str) -> dict:
    """ASR decode + light post-filter thresholds.

    Prefer recall: soft/intimate/JP dialogue often has low avg_logprob and mid
    no_speech_prob — aggressive filters were wiping most of long adult videos.
    """
    q = (quality or "balanced").lower().strip()

    if q == "fast":
        return dict(
            beam_size=3,
            best_of=3,
            patience=0.8,
            condition_on_previous_text=False,
            compression_ratio_threshold=2.6,
            log_prob_threshold=-1.2,
            no_speech_threshold=0.72,
            word_timestamps=False,
            temperature=0.0,
            vad_parameters=dict(
                threshold=0.34,
                min_speech_duration_ms=80,
                min_silence_duration_ms=300,
                speech_pad_ms=220,
            ),
            min_avg_logprob=-1.95,
            max_no_speech_prob=0.94,
            max_compression_ratio=2.9,
            soft_no_speech=0.9,
            soft_logprob=-1.45,
        )
    if q in ("high", "best"):
        return dict(
            beam_size=5,
            best_of=5,
            patience=1.0,
            condition_on_previous_text=False,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.2,
            no_speech_threshold=0.75,
            word_timestamps=False,
            temperature=[0.0, 0.2, 0.4],
            vad_parameters=dict(
                threshold=0.28,
                min_speech_duration_ms=60,
                min_silence_duration_ms=220,
                speech_pad_ms=280,
            ),
            min_avg_logprob=-2.2,
            max_no_speech_prob=0.97,
            max_compression_ratio=2.6,
            soft_no_speech=0.95,
            soft_logprob=-1.8,
        )
    if q in ("dialogue", "conversation", "talk", "oral", "口语", "完整"):
        return dict(
            beam_size=5,
            best_of=5,
            patience=1.2,
            condition_on_previous_text=False,
            compression_ratio_threshold=2.5,
            log_prob_threshold=-1.3,
            no_speech_threshold=0.82,
            word_timestamps=False,
            temperature=[0.0, 0.2, 0.4, 0.6],
            vad_parameters=dict(
                threshold=0.22,
                min_speech_duration_ms=40,
                min_silence_duration_ms=180,
                speech_pad_ms=320,
            ),
            min_avg_logprob=-2.5,
            max_no_speech_prob=0.99,
            max_compression_ratio=2.7,
            soft_no_speech=0.98,
            soft_logprob=-2.0,
        )

    return dict(
        beam_size=5,
        best_of=5,
        patience=1.0,
        condition_on_previous_text=False,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.2,
        no_speech_threshold=0.72,
        word_timestamps=False,
        temperature=[0.0, 0.2, 0.4],
        vad_parameters=dict(
            threshold=0.32,
            min_speech_duration_ms=80,
            min_silence_duration_ms=280,
            speech_pad_ms=240,
        ),
        min_avg_logprob=-2.0,
        max_no_speech_prob=0.95,
        max_compression_ratio=2.6,
        soft_no_speech=0.9,
        soft_logprob=-1.5,
    )

def parse_glossary(glossary: str) -> List[str]:
    if not glossary or not str(glossary).strip():
        return []
    raw = str(glossary).strip()

    if len(raw) > 80 or "。" in raw or ". " in raw or "：" in raw or ":" in raw:

        m = re.search(
            r"(?:常见名|专有名词|Proper names|Names)\s*[:：]\s*(.+)$",
            raw,
            flags=re.I | re.S,
        )
        if m:
            raw = m.group(1).strip()
        else:
            return []
    parts = re.split(r"[,，;；\n|/]+", raw)
    out: List[str] = []
    seen: set[str] = set()
    for p in parts:
        t = p.strip()
        if not t or len(t) > 40:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out[:80]

def _initial_prompt_for_lang(lang: Optional[str], glossary: str = "") -> Optional[str]:
    l = (lang or "").lower()

    if l.startswith("zh"):
        seed = "嗯。好的。我们走吧。"
    elif l.startswith("ja"):
        seed = "うん。わかった。行こう。"
    elif l.startswith("en"):
        seed = "Yeah. Okay. Let's go."
    elif l.startswith("ko"):
        seed = "응. 알겠어. 가자."
    elif l.startswith("fr"):
        seed = "Ouais. D'accord. On y va."
    elif l.startswith("de"):
        seed = "Ja. Okay. Los geht's."
    elif l.startswith("es"):
        seed = "Sí. Vale. Vamos."
    elif l.startswith("ru"):
        seed = "Да. Хорошо. Пойдём."
    else:
        seed = "Yeah. Okay."

    names = parse_glossary(glossary or "")
    if names:

        name_part = " ".join(names[:24])
        return f"{seed} {name_part}".strip()[:400]
    return seed

def transcribe(
    audio_path: str,
    model_size: str,
    language: str,
    device: str,
    compute_type: str,
    cb: ProgressCb = None,
    quality: str = "balanced",
    glossary: str = "",
    vad_threshold: float = 0.35,
) -> tuple[List[Segment], str]:
    _progress(cb, f"加载 Whisper 模型 ({model_size}, 质量={quality}, VAD={vad_threshold:.2f})…", 12)
    model, dev = _load_whisper_model(model_size, device, compute_type, cb)

    _progress(cb, f"语音识别中（{dev} / {quality} / VAD={vad_threshold:.2f}）…", 18)
    raw_lang = (language or "").strip()
    if raw_lang.lower() in ("", "auto", "detect"):
        lang = None
    else:
        low = raw_lang.lower()
        if low.startswith("zh"):
            lang = "zh"
        else:
            lang = raw_lang.split("-")[0].split("_")[0] or raw_lang
    qparams = _quality_asr_params(quality)
    min_lp = float(qparams.pop("min_avg_logprob", -1.45))
    max_no_speech = float(qparams.pop("max_no_speech_prob", 0.82))
    max_cr = float(qparams.pop("max_compression_ratio", 2.45))
    soft_no_speech = float(qparams.pop("soft_no_speech", 0.62))
    soft_logprob = float(qparams.pop("soft_logprob", -1.0))
    vad_parameters = qparams.pop("vad_parameters", None)
    if vad_parameters and vad_threshold > 0:
        vad_parameters = dict(vad_parameters)
        vad_parameters["threshold"] = vad_threshold
    leak_corpus = build_prompt_leak_corpus(lang, glossary or "")

    def _run(
        m,
        vad_filter: bool = True,
        force_lang: Optional[str] = None,
        *,
        relax_filters: bool = False,
    ):
        use_lang = force_lang if force_lang is not None else lang
        temp = qparams["temperature"]
        use_min_lp = min_lp if not relax_filters else min(min_lp, -2.8)
        use_max_ns = max_no_speech if not relax_filters else max(max_no_speech, 0.995)
        use_soft_ns = soft_no_speech if not relax_filters else 0.995
        use_soft_lp = soft_logprob if not relax_filters else -2.5
        use_max_cr = max_cr if not relax_filters else max(max_cr, 3.5)

        kwargs = dict(
            language=use_lang,
            task="transcribe",
            vad_filter=vad_filter,
            beam_size=qparams["beam_size"],
            best_of=qparams["best_of"],
            patience=qparams["patience"],
            condition_on_previous_text=qparams["condition_on_previous_text"],
            compression_ratio_threshold=qparams["compression_ratio_threshold"],
            log_prob_threshold=qparams["log_prob_threshold"],
            no_speech_threshold=qparams["no_speech_threshold"],
            word_timestamps=qparams["word_timestamps"],
            temperature=temp,
        )

        prompt = _initial_prompt_for_lang(use_lang, glossary=glossary)
        if prompt:
            kwargs["initial_prompt"] = prompt

            for p in _phrase_chunks(prompt, min_len=4):
                if p not in leak_corpus:
                    leak_corpus.append(p)
        if vad_filter and vad_parameters:
            kwargs["vad_parameters"] = dict(vad_parameters)
        segments_iter, info = m.transcribe(audio_path, **kwargs)
        segments: List[Segment] = []
        dropped_lp = 0
        dropped_silence = 0
        dropped_prompt = 0
        total_hint = max(float(getattr(info, "duration", 0) or 0), 1.0)
        last_kept_norm = ""
        last_kept_end = -999.0
        for seg in segments_iter:
            text = (seg.text or "").strip()
            if not text or _is_hallucination_text(text):
                continue
            text = strip_prompt_leak(text, leak_corpus)
            if not text or _is_hallucination_text(text) or looks_like_prompt_leak(text, leak_corpus):
                dropped_prompt += 1
                continue
            avg_lp = getattr(seg, "avg_logprob", None)
            no_speech = getattr(seg, "no_speech_prob", None)
            comp = getattr(seg, "compression_ratio", None)

            if no_speech is not None and float(no_speech) >= use_max_ns:
                dropped_silence += 1
                continue
            if avg_lp is not None and float(avg_lp) < use_min_lp:
                dropped_lp += 1
                continue

            if (
                no_speech is not None
                and avg_lp is not None
                and float(no_speech) >= use_soft_ns
                and float(avg_lp) < use_soft_lp
            ):
                dropped_silence += 1
                continue
            if comp is not None and float(comp) > use_max_cr:
                dropped_lp += 1
                continue

            norm = _normalize_cue_text(text)
            if norm and norm == last_kept_norm and (float(seg.start) - last_kept_end) < 0.9:
                dropped_silence += 1
                continue

            if norm in {
                _normalize_cue_text("嗯。好的。我们走吧。"),
                _normalize_cue_text("Yeah. Okay. Let's go."),
                _normalize_cue_text("うん。わかった。行こう。"),
            }:
                dropped_prompt += 1
                continue
            segments.append(Segment(start=float(seg.start), end=float(seg.end), text=text))
            last_kept_norm = norm
            last_kept_end = float(seg.end)
            pct = 18 + min(42.0, (float(seg.end) / total_hint) * 42.0)
            _progress(cb, f"识别中… {format_timestamp(seg.end)} ({len(segments)}段)", pct)
        detected = getattr(info, "language", use_lang) or use_lang or "unknown"
        lang_prob = float(getattr(info, "language_probability", 0) or 0)
        drops = {
            "lp": dropped_lp,
            "silence": dropped_silence,
            "prompt": dropped_prompt,
            "total": dropped_lp + dropped_silence + dropped_prompt,
            "duration": total_hint,
        }
        return segments, detected, lang_prob, drops

    def _coverage(segs: List[Segment], audio_dur: float) -> float:
        if not segs or audio_dur <= 1:
            return 0.0
        speech = sum(max(0.0, float(s.end) - float(s.start)) for s in segs)
        return speech / audio_dur

    def _is_sparse(segs: List[Segment], drops: dict) -> bool:
        if not segs:
            return True
        audio_dur = float(drops.get("duration") or 0) or max(s.end for s in segs)
        cov = _coverage(segs, audio_dur)
        n = len(segs)
        drop_n = int(drops.get("total") or 0)
        if audio_dur >= 300 and n < max(20, audio_dur / 25):
            return True
        if cov < 0.12 and audio_dur >= 60:
            return True
        if drop_n >= max(15, n * 2) and cov < 0.25:
            return True
        return False

    try:
        segments, detected, lang_prob, drop_info = _run(model, vad_filter=True)
    except Exception as e:
        err = str(e).lower()
        vad_related = any(
            k in err
            for k in ("silero", "vad", "no_suchfile", "nosuchfile", ".onnx", "onnxruntime")
        )
        cuda_related = any(
            k in err for k in ("cublas", "cuda", "cudnn", "nvrtc", "cufft", "library")
        )
        if vad_related:
            _progress(cb, "VAD 模型不可用，改为不使用 VAD 继续…", 18)
            segments, detected, lang_prob, drop_info = _run(model, vad_filter=False)
        elif dev != "cpu" and cuda_related:
            _progress(cb, f"GPU 推理失败，改用 CPU（{e}）…", 16)
            model, dev = _load_whisper_model(model_size, "cpu", "int8", cb)
            _progress(cb, f"语音识别中（{dev}）…", 18)
            try:
                segments, detected, lang_prob, drop_info = _run(model, vad_filter=True)
            except Exception as e2:
                err2 = str(e2).lower()
                if any(k in err2 for k in ("silero", "vad", "no_suchfile", ".onnx")):
                    _progress(cb, "VAD 不可用，关闭 VAD 重试…", 18)
                    segments, detected, lang_prob, drop_info = _run(model, vad_filter=False)
                else:
                    raise
        else:
            raise

    try:
        if _is_sparse(segments, drop_info):
            audio_dur = float(drop_info.get("duration") or 0)
            cov0 = _coverage(segments, audio_dur) if segments else 0.0
            _progress(
                cb,
                f"覆盖偏低（{len(segments)}段, 语音占比{cov0:.0%}），关闭 VAD 并放宽过滤重试…",
                20,
            )
            segments2, detected2, lang_prob2, drop2 = _run(
                model, vad_filter=False, relax_filters=True
            )
            cov2 = _coverage(segments2, float(drop2.get("duration") or audio_dur or 1))
            if len(segments2) > len(segments) or cov2 > cov0 + 0.03:
                segments, detected, lang_prob, drop_info = (
                    segments2,
                    detected2,
                    lang_prob2,
                    drop2,
                )
                _progress(
                    cb,
                    f"重试保留更多：{len(segments)} 段，语音占比 {cov2:.0%}",
                    22,
                )
    except Exception:
        pass

    if language in ("", "auto", "detect") and lang_prob and lang_prob < 0.65:
        _progress(
            cb,
            f"语言检测把握较低 ({detected}, {lang_prob:.0%})，可手动指定源语言",
            58,
        )
    dropped_total = int(drop_info.get("total") or 0)
    if dropped_total:
        _progress(
            cb,
            f"过滤丢弃 {dropped_total} 条"
            f"（低置信{drop_info.get('lp', 0)} / 静音{drop_info.get('silence', 0)} / 提示词{drop_info.get('prompt', 0)}）",
            58,
        )

    before = len(segments)
    audio_dur = float(drop_info.get("duration") or 0)
    if segments and audio_dur <= 0:
        audio_dur = max(s.end for s in segments)

    scrubbed: List[Segment] = []
    for s in segments:
        t = strip_prompt_leak(s.text, leak_corpus)
        if t and not _is_hallucination_text(t) and not looks_like_prompt_leak(t, leak_corpus):
            scrubbed.append(Segment(start=s.start, end=s.end, text=t))
    segments = scrubbed
    segments = refine_segments(segments)
    after_refine = len(segments)
    segments = dedupe_repeat_segments(segments)

    segments = [
        Segment(start=s.start, end=s.end, text=t)
        for s in segments
        for t in [strip_prompt_leak(s.text, leak_corpus)]
        if t and not looks_like_prompt_leak(t, leak_corpus) and not _is_hallucination_text(t)
    ]
    cov_final = _coverage(segments, audio_dur) if audio_dur > 0 else 0.0
    _progress(
        cb,
        f"识别完成：{before} → 断句 {after_refine} → 清理后 {len(segments)} 段"
        f"（语言: {detected}，语音占比约 {cov_final:.0%}）",
        60,
    )
    return segments, detected

def write_srt(segments: Sequence[Segment], srt_path: str) -> None:
    lines: List[str] = []
    for i, seg in enumerate(segments, 1):
        text = seg.text.replace("\n", " ").strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{format_timestamp(seg.start)} --> {format_timestamp(seg.end)}")
        lines.append(text)
        lines.append("")
    path = Path(srt_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text("\n".join(lines), encoding="utf-8-sig")

def parse_timestamp(ts: str) -> float:
    ts = (ts or "").strip().replace(".", ",")
    m = re.match(r"(\d+):(\d+):(\d+),(\d+)", ts)
    if not m:
        return 0.0
    h, mi, s, ms = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
    return h * 3600 + mi * 60 + s + ms / 1000.0

def read_srt(srt_path: str) -> List[Segment]:
    path = Path(srt_path)
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\n\s*\n", raw.strip(), flags=re.M)
    out: List[Segment] = []
    for block in blocks:
        lines = [ln.strip("\r") for ln in block.splitlines() if ln.strip() != ""]
        if len(lines) < 2:
            continue

        i = 0
        if re.match(r"^\d+$", lines[0].strip()):
            i = 1
        if i >= len(lines):
            continue
        m = re.match(
            r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})",
            lines[i].strip(),
        )
        if not m:
            continue
        start, end = parse_timestamp(m.group(1)), parse_timestamp(m.group(2))
        text = " ".join(lines[i + 1 :]).strip()
        if text:
            out.append(Segment(start=start, end=end, text=text))
    return out

STAGES = ("init", "audio", "asr", "translate", "srt", "embed", "done")

def _safe_job_id(video_path: str) -> str:
    import hashlib

    p = Path(video_path)
    stem = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", p.stem)[:80].strip("_") or "job"
    try:
        key = str(p.resolve()).lower().encode("utf-8", errors="replace")
    except OSError:
        key = str(p).lower().encode("utf-8", errors="replace")
    h = hashlib.md5(key).hexdigest()[:8]
    return f"{stem}_{h}"

def jobs_root() -> Path:
    d = default_output_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def job_dir(video_path: str, *, create: bool = True) -> Path:
    d = jobs_root() / _safe_job_id(video_path)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d

def checkpoint_path(video_path: str, *, create: bool = True) -> Path:
    return job_dir(video_path, create=create) / "checkpoint.json"

def config_fingerprint(config: "PipelineConfig") -> str:
    parts = [
        config.whisper_model,
        config.source_language,
        config.quality,
        config.glossary or "",
        str(bool(config.translate)),
        config.target_language,
        config.translator,
        config.ollama_model,
        config.openai_model,
        config.embed_mode,
        config.font_name,
        str(config.font_size),
    ]
    return "|".join(parts)

def load_checkpoint(video_path: str) -> dict:
    p = checkpoint_path(video_path, create=False)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_checkpoint(video_path: str, data: dict) -> None:
    p = checkpoint_path(video_path, create=True)
    data = dict(data)
    data["updated_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    data["video_path"] = str(Path(video_path).resolve())
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

def clear_checkpoint(video_path: str) -> None:
    p = checkpoint_path(video_path, create=False)
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        pass

def _rmtree_retry(path: Path, attempts: int = 6, delay: float = 0.35) -> bool:
    """Windows may lock files briefly after FFmpeg; retry delete."""
    import time

    p = Path(path)
    if not p.exists():
        return True
    for i in range(max(1, attempts)):
        try:
            shutil.rmtree(p, ignore_errors=False)
        except Exception:
            try:
                shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
        if not p.exists():
            return True
        time.sleep(delay * (1.0 + 0.25 * i))
    shutil.rmtree(p, ignore_errors=True)
    return not p.exists()

def delete_job(job: dict) -> bool:
    jd = (job or {}).get("job_dir") or ""
    if not jd:
        vp = (job or {}).get("video_path") or ""
        if vp:
            jd = str(job_dir(vp, create=False))
    if not jd:
        return False
    p = Path(jd)
    if not p.is_dir():
        return False
    return _rmtree_retry(p)

def cleanup_job_cache(video_path: str, cb: ProgressCb = None) -> None:
    """Remove intermediate job workspace (audio/asr/checkpoints) after success."""
    if not video_path:
        return
    jd = job_dir(video_path, create=False)
    name = jd.name if jd else "?"
    if jd.is_dir():
        _progress(cb, f"清理任务缓存: {name}", 99)
        ok = _rmtree_retry(jd)
        if not ok:
            _progress(cb, f"任务缓存未能完全删除（可能被占用）: {name}", 99)
        else:
            _progress(cb, f"已删除任务目录: {name}", 99.2)
    else:
        clear_checkpoint(video_path)


def purge_expired_job_dirs(retention_policy: str = "never", cb: ProgressCb = None) -> int:
    """Delete job folders based on retention policy (never, on_exit, 1_day, 3_days, 7_days)."""
    root = jobs_root()
    if not root.is_dir():
        return 0
    
    retention_seconds_map = {
        "1_day": 86400,
        "3_days": 3 * 86400,
        "7_days": 7 * 86400,
    }
    
    max_age_seconds = retention_seconds_map.get(retention_policy, None)
    import time
    now_ts = time.time()
    
    removed = 0
    for d in list(root.iterdir()):
        if not d.is_dir():
            continue
        cp = d / "checkpoint.json"
        should = False
        if not any(d.iterdir()):
            should = True
        elif cp.is_file():
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                st = (data.get("status") or "").lower()
                stage = (data.get("stage") or "").lower()
                is_done = (st == "done" or stage == "done")
                
                if is_done:
                    if retention_policy == "on_exit":
                        should = True
                    elif max_age_seconds is not None:
                        updated_at = data.get("updated_at") or ""
                        file_mtime = cp.stat().st_mtime
                        
                        age_seconds = now_ts - file_mtime
                        if age_seconds > max_age_seconds:
                            should = True
            except Exception:
                pass
        else:
            names = {p.name.lower() for p in d.iterdir()}
            if names and names <= {"audio.wav", "audio.m4a", "tmp", "work"}:
                should = True
                
        if should and _rmtree_retry(d):
            removed += 1
            
    if removed and cb:
        _progress(cb, f"已按保留策略清理历史任务 {removed} 个", 99.3)
    return removed


def purge_finished_job_dirs(cb: ProgressCb = None) -> int:
    """Delete job folders that are done / empty leftovers under exports/jobs (Legacy alias)."""
    return purge_expired_job_dirs("on_exit", cb=cb)


def post_success_cleanup(config: "PipelineConfig", video_path: str, cb: ProgressCb = None) -> None:
    """After one video finishes: keep job cache for potential retries; reset local AI context if requested."""
    if getattr(config, "clear_ai_context", True) and (config.translator or "").lower() == "ollama":
        try:
            from translators import clear_ollama_context

            if (config.ollama_base_url or "").strip():
                clear_ollama_context(
                    config.ollama_base_url.strip(),
                    config.ollama_model or "",
                )
            _progress(cb, "已重置本地 AI 上下文", 99.5)
        except Exception as e:
            _progress(cb, f"重置 AI 上下文时忽略: {e}", 99.5)

def list_jobs(*, include_done: bool = True) -> List[dict]:
    root = jobs_root()
    out: List[dict] = []
    if not root.is_dir():
        return out
    for d in root.iterdir():
        if not d.is_dir():
            continue
        cp_file = d / "checkpoint.json"
        if not cp_file.is_file():
            continue
        try:
            cp = json.loads(cp_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(cp, dict):
            continue
        stage = cp.get("stage") or "init"
        if not include_done and stage == "done" and not cp.get("last_error"):
            continue
        status = cp.get("status") or ""
        if not status:
            if stage == "done":
                status = "done"
            elif cp.get("last_error"):
                status = "failed"
            else:
                status = "pending"
        item = dict(cp)
        item["job_dir"] = str(d)
        item["status"] = status
        item["stage"] = stage

        item["has_audio"] = (d / "audio.wav").is_file()
        item["has_asr"] = (d / "asr.srt").is_file() or bool(cp.get("asr_srt") and Path(cp.get("asr_srt", "")).is_file())
        item["has_final"] = (d / "final.srt").is_file() or bool(
            cp.get("final_srt") and Path(cp.get("final_srt", "")).is_file()
        )
        out.append(item)
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out

def find_resumable_job(video_path: str = "") -> Optional[dict]:
    if video_path:
        cp = load_checkpoint(video_path)
        if cp and cp.get("stage") not in ("", "done", None):
            if Path(cp.get("video_path") or video_path).is_file():
                cp = dict(cp)
                cp["job_dir"] = str(job_dir(video_path, create=False))
                return cp
    jobs = list_jobs(include_done=False)
    for j in jobs:
        if j.get("status") == "done":
            continue
        vp = j.get("video_path") or ""
        if vp and Path(vp).is_file():
            return j
        if j.get("has_final") or j.get("has_asr"):
            return j
    return None

def _retry_call(
    label: str,
    fn,
    *,
    max_retries: int,
    cb: ProgressCb,
    cancelable: bool = True,
):
    last: Optional[BaseException] = None
    n = max(1, int(max_retries or 1))
    for attempt in range(1, n + 1):
        if cancelable:
            check_cancelled()
        try:
            return fn()
        except PipelineCancelled:
            raise
        except Exception as e:
            last = e
            if attempt >= n:
                break
            _progress(
                cb,
                f"{label}失败，重试 {attempt}/{n - 1}… ({e})",
                max(1.0, min(99.0, 5.0 + attempt)),
            )
            import time

            time.sleep(min(2.0 * attempt, 6.0))
            check_cancelled()
    assert last is not None
    raise last

def srt_path_for_ffmpeg(srt_path: str) -> str:
    p = Path(srt_path).resolve()
    s = str(p).replace("\\", "/").replace(":", "\\:")

    s = s.replace("'", r"\'")
    return s

def _ffprobe_path(ffmpeg: str) -> str:
    p = Path(ffmpeg)
    for name in ("ffprobe.exe", "ffprobe"):
        cand = p.with_name(name)
        if cand.is_file():
            return str(cand)
    which = shutil.which("ffprobe")
    return which or str(p.with_name("ffprobe.exe"))

def probe_duration_seconds(video_path: str, ffmpeg: str) -> float:
    probe = _ffprobe_path(ffmpeg)
    cmd = [
        probe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        run = _run_hidden(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if run.returncode == 0 and run.stdout.strip():
            return max(0.0, float(run.stdout.strip()))
    except Exception:
        pass

    try:
        run = _run_hidden(
            [ffmpeg, "-i", video_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", run.stderr or "")
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mi * 60 + s
    except Exception:
        pass
    return 0.0

def _nvenc_available(ffmpeg: str) -> bool:
    try:
        run = _run_hidden(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return "h264_nvenc" in (run.stdout or "")
    except Exception:
        return False

def _run_ffmpeg_with_progress(
    cmd: List[str],
    duration: float,
    cb: ProgressCb,
    phase_start: float = 88.0,
    phase_end: float = 98.0,
) -> None:

    if "-progress" not in cmd:

        cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]

    proc = _popen_hidden(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None

    last_pct = phase_start
    err_chunks: List[str] = []

    def _on_out_line(line: str) -> None:
        nonlocal last_pct
        line = line.strip()
        if not line or "=" not in line:
            return
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if key in ("out_time_ms", "out_time_us"):
            try:
                t = float(val)
                sec = t / 1_000_000.0
            except ValueError:
                return
            if duration > 0:
                sec = min(sec, duration)
                ratio = min(1.0, max(0.0, sec / duration))
                pct = phase_start + ratio * (phase_end - phase_start)
                if pct - last_pct >= 0.3:
                    last_pct = pct
                    _progress(cb, f"烧录字幕中… {sec:.0f}/{duration:.0f}s", pct)
        elif key == "out_time":
            m = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", val)
            if m and duration > 0:
                sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                sec = min(sec, duration)
                ratio = min(1.0, max(0.0, sec / duration))
                pct = phase_start + ratio * (phase_end - phase_start)
                if pct - last_pct >= 0.3:
                    last_pct = pct
                    _progress(cb, f"烧录字幕中… {sec:.0f}/{duration:.0f}s", pct)
        elif key == "progress" and val == "end":
            _progress(cb, "烧录收尾（整理文件头）…", phase_end - 0.2)

    import threading

    def _read_stdout() -> None:
        for line in proc.stdout:
            _on_out_line(line)

    def _read_stderr() -> None:
        nonlocal last_pct
        for line in proc.stderr:
            err_chunks.append(line)

            m = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
            if m and duration > 0:
                sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                sec = min(sec, duration)
                ratio = min(1.0, max(0.0, sec / duration))
                pct = phase_start + ratio * (phase_end - phase_start)
                if pct - last_pct >= 0.3:
                    last_pct = pct
                    _progress(cb, f"烧录字幕中… {sec:.0f}/{duration:.0f}s", pct)

    t1 = threading.Thread(target=_read_stdout, daemon=True)
    t2 = threading.Thread(target=_read_stderr, daemon=True)
    t1.start()
    t2.start()
    import time as _time
    _heartbeat_t = _time.monotonic()
    while proc.poll() is None:
        if is_cancelled():
            try:
                proc.kill()
            except Exception:
                pass
            t1.join(timeout=1)
            t2.join(timeout=1)
            raise PipelineCancelled("已停止")
        try:
            proc.wait(timeout=0.5)
        except Exception:
            pass
        _now = _time.monotonic()
        if _now - _heartbeat_t >= 10.0:
            _heartbeat_t = _now
            _progress(cb, "烧录收尾中（整理 MP4 文件头，请稍候）…", min(last_pct + 0.1, phase_end - 0.1))
    rc = proc.returncode if proc.returncode is not None else proc.wait()
    t1.join(timeout=2)
    t2.join(timeout=2)
    global _active_proc
    if _active_proc is proc:
        _active_proc = None
    if is_cancelled():
        raise PipelineCancelled("已停止")
    if rc != 0:
        err = "".join(err_chunks)[-3000:]
        raise RuntimeError(f"ffmpeg 失败 (code {rc}):\n{err}")

def remove_external_subtitle_sidecars(video_path: str, cb: ProgressCb = None) -> List[str]:
    vp = Path(video_path)
    parent = vp.parent
    stem = vp.stem
    removed: List[str] = []

    for ext in (".srt", ".ass", ".ssa", ".vtt", ".sub", ".smi", ".idx", ".sup", ".mks"):
        p = parent / f"{stem}{ext}"
        if p.is_file():
            try:
                p.unlink()
                removed.append(p.name)
            except OSError:
                pass

    for p in parent.glob(f"{stem}.*"):
        if not p.is_file() or p.resolve() == vp.resolve():
            continue
        name = p.name.lower()
        if any(
            name.endswith(suf)
            for suf in (
                ".srt",
                ".ass",
                ".ssa",
                ".vtt",
                ".sub",
                ".smi",
            )
        ):

            if p.name.startswith(stem + "."):
                try:
                    p.unlink()
                    removed.append(p.name)
                except OSError:
                    pass
    if removed and cb:
        _progress(cb, f"已清除会叠字的外挂字幕: {', '.join(removed)}", 99)
    return removed

def embed_subtitles(
    video_path: str,
    srt_path: str,
    output_path: str,
    mode: str,
    ffmpeg: str,
    font_name: str = "Microsoft YaHei",
    font_size: int = 22,
    cb: ProgressCb = None,
) -> str:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    duration = probe_duration_seconds(video_path, ffmpeg)

    if mode == "soft":
        _progress(cb, "正在挂载软字幕（几乎不重编码）…", 88)

        cmd = [
            ffmpeg,
            "-y",
            "-i",
            video_path,
            "-i",
            srt_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-map",
            "1:0",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            "language=chi",
            "-metadata:s:s:0",
            "title=Chinese",
            "-disposition:s:0",
            "default",
            str(out),
        ]
        try:
            _run_ffmpeg_with_progress(cmd, duration or 1.0, cb, 88, 98)
        except Exception:
            _progress(cb, "软字幕失败，尝试硬字幕烧录…", 90)
            return embed_subtitles(
                video_path, srt_path, output_path, "hard", ffmpeg, font_name, font_size, cb
            )

        try:
            sidecar = out.with_suffix(".srt")
            shutil.copy2(srt_path, sidecar)
            _progress(cb, f"已额外写出外挂字幕供播放器自动加载: {sidecar.name}", 97)
        except OSError:
            pass
        _progress(cb, "嵌入完成", 98)
        return str(out)

    if mode != "hard":
        raise ValueError(f"未知 embed_mode: {mode}")

    out = Path(ensure_h264_output_path(str(out)))
    out.parent.mkdir(parents=True, exist_ok=True)

    use_nvenc = _nvenc_available(ffmpeg)
    encoder_label = "NVENC 硬件加速" if use_nvenc else "CPU 软件编码(较慢)"
    _progress(
        cb,
        f"正在烧录硬字幕（{encoder_label} → {out.suffix}，去掉内挂字幕轨）…",
        88,
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="vidsub_srt_"))
    tmp_srt = tmp_dir / "subs.srt"
    try:
        try:
            srt_content = Path(srt_path).read_text(encoding="utf-8", errors="replace")
            if srt_content.startswith("\ufeff"):
                srt_content = srt_content[1:]
            tmp_srt.write_text(srt_content, encoding="utf-8")
        except Exception:
            shutil.copy2(srt_path, tmp_srt)

        escaped = srt_path_for_ffmpeg(str(tmp_srt))
        style = (
            f"FontName={font_name},FontSize={font_size},"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"BorderStyle=1,Outline=2,Shadow=1,MarginV=28"
        )
        vf = f"subtitles='{escaped}':force_style='{style}'"

        if use_nvenc:
            vcodec = [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p4",
                "-rc",
                "vbr",
                "-cq",
                "23",
                "-b:v",
                "0",
            ]
        else:
            vcodec = [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
            ]

        base_maps = ["-map", "0:v:0", "-map", "0:a?", "-sn"]

        trailer_mp4 = ["-movflags", "+faststart"] if out.suffix.lower() in (".mp4", ".m4v", ".mov") else []

        def _build_cmd(vcodec_args: List[str], audio_args: List[str]) -> List[str]:
            return [
                ffmpeg,
                "-y",
                "-i",
                video_path,
                *base_maps,
                "-vf",
                vf,
                *vcodec_args,
                *audio_args,
                "-max_interleave_delta",
                "0",
                *trailer_mp4,
                str(out),
            ]

        def _check_output_duration():
            if duration and duration > 10:
                out_dur = probe_duration_seconds(str(out), ffmpeg)
                if out_dur > 0 and out_dur < duration * 0.95:
                    raise RuntimeError(
                        f"烧录输出时长过短 ({out_dur:.1f}s / 源视频 {duration:.1f}s)，`-c:a copy` 导致提前截断。"
                    )

        cmd = _build_cmd(vcodec, ["-c:a", "copy"])
        try:
            _run_ffmpeg_with_progress(cmd, duration or 1.0, cb, 88, 98)
            _check_output_duration()
        except Exception as e:
            err_s = str(e).lower()

            if use_nvenc:
                _progress(cb, f"NVENC 失败，改用 CPU 快速编码…", 89)
                try:
                    _run_ffmpeg_with_progress(
                        _build_cmd(
                            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
                            ["-c:a", "copy"],
                        ),
                        duration or 1.0,
                        cb,
                        89,
                        98,
                    )
                    _check_output_duration()
                except Exception as e2:

                    _progress(cb, "音轨 copy 失败，改为 AAC 重编码…", 90)
                    _run_ffmpeg_with_progress(
                        _build_cmd(
                            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
                            ["-c:a", "aac", "-b:a", "192k"],
                        ),
                        duration or 1.0,
                        cb,
                        90,
                        98,
                    )
            elif "invalid argument" in err_s or "codec" in err_s or "webm" in err_s:
                _progress(cb, "封装/音轨不兼容，改用 H.264+AAC…", 89)
                _run_ffmpeg_with_progress(
                    _build_cmd(
                        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
                        ["-c:a", "aac", "-b:a", "192k"],
                    ),
                    duration or 1.0,
                    cb,
                    89,
                    98,
                )
            else:

                _progress(cb, "重试：H.264 + AAC…", 89)
                _run_ffmpeg_with_progress(
                    _build_cmd(
                        vcodec if not use_nvenc else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
                        ["-c:a", "aac", "-b:a", "192k"],
                    ),
                    duration or 1.0,
                    cb,
                    89,
                    98,
                )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    remove_external_subtitle_sidecars(str(out), cb=cb)
    _progress(cb, "硬字幕完成（仅画面内嵌，无外挂/内挂轨）", 98)
    return str(out)

def ensure_h264_output_path(path: str) -> str:
    p = Path(path)
    ext = p.suffix.lower()

    if ext in (".mp4", ".m4v", ".mov", ".mkv"):
        return str(p)

    return str(p.with_suffix(".mp4"))

def default_output_dir() -> Path:
    out = resolve_export_dir()
    try:
        out.mkdir(parents=True, exist_ok=True)
        return out
    except OSError:
        fallback = app_base_dir() / "exports"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
        except OSError:
            fallback = Path.home() / "Videos" / "SubtitleExports"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

def default_output_path(video_path: str, target_lang: str, mode: str) -> str:
    p = Path(video_path)
    tag = (target_lang or "out").replace("-", "") or "out"
    out_dir = default_output_dir()
    if mode == "srt_only":
        return str(out_dir / f"{p.stem}.{tag}.srt")
    if mode == "hard":

        return str(out_dir / f"{p.stem}.sub_{tag}.mp4")

    ext = p.suffix.lower()
    if ext in (".webm", ".ts", ".gif", ""):
        ext = ".mp4"
    return str(out_dir / f"{p.stem}.sub_{tag}{ext}")

def run_pipeline(config: PipelineConfig, cb: ProgressCb = None) -> PipelineResult:
    clear_cancel()
    ensure_cuda_dll_path()
    video = Path(config.video_path)
    if not video.is_file():
        raise FileNotFoundError(f"视频不存在: {video}")

    # 源语言允许 auto/detect/留空：交给 Whisper 自动检测（transcribe 内部会转为 language=None），
    # 检测置信度低时 transcribe 会提示可手动指定。手动指定具体语言仍是更稳的首选。
    src_lang = (config.source_language or "").strip().lower()
    if src_lang in ("", "auto", "detect"):
        _progress(cb, "源语言未指定，将由 Whisper 自动检测（如结果不准可手动选择源语言重试）", 3)

    ffmpeg = find_ffmpeg()
    mode = config.embed_mode
    out_path = config.output_path or default_output_path(
        str(video), config.target_language if config.translate else "src", mode
    )
    if mode == "hard":
        out_path = ensure_h264_output_path(out_path)

    jdir = job_dir(str(video))
    job_id = jdir.name
    fp = config_fingerprint(config)
    retries = max(1, int(getattr(config, "max_retries", 3) or 3))
    do_resume = bool(getattr(config, "resume", True))

    archive_dir = default_output_dir() / "srt_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    out_p = Path(out_path)

    audio_path = str(jdir / "audio.wav")
    asr_srt_path = str(jdir / "asr.srt")
    final_srt_job = str(jdir / "final.srt")

    cp = load_checkpoint(str(video)) if do_resume else {}
    if cp and cp.get("fingerprint") and cp.get("fingerprint") != fp:
        cp = {}
    stage = (cp.get("stage") or "init") if cp else "init"
    detected = cp.get("detected_lang") or config.source_language or "unknown"
    resumed_from = stage if stage not in ("init", "done") else ""

    state: dict = {
        "job_id": job_id,
        "stage": stage,
        "fingerprint": fp,
        "video_path": str(video.resolve()),
        "out_path": out_path,
        "mode": mode,
        "audio_path": audio_path,
        "asr_srt": cp.get("asr_srt") or asr_srt_path,
        "final_srt": cp.get("final_srt") or final_srt_job,
        "detected_lang": detected,
        "last_error": "",
        "source_language": config.source_language or "",
        "target_language": config.target_language or "",
        "translator": config.translator or "",
        "whisper_model": config.whisper_model or "",
        "quality": config.quality or "",
        "special_prompt": config.glossary or "",
    }

    def _save(st: str, **extra) -> None:
        state["stage"] = st
        state.update(extra)
        save_checkpoint(str(video), state)

    def _fail(st: str, err: BaseException) -> None:
        state["stage"] = st
        state["status"] = "failed"
        state["last_error"] = str(err)[:2000]
        save_checkpoint(str(video), state)

        raise RuntimeError(
            f"处理失败（阶段: {st}）\n"
            f"{err}\n\n"
            f"进度已保留（软件未退出）。\n"
            f"可在「任务」窗口查看/删除，或点「续跑」重试。\n"
            f"检查点: {checkpoint_path(str(video))}"
        ) from err

    segments: List[Segment] = []
    _save(stage if stage != "done" else "init")

    if resumed_from:
        _progress(cb, f"从检查点续跑（已完成到: {resumed_from}）…", 3)

    try:
        if stage in ("init",) or not Path(audio_path).is_file() or Path(audio_path).stat().st_size < 1000:
            def _do_audio():
                extract_audio(str(video), audio_path, ffmpeg, cb)

            _retry_call("提取音频", _do_audio, max_retries=retries, cb=cb)
            _save("audio", audio_path=audio_path)
            stage = "audio"
        else:
            _progress(cb, "跳过提取音频（使用缓存）", 8)
            stage = "audio" if stage == "init" else stage
            _save("audio" if stage == "init" else stage)
    except PipelineCancelled:
        _save(stage or "init", last_error="用户停止", status="stopped")
        raise
    except Exception as e:
        _fail("audio", e)

    try:
        can_skip_asr = (
            stage in ("asr", "translate", "srt", "embed", "done")
            and Path(state["asr_srt"]).is_file()
            and Path(state["asr_srt"]).stat().st_size > 50
        )

        arch_asr = archive_dir / f"{video.stem}.asr_{detected}.srt"
        if not can_skip_asr and stage in ("asr", "translate", "srt", "embed"):
            if arch_asr.is_file() and arch_asr.stat().st_size > 50:
                state["asr_srt"] = str(arch_asr)
                can_skip_asr = True

        if can_skip_asr and stage != "audio" and stage != "init":
            segments = read_srt(state["asr_srt"])
            if not segments:
                can_skip_asr = False
            else:
                detected = state.get("detected_lang") or detected
                _progress(cb, f"跳过识别（使用已有字幕 {len(segments)} 段）", 55)

        if not can_skip_asr or stage in ("init", "audio"):
            model_size = (config.whisper_model or "large-v3-turbo").strip()
            if (config.quality or "balanced").lower() == "high" and model_size in ("tiny", "base"):
                _progress(cb, f"质量=high 且模型={model_size}", 10)

            def _do_asr():
                return transcribe(
                    audio_path,
                    model_size=model_size,
                    language=config.source_language,
                    device=config.device,
                    compute_type=config.compute_type,
                    cb=cb,
                    quality=config.quality or "balanced",
                    glossary=config.glossary or "",
                    vad_threshold=float(getattr(config, "vad_threshold", 0.35) or 0.35),
                )

            segments, detected = _retry_call("语音识别", _do_asr, max_retries=retries, cb=cb)
            if not segments:
                raise RuntimeError("未识别到有效语音内容。")
            write_srt(segments, asr_srt_path)
            arch = archive_dir / f"{video.stem}.asr_{detected or 'src'}.srt"
            write_srt(segments, str(arch))
            state["asr_srt"] = asr_srt_path
            state["detected_lang"] = detected
            _save("asr", asr_srt=asr_srt_path, detected_lang=detected)
            stage = "asr"
            _progress(cb, f"已保存原文识别稿: {arch.name}", 62)
        else:
            if not segments:
                segments = read_srt(state["asr_srt"])
            stage = "asr" if stage in ("init", "audio") else stage
            _save(stage, asr_srt=state["asr_srt"], detected_lang=detected)
    except PipelineCancelled:
        _save(stage or "audio", last_error="用户停止", status="stopped")
        raise
    except Exception as e:
        _fail("asr", e)

    try:
        need_translate = config.translate and config.translator != "none"
        if need_translate and detected:
            det = str(detected).lower()
            tgt = (config.target_language or "").lower()
            if tgt.startswith("zh") and det.startswith("zh"):
                need_translate = False
                _progress(cb, "源语言已是中文，跳过翻译", 70)
            elif tgt.startswith("en") and det.startswith("en"):
                need_translate = False
                _progress(cb, "源语言已是英文，跳过翻译", 70)

        can_skip_tr = (
            stage in ("translate", "srt", "embed", "done")
            and Path(state.get("final_srt") or "").is_file()
            and Path(state["final_srt"]).stat().st_size > 50
        )

        hard_arch = archive_dir / f"{out_p.stem}.hard_burn.srt"
        if not can_skip_tr and stage in ("translate", "srt", "embed") and hard_arch.is_file():
            state["final_srt"] = str(hard_arch)
            can_skip_tr = True

        if can_skip_tr and stage not in ("init", "audio", "asr"):
            segments = read_srt(state["final_srt"])
            if segments:
                _progress(cb, f"跳过翻译（使用已有译文 {len(segments)} 段）", 80)
            else:
                can_skip_tr = False

        if need_translate and not can_skip_tr:
            from translators import Segment as TSeg
            from translators import translate_segments as dispatch_translate

            if not segments:
                segments = read_srt(state["asr_srt"])
            if not segments:
                raise RuntimeError("无识别结果可翻译，请重新开始。")

            def _do_tr():
                t_in = [TSeg(start=s.start, end=s.end, text=s.text) for s in segments]
                return dispatch_translate(
                    t_in,
                    translator=config.translator,
                    target_language=config.target_language,
                    source_language=config.source_language,
                    cb=cb,
                    ollama_model=config.ollama_model,
                    ollama_base_url=config.ollama_base_url,
                    openai_api_key=config.openai_api_key,
                    openai_base_url=config.openai_base_url,
                    openai_model=config.openai_model,
                    deepl_api_key=config.deepl_api_key,
                    deepl_use_free=config.deepl_use_free,
                    baidu_app_id=config.baidu_app_id,
                    baidu_app_key=config.baidu_app_key,
                    glossary=config.glossary or "",
                )

            t_out = _retry_call("翻译", _do_tr, max_retries=retries, cb=cb)
            segments = [Segment(start=s.start, end=s.end, text=s.text) for s in t_out]
            segments = refine_segments(segments)
            write_srt(segments, final_srt_job)
            state["final_srt"] = final_srt_job
            _save("translate", final_srt=final_srt_job, detected_lang=detected)
            stage = "translate"
        elif not need_translate:
            if not segments:
                segments = read_srt(state["asr_srt"])
            write_srt(segments, final_srt_job)
            state["final_srt"] = final_srt_job
            _save("translate", final_srt=final_srt_job, detected_lang=detected)
            stage = "translate"
            _progress(cb, "跳过翻译", 80)
        else:
            if not segments:
                segments = read_srt(state["final_srt"])
            stage = "translate"
            _save("translate", final_srt=state["final_srt"], detected_lang=detected)
    except PipelineCancelled:
        _save(stage or "asr", last_error="用户停止", status="stopped")
        raise
    except Exception as e:
        _fail("translate", e)

    try:
        if not segments:
            segments = read_srt(state.get("final_srt") or state.get("asr_srt") or "")
        if not segments:
            raise RuntimeError("无字幕数据，无法继续。")

        if mode == "srt_only":
            srt_path = (
                out_path
                if out_path.lower().endswith(".srt")
                else str(Path(out_path).with_suffix(".srt"))
            )

            def _do_write():
                write_srt(segments, srt_path)

            _retry_call("写入SRT", _do_write, max_retries=retries, cb=cb)
            _save("done", final_srt=srt_path, detected_lang=detected, status="done", last_error="")
            post_success_cleanup(config, str(video), cb=cb)
            _progress(cb, "SRT 已生成", 100)
            return PipelineResult(
                srt_path=srt_path,
                output_video="",
                source_language=detected,
                segments=segments,
                resumed_from=resumed_from,
                job_id=job_id,
            )

        if mode == "hard":
            srt_path = str(archive_dir / f"{out_p.stem}.hard_burn.srt")
            write_srt(segments, srt_path)
            write_srt(segments, final_srt_job)
            remove_external_subtitle_sidecars(str(out_p), cb=cb)
            for old in (
                out_p.with_name(out_p.stem + ".source.srt"),
                out_p.with_suffix(".srt"),
            ):
                if old.is_file():
                    try:
                        old.unlink()
                    except OSError:
                        pass
            _progress(cb, f"字幕稿存档: {Path(srt_path).name}", 86)
        else:
            srt_path = str(out_p.with_name(out_p.stem + ".source.srt"))
            write_srt(segments, srt_path)
            write_srt(segments, final_srt_job)
            _progress(cb, f"字幕稿: {Path(srt_path).name}", 86)

        state["final_srt"] = srt_path
        _save("srt", final_srt=srt_path, detected_lang=detected)
        stage = "srt"
    except PipelineCancelled:
        _save(stage or "translate", last_error="用户停止", status="stopped")
        raise
    except Exception as e:
        _fail("srt", e)

    try:
        srt_path = state.get("final_srt") or ""
        if not srt_path or not Path(srt_path).is_file():
            raise RuntimeError("找不到字幕稿，无法烧录。")

        out_exists = Path(out_path).is_file() and Path(out_path).stat().st_size > 10_000

        def _do_embed():
            return embed_subtitles(
                str(video),
                srt_path,
                out_path,
                mode=mode,
                ffmpeg=ffmpeg,
                font_name=config.font_name,
                font_size=config.font_size,
                cb=cb,
            )

        if out_exists and stage == "done":
            video_out = out_path
            _progress(cb, "输出已存在，跳过烧录", 98)
        else:
            video_out = _retry_call("烧录/嵌入字幕", _do_embed, max_retries=retries, cb=cb)

        final_srt = srt_path
        if mode == "soft":
            side = Path(video_out).with_suffix(".srt")
            if side.is_file():
                final_srt = str(side)
        elif mode == "hard":
            remove_external_subtitle_sidecars(video_out, cb=cb)

        _save("done", final_srt=final_srt, out_path=video_out, detected_lang=detected, status="done", last_error="")
        post_success_cleanup(config, str(video), cb=cb)
        _progress(cb, "全部完成", 100)
        return PipelineResult(
            srt_path=final_srt,
            output_video=video_out,
            source_language=detected,
            segments=segments,
            resumed_from=resumed_from,
            job_id=job_id,
        )
    except PipelineCancelled:
        _save("srt", last_error="用户停止", status="stopped")
        raise
    except Exception as e:
        _fail("embed", e)

def list_ollama_models(base_url: str = "") -> List[str]:
    try:
        from translators import list_ollama_models as _list

        return _list(base_url)
    except Exception:
        return []

CUDA_PIP_HINT = (
    "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 "
    "nvidia-cuda-runtime-cu12 nvidia-cuda-nvrtc-cu12"
)
CUDA12_PIP_HINT = CUDA_PIP_HINT


def _nvidia_smi_present() -> bool:
    try:
        smi = shutil.which("nvidia-smi")
        if not smi:
            root = (os.environ.get("SystemRoot") or os.environ.get("WINDIR") or "").strip()
            if root:
                cand = Path(root) / "System32" / "nvidia-smi.exe"
                if cand.is_file():
                    smi = str(cand)
        if not smi:
            return False
        r = _run_hidden(
            [smi, "-L"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            return False
        out = (r.stdout or "") + (r.stderr or "")

        return "GPU" in out.upper() or "NVIDIA" in out.upper()
    except Exception:
        return False

def detect_nvidia_gpu() -> dict:
    ensure_cuda_dll_path()
    result = {
        "nvidia_present": False,
        "cuda_device_count": 0,
        "cublas_ok": False,
        "cublas_path": None,
        "status": "no_nvidia",
        "message": "",
    }
    cublas = find_cublas_dll()
    result["cublas_ok"] = cublas is not None
    result["cublas_path"] = str(cublas) if cublas else None

    n = 0
    ct_err = None
    try:
        import ctranslate2

        n = int(ctranslate2.get_cuda_device_count() or 0)
    except Exception as e:
        ct_err = e
        n = 0
    result["cuda_device_count"] = n

    smi_ok = _nvidia_smi_present()
    nvidia = n > 0 or smi_ok
    result["nvidia_present"] = nvidia

    if n > 0 and result["cublas_ok"]:
        result["status"] = "ready"
        lib = Path(result["cublas_path"]).name if result["cublas_path"] else ""
        ver_hint = ""
        if "13" in lib:
            ver_hint = "（CUDA 13）"
        elif "12" in lib:
            ver_hint = "（CUDA 12）"
        elif "11" in lib:
            ver_hint = "（CUDA 11）"
        result["message"] = (
            f"已适配 NVIDIA GPU{ver_hint}（设备数 {n}），可用 GPU 加速。\n"
            + (f"[GPU] 运行库: {result['cublas_path']}" if result["cublas_path"] else "")
        )
    elif nvidia and not result["cublas_ok"]:
        result["status"] = "need_runtime"
        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            how = (
                "发布版不自带加速库。请在本机安装 NVIDIA 显卡驱动，并安装 CUDA 12 或 13 Toolkit"
                "（装好后一般不用拷文件进软件目录）。\n"
                "也可把 cublas64_12.dll / cublas64_13.dll 所在文件夹加入系统 PATH。"
            )
        else:
            how = (
                f"源码环境可执行:\n  {CUDA_PIP_HINT}\n"
                "或安装本机 CUDA 12 / 13 Toolkit（支持 cublas64_12 / cublas64_13）。"
            )
        result["message"] = (
            "已检测到 NVIDIA 显卡，但还没找到可用的 CUDA 加速库"
            "（如 cublas64_13.dll 或 cublas64_12.dll）。\n"
            "目前 GPU 加速不可用，会先用 CPU（更慢）。\n\n"
            f"{how}\n"
            "弄好后点「刷新环境」。仍不行就把设备改成 auto 或 cpu。"
        )
    elif nvidia and result["cublas_ok"] and n == 0:

        result["status"] = "need_runtime"
        result["message"] = (
            "找到了 CUDA 库，但推理引擎没枚举到 GPU 设备。\n"
            "常见原因：驱动过旧、库版本不匹配、或 PATH 未包含 CUDA 的 bin\\x64。\n\n"
            "可更新 NVIDIA 驱动，确认已装 CUDA 12 或 13，然后点「刷新环境」。\n"
            "修好前请用设备「cpu」或「auto」。"
        )
        if ct_err:
            result["message"] += f"\n详情: {ct_err}"
    else:
        result["status"] = "no_nvidia"
        result["message"] = (
            "未检测到可用的 NVIDIA（N 卡）CUDA 环境，GPU 加速不适配本机。\n"
            "本程序的 GPU 识别仅支持 NVIDIA + CUDA（不支持 AMD / Intel 独显加速）。\n"
            "仍可使用：设备选「cpu」或「auto」，识别与烧录在 CPU 上运行（较慢）。"
        )
        if ct_err:
            result["message"] += f"\n（检测异常: {ct_err}）"

    return result

def check_environment() -> dict:
    ensure_cuda_dll_path()
    info: dict = {
        "ffmpeg": None,
        "ffmpeg_ok": False,
        "faster_whisper": False,
        "ollama_models": [],
        "cuda": False,
        "cuda_device_count": 0,
        "cublas_path": None,
        "nvidia_present": False,
        "cuda_status": "no_nvidia",
        "cuda_message": "",
        "errors": [],
    }
    try:
        info["ffmpeg"] = find_ffmpeg()
        info["ffmpeg_ok"] = True
    except Exception as e:
        info["errors"].append(str(e))

    try:
        import faster_whisper

        info["faster_whisper"] = True
    except Exception:
        info["errors"].append("未安装 faster-whisper，请运行: pip install faster-whisper")

    gpu = detect_nvidia_gpu()
    info["nvidia_present"] = bool(gpu.get("nvidia_present"))
    info["cuda_device_count"] = int(gpu.get("cuda_device_count") or 0)
    info["cublas_path"] = gpu.get("cublas_path")
    info["cuda_status"] = gpu.get("status") or "no_nvidia"
    info["cuda_message"] = gpu.get("message") or ""
    info["cuda"] = info["cuda_status"] == "ready"
    if info["cuda_status"] == "need_runtime":
        info["errors"].append(info["cuda_message"])
    elif info["cuda_status"] == "no_nvidia":
        info["errors"].append(info["cuda_message"])

    info["ollama_models"] = []
    return info

if sys.platform == "win32":
    try:
        ensure_cuda_dll_path()
    except Exception:
        pass
