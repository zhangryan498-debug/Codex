"""Local media bridge to the existing pinned project CUDA transcription runner."""

import base64
import binascii
import html
import json
import math
from pathlib import Path
import re
import subprocess
import wave


class MediaError(ValueError):
    """A media, local runtime, or transcription problem suitable for the task UI."""


LANGUAGES = {
    "auto": "自动检测",
    "zh": "中文",
    "id": "印尼语",
    "tl": "菲律宾语（Tagalog）",
    "en": "英语",
}
DEMUXERS = {".wav": "wav", ".mp4": "mov", ".mov": "mov", ".m4a": "mov"}
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
MAX_DURATION_SECONDS = 300
SAMPLE_RATE = 16000
MAX_SAMPLES = SAMPLE_RATE * MAX_DURATION_SECONDS
EXTRACTION_TIMEOUT_SECONDS = 30
INFERENCE_TIMEOUT_SECONDS = 600
DATA_URL = re.compile(r"data:[\w.+/-]*;base64,", re.ASCII | re.IGNORECASE)


def _language_code(language):
    normalized = language.strip().lower() if isinstance(language, str) else None
    if normalized not in LANGUAGES:
        raise MediaError("请选择自动检测、中文、印尼语、菲律宾语或英语。")
    return normalized


def validate_upload(media, language):
    """Return a display name, normalized suffix, decoded bytes, and language."""
    normalized = _language_code(language)
    if not isinstance(media, dict):
        raise MediaError("请选择要转写的本地音频或视频。")
    filename, encoded = media.get("name"), media.get("data")
    if (not isinstance(filename, str) or not filename.strip() or len(filename) > 255
            or any(ord(char) < 32 or ord(char) == 127 for char in filename)
            or any(char in filename for char in '/\\:') or filename.endswith((" ", "."))):
        raise MediaError("本地文件名称无效。请重新选择文件。")
    suffix = Path(filename).suffix.lower()
    if suffix not in DEMUXERS:
        raise MediaError("请选择 WAV、MP4、MOV 或 M4A 文件。")
    if not isinstance(encoded, str) or not encoded:
        raise MediaError("文件内容为空。请重新选择文件。")
    match = DATA_URL.match(encoded) if encoded.startswith("data:") else None
    if match:
        encoded = encoded[match.end():]
    if len(encoded) > 4 * ((MAX_UPLOAD_BYTES + 2) // 3):
        raise MediaError("文件超过 24 MiB。请裁成较小片段后重试。")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise MediaError("文件内容读取失败。请重新选择文件。") from None
    if not payload:
        raise MediaError("文件内容为空。请重新选择文件。")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise MediaError("文件超过 24 MiB。请裁成较小片段后重试。")
    return filename, suffix, payload, normalized


def _find_ffmpeg(project):
    # The original evidence pipeline uses imageio-ffmpeg from the project venv.
    binaries = project / ".venv" / "Lib" / "site-packages" / "imageio_ffmpeg" / "binaries"
    executables = sorted(path for path in binaries.glob("ffmpeg-win-*.exe") if path.is_file())
    if len(executables) != 1:
        raise MediaError("未找到唯一的项目 FFmpeg 运行文件。请检查本地 D&T 媒体运行环境。")
    return executables[0]


def _extract_pcm(ffmpeg, project, source, demuxer):
    # Disable MOV external data references even for the local file protocol.
    container_options = ["-enable_drefs", "0", "-use_absolute_path", "0"] if demuxer == "mov" else []
    command = [
        str(ffmpeg), "-hide_banner", "-v", "error", "-nostdin", "-xerror",
        "-max_alloc", str(64 * 1024 * 1024), "-threads", "1",
        "-protocol_whitelist", "file,pipe", "-format_whitelist", demuxer,
        "-f", demuxer, *container_options, "-i", str(source),
        "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-t", str(MAX_DURATION_SECONDS + 1), "-fs", str(2 * (MAX_SAMPLES + SAMPLE_RATE)),
        "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command, cwd=project, stdin=subprocess.DEVNULL, capture_output=True,
            check=False, timeout=EXTRACTION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise MediaError("本地音轨提取超时。请裁成较短片段后重试。") from None
    except OSError:
        raise MediaError("本地 FFmpeg 无法启动。请检查 D&T 媒体运行环境。") from None
    diagnostics = completed.stderr.decode("utf-8", "replace").lower()
    if completed.returncode:
        if "matches no streams" in diagnostics or "does not contain any stream" in diagnostics:
            raise MediaError("所选文件没有可转写的音轨。")
        raise MediaError("无法读取音轨。请确认文件实际格式与扩展名一致，且文件可以播放。")
    pcm = completed.stdout
    if not pcm or len(pcm) % 2:
        raise MediaError("没有提取到完整的音频采样。请检查文件音轨。")
    if len(pcm) > 2 * MAX_SAMPLES:
        raise MediaError("音轨超过 5 分钟。请裁成较短片段后重试。")
    if pcm.count(0) == len(pcm):
        raise MediaError("提取的音轨为全零静音。请换用含有可听语音的文件。")
    return pcm


def _srt_time(seconds):
    hours, remaining = divmod(round(seconds * 1000), 3600000)
    minutes, remaining = divmod(remaining, 60000)
    whole_seconds, milliseconds = divmod(remaining, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{milliseconds:03}"


def _write_srt(path, segments, duration):
    entries = []
    for segment in segments:
        start, end = segment["start"], segment["end"]
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= duration + 0.1):
            raise MediaError("原始转写的时间戳不完整。已保留本地运行回执。")
        # One physical subtitle line prevents decoded speech from creating new SRT cues.
        line = " ".join(segment["text"].split())
        line = "".join(char for char in line if ord(char) >= 32 and ord(char) != 127)
        if line:
            entries.append(
                f"{len(entries) + 1}\n{_srt_time(start)} --> {_srt_time(end)}\n"
                f"{html.escape(line, quote=False)}"
            )
    if not entries:
        raise MediaError("本地模型没有返回可导出的语音内容。")
    path.write_text("\n\n".join(entries) + "\n", encoding="utf-8", newline="\n")


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise MediaError("本地转写未生成可读取的完整回执。") from None


def run_transcription(project, task_dir, input_file, language, progress):
    """Return official CUDA result and the local JSON, TXT, SRT and receipt paths."""
    language = _language_code(language)
    project = Path(project).resolve()
    task_dir = Path(task_dir).resolve()
    input_file = Path(input_file).resolve()
    suffix = input_file.suffix.lower()
    if not input_file.is_relative_to(task_dir) or suffix not in DEMUXERS or not input_file.is_file():
        raise MediaError("任务中找不到可转写的本地文件。")
    if not 0 < input_file.stat().st_size <= MAX_UPLOAD_BYTES:
        raise MediaError("任务文件为空或超过 24 MiB。")
    python = project / ".venv" / "Scripts" / "python.exe"
    runner = project / "scripts" / "transcribe_local_video.py"
    if not python.is_file() or not runner.is_file():
        raise MediaError("未找到项目的本地 CUDA 转写入口。请检查 D&T 媒体运行环境。")

    progress("正在本地提取并检查音轨")
    pcm = _extract_pcm(_find_ffmpeg(project), project, input_file, DEMUXERS[suffix])
    output = task_dir / "transcription"
    try:
        output.mkdir()
    except FileExistsError:
        raise MediaError("本任务已有转写运行记录。请使用新任务，已有文件会保留。") from None
    audio = output / "audio-16k.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(pcm)
    duration = len(pcm) / (2 * SAMPLE_RATE)
    del pcm

    progress("正在使用本机 CUDA 模型转写")
    try:
        completed = subprocess.run(
            [str(python), "-B", str(runner), "--audio", str(audio),
             "--language", language, "--output-dir", str(output)],
            cwd=project, stdin=subprocess.DEVNULL, capture_output=True,
            check=False, timeout=INFERENCE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise MediaError("本地 CUDA 转写超时。请尝试较短片段；已生成的运行记录会保留。") from None
    except OSError:
        raise MediaError("项目的本地 CUDA 转写程序无法启动。") from None

    files = {
        "json": output / "single-audio-turbo.json",
        "text": output / "single-audio-turbo.txt",
        "srt": output / "single-audio.srt",
        "receipt": output / "run-receipt.json",
    }
    if completed.returncode:
        diagnostics = completed.stderr.decode("utf-8", "replace").lower()
        if "no speech recognized" in diagnostics:
            raise MediaError("本地模型没有识别到语音。请检查音源或试用自动检测。")
        if "timing validation failed" in diagnostics:
            raise MediaError("本地转写的时间戳校验失败。原始输出和运行回执已保留。")
        if "missing or unverified pinned model" in diagnostics:
            raise MediaError("本地固定版本转写模型缺失或校验未通过。请检查 D&T 媒体运行环境。")
        raise MediaError("本地 CUDA 转写未完成。请检查 GPU 和 D&T 转写运行环境。")
    receipt = _read_json(files["receipt"])
    result = _read_json(files["json"])
    if (receipt.get("status") != "completed" or receipt.get("actual_device") != "cuda"
            or receipt.get("media_uploaded") is not False or receipt.get("external_asr_called") is not False
            or result.get("requested_language") != language or result.get("timestamp_issues") != []
            or not isinstance(result.get("segments"), list) or not files["text"].is_file()):
        raise MediaError("本地 CUDA 转写回执或输出不完整。已保留原始文件。")
    progress("正在检查结果并生成字幕文件")
    _write_srt(files["srt"], result["segments"], duration)
    return result, files
