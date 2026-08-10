from __future__ import annotations

import hashlib
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Union

ProgressCb = Optional[Callable[[str, float], None]]
JsonType = Union[dict, list, Any]

@dataclass
class Segment:
    start: float
    end: float
    text: str

def _progress(cb: ProgressCb, message: str, percent: float) -> None:
    if cb:
        cb(message, max(0.0, min(100.0, percent)))

def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict] = None,
    data: Optional[bytes] = None,
    timeout: int = 60,
) -> JsonType:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code} {url}\n{body[:800]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {url}\n{e}") from e

_TRANSLATE_META = (
    "user style",
    "domain notes",
    "proper names",
    "on-screen subtitles",
    "do not re-translate",
    "recent context",
    "index|translation",
    "translate into",
    "for subtitles",
    "natural spoken",
    "exactly ",
    "output one line",
    "翻译：",
    "译文：",
    "专有名词",
    "术语前后一致",
    "适合屏幕",
    "成人向",
    "勿过度委婉",
)

def _clean_line(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"<think>[\s\S]*?</think>", "", t, flags=re.I).strip()
    t = re.sub(r"^['\"「」『』]+|['\"「」『』]+$", "", t).strip()
    t = re.sub(r"^\d+[\.\)、\|]\s*", "", t).strip()
    for prefix in ("翻译：", "译文：", "Translation:", "Output:", "结果：", "Note:", "备注："):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix) :].strip()
    low = t.lower()

    if any(m in low or m in t for m in _TRANSLATE_META) and len(t) <= 100:

        meta_hits = sum(1 for m in _TRANSLATE_META if m in low or m in t)
        if meta_hits >= 1 and (len(t) < 60 or meta_hits >= 2):
            return ""
    if t.count("；") + t.count(";") >= 2 and any(k in t for k in ("保留", "避免", "转写", "术语")):
        return ""
    return t

def lang_name(code: str) -> str:
    return {
        "zh-CN": "Simplified Chinese",
        "zh-TW": "Traditional Chinese",
        "zh": "Simplified Chinese",
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
        "ru": "Russian",
        "vi": "Vietnamese",
        "th": "Thai",
        "pt": "Portuguese",
        "it": "Italian",
    }.get(code, code)

_LLM_SYSTEM = (
    "You are a professional video subtitle translator.\n"
    "Goals: accurate meaning, natural spoken target language, short lines for screen.\n"
    "Rules:\n"
    "1) Keep the same number of lines as the input batch.\n"
    "2) Output format only: index|translation (one line per item).\n"
    "3) Do not add notes, quotes, or explanations.\n"
    "4) Keep proper names consistent; do not invent content.\n"
    "5) Prefer concise phrasing suitable for subtitles."
)

def ollama_chat(base_url: str, model: str, prompt: str, timeout: int = 180) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0.05, "num_predict": 2048},
    }
    body = _http_json(
        url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"),
        timeout=timeout,
    )
    content = ((body.get("message") or {}).get("content") or "").strip()
    return re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.I).strip()


def clear_ollama_context(base_url: str, model: str = "", timeout: int = 30) -> None:
    """Drop model from VRAM / clear session so next video starts clean."""
    root = (base_url or "").strip().rstrip("/")
    if not root:
        return
    name = (model or "").strip()
    if not name:
        try:
            models = list_ollama_models(root)
            name = models[0] if models else ""
        except Exception:
            name = ""
    if not name:
        return
    try:
        _http_json(
            root + "/api/generate",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"model": name, "prompt": "", "keep_alive": 0, "stream": False}).encode("utf-8"),
            timeout=timeout,
        )
    except Exception:
        pass
    try:
        _http_json(
            root + "/api/chat",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "model": name,
                    "messages": [],
                    "keep_alive": 0,
                    "stream": False,
                }
            ).encode("utf-8"),
            timeout=timeout,
        )
    except Exception:
        pass

def list_ollama_models(base_url: str = "") -> List[str]:
    root = (base_url or "").strip().rstrip("/")
    if not root:
        return []
    try:
        data = _http_json(root + "/api/tags", timeout=3)
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []

def translate_ollama(
    segments: Sequence[Segment],
    target_language: str,
    model: str,
    base_url: str,
    cb: ProgressCb = None,
    batch_size: int = 6,
    glossary: str = "",
) -> List[Segment]:
    if not (base_url or "").strip():
        raise RuntimeError("未配置 Ollama 服务地址")
    if not (model or "").strip():
        raise RuntimeError("未选择 Ollama 模型（无模型）")
    if "vision" in model.lower():
        non_vision = [m for m in list_ollama_models(base_url) if "vision" not in m.lower()]
        if non_vision:
            model = non_vision[0]
            _progress(cb, f"Ollama 改用文本模型: {model}", 63)

    return _llm_batch_translate(
        segments,
        target_language,
        cb=cb,
        batch_size=batch_size,
        chat_fn=lambda prompt: ollama_chat(base_url, model, prompt),
        label="Ollama",
        glossary=glossary,
    )

def openai_chat(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int = 120,
) -> str:
    if not api_key.strip():
        raise RuntimeError("请填写 OpenAI 兼容 API Key")
    root = base_url.rstrip("/")
    if not root.endswith("/v1"):

        if root.endswith("openai.com"):
            root = root + "/v1"
    url = root + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.05,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }
    body = _http_json(
        url,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",
        },
        data=json.dumps(payload).encode("utf-8"),
        timeout=timeout,
    )
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI 兼容接口无返回: {str(body)[:400]}")
    return ((choices[0].get("message") or {}).get("content") or "").strip()

def translate_openai(
    segments: Sequence[Segment],
    target_language: str,
    api_key: str,
    base_url: str,
    model: str,
    cb: ProgressCb = None,
    batch_size: int = 8,
    glossary: str = "",
) -> List[Segment]:
    return _llm_batch_translate(
        segments,
        target_language,
        cb=cb,
        batch_size=batch_size,
        chat_fn=lambda prompt: openai_chat(base_url, api_key, model, prompt),
        label="LLM",
        glossary=glossary,
    )

def _parse_glossary_terms(glossary: str) -> List[str]:
    if not glossary or not str(glossary).strip():
        return []
    raw = str(glossary).strip()
    if len(raw) > 80 or "。" in raw or ". " in raw:
        m = re.search(
            r"(?:常见名|专有名词|Proper names|Names)\s*[:：]\s*(.+)$",
            raw,
            flags=re.I | re.S,
        )
        if not m:
            return []
        raw = m.group(1).strip()
    parts = re.split(r"[,，;；\n|/]+", raw)
    out: List[str] = []
    seen: set[str] = set()
    for p in parts:
        t = p.strip()
        if not t or len(t) > 40:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out[:80]

def _llm_batch_translate(
    segments: Sequence[Segment],
    target_language: str,
    *,
    cb: ProgressCb,
    batch_size: int,
    chat_fn: Callable[[str], str],
    label: str,
    glossary: str = "",
) -> List[Segment]:
    if not segments:
        return []
    target = lang_name(target_language)
    special = (glossary or "").strip()
    names = _parse_glossary_terms(special)
    glossary_block = ""
    if special:

        guide = re.sub(r"\s+", " ", special)[:500]
        glossary_block = f"User style / domain notes:\n{guide}\n\n"
        if names:
            glossary_block += "Proper names to keep consistent: " + ", ".join(names) + "\n\n"
    out: List[Segment] = []
    total = len(segments)

    recent_src: List[str] = []
    recent_tgt: List[str] = []
    for start in range(0, total, batch_size):
        batch = list(segments[start : start + batch_size])
        numbered = "\n".join(f"{i+1}|{s.text}" for i, s in enumerate(batch))
        ctx = ""
        if recent_src:
            pairs = []
            for s, t in zip(recent_src[-3:], recent_tgt[-3:]):
                pairs.append(f"- {s} => {t}")
            ctx = "Recent context (do not re-translate):\n" + "\n".join(pairs) + "\n\n"
        prompt = (
            f"{glossary_block}"
            f"{ctx}"
            f"Translate into {target} for on-screen subtitles.\n"
            f"- Natural spoken style, concise (prefer under ~20 CJK chars or ~12 English words).\n"
            f"- Keep tone; no added honorifics unless in source.\n"
            f"- Exactly {len(batch)} output lines: N|translation\n\n"
            f"{numbered}"
        )
        cleaned: List[str] = []
        try:
            raw = chat_fn(prompt)
            for ln in raw.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                m = re.match(r"^(\d+)\s*[|\.\)、\-:：]\s*(.+)$", ln)
                cleaned.append(_clean_line(m.group(2) if m else ln))
        except Exception:
            cleaned = []

        if len(cleaned) != len(batch):
            cleaned = []
            for s in batch:
                try:
                    one = chat_fn(
                        f"{glossary_block}"
                        f"Context: {' / '.join(recent_src[-3:])}\n"
                        f"Translate into {target} for subtitles. Output one line only, no index:\n"
                        f"{s.text}"
                    )
                    one = _clean_line(one.splitlines()[0] if one else "")
                except Exception:
                    one = s.text
                cleaned.append(one or s.text)

        for s, t in zip(batch, cleaned):
            tt = (t or "").strip()
            if not tt:
                tt = s.text

            if any(m in tt.lower() or m in tt for m in _TRANSLATE_META) and len(tt) <= 80:
                tt = s.text
            out.append(Segment(start=s.start, end=s.end, text=tt or s.text))
            recent_src.append(s.text)
            recent_tgt.append(tt or s.text)

        done = min(start + batch_size, total)
        _progress(cb, f"{label} 翻译中… {done}/{total}", 60 + (done / total) * 25)
    return out

def _deepl_target(code: str) -> str:
    m = {
        "zh-CN": "ZH",
        "zh": "ZH",
        "zh-TW": "ZH",
        "en": "EN-US",
        "ja": "JA",
        "ko": "KO",
        "fr": "FR",
        "de": "DE",
        "es": "ES",
        "ru": "RU",
        "pt": "PT-BR",
        "it": "IT",
    }
    return m.get(code, code.upper().split("-")[0])

def translate_deepl(
    segments: Sequence[Segment],
    target_language: str,
    api_key: str,
    use_free_endpoint: bool = True,
    cb: ProgressCb = None,
) -> List[Segment]:
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("请填写 DeepL API Key")

    host = "https://api-free.deepl.com" if use_free_endpoint else "https://api.deepl.com"

    if key.endswith(":fx"):
        host = "https://api-free.deepl.com"
    url = host + "/v2/translate"
    target = _deepl_target(target_language)

    out: List[Segment] = []
    total = len(segments) or 1

    batch_size = 40
    for start in range(0, total, batch_size):
        batch = list(segments[start : start + batch_size])
        form = [("target_lang", target)]
        for s in batch:
            form.append(("text", s.text))
        data = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
        body = _http_json(
            url,
            method="POST",
            headers={
                "Authorization": f"DeepL-Auth-Key {key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=data,
            timeout=90,
        )
        translations = body.get("translations") or []
        if len(translations) != len(batch):
            raise RuntimeError(f"DeepL 返回条数不匹配: {len(translations)} vs {len(batch)}")
        for s, tr in zip(batch, translations):
            out.append(
                Segment(start=s.start, end=s.end, text=(tr.get("text") or s.text).strip())
            )
        done = min(start + batch_size, total)
        _progress(cb, f"DeepL 翻译中… {done}/{total}", 60 + (done / total) * 25)
        time.sleep(0.05)
    return out

def _baidu_lang(code: str) -> str:
    m = {
        "zh-CN": "zh",
        "zh": "zh",
        "zh-TW": "cht",
        "en": "en",
        "ja": "jp",
        "ko": "kor",
        "fr": "fra",
        "de": "de",
        "es": "spa",
        "ru": "ru",
        "vi": "vie",
        "th": "th",
        "pt": "pt",
        "it": "it",
        "auto": "auto",
    }
    return m.get(code, "auto")

def translate_baidu(
    segments: Sequence[Segment],
    target_language: str,
    app_id: str,
    app_key: str,
    source_language: str = "auto",
    cb: ProgressCb = None,
) -> List[Segment]:
    app_id = (app_id or "").strip()
    app_key = (app_key or "").strip()
    if not app_id or not app_key:
        raise RuntimeError("请填写百度翻译 APP ID 与密钥")

    url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    fr = _baidu_lang(source_language)
    to = _baidu_lang(target_language)
    out: List[Segment] = []
    total = len(segments) or 1

    for i, seg in enumerate(segments):
        q = seg.text
        salt = str(random.randint(10000, 99999))
        sign = hashlib.md5((app_id + q + salt + app_key).encode("utf-8")).hexdigest()
        params = urllib.parse.urlencode(
            {"q": q, "from": fr, "to": to, "appid": app_id, "salt": salt, "sign": sign}
        )
        body = _http_json(url + "?" + params, timeout=30)
        if "error_code" in body and str(body.get("error_code")) not in ("0", "52000", ""):

            if "trans_result" not in body:
                raise RuntimeError(f"百度翻译错误: {body}")
        parts = body.get("trans_result") or []
        text = "".join(p.get("dst", "") for p in parts).strip() or seg.text
        out.append(Segment(start=seg.start, end=seg.end, text=text))
        if i % 5 == 0 or i == total - 1:
            _progress(cb, f"百度翻译中… {i+1}/{total}", 60 + ((i + 1) / total) * 25)
        time.sleep(0.12)
    return out

def _youdao_lang(code: str) -> str:
    m = {
        "zh-CN": "zh-CHS",
        "zh": "zh-CHS",
        "zh-TW": "zh-CHT",
        "en": "en",
        "ja": "ja",
        "ko": "ko",
        "fr": "fr",
        "de": "de",
        "es": "es",
        "ru": "ru",
        "vi": "vi",
        "th": "th",
        "pt": "pt",
        "it": "it",
        "auto": "auto",
    }
    return m.get(code, "auto")

def _youdao_sign(app_key: str, app_secret: str, q: str, salt: str, curtime: str) -> str:

    if len(q) <= 20:
        input_str = q
    else:
        input_str = q[:10] + str(len(q)) + q[-10:]
    sign_str = app_key + input_str + salt + curtime + app_secret
    return hashlib.sha256(sign_str.encode("utf-8")).hexdigest()

def translate_youdao(
    segments: Sequence[Segment],
    target_language: str,
    app_key: str,
    app_secret: str,
    source_language: str = "auto",
    cb: ProgressCb = None,
) -> List[Segment]:
    app_key = (app_key or "").strip()
    app_secret = (app_secret or "").strip()
    if not app_key or not app_secret:
        raise RuntimeError("请填写有道智云 应用ID 与 应用密钥")

    url = "https://openapi.youdao.com/api"
    fr = _youdao_lang(source_language)
    to = _youdao_lang(target_language)
    out: List[Segment] = []
    total = len(segments) or 1

    for i, seg in enumerate(segments):
        q = seg.text
        salt = str(uuid.uuid4())
        curtime = str(int(time.time()))
        sign = _youdao_sign(app_key, app_secret, q, salt, curtime)
        form = urllib.parse.urlencode(
            {
                "q": q,
                "from": fr,
                "to": to,
                "appKey": app_key,
                "salt": salt,
                "sign": sign,
                "signType": "v3",
                "curtime": curtime,
            }
        ).encode("utf-8")
        body = _http_json(
            url,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=form,
            timeout=30,
        )
        if str(body.get("errorCode", "0")) != "0":
            raise RuntimeError(f"有道翻译错误: {body}")
        texts = body.get("translation") or [seg.text]
        text = (texts[0] if texts else seg.text).strip()
        out.append(Segment(start=seg.start, end=seg.end, text=text))
        if i % 5 == 0 or i == total - 1:
            _progress(cb, f"有道翻译中… {i+1}/{total}", 60 + ((i + 1) / total) * 25)
        time.sleep(0.1)
    return out

def _azure_lang(code: str) -> str:
    m = {
        "zh-CN": "zh-Hans",
        "zh": "zh-Hans",
        "zh-TW": "zh-Hant",
        "en": "en",
        "ja": "ja",
        "ko": "ko",
        "fr": "fr",
        "de": "de",
        "es": "es",
        "ru": "ru",
        "vi": "vi",
        "th": "th",
        "pt": "pt",
        "it": "it",
    }
    return m.get(code, code.split("-")[0])

def translate_azure(
    segments: Sequence[Segment],
    target_language: str,
    api_key: str,
    region: str = "global",
    endpoint: str = "https://api.cognitive.microsofttranslator.com",
    cb: ProgressCb = None,
) -> List[Segment]:
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("请填写 Azure Translator Key")
    region = (region or "global").strip()
    endpoint = (endpoint or "https://api.cognitive.microsofttranslator.com").rstrip("/")
    to = _azure_lang(target_language)

    out: List[Segment] = []
    total = len(segments) or 1
    batch_size = 50
    for start in range(0, total, batch_size):
        batch = list(segments[start : start + batch_size])
        url = f"{endpoint}/translate?api-version=3.0&to={urllib.parse.quote(to)}"
        payload = [{"text": s.text} for s in batch]
        body = _http_json(
            url,
            method="POST",
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Ocp-Apim-Subscription-Region": region,
                "Content-Type": "application/json",
            },
            data=json.dumps(payload).encode("utf-8"),
            timeout=60,
        )
        if not isinstance(body, list) or len(body) != len(batch):
            raise RuntimeError(f"Azure 翻译返回异常: {str(body)[:500]}")
        for s, item in zip(batch, body):
            try:
                text = item["translations"][0]["text"]
            except Exception:
                text = s.text
            out.append(Segment(start=s.start, end=s.end, text=(text or s.text).strip()))
        done = min(start + batch_size, total)
        _progress(cb, f"Azure 翻译中… {done}/{total}", 60 + (done / total) * 25)
        time.sleep(0.05)
    return out

STABLE_TRANSLATORS = (
    "none",
    "ollama",
    "openai",
    "deepl",
    "baidu",
    "youdao",
    "azure",
)

def translate_segments(
    segments: Sequence[Segment],
    *,
    translator: str,
    target_language: str,
    source_language: str = "auto",
    cb: ProgressCb = None,

    ollama_model: str = "",
    ollama_base_url: str = "",

    openai_api_key: str = "",
    openai_base_url: str = "https://api.openai.com/v1",
    openai_model: str = "gpt-4o-mini",

    deepl_api_key: str = "",
    deepl_use_free: bool = True,

    baidu_app_id: str = "",
    baidu_app_key: str = "",

    youdao_app_key: str = "",
    youdao_app_secret: str = "",

    azure_api_key: str = "",
    azure_region: str = "global",
    azure_endpoint: str = "https://api.cognitive.microsofttranslator.com",
    glossary: str = "",
) -> List[Segment]:
    t = (translator or "none").lower().strip()
    if t in ("", "none"):
        return list(segments)
    if t == "google":
        raise RuntimeError("不支持 google。请使用 ollama / deepl / baidu / youdao / azure / openai。")
    if t == "ollama":
        return translate_ollama(
            list(segments),
            target_language,
            ollama_model,
            ollama_base_url,
            cb=cb,
            glossary=glossary,
        )
    if t == "openai":
        return translate_openai(
            list(segments),
            target_language,
            openai_api_key,
            openai_base_url,
            openai_model,
            cb=cb,
            glossary=glossary,
        )
    if t == "deepl":
        return translate_deepl(
            list(segments),
            target_language,
            deepl_api_key,
            use_free_endpoint=deepl_use_free,
            cb=cb,
        )
    if t == "baidu":
        return translate_baidu(
            list(segments),
            target_language,
            baidu_app_id,
            baidu_app_key,
            source_language=source_language,
            cb=cb,
        )
    if t == "youdao":
        return translate_youdao(
            list(segments),
            target_language,
            youdao_app_key,
            youdao_app_secret,
            source_language=source_language,
            cb=cb,
        )
    if t == "azure":
        return translate_azure(
            list(segments),
            target_language,
            azure_api_key,
            region=azure_region,
            endpoint=azure_endpoint,
            cb=cb,
        )
    raise ValueError(f"未知翻译器: {translator}（支持: {', '.join(STABLE_TRANSLATORS)}）")
