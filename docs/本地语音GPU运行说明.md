# 本地 Large-v3-turbo GPU 运行说明

适用当前 Windows x86-64 项目环境。模型支持运行时语言表内的原语转写及自动语言检测；历史实测中文及印尼语保持回归，新增语言按样本分项验收，中文解释由岗位另写；转写不负责验证实际材质、安全性或销售效果。最新实测与业务验收记录位于 `provider-evidence/turbo-upgrade-2026-11-09/`。

## 固定来源与依赖

- `interface/video-asr-requirements.lock.txt` 固定 faster-whisper、CTranslate2 与 PyAV 等版本；PyAV 保持18.1.0，避免已实测的19版兼容错误。
- `interface/video-gpu-requirements.lock.txt` 固定 NVIDIA 官方 PyPI Windows wheel 版本及 SHA256；补充 cuBLAS、CUDA runtime 和与 CTranslate2 自带前端一致的 cuDNN9.10.2.21。
- `interface/video-asr-model.lock.json` 固定 `dropbox-dash/faster-whisper-large-v3-turbo` 的不可变 revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`。这是 `openai/whisper-large-v3-turbo` 的 CTranslate2 转换版。权重1,617,884,929字节，官方LFS SHA256为 `e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da`。
- 模型标注MIT；归档保留上游许可原文和公共来源回执。NVIDIA库保留wheel中的原始许可证，不将其称为MIT模型权重。

权重及DLL留在本机缓存/虚拟环境，均不进入报告包。转移机器时按锁文件恢复，不能只复制报告当作模型已部署。

## 安装与恢复

在项目根目录执行，使用当前 `.venv`，不向系统Python安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -r interface/video-asr-requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --require-hashes -r interface/video-gpu-requirements.lock.txt
.\.venv\Scripts\python.exe scripts/fetch_asr_model.py
.\.venv\Scripts\python.exe -m unittest discover -s scripts -p test_local_asr.py
```

首次下载使用公共模型源；文件按固定大小和官方哈希验证，分段下载可续传。服务器返回错误范围会中止并保留已有分段；未验证的同名最终文件不会被覆盖。模型存放在锁文件指定的项目 `work/models/`，下载回执为模型目录下 `download-receipt.json`。

`scripts/video_gpu_runtime.py` 只对当前Python进程注册虚拟环境内的DLL目录并预载库，保持句柄存活。它不修改系统PATH、注册表、显卡驱动或安装全局CUDA Toolkit；必须在导入 `faster_whisper` / `ctranslate2` 之前调用。正式转写入口已包含此步骤。

## 岗位实际调用

先从已验证本地原片提取真正混音音轨；不可把平台 `music.playUrl` 的背景音乐当原片口播：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_video_evidence.py --video '本地原片.mp4' --output-dir 'work/video-evidence/样本' --interval 1
.\.venv\Scripts\python.exe scripts/transcribe_local_video.py --audio 'work/video-evidence/样本/audio-16k.wav' --language zh --output-dir 'work/asr/本次新目录'
```

印尼语改为 `--language id`。输入必须是16kHz、单声道、PCM16 WAV。需要批处理时用 `--manifest` 代替 `--audio/--language`；JSON数组逐项包含 `id`、音频绝对路径 `audio`、`language`，可额外给 `video`、预期 `expected_audio_sha256` / `expected_video_sha256` 和平台身份信息。

模型只读已验本地文件，推理启用离线模式，不上传媒体、不调用外部语音服务或平台接口。设备固定CUDA、`int8_float16`，失败明确报错，没有隐藏CPU降级。一次运行复用模型处理批次；同一显卡上先顺序执行，避免并发争用显存。

每次必须使用新输出目录，保留旧转写。输出包含：

1. `run-receipt.json`：模型锁、真实设备/精度、依赖版本、加载与逐条推理耗时、GPU显存抽样、源文件哈希和状态。
2. `*-turbo.json`：原语逐句/逐词时间戳、未经人工改写的模型原稿、参数和验证范围。
3. `*-turbo.txt`：便于岗位阅读的带时点文本。

GPU显存记录是采样时整张卡的总占用，含桌面及其他程序，不能当模型独占峰值。强制语言时返回的语言概率1不是准确率。时间戳与哈希通过也不证明每个字正确。

## 分析及验收

沿用 `双平台短视频拆解实跑工作流.md` 和 `短视频拆解验收口径与语音核验说明.md`。优先核对首3秒、商品/卖点、数字与否定、促单CTA，保留原语、中文解释、实际时间点和证据。

模型原稿与平台字幕、烧录字分别保留；不得用字幕提示词诱导本轮转写，再把一致性称为独立验证。本次固定 beam5、temperature0、不启用VAD、不承接前文、无initial_prompt。多个Whisper版本也可能共同犯错；没有独立人工真值时不报告WER或准确率百分比。

轻微拼写差异不阻断语义研究；数字、材料缩写、否定或承诺未可靠确认时，只限制对应结论。未做人工听审如实记录，不能据此否决所有结构分析。广告宣称的“TPR、麦芽糖、检测、安全”等只属于话术，不等于实物成分或合格证明。

抖音发布作品只须核对目标商品ID，具体规格SKU可选。商详演示不计发布作品；语音升级不会自动补出缺失挂车商品ID。业务效果仍需后续上线与真实经营数据验收。


## 2026-11-18 语言入口扩展

复用同一固定 large-v3-turbo 模型与 CUDA，不新增模型或云端依赖。入口支持当前运行时词表的100种语言代码及 `auto` 自动检测；不表示100种语言全部经过真实样本准确率验收。`--list-languages` 可列出实际支持代码。

单文件省略 `--language` 默认为 `auto`；明确英语用 `en`，菲律宾语/Tagalog 用 `tl`，中文 `zh`、印尼语 `id` 保持不变。批次 language 可省略或设 auto；不支持的代码明确报错，不偷偷改成其他语言。自动检测概率不是转写准确率。混合语言、歌曲、无口播、数字/色号仍需审核。结果记录 requested_language、detected_language 和原始 transcription_info。

本次验证：100语言代码入口单测、无效代码拒绝；中文/印尼语各1条真实回归原文一致；2条英语商品视频用en/auto各跑1次；1条103.583秒真实Tagalog/English混说商品广告用tl/auto各跑1次，均完成且时间戳无异常。英语按用户授权进行文本语义审核可用于项目研究；不等同逐字字幕准确。Tagalog完成真实样本技术冒烟，未做人类逐字准确率验收。其余语言尚未逐一实测。
