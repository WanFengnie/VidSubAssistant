# 第三方组件与下载说明

本文件将说明本仓库源码运行时会使用的第三方组件、**是否会自动下载**、以及许可边界。**该文件并非正式法律意见**；各组件以官方许可证与网站为准。

本仓库源码采用 **MIT**（见 [LICENSE](LICENSE)）。  
**MIT 只授权本仓库里的源代码**，不授权：FFmpeg 二进制、NVIDIA 驱动/CUDA、Whisper 等模型权重、Ollama 及各 LLM、云 API 服务。

---

## 1. 自动下载 vs 需自行安装

| 组件 | 程序是否自动下载 | 说明 |
|------|------------------|------|
| Whisper / faster-whisper 模型权重 | **会**（首次识别且本地无缓存时，经 Hugging Face 下载） | 会占一定体积；缓存目录可在界面或环境变量中配置。各模型许可以模型卡为准。 |
| Python 依赖（pip） | **否**（自行执行`pip install`） | 见 `src/requirements.txt` |
| FFmpeg / ffprobe | **否** | 须自行安装并加入 PATH；再分发其 exe 须遵守 FFmpeg 的 GPL/LGPL 等条款。打包脚本若**复制本机已有** ffmpeg，不改变其原有许可义务。 |
| NVIDIA 驱动 / CUDA | **否** | 专有许可。缺少驱动或运行库时程序会回退至 CPU。`requirements.txt` 中的 `nvidia-*-cu12` 为可选 GPU 运行库，安装即接受 NVIDIA 相关条款。 |
| Ollama 与本地模型 | **否** | 可选翻译后端；遵守 [Ollama](https://ollama.com/) 与各模型条款。 |
| 云翻译（DeepL / 百度 / 有道 / Azure / OpenAI 兼容等） | **否** | 用户自备 API Key，遵守各服务商条款与计费规则。本工具不代注册或托管密钥。 |

---

## 2. 主要 Python 依赖（源码运行）

安装：`pip install -r src/requirements.txt`  
版本以该文件及 PyPI 元数据为准；下表为常见用途与许可类型（可能随上游变更）。

| 包 | 常见许可 | 用途 |
|----|----------|------|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | 语音识别 |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | MIT | 推理后端（随 faster-whisper 引入） |
| [customtkinter](https://github.com/TomSchimansky/CustomTkinter) | MIT | 图形界面 |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | HTTP（部分网络场景） |
| [windnd](https://pypi.org/project/windnd/) | 见包说明 | Windows 拖放 |
| huggingface_hub / tokenizers 等 | Apache-2.0 等 | 模型下载与分词（传递依赖） |
| numpy / onnxruntime 等 | 见各包 | 数值与 VAD 等（传递依赖） |
| nvidia-cublas-cu12 等 | NVIDIA | 可选，CUDA 12 GPU 推理 |
| [PyInstaller](https://pyinstaller.org/) | GPL（含例外） | **打包使用**，源码日常运行不需要 |

完整传递依赖请以 `pip` 解析结果为准。

---

## 3. 用户内容

导入的视频、音频、字幕与提示词内容，**版权与合规由用户自行负责**。  
请确保有权进行转写、翻译与分发；勿用于侵犯他人权利的用途。

---

## 4. 打包 / 再分发 exe 时

- 本仓库**默认不包含**、也不以 Git 跟踪 `dist/` 中的 exe 与 FFmpeg。  
- 若向他人分发带 FFmpeg 的安装包，须自行满足 **FFmpeg 许可证**。  
- 若捆绑 NVIDIA 库或模型权重，须遵守对应专有/模型许可，**不能**仅凭本仓库的 MIT 覆盖它们。  
- 此外并不建议分发包含上述内容的 exe。

---

## 5. 免责

- 识别与翻译可能有误，重要用途请人工校对，翻译内容仅供参考。  
- 对于第三方服务中断、改价、封禁 Key 等问题不由本工具承担。  
- 使用本软件即表示你理解上述边界；有疑问请查阅各上游官方文档。

---
