# 第三方组件与开源许可声明 (Third-Party Notices & Compliance)

本文件说明本仓库源码涉及的第三方组件、许可边界以及再分发合规要求。

本仓库源代码采用 **GPL-3.0 (GNU General Public License v3.0)** 发布（详见 [LICENSE](LICENSE)）。**GPL-3.0 许可证仅覆盖本仓库的源代码**，不涵盖任何外部依赖二进制文件、显卡驱动、模型权重或第三方 API 服务。

---

## 1. 外部依赖与许可边界

> 💡 **特别说明**：**Whisper 语音识别模型权重无需用户手动寻找或下载**。首次发起识别时，程序会自动在线连接 Hugging Face 并下载至本地缓存目录。

| 组件 / 依赖 | 下载/安装机制 | 许可类型 / 上游条款 | 许可边界与合规说明 |
|:---|:---|:---|:---|
| **Whisper 模型权重** | **无需手动下载（程序首次运行时自动在线下载）** | 见各模型卡（MIT / Apache-2.0 等） | 模型由 `faster-whisper` 在首次识别时自动下载并缓存至本地，模型版权归原作者所有。 |
| **FFmpeg / ffprobe** | **须自行安装（发布包未内置）** | GPL v2+ / LGPL v2.1+ | 本仓库代码不直接包含 FFmpeg 编译二进制。若再分发或打包包含 FFmpeg 二进制的文件，需自行遵守 FFmpeg 许可证规定。 |
| **NVIDIA CUDA 运行库（pip 包）** | **源码运行时随 `pip install -r src/requirements.txt` 自动安装** | NVIDIA Software License（CUDA EULA 补充条款，专有许可） | `nvidia-cublas-cu12` 等运行库 wheel 由 NVIDIA 官方发布于 PyPI，安装时由 pip 在线下载，本仓库不包含其二进制文件；程序通过动态链接方式调用。 |
| **NVIDIA CUDA Toolkit** | **仅打包版 exe 用户须自行安装（发布包未内置）** | NVIDIA EULA (专有许可) | 打包版未携带 CUDA 动态库，exe 用户需自行安装系统级 CUDA 12/13 Toolkit（或将运行库目录加入 PATH）。任何场景下未找到 CUDA 运行库时，软件均自动降级为 CPU 推理。 |
| **Ollama 及 LLM 模型** | **须自行安装（发布包未内置）** | MIT / Llama License 等 | 本软件仅作为客户端进行 API 交互，用户需自行遵守对应模型及 Ollama 官方使用条款。 |
| **云翻译 API 服务** | **需自备 API Key** | 各服务提供商商用条款 | 用户自备 Key 并遵守各自计费与服务协议。 |

---

## 2. 主要 Python 依赖包

项目使用的主要 Python 依赖及其开源许可汇总如下（具体版本与传递依赖以 `src/requirements.txt` 及 PyPI 解析结果为准）：

| 依赖包 | 许可类型 | 主要用途 |
|:---|:---|:---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | 语音识别 ASR 引擎 |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | MIT | 快速推理后端 |
| [customtkinter](https://github.com/TomSchimansky/CustomTkinter) | MIT | GUI 界面构建 |
| [nvidia-cublas-cu12](https://pypi.org/project/nvidia-cublas-cu12/) / [nvidia-cudnn-cu12](https://pypi.org/project/nvidia-cudnn-cu12/) / [nvidia-cuda-runtime-cu12](https://pypi.org/project/nvidia-cuda-runtime-cu12/) / [nvidia-cuda-nvrtc-cu12](https://pypi.org/project/nvidia-cuda-nvrtc-cu12/) | NVIDIA Software License（专有许可） | N 卡 GPU 推理所需的 CUDA 运行库（cuBLAS / cuDNN / CUDA Runtime / NVRTC） |
| [PyInstaller](https://pyinstaller.org/) | GPL v2+ (含 Bootloader 例外) | 构建可执行程序（仅打包阶段使用） |

---

## 3. 再分发与二次开发告知

1. **二进制打包**：若将本软件打包为独立的可执行文件（`.exe`）并进行二次分发，请勿将包含 GPL/LGPL 限制的 FFmpeg 二进制或受专有 EULA 保护的 NVIDIA 动态库直接封装进发布包中，除非已满足上游开源协议的相关开源及授权义务。
2. **生成内容归属**：用户通过本软件转写、翻译与烧录产生的音视频与字幕文件，其版权与法律合规责任由内容提供方与使用者自行承担。

---

## 4. 免责声明

1. **识别与翻译结果**：自动语音识别与大模型翻译结果受限于模型能力与语音质量，存在错漏或不准确之处，请勿直接用于严谨法律、医疗或决策场景，建议只用于参考。
2. **第三方服务变更**：对于第三方 API 服务商（如 OpenAI、DeepL 等）发生的网络中断、接口调整、价格变更或账户封禁等问题，本软件不承担连带责任。
