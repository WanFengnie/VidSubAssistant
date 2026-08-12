from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

def _hot_dir() -> Path | None:

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "hot"
    return None

def _prefer_hot_modules() -> None:

    hot = _hot_dir()
    if not hot or not hot.is_dir():
        return
    for name in ("pipeline", "translators"):
        path = hot / f"{name}.py"
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        except Exception:

            sys.modules.pop(name, None)

_prefer_hot_modules()

import customtkinter as ctk

from pipeline import (
    PipelineCancelled,
    PipelineConfig,
    apply_path_config,
    check_environment,
    clear_cancel,
    default_output_dir,
    delete_job,
    find_resumable_job,
    jobs_root,
    list_jobs,
    list_ollama_models,
    load_checkpoint,
    purge_finished_job_dirs,
    request_cancel,
    resolve_export_dir,
    resolve_model_cache_dir,
    run_pipeline,
    set_path_overrides,
)

APP_TITLE = "视频字幕助手"
APP_DIR = Path(__file__).resolve().parent


def _user_settings_path() -> Path:
    r"""Where to persist UI settings: %LOCALAPPDATA%\VideoSubtitleHelper\settings.json."""
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        base = Path(local) / "VideoSubtitleHelper"
    else:
        base = Path.home() / ".videosubtitlehelper"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path.home() / ".videosubtitlehelper"
        base.mkdir(parents=True, exist_ok=True)
    return base / "settings.json"


SETTINGS_PATH = _user_settings_path()


def _maybe_migrate_legacy_src_settings() -> None:
    """One-time: copy old src/settings.json into user config if needed."""
    if getattr(sys, "frozen", False):
        return
    dest = SETTINGS_PATH
    if dest.is_file():
        return
    legacy = APP_DIR / "settings.json"
    if not legacy.is_file():
        return
    try:
        dest.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        return


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".m4v",
    ".ts",
    ".flv",
    ".wmv",
    ".mpeg",
    ".mpg",
}


def _normalize_drop_paths(files) -> list[str]:
    """Drag-drop may pass bytes or str; Chinese paths need gbk/mbcs on Windows."""
    out: list[str] = []
    for f in files or []:
        if isinstance(f, bytes):
            for enc in ("utf-8", "gbk", "mbcs", "cp936", "latin-1"):
                try:
                    f = f.decode(enc)
                    break
                except Exception:
                    continue
            else:
                f = f.decode("utf-8", errors="replace")
        s = str(f).strip().strip("\x00").strip().strip('"')
        if s:
            out.append(s)
    return out


def _toplevel_hwnd(widget) -> int:
    """CustomTkinter winfo_id() is often a child frame; drops hit the real top-level HWND."""
    try:
        hwnd = int(widget.winfo_id())
    except Exception:
        return 0
    if sys.platform != "win32" or not hwnd:
        return hwnd
    try:
        import ctypes

        user32 = ctypes.windll.user32
        GA_ROOT = 2
        root = int(user32.GetAncestor(hwnd, GA_ROOT) or 0)
        if root:
            return root
        parent = int(user32.GetParent(hwnd) or 0)
        return parent or hwnd
    except Exception:
        return hwnd


def _win_collect_hwnds(root_hwnd: int) -> list[int]:
    """Root + all child HWNDs (unique)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    out: list[int] = []
    seen: set[int] = set()

    def add(h: int) -> None:
        h = int(h or 0)
        if h and h not in seen:
            seen.add(h)
            out.append(h)

    add(root_hwnd)
    EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @EnumChildProc
    def _enum(hwnd, _lp):
        add(int(hwnd))
        return True

    try:
        user32.EnumChildWindows(int(root_hwnd), _enum, 0)
    except Exception:
        pass
    return out

THIRD_PARTY_NOTICE = """【第三方组件与下载说明】

本工具（含发布包）不内置、也不自动下载安装 FFmpeg 与 NVIDIA CUDA 运行库。
请在本机安装后使用；程序会从 PATH / Scoop / WinGet / CUDA 安装目录等自动查找，
一般无需把文件拷进本软件目录。

会自动获取的内容（仅在开始识别且本地没有缓存时）：
• Whisper 语音模型权重（经 faster-whisper / Hugging Face）
  — 版权与许可以各模型页面为准
  — 下载与使用即表示接受模型作者与 Hugging Face 的条款
  — 本程序不拥有、不转售这些模型文件

需自行安装：
• FFmpeg（必须 安装方法：例如 winget install Gyan.FFmpeg 或 scoop install ffmpeg）
• 如有 N 卡：显卡驱动 + CUDA 12 或 13（缺少则使用 CPU）
• Ollama 与本地大模型、云翻译 API（自行安装或申请）

处理的视频内容版权自行负责；请确保有权转写与翻译。
识别/翻译结果可能有误，仅供参考使用，重要用途请人工校对。

详见程序目录里的 THIRD_PARTY_NOTICES.md 与 README_RUN.txt"""


_LANG_PAIRS = [
    ("English", "en"),
    ("简体中文", "zh-CN"),
    ("繁体中文", "zh-TW"),
    ("日本語", "ja"),
    ("한국어", "ko"),
    ("Français", "fr"),
    ("Deutsch", "de"),
    ("Español", "es"),
    ("Русский", "ru"),
    ("Tiếng Việt", "vi"),
    ("ไทย", "th"),
]

LANG_OPTIONS = [
    ("（请选择目标语言）", ""),
    *_LANG_PAIRS,
]

SOURCE_LANG_OPTIONS = [
    ("（请选择源语言）", ""),
    ("自动识别", "auto"),
    *_LANG_PAIRS,
]

WHISPER_MODELS = [
    ("tiny (最低)", "tiny"),
    ("base (低)", "base"),
    ("small (中)", "small"),
    ("medium (高)", "medium"),
    ("large-v3-turbo (很高)", "large-v3-turbo"),
    ("large-v3 (最高)", "large-v3"),
]
QUALITY_PRESETS = [
    ("快速", "fast"),
    ("均衡", "balanced"),
    ("高质量", "high"),
    ("最高质量", "dialogue"),
]
VAD_OPTIONS = [
    ("极松散 (0.15)", "0.15"),
    ("松散 (0.25)", "0.25"),
    ("标准 (0.35)", "0.35"),
    ("严格 (0.50)", "0.50"),
    ("极严格 (0.65)", "0.65"),
]

_DEFAULT_MODEL = "medium"
_DEFAULT_QUALITY = "balanced"
_RECOMMENDED_CUDA_MODEL = "large-v3-turbo"
_RECOMMENDED_CUDA_QUALITY = "high"

_QUEUE_STATUS_CN = {
    "queued": "等待",
    "running": "进行中",
    "done": "完成",
    "failed": "失败",
    "stopped": "已停止",
}
EMBED_MODES = [
    ("硬字幕", "hard"),
    ("软字幕", "soft"),
    ("仅 SRT", "srt_only"),
]

TRANSLATORS = [
    ("Ollama", "ollama"),
    ("OpenAI 兼容接口", "openai"),
    ("百度翻译", "baidu"),
    ("谷歌翻译", "google"),
    ("DeepL", "deepl"),
    ("不翻译", "none"),
]

PROMPT_MENU_PLACEHOLDER = "（提示词模板）"
PROMPT_HISTORY_MAX = 48
OLLAMA_NO_MODEL = "（无模型）"


def _shorten_prompt_label(text: str, max_len: int = 36) -> str:
    one = " ".join((text or "").split())
    if not one:
        return ""
    if len(one) <= max_len:
        return one
    return one[: max_len - 1] + "…"


def _normalize_prompt_item(item) -> dict | None:
    """Normalize settings entry to {title, text}. Accepts legacy plain strings."""
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        return {"title": _shorten_prompt_label(text, 28) or "未命名", "text": text}
    if isinstance(item, dict):
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            return None
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            title = _shorten_prompt_label(text, 28) or "未命名"
        return {"title": title, "text": text}
    return None

def _settings_path() -> Path:
    return SETTINGS_PATH

def _settings_richness(data: dict) -> int:
    """Higher = more worth keeping (prompt templates, paths, keys)."""
    if not isinstance(data, dict):
        return -1
    score = 0
    hist = data.get("prompt_history")
    if isinstance(hist, list):
        score += 100 * len(hist)
    for k in (
        "ollama_model",
        "export_dir",
        "model_cache_dir",
        "openai_api_key",
        "deepl_api_key",
        "baidu_app_key",
    ):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            score += 10
    return score


def _read_settings_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_settings() -> dict:
    _maybe_migrate_legacy_src_settings()
    primary = _settings_path()
    return _read_settings_file(primary)

def save_settings(data: dict) -> None:
    p = _settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def format_user_friendly_error(e: Exception) -> tuple[str, str]:
    """Return (user_friendly_reason, solution_guide)."""
    raw_msg = str(e)
    low_msg = raw_msg.lower()

    if "未找到可用的 ffmpeg" in raw_msg or ("ffmpeg" in low_msg and ("not found" in low_msg or "系统找不到" in low_msg or "filenotfounderror" in low_msg)):
        reason = "未检测到 FFmpeg 运行环境。"
        solution = "解决方法：请在终端输入命令  winget install Gyan.FFmpeg  一键安装。\n💡 详细教程与步骤请参阅项目 README.md 的「外部依赖指南」。"
    elif "11434" in low_msg or ("ollama" in low_msg and ("connection" in low_msg or "refused" in low_msg or "网络错误" in low_msg)):
        reason = "无法连接到 Ollama 本地翻译服务 (127.0.0.1:11434)。"
        solution = "解决方法：请确认已安装并启动 Ollama，且已通过  ollama run <模型>  下载模型。\n💡 详见 README.md 中的「Ollama 配置指南」。"
    elif "cuda" in low_msg and ("out of memory" in low_msg or "alloc" in low_msg or "oom" in low_msg):
        reason = "GPU 显存不足，无法加载当前 Whisper 识别模型。"
        solution = "解决方法：请在界面顶部将推理设备切换为「CPU」，或使用更轻量的模型（如 large-v3-turbo）。"
    elif "未识别到有效语音内容" in raw_msg:
        reason = "原视频音频中未能识别出有效的人声对话。"
        solution = "解决方法：请检查原视频音频轨是否正常，或手动在界面主面板选择具体「源语言」（不使用自动识别）。"
    elif "请手动选择源语言" in raw_msg:
        reason = "未手动选择源语言。"
        solution = "解决方法：请在主面板源语言菜单中手动选择具体的语言（如 日语/英语）。"
    elif "ffmpeg 失败" in low_msg:
        reason = "FFmpeg 视频硬烧录编码中途异常中断。"
        solution = "解决方法：请检查磁盘剩余空间，或在设置中尝试改用不同的字幕嵌入模式。"
    else:
        reason = f"运行时遇到错误: {raw_msg[:250]}"
        solution = "解决方法：请检查音频轨、文件路径及网络设置。详见 README.md 文档与指南。"

    return reason, solution


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(APP_TITLE)
        self.geometry("860x860")
        self.minsize(780, 720)
        self._apply_window_icon()

        self._worker: threading.Thread | None = None
        self._stopping = False
        self._settings_ready = False
        # Tk after() id type differs across stubs/runtime (str | int)
        self._save_after_id: Any = None
        self._queue: list[dict] = []
        self._queue_running = False
        self._prompt_history: list[dict] = []  # [{title, text}, ...]
        self._prompt_menu_map: dict[str, int] = {}  # dropdown label -> index
        self._selected_prompt_index: int | None = None
        self._prompt_manage_win: ctk.CTkToplevel | None = None
        self._jobs_win: JobsWindow | None = None
        self._window_icon_photo: Any = None
        self._gpu_need_runtime_warned: bool = False
        self._gpu_no_nvidia_warned: bool = False
        self._cpu_model_tip_shown: bool = False
        self._cuda_model_reco_done: bool = False
        self._cfg = load_settings()
        try:
            paths = apply_path_config(self._cfg)
        except Exception:
            paths = {}
        self._build_ui()
        self._apply_settings_to_ui()
        self._on_translator_change()
        self._wire_settings_autosave()
        self._refresh_queue_list()
        self._enable_drag_drop()
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self.after(200, self._refresh_env)
        self.after(350, self._purge_done_jobs_quiet)
        self.after(400, self._check_resume_hint)
        self.after(600, self._maybe_show_third_party_notice)

    def _icon_candidates(self) -> list[Path]:
        """Locate app.ico / app_icon.png (prefer packed _internal/assets; source: src/assets)."""
        names = ("app.ico", "app_icon.png")
        roots: list[Path] = [APP_DIR / "assets", APP_DIR]
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            # Canonical pack location first; legacy paths last for old installs
            roots = [
                exe_dir / "_internal" / "assets",
                exe_dir / "assets",
                exe_dir,
                exe_dir / "hot" / "assets",
            ]
        out: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            for name in names:
                p = root / name
                key = str(p).lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(p)
        return out

    def _apply_window_icon(self) -> None:
        """Taskbar / title-bar icon (Windows prefers .ico)."""
        for path in self._icon_candidates():
            if not path.is_file():
                continue
            try:
                if path.suffix.lower() == ".ico":
                    self.iconbitmap(default=str(path))
                    self.iconbitmap(str(path))
                    return
            except Exception:
                pass
            try:
                from PIL import Image, ImageTk

                img = Image.open(path).convert("RGBA")
                photo = ImageTk.PhotoImage(img.resize((32, 32), Image.Resampling.LANCZOS))
                # ImageTk.PhotoImage is accepted by Tk at runtime; stubs expect tk.PhotoImage.
                self.iconphoto(True, photo)  # type: ignore[arg-type]
                self._window_icon_photo = photo  # prevent GC
                return
            except Exception:
                continue

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkLabel(self, text="视频字幕助手", font=ctk.CTkFont(size=22, weight="bold"))
        header.grid(row=0, column=0, sticky="w", padx=16, pady=(16, 8))

        file_frame = ctk.CTkFrame(self)
        file_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=4)
        file_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(file_frame, text="视频文件").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        self.video_var = ctk.StringVar()
        self.video_entry = ctk.CTkEntry(
            file_frame,
            textvariable=self.video_var,
            placeholder_text="可拖入视频到窗口任意处，也可点“浏览”",
        )
        self.video_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=10)
        vid_btns = ctk.CTkFrame(file_frame, fg_color="transparent")
        vid_btns.grid(row=0, column=2, padx=6, pady=10)
        ctk.CTkButton(vid_btns, text="浏览…", width=72, command=self._pick_video).grid(row=0, column=0, padx=2)
        ctk.CTkButton(vid_btns, text="加入队列", width=80, command=self._queue_add_current).grid(
            row=0, column=1, padx=2
        )

        ctk.CTkLabel(file_frame, text="导出目录").grid(row=1, column=0, sticky="w", padx=10, pady=(0, 6))
        self.export_dir_var = ctk.StringVar(value="")
        self.export_entry = ctk.CTkEntry(
            file_frame,
            textvariable=self.export_dir_var,
            placeholder_text="空=程序目录下 exports（可拖入文件夹）",
        )
        self.export_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=(0, 6))
        ctk.CTkButton(file_frame, text="浏览…", width=80, command=self._pick_export_dir).grid(
            row=1, column=2, padx=10, pady=(0, 6)
        )

        self.enqueue_banner_var = ctk.StringVar(value="入队设置：请先在下方选择源语言 / 目标语言（可选择加入提示词）")
        self.enqueue_banner = ctk.CTkLabel(
            file_frame,
            textvariable=self.enqueue_banner_var,
            anchor="w",
            justify="left",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#1a5fb4", "#78aeed"),
        )
        self.enqueue_banner.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=(2, 4))

        ctk.CTkLabel(file_frame, text="任务队列").grid(row=3, column=0, sticky="nw", padx=10, pady=(0, 4))
        qwrap = ctk.CTkFrame(file_frame, fg_color="transparent")
        qwrap.grid(row=3, column=1, sticky="ew", padx=6, pady=(0, 4))
        qwrap.grid_columnconfigure(0, weight=1)
        self.queue_progress_var = ctk.StringVar(value="队列：空")
        ctk.CTkLabel(
            qwrap,
            textvariable=self.queue_progress_var,
            anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", pady=(0, 2))
        self.queue_list = ctk.CTkTextbox(qwrap, height=110, wrap="none")
        self.queue_list.grid(row=1, column=0, sticky="ew")
        self.queue_list.configure(state="disabled")
        qbtns = ctk.CTkFrame(file_frame, fg_color="transparent")
        qbtns.grid(row=3, column=2, padx=6, pady=(0, 4), sticky="n")
        ctk.CTkButton(qbtns, text="移除末项", width=80, command=self._queue_remove_selected).grid(
            row=0, column=0, pady=2
        )
        ctk.CTkButton(qbtns, text="清空", width=80, command=self._queue_clear).grid(row=1, column=0, pady=2)

        scroll = ctk.CTkScrollableFrame(self, height=320)
        scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=8)
        scroll.grid_columnconfigure(1, weight=1)

        r = 0
        ctk.CTkLabel(scroll, text="识别质量").grid(row=r, column=0, sticky="w", pady=5, padx=6)
        self.quality_label_var = ctk.StringVar(
            value=next(l for l, c in QUALITY_PRESETS if c == _RECOMMENDED_CUDA_QUALITY)
        )
        ctk.CTkOptionMenu(
            scroll, variable=self.quality_label_var, values=[x[0] for x in QUALITY_PRESETS]
        ).grid(row=r, column=1, sticky="ew", pady=5, padx=6)

        r += 1
        ctk.CTkLabel(scroll, text="Whisper 模型").grid(row=r, column=0, sticky="w", pady=5, padx=6)
        self.model_label_var = ctk.StringVar(
            value=next(l for l, c in WHISPER_MODELS if c == _RECOMMENDED_CUDA_MODEL)
        )
        ctk.CTkOptionMenu(
            scroll, variable=self.model_label_var, values=[x[0] for x in WHISPER_MODELS]
        ).grid(row=r, column=1, sticky="ew", pady=5, padx=6)

        r += 1
        ctk.CTkLabel(scroll, text="VAD 静音过滤").grid(row=r, column=0, sticky="w", pady=5, padx=6)
        self.vad_label_var = ctk.StringVar(value=VAD_OPTIONS[2][0])
        ctk.CTkOptionMenu(
            scroll, variable=self.vad_label_var, values=[x[0] for x in VAD_OPTIONS]
        ).grid(row=r, column=1, sticky="ew", pady=5, padx=6)

        r += 1
        ctk.CTkLabel(scroll, text="源语言").grid(row=r, column=0, sticky="w", pady=5, padx=6)
        self.src_lang_label_var = ctk.StringVar(value=SOURCE_LANG_OPTIONS[0][0])
        ctk.CTkOptionMenu(
            scroll, variable=self.src_lang_label_var, values=[x[0] for x in SOURCE_LANG_OPTIONS]
        ).grid(row=r, column=1, sticky="ew", pady=5, padx=6)

        r += 1
        ctk.CTkLabel(scroll, text="目标语言").grid(row=r, column=0, sticky="w", pady=5, padx=6)
        self.tgt_lang_label_var = ctk.StringVar(value=LANG_OPTIONS[0][0])
        ctk.CTkOptionMenu(scroll, variable=self.tgt_lang_label_var, values=[x[0] for x in LANG_OPTIONS]).grid(
            row=r, column=1, sticky="ew", pady=5, padx=6
        )

        r += 1
        ctk.CTkLabel(scroll, text="特殊提示词").grid(row=r, column=0, sticky="nw", pady=5, padx=6)
        prompt_wrap = ctk.CTkFrame(scroll, fg_color="transparent")
        prompt_wrap.grid(row=r, column=1, sticky="ew", pady=5, padx=6)
        prompt_wrap.grid_columnconfigure(0, weight=1)

        prompt_bar = ctk.CTkFrame(prompt_wrap, fg_color="transparent")
        prompt_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        prompt_bar.grid_columnconfigure(0, weight=1)

        self.prompt_template_var = ctk.StringVar(value=PROMPT_MENU_PLACEHOLDER)
        self.prompt_template_menu = ctk.CTkOptionMenu(
            prompt_bar,
            variable=self.prompt_template_var,
            values=[PROMPT_MENU_PLACEHOLDER],
            command=self._on_prompt_template,
            width=220,
        )
        self.prompt_template_menu.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            prompt_bar,
            text="保存",
            width=56,
            command=self._save_prompt_template,
        ).grid(row=0, column=1, sticky="e", padx=(6, 0))
        ctk.CTkButton(
            prompt_bar,
            text="管理",
            width=56,
            command=self._open_prompt_manager,
        ).grid(row=0, column=2, sticky="e", padx=(6, 0))
        ctk.CTkButton(
            prompt_bar,
            text="清空",
            width=56,
            command=self._clear_special_prompt,
        ).grid(row=0, column=3, sticky="e", padx=(6, 0))

        self.special_prompt_var = ctk.StringVar()
        self.special_prompt_entry = ctk.CTkTextbox(prompt_wrap, height=64, wrap="word")
        self.special_prompt_entry.grid(row=1, column=0, sticky="ew")

        self.glossary_var = self.special_prompt_var

        r += 1
        ctk.CTkLabel(scroll, text="翻译方式").grid(row=r, column=0, sticky="w", pady=5, padx=6)
        self.translator_label_var = ctk.StringVar(value=TRANSLATORS[0][0])
        ctk.CTkOptionMenu(
            scroll,
            variable=self.translator_label_var,
            values=[x[0] for x in TRANSLATORS],
            command=self._on_translator_change,
        ).grid(row=r, column=1, sticky="ew", pady=5, padx=6)

        r += 1
        ctk.CTkLabel(scroll, text="嵌入方式").grid(row=r, column=0, sticky="w", pady=5, padx=6)
        self.embed_label_var = ctk.StringVar(value=EMBED_MODES[0][0])
        ctk.CTkOptionMenu(scroll, variable=self.embed_label_var, values=[x[0] for x in EMBED_MODES]).grid(
            row=r, column=1, sticky="ew", pady=5, padx=6
        )

        r += 1
        ctk.CTkLabel(scroll, text="计算设备").grid(row=r, column=0, sticky="w", pady=5, padx=6)
        self.device_var = ctk.StringVar(value="auto")
        ctk.CTkOptionMenu(scroll, variable=self.device_var, values=["auto", "cuda", "cpu"]).grid(
            row=r, column=1, sticky="ew", pady=5, padx=6
        )

        r += 1
        self.backend_host = ctk.CTkFrame(scroll, fg_color="transparent")
        self.backend_host.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.backend_host.grid_columnconfigure(0, weight=1)

        self.backend_title = ctk.CTkLabel(
            self.backend_host, text="翻译参数", text_color=("gray40", "gray60")
        )
        self.backend_title.grid(row=0, column=0, sticky="w", padx=6, pady=(4, 2))

        self.ollama_url_var = ctk.StringVar(value="")
        self.ollama_model_var = ctk.StringVar(value=OLLAMA_NO_MODEL)
        self.deepl_key_var = ctk.StringVar()
        self.deepl_free_var = ctk.BooleanVar(value=True)
        self.baidu_id_var = ctk.StringVar()
        self.baidu_key_var = ctk.StringVar()
        self.openai_url_var = ctk.StringVar(value="https://api.openai.com/v1")
        self.openai_key_var = ctk.StringVar()
        self.openai_model_var = ctk.StringVar(value="gpt-4o-mini")

        self._backend_frames: dict[str, ctk.CTkFrame] = {}

        fr = ctk.CTkFrame(self.backend_host, fg_color="transparent")
        fr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(fr, text="Ollama 地址").grid(row=0, column=0, sticky="w", pady=4, padx=6)
        ctk.CTkEntry(fr, textvariable=self.ollama_url_var).grid(row=0, column=1, sticky="ew", pady=4, padx=6)
        ctk.CTkLabel(fr, text="Ollama 模型").grid(row=1, column=0, sticky="w", pady=4, padx=6)
        self.ollama_menu = ctk.CTkOptionMenu(
            fr, variable=self.ollama_model_var, values=[OLLAMA_NO_MODEL]
        )
        self.ollama_menu.grid(row=1, column=1, sticky="ew", pady=4, padx=6)
        self._backend_frames["ollama"] = fr

        fr = ctk.CTkFrame(self.backend_host, fg_color="transparent")
        fr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(fr, text="API Key").grid(row=0, column=0, sticky="w", pady=4, padx=6)
        ctk.CTkEntry(fr, textvariable=self.deepl_key_var, show="*").grid(
            row=0, column=1, sticky="ew", pady=4, padx=6
        )
        ctk.CTkCheckBox(fr, text="Free 端点", variable=self.deepl_free_var).grid(
            row=1, column=1, sticky="w", pady=4, padx=6
        )
        self._backend_frames["deepl"] = fr

        fr = ctk.CTkFrame(self.backend_host, fg_color="transparent")
        fr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(fr, text="APP ID").grid(row=0, column=0, sticky="w", pady=4, padx=6)
        ctk.CTkEntry(fr, textvariable=self.baidu_id_var, placeholder_text="百度翻译开放平台 APP ID").grid(row=0, column=1, sticky="ew", pady=4, padx=6)
        ctk.CTkLabel(fr, text="密钥").grid(row=1, column=0, sticky="w", pady=4, padx=6)
        ctk.CTkEntry(fr, textvariable=self.baidu_key_var, show="*", placeholder_text="密钥 Key").grid(
            row=1, column=1, sticky="ew", pady=4, padx=6
        )
        self._backend_frames["baidu"] = fr

        fr = ctk.CTkFrame(self.backend_host, fg_color="transparent")
        fr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(fr, text="谷歌翻译").grid(row=0, column=0, sticky="w", pady=4, padx=6)
        self._backend_frames["google"] = fr

        fr = ctk.CTkFrame(self.backend_host, fg_color="transparent")
        fr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(fr, text="Base URL").grid(row=0, column=0, sticky="w", pady=4, padx=6)
        ctk.CTkEntry(fr, textvariable=self.openai_url_var).grid(row=0, column=1, sticky="ew", pady=4, padx=6)
        ctk.CTkLabel(fr, text="API Key").grid(row=1, column=0, sticky="w", pady=4, padx=6)
        ctk.CTkEntry(fr, textvariable=self.openai_key_var, show="*").grid(
            row=1, column=1, sticky="ew", pady=4, padx=6
        )
        ctk.CTkLabel(fr, text="模型名").grid(row=2, column=0, sticky="w", pady=4, padx=6)
        ctk.CTkEntry(fr, textvariable=self.openai_model_var).grid(row=2, column=1, sticky="ew", pady=4, padx=6)
        self._backend_frames["openai"] = fr

        self._backend_frames["none"] = ctk.CTkFrame(self.backend_host, fg_color="transparent")

        r += 1
        ctk.CTkLabel(scroll, text="字体大小").grid(row=r, column=0, sticky="w", pady=4, padx=6)
        self.font_size_var = ctk.StringVar(value="22")
        ctk.CTkEntry(scroll, textvariable=self.font_size_var).grid(row=r, column=1, sticky="ew", pady=4, padx=6)

        r += 1
        ctk.CTkLabel(scroll, text="字体名称").grid(row=r, column=0, sticky="w", pady=4, padx=6)
        self.font_name_var = ctk.StringVar(value="Microsoft YaHei")
        ctk.CTkEntry(scroll, textvariable=self.font_name_var).grid(row=r, column=1, sticky="ew", pady=4, padx=6)

        r += 1
        ctk.CTkLabel(scroll, text="模型缓存").grid(row=r, column=0, sticky="w", pady=4, padx=6)
        cache_row = ctk.CTkFrame(scroll, fg_color="transparent")
        cache_row.grid(row=r, column=1, sticky="ew", pady=4, padx=6)
        cache_row.grid_columnconfigure(0, weight=1)
        self.model_cache_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            cache_row,
            textvariable=self.model_cache_var,
            placeholder_text="空=程序目录下 cache/huggingface",
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(cache_row, text="浏览…", width=64, command=self._pick_model_cache_dir).grid(
            row=0, column=1, padx=(6, 0)
        )

        r += 1
        self.env_label = ctk.CTkLabel(scroll, text="环境检测中…", text_color=("gray30", "gray70"), anchor="w")
        self.env_label.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(10, 4), padx=6)

        bottom = ctk.CTkFrame(self)
        bottom.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 16))
        bottom.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(bottom)
        self.progress.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        self.progress.set(0)

        self.status_var = ctk.StringVar(value="就绪")
        ctk.CTkLabel(bottom, textvariable=self.status_var, anchor="w").grid(
            row=1, column=0, sticky="ew", padx=12, pady=2
        )

        self.log_box = ctk.CTkTextbox(bottom, height=120)
        self.log_box.grid(row=2, column=0, sticky="ew", padx=12, pady=8)

        btn_row = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        btn_row.grid_columnconfigure(0, weight=1)

        self.start_btn = ctk.CTkButton(
            btn_row,
            text="开始处理",
            height=40,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._start_queue_or_single,
        )
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.stop_btn = ctk.CTkButton(
            btn_row,
            text="停止",
            width=90,
            height=40,
            fg_color="#8B2942",
            hover_color="#6B1F32",
            state="disabled",
            command=self._stop,
        )
        self.stop_btn.grid(row=0, column=1, padx=4)
        self.resume_btn = ctk.CTkButton(
            btn_row,
            text="续跑",
            width=80,
            height=40,
            command=self._resume_last,
        )
        self.resume_btn.grid(row=0, column=2, padx=4)
        self.jobs_btn = ctk.CTkButton(
            btn_row,
            text="任务",
            width=80,
            height=40,
            command=self._open_jobs_window,
        )
        self.jobs_btn.grid(row=0, column=3, padx=4)
        ctk.CTkButton(btn_row, text="打开导出目录", width=100, command=self._open_export_dir).grid(
            row=0, column=4, padx=4
        )
        ctk.CTkButton(btn_row, text="刷新环境", width=80, command=self._refresh_env).grid(
            row=0, column=5, padx=(4, 0)
        )
        ctk.CTkButton(btn_row, text="许可说明", width=80, command=self._show_third_party_notice).grid(
            row=0, column=6, padx=(4, 0)
        )

    def _apply_settings_to_ui(self) -> None:

        s = self._cfg or {}
        code = s.get("translator", "ollama")
        for label, c in TRANSLATORS:
            if c == code:
                self.translator_label_var.set(label)
                break
        # Ollama 不预填本机地址/模型；仅恢复用户曾保存的值
        self.ollama_url_var.set(str(s.get("ollama_base_url") or "").strip())
        om = str(s.get("ollama_model") or "").strip()
        self.ollama_model_var.set(om if om else OLLAMA_NO_MODEL)
        self.deepl_key_var.set(s.get("deepl_api_key", ""))
        self.deepl_free_var.set(bool(s.get("deepl_use_free", True)))
        self.baidu_id_var.set(s.get("baidu_app_id", ""))
        self.baidu_key_var.set(s.get("baidu_app_key", ""))
        self.openai_url_var.set(s.get("openai_base_url", "https://api.openai.com/v1") or "https://api.openai.com/v1")
        self.openai_key_var.set(s.get("openai_api_key", ""))
        self.openai_model_var.set(s.get("openai_model", "gpt-4o-mini") or "gpt-4o-mini")
        wm = s.get("whisper_model") or _RECOMMENDED_CUDA_MODEL
        # 旧默认 medium + 无显式升级标记时，交给 GPU 检测后再建议；此处先规范化
        self._set_whisper_model_code(str(wm))
        q = s.get("quality") or _RECOMMENDED_CUDA_QUALITY
        if q in ("最高质量",):
            q = "dialogue"
        self._set_quality_code(str(q))

        vad_code = str(s.get("vad_threshold") or "0.35").strip()
        matched_vad = False
        for label, code in VAD_OPTIONS:
            if code == vad_code:
                self.vad_label_var.set(label)
                matched_vad = True
                break
        if not matched_vad:
            self.vad_label_var.set(VAD_OPTIONS[2][0])

        dev = s.get("device") or "auto"
        if dev in ("cuda", "auto", "cpu"):
            self.device_var.set(dev)
        emb = s.get("embed_mode")
        if emb:
            for label, code in EMBED_MODES:
                if code == emb:
                    self.embed_label_var.set(label)
                    break
        if s.get("font_size") is not None and str(s.get("font_size")).strip() != "":
            self.font_size_var.set(str(s.get("font_size")))
        if s.get("font_name"):
            self.font_name_var.set(str(s.get("font_name")))
        self.export_dir_var.set(str(s.get("export_dir") or ""))
        self.model_cache_var.set(str(s.get("model_cache_dir") or ""))
        self._apply_path_fields_to_runtime()

        self.video_var.set("")
        self._load_prompt_history_from_cfg(s)
        self._reset_session_fields(log=False)

    def _load_prompt_history_from_cfg(self, s: dict | None = None) -> None:
        cfg = s if isinstance(s, dict) else (self._cfg if isinstance(self._cfg, dict) else {})
        raw = cfg.get("prompt_history")
        hist: list[dict] = []
        seen_text: set[str] = set()
        if isinstance(raw, list):
            for item in raw:
                norm = _normalize_prompt_item(item)
                if not norm:
                    continue
                if norm["text"] in seen_text:
                    continue
                seen_text.add(norm["text"])
                hist.append(norm)
                if len(hist) >= PROMPT_HISTORY_MAX:
                    break
        self._prompt_history = hist
        self._selected_prompt_index = None
        self._refresh_prompt_menu()

    def _serialize_prompt_history(self) -> list[dict]:
        out: list[dict] = []
        for item in self._prompt_history:
            norm = _normalize_prompt_item(item)
            if norm:
                out.append({"title": norm["title"], "text": norm["text"]})
        return out[:PROMPT_HISTORY_MAX]

    def _refresh_prompt_menu(self) -> None:
        """Rebuild dropdown from user templates (title labels only)."""
        labels = [PROMPT_MENU_PLACEHOLDER]
        mapping: dict[str, int] = {}
        used: set[str] = set(labels)
        for idx, item in enumerate(self._prompt_history):
            base = (item.get("title") or _shorten_prompt_label(item.get("text", "")) or "未命名").strip()
            base = _shorten_prompt_label(base, 40) or "未命名"
            label = base
            n = 2
            while label in used:
                suffix = f" ·{n}"
                label = (
                    (base[: max(1, 40 - len(suffix))] + suffix)
                    if len(base) + len(suffix) > 44
                    else base + suffix
                )
                n += 1
            used.add(label)
            labels.append(label)
            mapping[label] = idx
        self._prompt_menu_map = mapping
        try:
            self.prompt_template_menu.configure(values=labels)
        except Exception:
            pass
        cur = self.prompt_template_var.get()
        if cur not in labels:
            self.prompt_template_var.set(PROMPT_MENU_PLACEHOLDER)

    def _persist_prompt_history(self) -> None:
        self._refresh_prompt_menu()
        if getattr(self, "_settings_ready", False):
            self._persist_settings(quiet=True)
        else:
            self._persist_settings(quiet=True)

    def _prompt_item_at(self, index: int | None) -> dict | None:
        if index is None:
            return None
        if 0 <= index < len(self._prompt_history):
            return self._prompt_history[index]
        return None

    def _upsert_prompt_template(
        self,
        text: str,
        title: str,
        *,
        index: int | None = None,
        move_to_top: bool = False,
    ) -> int | None:
        """Insert or update a template. Returns new index, or None if empty."""
        text = (text or "").strip()
        title = (title or "").strip() or (_shorten_prompt_label(text, 28) or "未命名")
        if not text:
            return None
        item = {"title": title, "text": text}
        if index is not None and 0 <= index < len(self._prompt_history):
            self._prompt_history[index] = item
            if move_to_top and index != 0:
                self._prompt_history.insert(0, self._prompt_history.pop(index))
                index = 0
            return index
        # Same text already exists → update that entry
        for i, old in enumerate(self._prompt_history):
            if old.get("text") == text:
                self._prompt_history[i] = item
                if move_to_top and i != 0:
                    self._prompt_history.insert(0, self._prompt_history.pop(i))
                    return 0
                return i
        self._prompt_history.insert(0, item)
        if len(self._prompt_history) > PROMPT_HISTORY_MAX:
            self._prompt_history = self._prompt_history[:PROMPT_HISTORY_MAX]
        return 0

    def _delete_prompt_template(self, index: int) -> bool:
        if not (0 <= index < len(self._prompt_history)):
            return False
        self._prompt_history.pop(index)
        if self._selected_prompt_index is not None:
            if self._selected_prompt_index == index:
                self._selected_prompt_index = None
            elif self._selected_prompt_index > index:
                self._selected_prompt_index -= 1
        return True

    def _move_prompt_template(self, index: int, delta: int) -> int | None:
        j = index + delta
        if not (0 <= index < len(self._prompt_history)):
            return None
        if not (0 <= j < len(self._prompt_history)):
            return index
        self._prompt_history[index], self._prompt_history[j] = (
            self._prompt_history[j],
            self._prompt_history[index],
        )
        if self._selected_prompt_index == index:
            self._selected_prompt_index = j
        elif self._selected_prompt_index == j:
            self._selected_prompt_index = index
        return j

    def _save_prompt_template(self) -> None:
        """Open editor to save current prompt as a named template."""
        text = self._get_special_prompt()
        if not text:
            messagebox.showinfo("保存模板", "当前提示词为空，请先填写再保存。")
            return
        default_title = ""
        sel = self._prompt_item_at(self._selected_prompt_index)
        if sel and sel.get("text") == text:
            default_title = sel.get("title") or ""
        if not default_title:
            default_title = _shorten_prompt_label(text, 28) or "未命名"
        self._open_prompt_editor(
            title=default_title,
            text=text,
            index=self._selected_prompt_index if (sel and sel.get("text") == text) else None,
            mode="save",
        )

    def _open_prompt_editor(
        self,
        *,
        title: str = "",
        text: str = "",
        index: int | None = None,
        mode: str = "edit",
    ) -> None:
        win = ctk.CTkToplevel(self)
        win.title("编辑模板" if mode == "edit" else "保存模板")
        win.geometry("520x360")
        win.minsize(420, 300)
        win.transient(self)
        win.grab_set()
        try:
            win.focus_force()
        except Exception:
            pass

        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(win, text="标题（下拉菜单显示）").grid(
            row=0, column=0, sticky="w", padx=14, pady=(14, 4)
        )
        title_var = ctk.StringVar(value=title or "")
        title_entry = ctk.CTkEntry(win, textvariable=title_var)
        title_entry.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))

        ctk.CTkLabel(win, text="提示词内容").grid(row=2, column=0, sticky="w", padx=14, pady=(4, 4))
        body = ctk.CTkTextbox(win, wrap="word")
        body.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 8))
        if text:
            body.insert("1.0", text)

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 14))
        btn_row.grid_columnconfigure(0, weight=1)

        def do_save(as_new: bool = False) -> None:
            new_title = title_var.get().strip()
            new_text = body.get("1.0", "end").strip()
            if not new_text:
                messagebox.showinfo("保存模板", "提示词内容不能为空。", parent=win)
                return
            if not new_title:
                new_title = _shorten_prompt_label(new_text, 28) or "未命名"
            use_index = None if as_new else index
            new_idx = self._upsert_prompt_template(
                new_text,
                new_title,
                index=use_index,
                move_to_top=False,
            )
            self._selected_prompt_index = new_idx
            self._set_special_prompt(new_text)
            self._persist_prompt_history()
            self._refresh_prompt_manager_if_open()
            self._log(f"[提示词] 已保存模板：{new_title}")
            try:
                win.destroy()
            except Exception:
                pass
            self._refresh_enqueue_banner()

        if index is not None:
            ctk.CTkButton(btn_row, text="另存为新模板", width=110, command=lambda: do_save(True)).grid(
                row=0, column=0, sticky="w"
            )
            ctk.CTkButton(btn_row, text="保存修改", width=90, command=lambda: do_save(False)).grid(
                row=0, column=1, sticky="e", padx=(8, 0)
            )
        else:
            ctk.CTkButton(btn_row, text="保存", width=90, command=lambda: do_save(False)).grid(
                row=0, column=1, sticky="e"
            )
        ctk.CTkButton(btn_row, text="取消", width=72, command=win.destroy).grid(
            row=0, column=2, sticky="e", padx=(8, 0)
        )
        title_entry.focus_set()

    def _open_prompt_manager(self) -> None:
        if self._prompt_manage_win is not None:
            try:
                if self._prompt_manage_win.winfo_exists():
                    self._prompt_manage_win.lift()
                    self._prompt_manage_win.focus_force()
                    self._rebuild_prompt_manager_list()
                    return
            except Exception:
                self._prompt_manage_win = None

        win = ctk.CTkToplevel(self)
        self._prompt_manage_win = win
        win.title("管理提示词模板")
        win.geometry("640x480")
        win.minsize(520, 360)
        win.transient(self)
        try:
            win.focus_force()
        except Exception:
            pass

        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(win, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head,
            text="可设定标题、编辑内容、上下排序、删除单条。选择「应用」填入主界面。",
            anchor="w",
            justify="left",
            wraplength=580,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="新建", width=64, command=self._manager_new_prompt).grid(
            row=0, column=1, padx=(8, 0)
        )

        self._prompt_manage_scroll = ctk.CTkScrollableFrame(win, label_text="模板列表")
        self._prompt_manage_scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        self._prompt_manage_scroll.grid_columnconfigure(0, weight=1)

        foot = ctk.CTkFrame(win, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 12))
        foot.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(foot, text="关闭", width=80, command=win.destroy).grid(row=0, column=1, sticky="e")

        def _on_close() -> None:
            self._prompt_manage_win = None
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", _on_close)
        self._rebuild_prompt_manager_list()

    def _refresh_prompt_manager_if_open(self) -> None:
        if self._prompt_manage_win is None:
            return
        try:
            if self._prompt_manage_win.winfo_exists():
                self._rebuild_prompt_manager_list()
        except Exception:
            self._prompt_manage_win = None

    def _rebuild_prompt_manager_list(self) -> None:
        scroll = getattr(self, "_prompt_manage_scroll", None)
        if scroll is None:
            return
        for child in scroll.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        if not self._prompt_history:
            ctk.CTkLabel(scroll, text="暂无模板。可在主界面填写后点「保存模板」，或点「新建」。").grid(
                row=0, column=0, sticky="w", padx=8, pady=12
            )
            return
        for i, item in enumerate(self._prompt_history):
            row = ctk.CTkFrame(scroll)
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(0, weight=1)
            title = item.get("title") or "未命名"
            preview = _shorten_prompt_label(item.get("text") or "", 48)
            label = ctk.CTkLabel(
                row,
                text=f"{i + 1}. {title}\n{preview}",
                anchor="w",
                justify="left",
            )
            label.grid(row=0, column=0, sticky="ew", padx=8, pady=6)

            btns = ctk.CTkFrame(row, fg_color="transparent")
            btns.grid(row=0, column=1, sticky="e", padx=6, pady=4)
            ctk.CTkButton(btns, text="应用", width=52, command=lambda idx=i: self._manager_apply(idx)).grid(
                row=0, column=0, padx=2
            )
            ctk.CTkButton(btns, text="编辑", width=52, command=lambda idx=i: self._manager_edit(idx)).grid(
                row=0, column=1, padx=2
            )
            ctk.CTkButton(btns, text="↑", width=36, command=lambda idx=i: self._manager_move(idx, -1)).grid(
                row=0, column=2, padx=2
            )
            ctk.CTkButton(btns, text="↓", width=36, command=lambda idx=i: self._manager_move(idx, 1)).grid(
                row=0, column=3, padx=2
            )
            ctk.CTkButton(
                btns,
                text="删除",
                width=52,
                fg_color="#8B3A3A",
                hover_color="#A04545",
                command=lambda idx=i: self._manager_delete(idx),
            ).grid(row=0, column=4, padx=2)

    def _manager_apply(self, index: int) -> None:
        item = self._prompt_item_at(index)
        if not item:
            return
        self._selected_prompt_index = index
        self._set_special_prompt(item.get("text") or "")
        self.prompt_template_var.set(PROMPT_MENU_PLACEHOLDER)
        self._refresh_enqueue_banner()
        self._log(f"[提示词] 已应用模板：{item.get('title') or '未命名'}")

    def _manager_edit(self, index: int) -> None:
        item = self._prompt_item_at(index)
        if not item:
            return
        self._open_prompt_editor(
            title=item.get("title") or "",
            text=item.get("text") or "",
            index=index,
            mode="edit",
        )

    def _manager_new_prompt(self) -> None:
        self._open_prompt_editor(
            title="",
            text=self._get_special_prompt(),
            index=None,
            mode="save",
        )

    def _manager_move(self, index: int, delta: int) -> None:
        self._move_prompt_template(index, delta)
        self._persist_prompt_history()
        self._rebuild_prompt_manager_list()

    def _manager_delete(self, index: int) -> None:
        item = self._prompt_item_at(index)
        if not item:
            return
        title = item.get("title") or "未命名"
        if not messagebox.askyesno("删除模板", f"确定删除模板「{title}」？", parent=self):
            return
        self._delete_prompt_template(index)
        self._persist_prompt_history()
        self._rebuild_prompt_manager_list()
        self._log(f"[提示词] 已删除模板：{title}")

    def _get_special_prompt(self) -> str:
        try:
            return self.special_prompt_entry.get("1.0", "end").strip()
        except Exception:
            return (self.special_prompt_var.get() or "").strip()

    def _set_special_prompt(self, text: str) -> None:
        text = text or ""
        self.special_prompt_var.set(text)
        try:
            self.special_prompt_entry.delete("1.0", "end")
            if text:
                self.special_prompt_entry.insert("1.0", text)
        except Exception:
            pass

    def _reset_session_fields(self, log: bool = True) -> None:

        self.src_lang_label_var.set(SOURCE_LANG_OPTIONS[0][0])
        self.tgt_lang_label_var.set(LANG_OPTIONS[0][0])
        self._set_special_prompt("")
        self._selected_prompt_index = None
        self.prompt_template_var.set(PROMPT_MENU_PLACEHOLDER)
        if log:
            self._log("源语言 / 目标语言 / 特殊提示词已清空")

    def _clear_special_prompt(self) -> None:
        self._set_special_prompt("")
        self._selected_prompt_index = None
        self.prompt_template_var.set(PROMPT_MENU_PLACEHOLDER)
        self._refresh_enqueue_banner()

    def _on_prompt_template(self, choice: str = "") -> None:
        name = choice or self.prompt_template_var.get()
        if name in ("", PROMPT_MENU_PLACEHOLDER, "（选择模板）", "（使用记录）"):
            return
        idx = self._prompt_menu_map.get(name)
        if idx is None:
            self.prompt_template_var.set(PROMPT_MENU_PLACEHOLDER)
            return
        item = self._prompt_item_at(idx)
        if item:
            self._selected_prompt_index = idx
            self._set_special_prompt(item.get("text") or "")
        self.prompt_template_var.set(PROMPT_MENU_PLACEHOLDER)
        self._refresh_enqueue_banner()

    def _collect_settings(self) -> dict:

        try:
            font_size_val: int | str = int(str(self.font_size_var.get()).strip() or "22")
        except ValueError:
            font_size_val = str(self.font_size_var.get()).strip() or "22"
        prev = self._cfg if isinstance(self._cfg, dict) else {}
        # keep history even if UI not ready; prefer live list
        hist = self._serialize_prompt_history()
        if not hist and isinstance(prev.get("prompt_history"), list):
            hist = []
            for x in prev["prompt_history"]:
                norm = _normalize_prompt_item(x)
                if norm:
                    hist.append({"title": norm["title"], "text": norm["text"]})
                if len(hist) >= PROMPT_HISTORY_MAX:
                    break
        return {
            "translator": self._map_label(self.translator_label_var.get(), TRANSLATORS),
            "ollama_base_url": self.ollama_url_var.get().strip(),
            "ollama_model": self._ollama_model_selected(),
            "deepl_api_key": self.deepl_key_var.get().strip(),
            "deepl_use_free": bool(self.deepl_free_var.get()),
            "baidu_app_id": self.baidu_id_var.get().strip(),
            "baidu_app_key": self.baidu_key_var.get().strip(),
            "openai_base_url": self.openai_url_var.get().strip(),
            "openai_api_key": self.openai_key_var.get().strip(),
            "openai_model": self.openai_model_var.get().strip(),
            "whisper_model": self._normalize_whisper_model(
                self._map_label(self.model_label_var.get(), WHISPER_MODELS)
            ),
            "quality": self._map_label(self.quality_label_var.get(), QUALITY_PRESETS),
            "vad_threshold": float(self._map_label(self.vad_label_var.get(), VAD_OPTIONS) or 0.35),
            "device": self.device_var.get() if self.device_var.get() in ("auto", "cuda", "cpu") else "auto",
            "embed_mode": self._map_label(self.embed_label_var.get(), EMBED_MODES),
            "font_size": font_size_val,
            "font_name": self.font_name_var.get().strip() or "Microsoft YaHei",
            "export_dir": self.export_dir_var.get().strip(),
            "model_cache_dir": self.model_cache_var.get().strip(),
            "prompt_history": hist,
            "source_language": "",
            "target_language": "",
            "special_prompt": "",
            "glossary": "",
            "video_path": "",
            "third_party_notice_ack": bool(prev.get("third_party_notice_ack", False)),
            "job_retention": (self._cfg or {}).get("job_retention", "never"),
        }

    def _show_third_party_notice(self) -> None:
        messagebox.showinfo("许可与下载说明", THIRD_PARTY_NOTICE)

    def _maybe_show_third_party_notice(self) -> None:

        s = self._cfg if isinstance(self._cfg, dict) else {}
        if s.get("third_party_notice_ack"):
            return
        messagebox.showinfo("许可与下载说明（首次）", THIRD_PARTY_NOTICE)
        s = dict(s)
        s["third_party_notice_ack"] = True
        self._cfg = s
        try:

            data = self._collect_settings()
            data["third_party_notice_ack"] = True
            save_settings(data)
            self._cfg = data
        except Exception:
            pass

    def _apply_path_fields_to_runtime(self) -> None:
        set_path_overrides(
            export_dir=self.export_dir_var.get().strip(),
            model_cache_dir=self.model_cache_var.get().strip(),
        )
        try:
            apply_path_config()
        except Exception:
            pass

    def _pick_export_dir(self) -> None:
        path = filedialog.askdirectory(title="选择默认导出目录")
        if path:
            self.export_dir_var.set(path)
            self._apply_path_fields_to_runtime()
            self._persist_settings(quiet=True)
            self._log(f"[路径] 导出目录已设为: {path}")

    def _pick_model_cache_dir(self) -> None:
        path = filedialog.askdirectory(title="选择模型缓存目录")
        if path:
            self.model_cache_var.set(path)
            self._apply_path_fields_to_runtime()
            self._persist_settings(quiet=True)
            self._log(f"[路径] 模型缓存已设为: {path}")

    def _persist_settings(self, *, quiet: bool = True) -> None:
        data = self._collect_settings()
        save_settings(data)
        self._cfg = data
        self._apply_path_fields_to_runtime()
        if not quiet:
            self._log(f"设置已保存: {_settings_path()}")
            self._log(
                f"[路径] 导出={resolve_export_dir()} | 模型缓存={resolve_model_cache_dir()}"
            )

    def _wire_settings_autosave(self) -> None:

        vars_to_watch = [
            self.quality_label_var,
            self.vad_label_var,
            self.model_label_var,
            self.translator_label_var,
            self.embed_label_var,
            self.device_var,
            self.ollama_url_var,
            self.ollama_model_var,
            self.deepl_key_var,
            self.deepl_free_var,
            self.baidu_id_var,
            self.baidu_key_var,
            self.openai_url_var,
            self.openai_key_var,
            self.openai_model_var,
            self.font_size_var,
            self.font_name_var,
            self.export_dir_var,
            self.model_cache_var,
        ]
        for var in vars_to_watch:
            try:
                var.trace_add("write", self._on_persistent_setting_changed)
            except Exception:
                pass
        for var in (
            self.src_lang_label_var,
            self.tgt_lang_label_var,
            self.quality_label_var,
            self.translator_label_var,
        ):
            try:
                var.trace_add("write", lambda *_a: self._refresh_enqueue_banner())
            except Exception:
                pass
        try:
            self.special_prompt_entry.bind(
                "<KeyRelease>", lambda _e: self._refresh_enqueue_banner()
            )
        except Exception:
            pass
        self._settings_ready = True
        self.after(100, self._refresh_enqueue_banner)

    def _on_persistent_setting_changed(self, *_args) -> None:
        if not getattr(self, "_settings_ready", False):
            return
        if self._save_after_id is not None:
            try:
                self.after_cancel(self._save_after_id)
            except Exception:
                pass
        self._save_after_id = self.after(450, self._autosave_settings)

    def _autosave_settings(self) -> None:
        self._save_after_id = None
        try:
            self._persist_settings(quiet=True)
        except Exception:
            pass

    def _save_settings_ui(self) -> None:
        self._persist_settings(quiet=False)
        messagebox.showinfo("已保存", f"设置已写入:\n{_settings_path()}\n\n（改选项后也会自动保存）")

    def _on_app_close(self) -> None:
        try:
            if self._save_after_id is not None:
                try:
                    self.after_cancel(self._save_after_id)
                except Exception:
                    pass
            self._persist_settings(quiet=True)
        except Exception:
            pass
        try:
            self._apply_job_retention_purge(on_exit=True)
        except Exception:
            pass
        self.destroy()

    def _log(self, msg: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, lambda: self._log(msg))
            except Exception:
                pass
            return
        import time
        now_str = time.strftime("[%H:%M:%S] ")
        clean_msg = msg.rstrip()
        # Add timestamp if not present
        if not clean_msg.startswith("[") and not clean_msg.startswith("——"):
            formatted = f"{now_str}{clean_msg}"
        else:
            formatted = f"{now_str}{clean_msg}"
        try:
            self.log_box.insert("end", formatted + "\n")
            self.log_box.see("end")
        except Exception:
            pass

    def _map_label(self, label: str, pairs: list[tuple[str, str]]) -> str:
        for name, code in pairs:
            if name == label:
                return code
        return pairs[0][1]

    def _normalize_whisper_model(self, code: str) -> str:
        c = (code or "").strip().lower()
        if not c:
            return _RECOMMENDED_CUDA_MODEL
        known = {m for _, m in WHISPER_MODELS}
        if c in known:
            return c
        return _RECOMMENDED_CUDA_MODEL

    def _set_whisper_model_code(self, code: str) -> None:
        code = self._normalize_whisper_model(code)
        for label, c in WHISPER_MODELS:
            if c == code:
                self.model_label_var.set(label)
                return

    def _set_quality_code(self, code: str) -> None:
        code = (code or "").strip().lower()
        # 旧文案「最高质量」等
        if code in ("最高质量", "max", "best"):
            code = "dialogue"
        for label, c in QUALITY_PRESETS:
            if c == code:
                self.quality_label_var.set(label)
                return

    def _show_backend_fields(self, code: str) -> None:

        for key, fr in self._backend_frames.items():
            if key == code:
                fr.grid(row=1, column=0, sticky="ew")
            else:
                fr.grid_remove()
        titles = {
            "ollama": "Ollama",
            "deepl": "DeepL",
            "baidu": "百度翻译",
            "youdao": "有道",
            "azure": "Azure",
            "openai": "大模型 API",
            "none": "",
        }
        title = titles.get(code, "")
        if title:
            self.backend_title.configure(text=title)
            self.backend_title.grid(row=0, column=0, sticky="w", padx=6, pady=(4, 2))
        else:
            self.backend_title.grid_remove()

    def _on_translator_change(self, _value: str = "") -> None:
        code = self._map_label(self.translator_label_var.get(), TRANSLATORS)
        self._show_backend_fields(code)
        try:
            self.status_var.set(code if code != "none" else "仅识别")
        except Exception:
            pass

    def _default_output_for(self, video_path: str, target_language: str = "") -> str:
        p = Path(video_path)
        if not target_language:
            target_language = self._map_label(self.tgt_lang_label_var.get(), LANG_OPTIONS)
        tag = (target_language or "out").replace("-", "") or "out"
        embed = self._map_label(self.embed_label_var.get(), EMBED_MODES)
        out_dir = default_output_dir()
        if embed == "srt_only":
            name = f"{p.stem}.{tag}.srt"
        elif embed == "hard":
            name = f"{p.stem}.sub_{tag}.mp4"
        else:
            ext = p.suffix.lower()
            if ext in (".webm", ".ts", ".gif", ""):
                ext = ".mp4"
            name = f"{p.stem}.sub_{tag}{ext}"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            return str(out_dir / name)
        except OSError:
            return str(p.with_name(name))

    def _enqueue_settings_summary(self) -> tuple[str, str, str, str, str]:
        """Return (src_code, tgt_code, prompt, quality_code, human_line)."""
        src = self._map_label(self.src_lang_label_var.get(), SOURCE_LANG_OPTIONS)
        tgt = self._map_label(self.tgt_lang_label_var.get(), LANG_OPTIONS)
        translator = self._map_label(self.translator_label_var.get(), TRANSLATORS)
        quality = self._map_label(self.quality_label_var.get(), QUALITY_PRESETS)
        prompt = self._get_special_prompt()
        src_label = self.src_lang_label_var.get()
        tgt_label = self.tgt_lang_label_var.get()
        if not tgt and translator == "none":
            tgt_disp = "不翻译"
        elif not tgt:
            tgt_disp = "（未选目标语言）"
        else:
            tgt_disp = tgt_label
        pr_disp = (prompt.replace("\n", " ").strip()[:40] + ("…" if len(prompt) > 40 else "")) or "（无提示词）"
        q_disp = self.quality_label_var.get()
        line = f"入队设置：{src_label}  →  {tgt_disp}  |  质量:{q_disp}  |  提示词:{pr_disp}"
        return src, tgt, prompt, quality, line

    def _refresh_enqueue_banner(self) -> None:
        if not hasattr(self, "enqueue_banner_var"):
            return
        src, tgt, _prompt, _q, line = self._enqueue_settings_summary()
        translator = self._map_label(self.translator_label_var.get(), TRANSLATORS)
        if src == "":
            self.enqueue_banner_var.set(
                "入队设置：先选择「源语言」"
            )
        elif not tgt and translator != "none":
            self.enqueue_banner_var.set("入队设置：选择「目标语言」")
        else:
            self.enqueue_banner_var.set(line)

    def _refresh_queue_list(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self._refresh_queue_list)
            except Exception:
                pass
            return
        lines = []
        queue_snapshot = list(self._queue)
        total = len(queue_snapshot)
        running_i = 0
        done_n = 0
        fail_n = 0
        wait_n = 0
        for i, item in enumerate(queue_snapshot, 1):
            name = Path(item.get("video_path") or "").name
            src = item.get("source_language") or "?"
            tgt = item.get("target_language") or "?"
            pr = (item.get("special_prompt") or "").replace("\n", " ")
            if len(pr) > 20:
                pr = pr[:20] + "…"
            st = item.get("status") or "queued"
            st_cn = _QUEUE_STATUS_CN.get(st, st)
            if st == "running":
                running_i = i
                mark = "▶"
            elif st == "done":
                done_n += 1
                mark = "✓"
            elif st == "failed":
                fail_n += 1
                mark = "✗"
            elif st == "stopped":
                mark = "■"
            else:
                wait_n += 1
                mark = "·"
            err = (item.get("error") or "").strip()
            extra = f"  原因:{err[:30]}" if err and st == "failed" else ""
            lines.append(f"{mark} {i}/{total} [{st_cn}] {name}  |  {src}→{tgt}  |  提示:{pr or '无'}{extra}")
        self.queue_list.configure(state="normal")
        self.queue_list.delete("1.0", "end")
        if lines:
            self.queue_list.insert("1.0", "\n".join(lines))
        else:
            self.queue_list.insert(
                "1.0",
                "(1) 下方设好源语言/目标语言/提示词\n(2) 拖入或多个浏览视频，或单文件后点「加入队列」（多选时会使用相同语言与提示词）\n(3) 点「开始处理」",
            )
        self.queue_list.configure(state="disabled")
        if hasattr(self, "queue_progress_var"):
            if total == 0:
                self.queue_progress_var.set("队列：空")
            elif running_i:
                self.queue_progress_var.set(
                    f"队列进度：第 {running_i}/{total} 个进行中 · 完成{done_n} · 失败{fail_n} · 等待{wait_n}"
                )
            else:
                self.queue_progress_var.set(
                    f"队列：共 {total} 个 · 等待{wait_n} · 完成{done_n} · 失败{fail_n}"
                )
        if not (self._worker and self._worker.is_alive()):
            n = len([q for q in self._queue if q.get("status") == "queued"])
            self.start_btn.configure(text=f"开始队列 ({n})" if n else "开始处理")
        self._refresh_enqueue_banner()

    def _queue_add_current(self) -> None:
        if self._queue_running or (self._worker and self._worker.is_alive()):
            messagebox.showwarning("队列", "正在处理中，请稍候")
            return
        video = self.video_var.get().strip().strip('"')
        if not video or not Path(video).is_file():
            messagebox.showerror("队列", "请先选择有效的视频文件")
            return
        src, tgt, prompt, _q, summary = self._enqueue_settings_summary()
        translator = self._map_label(self.translator_label_var.get(), TRANSLATORS)
        if src == "":
            messagebox.showerror(
                "源语言",
                "加入队列前请为该视频选择源语言。\n\n"
                "当前入队设置会显示在视频框下方蓝色提示行。",
            )
            return
        if not tgt and translator != "none":
            messagebox.showerror("目标语言", "加入队列前请为该视频选择目标语言")
            return
        if not tgt and translator == "none":
            tgt = "src"
        item = {
            "video_path": str(Path(video).resolve()),
            "source_language": src,
            "target_language": tgt,
            "special_prompt": prompt,
            "status": "queued",
            "error": "",
        }
        for q in self._queue:
            if q.get("video_path") == item["video_path"] and q.get("status") in ("queued", "running"):
                messagebox.showwarning("队列", "该视频已在队列中")
                return
        self._queue.append(item)
        self._log(f"[队列] + {Path(video).name} | {summary}")
        self._refresh_queue_list()
        self.video_var.set("")
        self._log("已加入队列（语言/提示词已锁定到该任务；可改设置后继续加下一个）")

    def _queue_remove_selected(self) -> None:
        if not self._queue:
            return
        if self._queue_running:
            messagebox.showwarning("队列", "队列运行中，请先停止")
            return
        removed = self._queue.pop()
        self._log(f"[队列] 已移除末项: {Path(removed.get('video_path') or '').name}")
        self._refresh_queue_list()

    def _queue_clear(self) -> None:
        if self._queue_running:
            messagebox.showwarning("队列", "队列运行中，请先停止")
            return
        self._queue.clear()
        self._log("[队列] 已清空")
        self._refresh_queue_list()

    def _enable_drag_drop(self) -> None:
        """Windows file drop only (no global message hooks — those crash on drag)."""
        if sys.platform != "win32":
            return
        self._dnd_queue: list[list[str]] = []
        self._dnd_old_procs: dict[int, int] = {}
        self._dnd_refs: list = []
        self._dnd_ready = False
        # Window must be mapped; delay install
        self.after(500, self._install_drag_drop)
        self.after(2000, self._dnd_refresh_hooks)
        # Poll queue on Tk thread — never touch Tk from WndProc
        self.after(150, self._dnd_poll_queue)

    def _dnd_query_files(self, hdrop) -> list[str]:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.DragQueryFileW.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
            wintypes.LPWSTR,
            wintypes.UINT,
        ]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragFinish.argtypes = [wintypes.HANDLE]
        files: list[str] = []
        try:
            count = int(shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0) or 0)
            buf = ctypes.create_unicode_buffer(2048)
            for i in range(count):
                n = shell32.DragQueryFileW(hdrop, i, buf, 2048)
                if n and buf.value:
                    files.append(buf.value)
        finally:
            try:
                shell32.DragFinish(hdrop)
            except Exception:
                pass
        return files

    def _dnd_poll_queue(self) -> None:
        """Drain drops on the Tk main thread only."""
        try:
            q = getattr(self, "_dnd_queue", None)
            if q:
                while q:
                    files = q.pop(0)
                    try:
                        self._on_drop_files(files)
                    except Exception as e:
                        try:
                            self._log(f"[拖放] 处理失败: {e}")
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            if self.winfo_exists():
                self.after(120, self._dnd_poll_queue)
        except Exception:
            pass

    def _dnd_candidate_hwnds(self) -> list[int]:
        try:
            self.update_idletasks()
        except Exception:
            pass
        top = _toplevel_hwnd(self)
        if not top:
            try:
                top = int(self.winfo_id())
            except Exception:
                top = 0
        hwnds = _win_collect_hwnds(top) if top else []
        for w in (
            self,
            getattr(self, "video_entry", None),
            getattr(self, "export_entry", None),
            getattr(self, "queue_list", None),
        ):
            if w is None:
                continue
            try:
                hwnds.append(int(w.winfo_id()))
            except Exception:
                pass
            try:
                inner = getattr(w, "_entry", None) or getattr(w, "_textbox", None)
                if inner is not None:
                    hwnds.append(int(inner.winfo_id()))
            except Exception:
                pass
        seen: set[int] = set()
        out: list[int] = []
        for h in hwnds:
            h = int(h or 0)
            if h and h not in seen:
                seen.add(h)
                out.append(h)
        return out

    def _dnd_subclass_hwnd(self, hwnd: int) -> bool:
        """Subclass one HWND for WM_DROPFILES. Safe: no Tk calls inside WndProc."""
        if not hwnd or hwnd in getattr(self, "_dnd_old_procs", {}):
            return hwnd in getattr(self, "_dnd_old_procs", {})
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            WM_DROPFILES = 0x0233
            GWL_WNDPROC = -4
            LRESULT = ctypes.c_ssize_t
            WPARAM = ctypes.c_size_t
            LPARAM = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(
                LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM
            )

            if ctypes.sizeof(ctypes.c_void_p) == 8:
                get_long = user32.GetWindowLongPtrW
                set_long = user32.SetWindowLongPtrW
            else:
                get_long = user32.GetWindowLongW
                set_long = user32.SetWindowLongW
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_void_p
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            set_long.restype = ctypes.c_void_p
            CallWindowProc = user32.CallWindowProcW
            CallWindowProc.argtypes = [
                ctypes.c_void_p,
                wintypes.HWND,
                wintypes.UINT,
                WPARAM,
                LPARAM,
            ]
            CallWindowProc.restype = LRESULT
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell32.DragQueryFileW.argtypes = [
                wintypes.HANDLE,
                wintypes.UINT,
                wintypes.LPWSTR,
                wintypes.UINT,
            ]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragFinish.argtypes = [wintypes.HANDLE]

            old = get_long(hwnd, GWL_WNDPROC)
            if not old:
                return False
            self._dnd_old_procs[int(hwnd)] = int(old)

            queue = self._dnd_queue
            old_procs = self._dnd_old_procs

            @WNDPROC
            def wndproc(h, msg, wp, lp):
                try:
                    if int(msg) == WM_DROPFILES and wp:
                        files: list[str] = []
                        try:
                            count = int(
                                shell32.DragQueryFileW(wp, 0xFFFFFFFF, None, 0) or 0
                            )
                            buf = ctypes.create_unicode_buffer(2048)
                            for i in range(count):
                                shell32.DragQueryFileW(wp, i, buf, 2048)
                                if buf.value:
                                    files.append(buf.value)
                        finally:
                            try:
                                shell32.DragFinish(wp)
                            except Exception:
                                pass
                        # Queue only — never call Tk/Python UI here
                        if files:
                            try:
                                queue.append(files)
                            except Exception:
                                pass
                        return 0
                except Exception:
                    pass
                try:
                    prev = old_procs.get(int(h)) or old
                    return CallWindowProc(ctypes.c_void_p(prev), h, msg, wp, lp)
                except Exception:
                    return 0

            self._dnd_refs.append(wndproc)
            shell32.DragAcceptFiles(hwnd, True)
            set_long(hwnd, GWL_WNDPROC, ctypes.cast(wndproc, ctypes.c_void_p).value)
            return True
        except Exception:
            return False

    def _dnd_refresh_hooks(self) -> None:
        """Accept drops on newly created child HWNDs (CTk recreates frames)."""
        if sys.platform != "win32" or not getattr(self, "_dnd_ready", False):
            return
        try:
            n = 0
            for h in self._dnd_candidate_hwnds():
                if self._dnd_subclass_hwnd(h):
                    n += 1
            self._dnd_hwnds = list(self._dnd_old_procs.keys())
        except Exception:
            pass

    def _install_drag_drop(self) -> None:
        """Subclass app HWNDs for WM_DROPFILES only (no global hooks)."""
        if sys.platform != "win32":
            return
        if getattr(self, "_dnd_ready", False):
            self._dnd_refresh_hooks()
            return
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            # Unhook any previous global hooks from older builds (if process reused)
            for hk in list(getattr(self, "_dnd_hooks", []) or []):
                try:
                    import ctypes

                    ctypes.windll.user32.UnhookWindowsHookEx(hk)
                except Exception:
                    pass
            self._dnd_hooks = []

            hwnds = self._dnd_candidate_hwnds()
            ok = 0
            for h in hwnds:
                if self._dnd_subclass_hwnd(h):
                    ok += 1
            self._dnd_ready = ok > 0
            self._dnd_hwnds = list(self._dnd_old_procs.keys())

            def _cleanup(_e=None):
                # Do not restore WndProc on destroy (window is dying); just drop refs
                try:
                    self._dnd_refs.clear()
                except Exception:
                    pass

            try:
                self.bind("<Destroy>", _cleanup, add="+")
            except Exception:
                pass

            if not ok:
                self._log("[拖放] 启用失败，请用「浏览」选文件")
        except Exception as e:
            self._log(f"[拖放] 启用失败: {e}（请用「浏览」选文件）")
            self._dnd_ready = False

    def _collect_video_paths_from_drop(self, paths: list[str]) -> list[str]:
        videos: list[str] = []
        for p in paths:
            path = Path(p)
            try:
                path = path.resolve()
            except Exception:
                pass
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                videos.append(str(path))
            elif path.is_dir():
                try:
                    for child in sorted(path.rglob("*")):
                        if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS:
                            videos.append(str(child.resolve()))
                except Exception:
                    pass
        seen: set[str] = set()
        out: list[str] = []
        for v in videos:
            key = v.lower()
            if key not in seen:
                seen.add(key)
                out.append(v)
        return out

    def _on_drop_files(self, files) -> None:
        paths = _normalize_drop_paths(files)
        if not paths:
            self._log("[拖放] 未收到路径")
            return
        self._log(f"[拖放] 收到 {len(paths)} 项")
        videos = self._collect_video_paths_from_drop(paths)
        if videos:
            self._apply_dropped_videos(videos)
            return
        if len(paths) == 1 and Path(paths[0]).is_dir():
            self._apply_dropped_export_dir(paths[0])
            return
        self._log("[拖放] 未识别到视频文件（支持 mp4/mkv/mov/… 或含视频的文件夹）")

    def _on_drop_video_files(self, files) -> None:
        paths = _normalize_drop_paths(files)
        videos = self._collect_video_paths_from_drop(paths)
        if not videos:
            self._log("[拖放] 请拖入视频文件（或含视频的文件夹）")
            return
        self._apply_dropped_videos(videos)

    def _on_drop_export_path(self, files) -> None:
        paths = _normalize_drop_paths(files)
        if not paths:
            return
        p = Path(paths[0])
        if p.is_dir():
            self._apply_dropped_export_dir(str(p.resolve()))
            return
        if p.is_file():
            self._apply_dropped_export_dir(str(p.parent.resolve()))
            return

    def _apply_dropped_export_dir(self, folder: str) -> None:
        self.export_dir_var.set(folder)
        self._apply_path_fields_to_runtime()
        self._persist_settings(quiet=True)
        self._log(f"[拖放] 导出目录: {folder}")

    def _batch_enqueue_videos(self, videos: list[str], *, confirm: bool = True) -> int:
        """Enqueue many videos with current banner settings. Returns count added."""
        if not videos:
            return 0
        if self._queue_running or (self._worker and self._worker.is_alive()):
            messagebox.showwarning("队列", "正在处理中，请稍候再加入")
            return 0
        src, tgt, prompt, _q, summary = self._enqueue_settings_summary()
        translator = self._map_label(self.translator_label_var.get(), TRANSLATORS)
        if src == "":
            self.video_var.set(videos[0])
            messagebox.showinfo(
                "批量入队",
                f"已选中 {len(videos)} 个视频。\n\n"
                "批量入队使用同一套「入队设置」。\n"
                "请先在下方选择【源语言】和【目标语言】，\n"
                "设置会显示在蓝色提示行，然后再拖入/多选一次，或点「加入队列」。\n\n"
                f"已将第一个文件填入视频框：\n{Path(videos[0]).name}",
            )
            self._log(f"[队列] 待批量 {len(videos)} 个；请先设好入队设置（蓝色提示行）")
            self._refresh_enqueue_banner()
            return 0
        if not tgt and translator != "none":
            self.video_var.set(videos[0])
            messagebox.showinfo(
                "批量入队",
                f"已选中 {len(videos)} 个视频。\n请先选择【目标语言】，或将翻译引擎设为「不翻译」。\n\n{summary}",
            )
            return 0
        if not tgt:
            tgt = "src"
        if confirm:
            names = "\n".join(f"  · {Path(v).name}" for v in videos[:12])
            if len(videos) > 12:
                names += f"\n  … 另有 {len(videos) - 12} 个"
            if not messagebox.askyesno(
                "确认批量入队",
                f"将用同一套设置加入 {len(videos)} 个视频：\n\n"
                f"{summary}\n\n文件：\n{names}\n\n确定加入队列？",
            ):
                return 0
        n = 0
        for path in videos:
            if any(
                q.get("video_path") == path and q.get("status") in ("queued", "running")
                for q in self._queue
            ):
                continue
            self._queue.append(
                {
                    "video_path": path,
                    "source_language": src,
                    "target_language": tgt,
                    "special_prompt": prompt,
                    "status": "queued",
                    "error": "",
                }
            )
            n += 1
        if n:
            self._log(f"[队列] 批量加入 {n}/{len(videos)} 项\n  {summary}")
            self._refresh_queue_list()
            self.video_var.set("")
        return n

    def _apply_dropped_videos(self, videos: list[str]) -> None:
        if not videos:
            return
        if len(videos) == 1:
            self.video_var.set(videos[0])
            self._log(f"[拖放] 已选视频: {videos[0]}")
            self._refresh_enqueue_banner()
            self._log("请确认蓝色「入队设置」后点「加入队列」或「开始处理」")
            return
        self._batch_enqueue_videos(videos, confirm=True)

    def _pick_video(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择视频（可多选：全部使用当前蓝色「入队设置」）",
            filetypes=[
                ("视频文件", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.flv *.wmv"),
                ("所有文件", "*.*"),
            ],
        )
        if not paths:
            return
        self._apply_dropped_videos([str(Path(p).resolve()) for p in paths])

    def _open_export_dir(self) -> None:
        self._apply_path_fields_to_runtime()
        folder = default_output_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def _ollama_model_selected(self) -> str:
        m = (self.ollama_model_var.get() or "").strip()
        if not m or m == OLLAMA_NO_MODEL:
            return ""
        return m

    def _set_ollama_model_menu(self, models: list[str] | None = None) -> None:
        names = [m for m in (models or []) if m and m != OLLAMA_NO_MODEL]
        if names:
            self.ollama_menu.configure(values=names)
            cur = self._ollama_model_selected()
            if cur in names:
                self.ollama_model_var.set(cur)
            else:
                self.ollama_model_var.set(names[0])
        else:
            self.ollama_menu.configure(values=[OLLAMA_NO_MODEL])
            self.ollama_model_var.set(OLLAMA_NO_MODEL)

    def _refresh_env(self) -> None:
        def work() -> None:
            info = check_environment()
            ollama_url = self.ollama_url_var.get().strip()
            models: list[str] = []
            if ollama_url:
                models = list_ollama_models(ollama_url) or []

            def apply() -> None:
                parts = []
                parts.append("FFmpeg ✓" if info["ffmpeg_ok"] else "FFmpeg ✗")
                parts.append("Whisper ✓" if info["faster_whisper"] else "Whisper ✗")
                st = info.get("cuda_status") or ("ready" if info.get("cuda") else "no_nvidia")
                if st == "ready":
                    parts.append("GPU ✓ N卡")
                elif st == "need_runtime":
                    parts.append("GPU 需装运行库")
                else:
                    parts.append("GPU 不适配")
                if not ollama_url:
                    parts.append("Ollama 未配置地址")
                    self._set_ollama_model_menu([])
                elif models:
                    parts.append(f"Ollama {len(models)}")
                    self._set_ollama_model_menu(models)
                else:
                    parts.append("Ollama 无模型/未连接")
                    self._set_ollama_model_menu([])
                self.env_label.configure(text=" | ".join(parts))

                if not info.get("ffmpeg_ok"):
                    self._log(
                        "[许可] 未检测到 FFmpeg：请自行安装并加入 PATH，"
                        "并遵守 FFmpeg 官方许可条款。"
                    )

                msg = (info.get("cuda_message") or "").strip()
                if st == "ready":
                    first_line = msg.splitlines()[0].strip() if msg else "已适配 NVIDIA CUDA 加速"
                    self._log(f"[GPU] {first_line}")
                    self._apply_cuda_model_recommendation(silent=True)
                elif st == "need_runtime":
                    self._log("[GPU] 已检测到 N 卡，但未找到可用 CUDA 库（支持 12/13）")
                    if not getattr(self, "_gpu_need_runtime_warned", False):
                        self._gpu_need_runtime_warned = True
                        messagebox.showwarning(
                            "NVIDIA GPU：请安装 CUDA 运行库",
                            msg
                            or (
                                "已检测到 N 卡，但没找到 cublas64_12 / cublas64_13。\n"
                                "请安装 CUDA 12 或 13 Toolkit。"
                            ),
                        )
                else:
                    self._log("[GPU] 未检测到可用 NVIDIA CUDA，识别将使用 CPU 模式")
                    if not getattr(self, "_gpu_no_nvidia_warned", False):
                        self._gpu_no_nvidia_warned = True
                        tip = msg or (
                            "未检测到可用的 NVIDIA CUDA。\n"
                            "请将设备设为 auto 或 cpu，仍可正常处理（较慢）。"
                        )
                        if self.device_var.get() == "cuda":
                            tip += "\n\n当前设备为 cuda，建议改为 auto/cpu。"
                        messagebox.showinfo("GPU 不适配", tip)

                for e in info.get("errors") or []:
                    if e and e != msg and "NVIDIA" not in e and "CUDA" not in e and "N 卡" not in e:
                        self._log(f"[环境] {e}")

            self.after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    def _apply_cuda_model_recommendation(self, *, silent: bool = True) -> None:
        """检测到 GPU 环境可用时，自动调整默认模型与质量预设。"""
        if getattr(self, "_cuda_model_reco_done", False):
            return
        self._cuda_model_reco_done = True
        cur_m = self._normalize_whisper_model(
            self._map_label(self.model_label_var.get(), WHISPER_MODELS)
        )
        cur_q = self._map_label(self.quality_label_var.get(), QUALITY_PRESETS)
        basic_models = {"tiny", "base", "small", "medium", "distil-large-v3"}
        basic_quality = {"fast", "balanced"}
        changed = []
        if cur_m in basic_models:
            self._set_whisper_model_code(_RECOMMENDED_CUDA_MODEL)
            changed.append(f"模型 → {_RECOMMENDED_CUDA_MODEL}")
        if cur_q in basic_quality:
            self._set_quality_code(_RECOMMENDED_CUDA_QUALITY)
            changed.append("质量 → 高质量")
        if self.device_var.get() == "cpu":
            self.device_var.set("auto")
            changed.append("设备 → auto")
        if changed:
            try:
                self._persist_settings(quiet=True)
            except Exception:
                pass

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self.start_btn.configure(state="disabled", text="处理中…")
            self.stop_btn.configure(state="normal")
        else:
            self._stopping = False
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled", text="停止")
            self._refresh_queue_list()

    def _stop(self) -> None:
        if not (self._worker and self._worker.is_alive()):
            return
        self._stopping = True
        self.stop_btn.configure(state="disabled", text="停止中…")
        self.status_var.set("正在停止…")
        self._log("用户请求停止…")
        try:
            request_cancel()
        except Exception:
            pass

    def _apply_job_retention_purge(self, on_exit: bool = False) -> None:
        """Purge expired jobs according to job_retention policy (never, on_exit, 1_day, 3_days, 7_days)."""
        try:
            from pipeline import purge_expired_job_dirs
            policy = (self._cfg or {}).get("job_retention", "never")
            if on_exit:
                if policy == "on_exit":
                    purge_expired_job_dirs("on_exit")
            else:
                if policy in ("1_day", "3_days", "7_days"):
                    n = purge_expired_job_dirs(policy)
                    if n:
                        self._log(f"[自动清理] 已按保留策略（{policy}）清理历史任务 {n} 个")
        except Exception:
            pass

    def _purge_done_jobs_quiet(self) -> None:
        self._apply_job_retention_purge(on_exit=False)

    def _check_resume_hint(self) -> None:
        try:
            jobs = list_jobs(include_done=False)
            if not jobs:
                return
            n_fail = sum(1 for j in jobs if j.get("status") == "failed")
            n_stop = sum(1 for j in jobs if j.get("status") == "stopped")
            n_pend = len(jobs) - n_fail - n_stop
            self._log(f"任务列表: 未完成 {len(jobs)}（失败 {n_fail} / 停止 {n_stop} / 进行中 {max(0, n_pend)}）— 点「任务」查看")
            self.status_var.set(f"有 {len(jobs)} 个可续跑/待处理任务")
        except Exception:
            pass

    def _open_jobs_window(self) -> None:
        if self._jobs_win is not None:
            try:
                if self._jobs_win.winfo_exists():
                    self._jobs_win.focus()
                    self._jobs_win._reload()
                    return
            except Exception:
                self._jobs_win = None
        win = JobsWindow(self)
        self._jobs_win = win

    def _apply_job_settings_to_ui(self, job: dict) -> None:
        """恢复未完成任务 checkpoint 中保存的原参数设定到 GUI 界面"""
        if not isinstance(job, dict):
            return
        
        src_code = (job.get("source_language") or job.get("detected_lang") or "").lower()
        matched_src = False
        if src_code:
            for label, code in SOURCE_LANG_OPTIONS:
                if code and (src_code == code.lower() or src_code.startswith(code.lower().split("-")[0])):
                    self.src_lang_label_var.set(label)
                    matched_src = True
                    break
        if not matched_src and self._map_label(self.src_lang_label_var.get(), SOURCE_LANG_OPTIONS) == "":
            for label, code in SOURCE_LANG_OPTIONS:
                if code == "auto":
                    self.src_lang_label_var.set(label)
                    break


        tgt_code = (job.get("target_language") or "").lower()
        matched_tgt = False
        if tgt_code:
            for label, code in LANG_OPTIONS:
                if code and tgt_code == code.lower():
                    self.tgt_lang_label_var.set(label)
                    matched_tgt = True
                    break
        if not matched_tgt and self._map_label(self.tgt_lang_label_var.get(), LANG_OPTIONS) == "":
            for label, code in LANG_OPTIONS:
                if code == "zh-CN":
                    self.tgt_lang_label_var.set(label)
                    break

        tr = (job.get("translator") or "").lower()
        if tr:
            for label, code in TRANSLATORS:
                if code and tr == code.lower():
                    self.translator_label_var.set(label)
                    break

        if job.get("whisper_model"):
            self._set_whisper_model_code(str(job["whisper_model"]))
        if job.get("quality"):
            for label, code in QUALITY_PRESETS:
                if code == job["quality"]:
                    self.quality_label_var.set(label)
                    break

        # 5. 恢复提示词 / 领域术语词汇表
        p_text = job.get("special_prompt") or job.get("glossary") or job.get("prompt") or ""
        if p_text:
            self._set_special_prompt(str(p_text))

    def _resume_job_dict(self, job: dict) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("提示", "正在处理中")
            return
        vp = job.get("video_path") or ""
        if not vp or not Path(vp).is_file():
            messagebox.showerror("续跑", f"原视频不存在，无法续跑:\n{vp}\n\n可删除该任务记录。")
            return
        self.video_var.set(vp)
        self._apply_job_settings_to_ui(job)
        self._log(f"—— 从任务窗口续跑: {vp} | 阶段={job.get('stage')}")
        self._start(force_resume=True)

    def _resume_last(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("提示", "正在处理中")
            return
        video = self.video_var.get().strip().strip('"')
        job = None
        if video and Path(video).is_file():
            job = load_checkpoint(video)
            if job.get("stage") in ("", "done", None):
                job = None
        if not job:
            job = find_resumable_job(video if video else "")
        if not job:
            messagebox.showinfo("续跑", "没有可续跑的未完成任务。")
            return

        vp = job.get("video_path") or ""
        if not vp or not Path(vp).is_file():
            messagebox.showerror("续跑", f"原视频不存在:\n{vp}")
            return

        stage = job.get("stage") or "?"
        err = (job.get("last_error") or "")[:300]
        msg = f"从阶段「{stage}」续跑？\n\n视频:\n{vp}"
        if err:
            msg += f"\n\n上次错误:\n{err}"
        if not messagebox.askyesno("续跑", msg):
            return

        self.video_var.set(vp)
        self._apply_job_settings_to_ui(job)
        self._log(f"—— 续跑阶段 {stage}: {vp}")
        self._start(force_resume=True)

    def _start_queue_or_single(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("提示", "正在处理中，请先停止或等待完成")
            return
        pending = [q for q in self._queue if q.get("status") == "queued"]
        if pending:
            self._run_queue()
            return
        self._start(force_resume=False)

    def _run_queue(self) -> None:
        pending = [q for q in self._queue if q.get("status") == "queued"]
        if not pending:
            messagebox.showinfo("队列", "没有待处理任务")
            return
        if not self._validate_global_keys():
            return
        try:
            font_size = int(self.font_size_var.get().strip() or "22")
        except ValueError:
            messagebox.showerror("错误", "字体大小必须是数字")
            return
        if not self._confirm_gpu_if_needed():
            return
        save_settings(self._collect_settings())
        self._apply_path_fields_to_runtime()
        self._queue_running = True
        self._stopping = False
        clear_cancel()
        self._set_busy(True)
        self.progress.set(0)
        batch_total = len(pending)
        self._log(f"—— 开始队列：本批 {batch_total} 个视频（队列共 {len(self._queue)} 项）——")
        self.queue_progress_var.set(f"队列进度：准备开始 · 本批 {batch_total} 个")
        self.start_btn.configure(text=f"处理中 0/{batch_total}")

        def work() -> None:
            ok_n = 0
            fail_n = 0
            cur_slot = [0]
            cur_name = [""]

            q_last_log_cat = [""]
            q_last_log_pct = [-100.0]

            def progress_cb(message: str, percent: float) -> None:
                if self._stopping:
                    raise PipelineCancelled("已停止")

                def ui() -> None:
                    self.progress.set(percent / 100.0)
                    if not self._stopping:
                        head = f"第{cur_slot[0]}/{batch_total} {cur_name[0]}"
                        self.status_var.set(f"[{percent:5.1f}%] {head} · {message}")
                        self.queue_progress_var.set(
                            f"队列进度：第 {cur_slot[0]}/{batch_total} · {cur_name[0]} · {percent:.0f}%"
                        )
                        self.start_btn.configure(text=f"处理中 {cur_slot[0]}/{batch_total}")
                    
                    cat = message.split("…")[0].split("...")[0].strip()
                    if cat != q_last_log_cat[0] or (percent - q_last_log_pct[0]) >= 10.0 or percent == 100.0:
                        q_last_log_cat[0] = cat
                        q_last_log_pct[0] = percent
                        self._log(f"[{percent:5.1f}%] [{cur_slot[0]}/{batch_total}] {message}")

                self.after(0, ui)

            try:
                slot = 0
                for item in list(self._queue):
                    if item.get("status") != "queued":
                        continue
                    if self._stopping:
                        break
                    slot += 1
                    video = item["video_path"]
                    name = Path(video).name
                    cur_slot[0] = slot
                    cur_name[0] = name
                    item["status"] = "running"
                    self.after(0, self._refresh_queue_list)
                    sp = item.get("special_prompt") or ""
                    self.after(
                        0,
                        lambda v=name, s=item["source_language"], t=item["target_language"], i=slot, has_p=bool(sp): (
                            self.status_var.set(f"第{i}/{batch_total} {v} · 准备中…"),
                            self.queue_progress_var.set(f"队列进度：第 {i}/{batch_total} · {v}"),
                            self.start_btn.configure(text=f"处理中 {i}/{batch_total}"),
                            self._log(
                                f"—— 队列 {i}/{batch_total}: {v} | {s}→{t} ——"
                            ),
                        ),
                    )
                    try:
                        cfg = self._build_config_for_task(item, font_size=font_size, force_resume=False)
                        result = run_pipeline(cfg, cb=progress_cb)
                        item["status"] = "done"
                        ok_n += 1
                        srt_p = result.srt_path
                        vid_p = result.output_video or ""
                        self.after(0, lambda p=srt_p: self._log(f"[完成] 字幕: {p}"))
                        if vid_p:
                            self.after(0, lambda p=vid_p: self._log(f"[完成] 视频: {p}"))
                    except PipelineCancelled:
                        item["status"] = "stopped"
                        item["error"] = "用户停止"
                        break
                    except Exception as e:
                        item["status"] = "failed"
                        reason, solution = format_user_friendly_error(e)
                        item["error"] = reason
                        fail_n += 1
                        self.after(0, lambda r=reason, s=solution: (
                            self._log(f"[队列失败] {r}"),
                            self._log(f"[解决建议] {s}")
                        ))
                    self.after(0, self._refresh_queue_list)

                def finished() -> None:
                    self._queue_running = False
                    self._set_busy(False)
                    self.progress.set(1 if fail_n == 0 and not self._stopping else self.progress.get())
                    self.video_var.set("")
                    left = [q for q in self._queue if q.get("status") == "queued"]
                    msg = f"队列结束：成功 {ok_n}/{batch_total}，失败 {fail_n}"
                    if self._stopping:
                        msg += "（已停止）"
                    if left:
                        msg += f"，剩余待处理 {len(left)}"
                    self.status_var.set(msg)
                    self._log(f"—— {msg} ——")
                    self._refresh_queue_list()
                    messagebox.showinfo("队列完成", msg)

                self.after(0, finished)
            except Exception as e:
                def boom() -> None:
                    self._queue_running = False
                    self._set_busy(False)
                    self._log(str(e))
                    messagebox.showerror("队列错误", str(e))

                self.after(0, boom)

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _validate_global_keys(self) -> bool:
        translator = self._map_label(self.translator_label_var.get(), TRANSLATORS)
        if translator == "ollama":
            if not self.ollama_url_var.get().strip():
                messagebox.showerror(
                    "Ollama 未配置",
                    "请填写 Ollama 服务地址（例如 http://127.0.0.1:11434），不默认预填。",
                )
                return False
            if not self._ollama_model_selected():
                messagebox.showerror(
                    "无模型",
                    "当前没有可用的 Ollama 模型。\n请先在本机拉取模型，填写服务地址后点刷新环境，再选择模型。",
                )
                return False
        if translator == "deepl" and not self.deepl_key_var.get().strip():
            messagebox.showerror("缺少 Key", "请填写 DeepL API Key")
            return False
        if translator == "baidu" and (
            not self.baidu_id_var.get().strip() or not self.baidu_key_var.get().strip()
        ):
            messagebox.showerror("缺少密钥", "请填写百度 APP ID 与密钥")
            return False
        if translator == "openai" and not self.openai_key_var.get().strip():
            messagebox.showerror("缺少 Key", "请填写 OpenAI 兼容 API Key")
            return False
        return True

    def _confirm_gpu_if_needed(self) -> bool:
        try:
            from pipeline import detect_nvidia_gpu

            gpu = detect_nvidia_gpu()
            st = gpu.get("status")
            gmsg = (gpu.get("message") or "").strip()
            dev_choice = self.device_var.get()
            if st == "need_runtime" and dev_choice in ("cuda", "auto"):
                if not messagebox.askyesno(
                    "N 卡需安装 CUDA 运行库",
                    (gmsg + "\n\n" if gmsg else "")
                    + "是否仍继续？（将尽量回退 CPU）\n选「否」可先去安装运行库。",
                ):
                    return False
            elif st == "no_nvidia" and dev_choice == "cuda":
                if not messagebox.askyesno(
                    "GPU 不适配",
                    (gmsg + "\n\n" if gmsg else "")
                    + "当前设备选了 cuda，但本机无可用 N 卡 CUDA。\n"
                    "是否改为 auto 并继续（走 CPU）？\n选「否」取消开始。",
                ):
                    return False
                self.device_var.set("auto")
                self._persist_settings(quiet=True)
        except Exception:
            pass
        return True

    def _build_config_for_task(
        self,
        task: dict,
        *,
        font_size: int,
        force_resume: bool,
    ) -> PipelineConfig:
        video = task["video_path"]
        source_language = task.get("source_language") or ""
        target_language = task.get("target_language") or "src"
        special_now = task.get("special_prompt") or ""
        translator = self._map_label(self.translator_label_var.get(), TRANSLATORS)
        out_path = self._default_output_for(video, target_language=target_language)
        try:
            vad_val = float(self._map_label(self.vad_label_var.get(), VAD_OPTIONS))
        except ValueError:
            vad_val = 0.35

        return PipelineConfig(
            video_path=video,
            output_path=out_path,
            whisper_model=self._normalize_whisper_model(
                self._map_label(self.model_label_var.get(), WHISPER_MODELS)
            ),
            source_language=source_language,
            device=self.device_var.get(),
            quality=self._map_label(self.quality_label_var.get(), QUALITY_PRESETS),
            vad_threshold=vad_val,
            glossary=special_now,
            translate=translator != "none",
            target_language=target_language,
            translator=translator,
            ollama_model=self._ollama_model_selected(),
            ollama_base_url=self.ollama_url_var.get().strip(),
            openai_api_key=self.openai_key_var.get().strip(),
            openai_base_url=self.openai_url_var.get().strip(),
            openai_model=self.openai_model_var.get().strip(),
            deepl_api_key=self.deepl_key_var.get().strip(),
            deepl_use_free=bool(self.deepl_free_var.get()),
            baidu_app_id=self.baidu_id_var.get().strip(),
            baidu_app_key=self.baidu_key_var.get().strip(),
            embed_mode=self._map_label(self.embed_label_var.get(), EMBED_MODES),
            font_name=self.font_name_var.get().strip() or "Microsoft YaHei",
            font_size=font_size,
            resume=True if force_resume else True,
            max_retries=3,
            cleanup_cache=True,
            clear_ai_context=True,
        )

    def _start(self, force_resume: bool = False) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("提示", "正在处理中，请先停止或等待完成")
            return

        video = self.video_var.get().strip().strip('"')
        if not video or not Path(video).is_file():
            messagebox.showerror("错误", "请先选择有效的视频文件，或先「加入队列」")
            return

        try:
            font_size = int(self.font_size_var.get().strip() or "22")
        except ValueError:
            messagebox.showerror("错误", "字体大小必须是数字")
            return

        translator = self._map_label(self.translator_label_var.get(), TRANSLATORS)
        source_language = self._map_label(self.src_lang_label_var.get(), SOURCE_LANG_OPTIONS)
        target_language = self._map_label(self.tgt_lang_label_var.get(), LANG_OPTIONS)
        if source_language == "":
            if force_resume:
                source_language = "auto"
            else:
                messagebox.showerror("源语言", "请选择源语言")
                return
        if not target_language and translator != "none":
            if force_resume:
                target_language = "zh-CN"
            else:
                messagebox.showerror("目标语言", "请选择目标语言")
                return
        if not target_language and translator == "none":
            target_language = "src"

        if not self._validate_global_keys():
            return
        if not self._confirm_gpu_if_needed():
            return

        special_now = self._get_special_prompt()
        save_settings(self._collect_settings())
        self._apply_path_fields_to_runtime()

        task = {
            "video_path": str(Path(video).resolve()),
            "source_language": source_language,
            "target_language": target_language,
            "special_prompt": special_now,
        }

        auto_resume = force_resume
        if not auto_resume:
            prev = load_checkpoint(video)
            if prev and prev.get("stage") not in ("", "done", None):
                st = prev.get("stage") or "?"
                if messagebox.askyesno(
                    "发现未完成进度",
                    f"该视频有未完成任务（阶段: {st}）。\n\n是否从断点续跑？\n选「否」将尽量重跑失败阶段（仍会复用可用缓存）。",
                ):
                    auto_resume = True

        cfg = self._build_config_for_task(task, font_size=font_size, force_resume=auto_resume)

        clear_cancel()
        self._stopping = False
        self._set_busy(True)
        self.progress.set(0)
        self.status_var.set("续跑…" if auto_resume else "开始…")
        self._log(
            f"—— {'续跑' if auto_resume else '开始处理'}: {Path(video).name} | "
            f"{source_language}→{target_language} | 翻译={translator}"
        )

        last_log_cat = [""]
        last_log_pct = [-100.0]

        def progress_cb(message: str, percent: float) -> None:
            if self._stopping:
                raise PipelineCancelled("已停止")

            def ui() -> None:
                self.progress.set(percent / 100.0)
                if not self._stopping:
                    self.status_var.set(f"[{percent:5.1f}%] {message}")
                
                cat = message.split("…")[0].split("...")[0].strip()
                if cat != last_log_cat[0] or (percent - last_log_pct[0]) >= 10.0 or percent == 100.0:
                    last_log_cat[0] = cat
                    last_log_pct[0] = percent
                    self._log(f"[{percent:5.1f}%] {message}")

            self.after(0, ui)

        def work() -> None:
            try:
                result = run_pipeline(cfg, cb=progress_cb)

                def done() -> None:
                    self._set_busy(False)
                    self.progress.set(1)
                    self.status_var.set("处理完成")
                    self._reset_session_fields(log=True)
                    self.video_var.set("")
                    self._log(f"[完成] 导出字幕: {result.srt_path}")
                    if result.output_video:
                        self._log(f"[完成] 导出视频: {result.output_video}")
                    msg = f"处理完成！\n\n字幕: {result.srt_path}"
                    if result.output_video:
                        msg += f"\n视频: {result.output_video}"
                    messagebox.showinfo("完成", msg)

                self.after(0, done)
            except PipelineCancelled:
                def stopped() -> None:
                    self._set_busy(False)
                    self.status_var.set("已停止（进度已保留）")
                    self._reset_session_fields(log=True)
                    self._log("—— 已中途停止（可点重试续跑）——")
                    messagebox.showinfo("已停止", "处理已停止。\n进度已保留，可在「任务」列表中重试。")

                self.after(0, stopped)
            except Exception as e:
                reason, solution = format_user_friendly_error(e)

                def fail() -> None:
                    self._set_busy(False)
                    self.status_var.set("失败（可在任务中重试）")
                    self._reset_session_fields(log=True)
                    self._log(f"[错误] {reason}")
                    self._log(f"[解决建议] {solution}")

                    again = messagebox.askyesnocancel(
                        "处理失败",
                        f"出错原因:\n{reason}\n\n{solution}\n\n"
                        f"是 = 打开重试弹窗\n否 = 打开任务列表\n取消 = 关闭",
                    )
                    if again is True:
                        self.after(100, lambda: self._start(force_resume=True))
                    elif again is False:
                        self.after(100, self._open_jobs_window)

                self.after(0, fail)

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

class RetryStepDialog(ctk.CTkToplevel):
    def __init__(self, parent, job: dict, on_confirm_cb) -> None:
        super().__init__(parent)
        self.job = job
        self.on_confirm_cb = on_confirm_cb
        self.title("选择重试起始步骤")
        self.geometry("400x260")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        vp = job.get("video_path") or ""
        name = Path(vp).name if vp else "任务"

        ctk.CTkLabel(self, text=f"任务: {name}", font=ctk.CTkFont(weight="bold"), wraplength=360).pack(pady=(15, 5))
        ctk.CTkLabel(self, text="请选择要从哪一步重新开始处理：", text_color=("gray30", "gray70")).pack(pady=(0, 10))

        st = (job.get("stage") or "init").lower()
        has_audio = bool(job.get("has_audio"))
        has_asr = bool(job.get("has_asr"))
        has_final = bool(job.get("has_final"))

        try:
            from pipeline import job_dir
            jdir = job_dir(vp) if vp else None
            if jdir and jdir.is_dir():
                if (jdir / "audio.wav").is_file():
                    has_audio = True
                if (jdir / "asr.srt").is_file():
                    has_asr = True
                if (jdir / "final.srt").is_file():
                    has_final = True
        except Exception:
            pass

        if st in ("audio", "asr", "translate", "srt", "embed", "done"):
            has_audio = True
        if st in ("asr", "translate", "srt", "embed", "done"):
            has_asr = True
        if st in ("translate", "srt", "embed", "done"):
            has_final = True

        options = [("从头开始 (重新抽取音频与识别)", "init")]

        if has_audio:
            options.append(("从 ASR 语音识别开始 (已有音频缓存)", "audio"))

        if has_asr:
            options.append(("从字幕翻译开始 (已有原文 SRT)", "asr"))

        if has_final or (has_asr and (job.get("translator") or "").lower() == "none"):
            options.append(("从字幕烧录开始 (已有字幕文件)", "translate"))

        self._options_map = {opt[0]: opt[1] for opt in options}
        self.opt_menu = ctk.CTkOptionMenu(
            self,
            values=[opt[0] for opt in options],
            width=340,
        )
        self.opt_menu.pack(pady=10)

        self.opt_menu.set(options[-1][0])

        btn_box = ctk.CTkFrame(self, fg_color="transparent")
        btn_box.pack(pady=15)

        ctk.CTkButton(btn_box, text="确定重试", width=100, command=self._confirm).pack(side="left", padx=10)
        ctk.CTkButton(btn_box, text="取消", width=80, fg_color="gray50", command=self.destroy).pack(side="left", padx=10)

    def _confirm(self) -> None:
        sel_label = self.opt_menu.get()
        stage_target = self._options_map.get(sel_label, "init")
        self.destroy()
        self.on_confirm_cb(stage_target)


class JobsWindow(ctk.CTkToplevel):

    STATUS_CN = {
        "failed": "失败",
        "stopped": "已停止",
        "pending": "未完成",
        "done": "已完成",
    }
    STAGE_CN = {
        "init": "初始化",
        "audio": "抽音频",
        "asr": "识别",
        "translate": "翻译",
        "srt": "字幕稿",
        "embed": "烧录/嵌入",
        "done": "完成",
    }

    RETENTION_OPTIONS = [
        ("永不自动清理", "never"),
        ("关闭App时清理", "on_exit"),
        ("保留 1 天", "1_day"),
        ("保留 3 天", "3_days"),
        ("保留 7 天", "7_days"),
    ]

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.title("任务列表 — 历史保留 / 任意步骤重试")
        self.geometry("820x540")
        self.minsize(680, 420)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        top.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(
            top,
            text="自动清理:",
            anchor="e",
        ).grid(row=0, column=0, sticky="e", padx=(0, 4))
        
        ret_labels = [opt[0] for opt in self.RETENTION_OPTIONS]
        self.retention_menu = ctk.CTkOptionMenu(
            top,
            values=ret_labels,
            width=130,
            command=self._on_retention_change,
        )
        self.retention_menu.grid(row=0, column=1, sticky="w", padx=(0, 10))
        
        # Set initial retention UI
        curr_policy = (self.app._cfg or {}).get("job_retention", "never")
        for label, code in self.RETENTION_OPTIONS:
            if code == curr_policy:
                self.retention_menu.set(label)
                break

        ctk.CTkButton(top, text="刷新", width=70, command=self._reload).grid(row=0, column=2, padx=4)
        ctk.CTkButton(top, text="打开任务目录", width=100, command=self._open_root).grid(
            row=0, column=3, padx=4
        )
        ctk.CTkButton(top, text="清理已完成", width=90, command=self._clear_done).grid(
            row=0, column=4, padx=4
        )

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        self.scroll.grid_columnconfigure(0, weight=1)

        self.hint = ctk.CTkLabel(self, text="", text_color=("gray40", "gray60"), anchor="w")
        self.hint.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._reload()

    def _on_retention_change(self, selected_label: str) -> None:
        code = "never"
        for label, val in self.RETENTION_OPTIONS:
            if label == selected_label:
                code = val
                break
        self.app._cfg["job_retention"] = code
        self.app._on_persistent_setting_changed()

    def _on_close(self) -> None:
        self.app._jobs_win = None
        self.destroy()

    def _open_root(self) -> None:
        d = jobs_root()
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))

    def _clear_done(self) -> None:
        jobs = list_jobs(include_done=True)
        done = [j for j in jobs if j.get("status") == "done" or j.get("stage") == "done"]
        if not done:
            messagebox.showinfo("清理", "没有已完成任务可清理。", parent=self)
            return
        if not messagebox.askyesno("清理", f"删除 {len(done)} 条已完成任务记录？", parent=self):
            return
        for j in done:
            delete_job(j)
        self._reload()

    def _reload(self) -> None:
        for w in self.scroll.winfo_children():
            w.destroy()
        jobs = list_jobs(include_done=True)
        jobs_open = [j for j in jobs if j.get("status") != "done"]
        jobs_done = [j for j in jobs if j.get("status") == "done"]

        pri = {"failed": 0, "stopped": 1, "pending": 2}
        jobs_open.sort(
            key=lambda j: (pri.get(j.get("status") or "pending", 2), j.get("updated_at") or ""),
            reverse=False,
        )

        jobs_open.sort(key=lambda j: j.get("updated_at") or "", reverse=True)
        jobs_open.sort(key=lambda j: pri.get(j.get("status") or "pending", 2))
        jobs_done.sort(key=lambda j: j.get("updated_at") or "", reverse=True)
        jobs = jobs_open + jobs_done

        if not jobs:
            ctk.CTkLabel(self.scroll, text="暂无任务记录").grid(row=0, column=0, pady=20)
            self.hint.configure(text=f"目录: {jobs_root()}")
            return

        for i, job in enumerate(jobs):
            self._add_row(i, job)
        self.hint.configure(
            text=f"共 {len(jobs)} 条 | 未完成 {len(jobs_open)} | 已完成 {len(jobs_done)} | {jobs_root()}"
        )

    def _add_row(self, index: int, job: dict) -> None:
        frame = ctk.CTkFrame(self.scroll)
        frame.grid(row=index, column=0, sticky="ew", pady=4, padx=2)
        frame.grid_columnconfigure(0, weight=1)

        vp = job.get("video_path") or ""
        name = Path(vp).name if vp else (Path(job.get("job_dir") or "").name)
        status = job.get("status") or "pending"
        stage = job.get("stage") or "?"
        st_cn = self.STATUS_CN.get(status, status)
        sg_cn = self.STAGE_CN.get(stage, stage)
        updated = job.get("updated_at") or ""
        err = (job.get("last_error") or "").replace("\n", " ")
        if len(err) > 120:
            err = err[:120] + "…"

        flags = []
        if job.get("has_audio"):
            flags.append("音")
        if job.get("has_asr"):
            flags.append("识")
        if job.get("has_final"):
            flags.append("译")
        flag_s = "/".join(flags) if flags else "无缓存"

        color = {
            "failed": ("#8B2942", "#c44"),
            "stopped": ("#6a5a20", "#c9a227"),
            "done": ("gray40", "gray60"),
            "pending": ("#1f4e79", "#3a7ebf"),
        }.get(status, (("gray40", "gray60")))

        title = f"[{st_cn}] {name}"
        ctk.CTkLabel(frame, text=title, anchor="w", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 0)
        )
        meta = f"阶段: {sg_cn}  |  缓存: {flag_s}  |  {updated}"
        ctk.CTkLabel(frame, text=meta, anchor="w", text_color=color).grid(
            row=1, column=0, sticky="w", padx=10, pady=2
        )
        if err:
            ctk.CTkLabel(frame, text=f"错误: {err}", anchor="w", text_color=("gray30", "gray70"), wraplength=520).grid(
                row=2, column=0, sticky="w", padx=10, pady=(0, 4)
            )

        btns = ctk.CTkFrame(frame, fg_color="transparent")
        btns.grid(row=0, column=1, rowspan=3, padx=8, pady=8)

        def do_retry(j=job) -> None:
            def _on_target_stage_selected(target_stage: str) -> None:
                video_p = j.get("video_path") or ""
                if video_p and Path(video_p).is_file():
                    # Modify checkpoint stage to target_stage
                    try:
                        save_data = dict(j)
                        save_data["stage"] = target_stage
                        save_data["status"] = "pending"
                        save_data["last_error"] = ""
                        save_checkpoint(video_p, save_data)
                    except Exception:
                        pass
                self.app._resume_job_dict(j)
                self._on_close()

            RetryStepDialog(self, j, _on_target_stage_selected)

        def do_delete(j=job) -> None:
            if not messagebox.askyesno("删除", f"删除该任务及其缓存？\n{Path(j.get('video_path') or '').name}", parent=self):
                return
            if delete_job(j):
                self.app._log(f"已删除任务: {j.get('video_path')}")
            self._reload()

        def do_folder(j=job) -> None:
            d = j.get("job_dir") or ""
            if d and Path(d).is_dir():
                os.startfile(d)
            else:
                self._open_root()

        btn_text = "重试" if status == "done" else "重试/续跑"
        ctk.CTkButton(btns, text=btn_text, width=75, command=do_retry).pack(pady=2)
        ctk.CTkButton(btns, text="删除", width=75, fg_color="#8B2942", hover_color="#6B1F32", command=do_delete).pack(
            pady=2
        )
        ctk.CTkButton(btns, text="目录", width=75, command=do_folder).pack(pady=2)

def main() -> None:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        internal = base / "_internal"
        for p in (base, internal, Path(getattr(sys, "_MEIPASS", base))):
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)

        _prefer_hot_modules()
    elif str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))

    try:
        from pipeline import ensure_cuda_dll_path, ensure_model_cache_env

        ensure_model_cache_env()
        ensure_cuda_dll_path()
    except Exception:
        pass

    app_cls = App
    hot = _hot_dir()
    if getattr(sys, "frozen", False) and hot is not None:
        hot_app = hot / "app.py"
        if hot_app.is_file():
            try:
                spec = importlib.util.spec_from_file_location("vidsub_hot_app", hot_app)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules["vidsub_hot_app"] = mod
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "App"):
                        app_cls = mod.App
            except Exception:
                app_cls = App

    app = app_cls()
    app.mainloop()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        log_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR
        log_path = log_dir / "error.log"
        try:
            log_path.write_text(err, encoding="utf-8")
        except OSError:
            pass
        try:
            messagebox.showerror("启动失败", f"{err}\n\n详情: {log_path}")
        except Exception:
            print(err, file=sys.stderr)
        raise
