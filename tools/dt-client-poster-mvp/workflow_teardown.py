"""Make a traceable first-pass short-video teardown from one authorized local MP4."""
from datetime import datetime, timezone
from hashlib import file_digest
from importlib.util import module_from_spec, spec_from_file_location
import json
import math
from pathlib import Path
import re
import subprocess

from transcription_job import MediaError, MAX_UPLOAD_BYTES, _find_ffmpeg, run_transcription


class WorkflowError(ValueError):
    """Input or local media problem suitable for the customer task view."""


TOOL_ID = "teardown-local-video-review"
SITES = {"CN": ("抖音中国", ("zh", "auto")), "PH": ("TikTok 菲律宾", ("tl", "en", "auto"))}
MAX_DURATION = 60
INTERVAL_SECONDS = 3
MEDIA_TIMEOUT = 35
DURATION = re.compile(r"^  Duration: (\d+):(\d+):([\d.]+),", re.M)
VIDEO = re.compile(r"^\s*Stream #0:\d+(?:\[[^\]]+\])?(?:\([^\n)]+\))?: Video: ([^\n]+)", re.M)
AUDIO = re.compile(r"^\s*Stream #0:\d+(?:\[[^\]]+\])?(?:\([^\n)]+\))?: Audio:", re.M)
FRAME_TIME = re.compile(r"\[Parsed_showinfo_\d+ [^\]\n]+\]\s*n:\s*(\d+)[^\n]*?pts_time:\s*([-+\deE.]+)")


def _example(site):
    return {"site": site, "rights": "authorized", "synthetic_material": True,
            "source_kind": "local_clip", "transcription_language": None}


def public_tools():
    return [{
        "id": TOOL_ID, "label": "拆解本地短视频", "role_suffix": "video-teardown", "sites": list(SITES),
        "description": (
            "上传 1 个已获授权、60 秒以内的标准 MP4。生成带时间点的画面总览、证据清单和分段复核报告。"
            "按需将 transcription_language 改为本站支持的语言代码，以便使用本地模型生成字幕草稿。"
        ),
        "input_example": _example("CN"), "input_examples": {site: _example(site) for site in SITES},
        "max_media_files": 1,
    }]


def _validate(site, tool_id, payload, task_dir, assets):
    if tool_id != TOOL_ID or not isinstance(site, str) or site not in SITES:
        raise WorkflowError("请选择抖音中国或 TikTok 菲律宾的本地短视频拆解。")
    if not isinstance(payload, dict) or payload.keys() != _example(site).keys() or payload["site"] != site:
        raise WorkflowError("站点或参数不匹配，请按本站示例填写。")
    if payload["rights"] != "authorized" or type(payload["synthetic_material"]) is not bool:
        raise WorkflowError("请确认素材使用权，并注明是否为合成素材。")
    if payload["source_kind"] not in ("local_clip", "published_work", "merchant_demo"):
        raise WorkflowError("素材来源请选择 local_clip、published_work 或 merchant_demo。")
    if payload["transcription_language"] not in (None, *SITES[site][1]):
        raise WorkflowError("请填写本站支持的转写语言：抖音中国 zh/auto，菲律宾 tl/en/auto，或 null。")
    if len(assets) != 1:
        raise WorkflowError("请选择 1 个已获授权的本机 MP4。")
    name, supplied = next(iter(assets.items()))
    source = Path(supplied).resolve()
    if (not source.is_relative_to(task_dir) or source.suffix.lower() != ".mp4"
            or not source.is_file() or not 0 < source.stat().st_size <= MAX_UPLOAD_BYTES):
        raise WorkflowError("视频必须是任务目录内 24 MiB 以下的本机 MP4。")
    with source.open("rb") as stream:
        header = stream.read(12)
    if len(header) != 12 or header[4:8] != b"ftyp" or int.from_bytes(header[:4], "big") < 12:
        raise WorkflowError("文件内容无法识别为标准 MP4，请重新导出视频。")
    return name, source


def _ffmpeg(ffmpeg, source, arguments, *, probe=False):
    # Match the transcription bridge's local-only input restrictions for customer files.
    command = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-xerror", "-max_alloc", str(64 * 1024 * 1024),
        "-filter_threads", "1", "-threads", "1", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", "mov", "-f", "mov", "-enable_drefs", "0",
        "-use_absolute_path", "0", "-i", str(source), *arguments,
    ]
    try:
        process = subprocess.run(command, cwd=source.parent, stdin=subprocess.DEVNULL,
                                 capture_output=True, timeout=MEDIA_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise WorkflowError("本机视频解码超时，请先剪出较短片段。") from None
    except OSError:
        raise WorkflowError("无法启动本机 FFmpeg，请检查 D&T 媒体运行环境。") from None
    diagnostics = process.stderr.decode("utf-8", "replace")
    if process.returncode and not (probe and diagnostics.rstrip().endswith("At least one output file must be specified")):
        raise WorkflowError("视频无法解码，请确认文件可以播放并重新导出标准 MP4。")
    return diagnostics


def _sampling_plan(project, duration):
    # Reuse project timing (0/1/2/3s, regular interval and tail). The old CLI
    # uses autodetection and unbounded subprocesses, so do not pass uploads to it.
    script = project / "scripts" / "prepare_video_evidence.py"
    spec = spec_from_file_location("dt_prepare_video_evidence", script)
    if spec is None or spec.loader is None or not script.is_file():
        raise WorkflowError("缺少项目原有定时采样脚本，请恢复 D&T 媒体运行环境。")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.frame_times(duration, INTERVAL_SECONDS)


def _frames(ffmpeg, source, output, targets):
    # Select the first decoded frame crossing each target, once per frame.
    # On low frame-rate media one actual frame may cross several targets.
    gates = "+".join(f"gte(t,{target:.3f})*not(gte(prev_selected_t,{target:.3f}))" for target in targets)
    filters = (f"setpts=PTS-STARTPTS,select='{gates}',"
               "scale=640:360:force_original_aspect_ratio=decrease:force_divisible_by=2,showinfo")
    diagnostics = _ffmpeg(ffmpeg, source, [
        "-map", "0:V:0", "-an", "-sn", "-dn", "-vf", filters, "-fps_mode", "passthrough",
        "-frames:v", str(len(targets)), "-threads", "1", "-q:v", "3", "-map_metadata", "-1",
        str(output / "frame-%03d.jpg"),
    ])
    files = sorted(output.glob("frame-*.jpg"))
    decoded = FRAME_TIME.findall(diagnostics)
    if not files or len(files) != len(decoded):
        raise WorkflowError("没有取得可核对时间点的画面，请确认 MP4 含可播放的视频轨。")
    rows = []
    remaining = iter(targets)
    target = next(remaining, None)
    for index, (path, (sequence, timestamp)) in enumerate(zip(files, decoded)):
        seconds = float(timestamp)
        if (int(sequence) != index or not math.isfinite(seconds) or seconds < 0
                or (rows and seconds <= rows[-1]["actual_seconds"])):
            raise WorkflowError("视频帧时间异常，请重新导出原片。")
        matched = []
        while target is not None and seconds + 0.00005 >= target:
            matched.append(target)
            target = next(remaining, None)
        rows.append({"id": f"F{index + 1:03d}", "file": path.name, "actual_seconds": seconds,
                     "target_seconds": matched, "sha256": _digest(path)})
    return rows, [] if target is None else [target, *remaining]


def _contact_sheet(output, rows):
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError:
        raise WorkflowError("缺少本地证据图依赖 Pillow，请检查 D&T 媒体运行环境。") from None
    sheet = Image.new("RGB", (1000, ((len(rows) + 3) // 4) * 175), "#e9edf2")
    draw = ImageDraw.Draw(sheet)
    for index, frame in enumerate(rows):
        x, y = index % 4 * 250 + 5, index // 4 * 175 + 7
        draw.text((x, y), f"{frame['id']}  {frame['actual_seconds']:.3f}s", fill="#182c3b")
        with Image.open(output / frame["file"]) as source:
            sheet.paste(ImageOps.contain(source.convert("RGB"), (240, 145)), (x, y + 19))
    sheet.save(output / "contact-sheet.png")


def _digest(path):
    with path.open("rb") as handle:
        return file_digest(handle, "sha256").hexdigest()


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _sections(rows, duration):
    boundaries = (0, 3, 8, 15)
    sections = []
    for index, start in enumerate(boundaries):
        if start >= duration:
            break
        end = min(boundaries[index + 1], duration) if index + 1 < len(boundaries) else duration
        sections.append({"start": start, "end": end,
                         "frames": [item["id"] for item in rows if start <= item["actual_seconds"] < end]})
    return sections


def _report(result):
    opening = result["sections"][0]
    hook_frames = "、".join(opening["frames"]) or "未取到"
    series = [
        f"- {section['start']:.3f}–{section['end']:.3f}s："
        f"画面 {'、'.join(section['frames']) or '未抽到该段'}。"
        for section in result["sections"]
    ]
    cn = result["site"] == "CN"
    return "\n".join([
        f"# {SITES[result['site']][0]}短视频拆解证据",
        "", f"素材 SHA-256：{result['source_sha256']}。"
        f"时长 {result['duration_seconds']:.3f}s，采样帧 {result['frame_count']} 张。",
        f"素材性质：{'合成测试素材' if result['synthetic_material'] else '用户提供素材'}。"
        f"原语音状态：{result['audio']['review_message']}",
        "", "## 一、前 3 秒钩子",
        f"对应画面 {hook_frames}。回原片核对第一处主张、动作及听感，机制和台词待复核。",
        "", "## 二、内容结构", *series,
        "", "## 三、要素提取",
        "先给画面或原声标注时间点，再分别登记钩子、卖点、演示、信任、促单和情绪。可复制度待审。",
        "", "## 四、转化链路",
        ("待核推荐或搜索入口、同作品商品入口、目标商品 ID 和商品卡承接。"
         if cn else "待核推荐或达人入口、同视频商品关系、结账方式与 COD 适用性。"),
        "", "## 五、量化归因",
        "未提供同账号基线、播放、完播、点击、成交或分母。保留未知，暂不归因为爆款或广告增量。",
        "", "## 六、可复用测试",
        ("编导以同产品和同主体内容为对照，仅变更首 3 秒钩子，预先约定留存与点击口径。"
         if cn else "先由菲律宾语境审阅者确认语言、场景和促单表达，完成四档本地化评估后再比较单一钩子变化。"),
        "", "## 七、风险复核",
        "抽样帧之间的动作、商品身份、音画冲突和数字需回原片逐项确认。"
        "画面时点对首个解码帧，字幕时点对抽取音轨，未验证音画偏移。",
        "", "## 八、归档",
        "结果 JSON 保存画面编号、实际解码时间与哈希。档案状态为本地拆解证据待审。",
    ]) + "\n"


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress):
    project, task_dir = Path(project).resolve(), Path(task_dir).resolve()
    try:
        filename, source = _validate(site, tool_id, payload, task_dir, assets)
        ffmpeg = _find_ffmpeg(project)
        progress("正在核对视频并准备带时间点的抽样证据。")
        probe = _ffmpeg(ffmpeg, source, [], probe=True)
        tracks = [track for track in VIDEO.findall(probe) if "attached pic" not in track.lower()]
        match = DURATION.search(probe)
        if not tracks or not match:
            raise WorkflowError("上传文件没有可用的视频轨及完整时长。")
        hours, minutes, seconds = map(float, match.groups())
        duration = hours * 3600 + minutes * 60 + seconds
        if not 0 < duration <= MAX_DURATION:
            raise WorkflowError("视频时长须在 60 秒以内，请先剪出所需片段。")
        output = task_dir / "teardown"
        output.mkdir()
        rows, missed = _frames(ffmpeg, source, output, _sampling_plan(project, duration))
        _contact_sheet(output, rows)

        has_audio = bool(AUDIO.search(probe))
        language = payload["transcription_language"]
        audio = {"track_present": has_audio, "requested_language": language, "status": "not_requested",
                 "review_message": "未请求转写。", "independent_accuracy_review": False,
                 "video_alignment_verified": False, "segments": []}
        files = {"json": output / "evidence.json", "report": output / "report.md",
                 "contact": output / "contact-sheet.png", "receipt": output / "receipt.json"}
        if not has_audio:
            audio.update(status="no_audio_track", review_message="未发现音轨。")
        elif language is not None:
            try:
                transcript, transcript_files = run_transcription(project, task_dir, source, language, progress)
                files["srt"] = transcript_files["srt"]
                audio.update(status="machine_draft", review_message="本地模型已生成字幕草稿，待人工听审。",
                             detected_language=transcript.get("detected_language"),
                             time_reference="extracted_audio_origin",
                             segments=[{key: segment[key] for key in ("start", "end", "text")}
                                       for segment in transcript["segments"]],
                             transcript_receipt_sha256=_digest(transcript_files["receipt"]))
            except MediaError as error:
                audio.update(status="transcription_unavailable", review_message=str(error))

        progress("正在保存抽样证据、分段报告和核对回执。")
        platform = "douyin" if site == "CN" else "tiktok"
        result = {
            "tool_id": TOOL_ID, "site": site, "source_name": filename, "source_sha256": _digest(source),
            "source_kind_declared": payload["source_kind"], "publication_verified": False,
            "synthetic_material": payload["synthetic_material"], "duration_seconds": duration,
            "frame_count": len(rows), "sample_times_not_reached": missed, "frames": rows, "audio": audio,
            "frame_time_reference": "first_decoded_video_frame",
            "video_track_count": len(tracks), "sections": _sections(rows, duration),
            "review_state": "evidence_ready_review_pending",
            "product_id": None, "product_entry": None, "causal_attribution": None,
            "metrics": dict.fromkeys(("views", "completion_rate", "click_rate", "conversion_rate", "gmv")),
            "role": f"{platform}-video-teardown",
            "sources": [f"runtime-agents/{platform}-video-teardown/agent.md",
                        f".agents/skills/{platform}-video-teardown-playbook/SKILL.md",
                        "templates/拆解分析/爆款拆解报告模板.md", "scripts/prepare_video_evidence.py"],
        }
        _json(files["json"], result)
        files["report"].write_text(_report(result), encoding="utf-8")
        _json(files["receipt"], {
            "created_at_utc": datetime.now(timezone.utc).isoformat(), "source_sha256": result["source_sha256"],
            "status": result["review_state"], "audio_status": audio["status"], "media_uploaded": False,
            "external_api_called": False, "sampling": "local FFmpeg MOV decoder, first frame crossing each target",
            "artifacts": {kind: {"file": path.relative_to(task_dir).as_posix(), "sha256": _digest(path)}
                          for kind, path in files.items() if kind != "receipt"},
        })
        return result, files
    except MediaError as error:
        raise WorkflowError(str(error)) from None
    except OSError:
        raise WorkflowError("无法读取素材或保存证据。请检查工作区权限、剩余空间或重新发起任务。") from None

