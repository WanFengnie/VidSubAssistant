from __future__ import annotations

import argparse
import sys

from pipeline import PipelineConfig, run_pipeline
from translators import STABLE_TRANSLATORS

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="视频字幕助手")
    p.add_argument("video", help="输入视频路径")
    p.add_argument("-o", "--output", default="", help="输出路径")
    p.add_argument(
        "--model",
        default="large-v3-turbo",
        help="Whisper 模型（推荐 large-v3-turbo；极限 large-v3）",
    )
    p.add_argument(
        "--quality",
        default="high",
        choices=["fast", "balanced", "high", "dialogue"],
        help="识别质量",
    )
    p.add_argument("--source-lang", default="auto", help="源语言（auto 自动检测）")
    p.add_argument("--to", default="zh-CN", help="目标语言")
    p.add_argument(
        "--translator",
        default="ollama",
        choices=list(STABLE_TRANSLATORS),
        help="翻译后端",
    )
    p.add_argument("--ollama-model", default="", help="Ollama 模型名（不默认预填）")
    p.add_argument("--ollama-url", default="", help="Ollama 地址（不默认预填本机）")
    p.add_argument("--openai-key", default="")
    p.add_argument("--openai-url", default="https://api.openai.com/v1")
    p.add_argument("--openai-model", default="gpt-4o-mini")
    p.add_argument("--deepl-key", default="")
    p.add_argument("--deepl-pro", action="store_true", help="使用 DeepL Pro 端点")
    p.add_argument("--baidu-id", default="")
    p.add_argument("--baidu-key", default="")
    p.add_argument("--youdao-key", default="")
    p.add_argument("--youdao-secret", default="")
    p.add_argument("--azure-key", default="")
    p.add_argument("--azure-region", default="global")
    p.add_argument(
        "--embed",
        default="hard",
        choices=["hard", "soft", "srt_only"],
        help="嵌入方式",
    )
    p.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    args = p.parse_args(argv)

    def cb(msg: str, pct: float) -> None:
        print(f"[{pct:5.1f}%] {msg}")

    cfg = PipelineConfig(
        video_path=args.video,
        output_path=args.output,
        whisper_model=args.model,
        source_language=args.source_lang,
        device=args.device,
        quality=args.quality,
        translate=args.translator != "none",
        target_language=args.to,
        translator=args.translator,
        ollama_model=args.ollama_model,
        ollama_base_url=args.ollama_url,
        openai_api_key=args.openai_key,
        openai_base_url=args.openai_url,
        openai_model=args.openai_model,
        deepl_api_key=args.deepl_key,
        deepl_use_free=not args.deepl_pro,
        baidu_app_id=args.baidu_id,
        baidu_app_key=args.baidu_key,
        youdao_app_key=args.youdao_key,
        youdao_app_secret=args.youdao_secret,
        azure_api_key=args.azure_key,
        azure_region=args.azure_region,
        embed_mode=args.embed,
    )
    result = run_pipeline(cfg, cb=cb)
    print("SRT:", result.srt_path)
    if result.output_video:
        print("VIDEO:", result.output_video)
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        raise SystemExit(1)
