"""Prepare and execute a user-specified montage through D&T's local renderer."""

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from zipfile import ZIP_STORED, ZipFile


class WorkflowError(ValueError):
    """Input or local runtime problem that a customer can resolve."""


TOOL_ID = "director-local-montage"
VERSIONS_TOOL = "cn-director-version-delivery"
VERSIONS_FILES = {"json": "result.json", "director_bundle": "director-versions.zip"}
VERSION_DESTINATIONS = {
    "paid_creative": "douyin-ad-buyer",
    "livestream_teaser": "douyin-live-ops",
    "livestream_clip": "douyin-live-ops",
    "campaign_creative": "douyin-campaign-ops",
}
CN_SCHEDULE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+08:00")
PLATFORMS = {"CN": "douyin", "PH": "tiktok"}
LANGUAGES = {"CN": ("zh",), "PH": ("fil", "en", "taglish")}
PRODUCT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
DRIVE_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])", re.IGNORECASE)
FLAT_MP4 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.mp4", re.IGNORECASE)
MAX_MEDIA = 8
MAX_SOURCE_BYTES = 24 * 1024 * 1024
MAX_LENGTH_MS = 120_000


def _example(site):
    captions = ["合成素材 · 开场", "合成素材 · 细节", "合成素材 · 收尾"] if site == "CN" else [
        "Panimula (simulasyon)", "Detalye (simulasyon)", "Pangwakas (simulasyon)",
    ]
    clips = ["overview.mp4", "detail.mp4", "hero.mp4"]
    product_id = f"DEMO-{site}-DIRECTOR"
    return {
        "site": site,
        "publish_product_id": product_id,
        "reference_product_id": None,
        "language": LANGUAGES[site][0],
        "synthetic_material": True,
        "assets": [
            {"file": name, "rights": "authorized", "product_id": product_id} for name in clips
        ],
        "cuts": [{"file": name, "in_seconds": 0, "out_seconds": 1} for name in clips],
        "captions": [
            {"start_seconds": index, "end_seconds": index + 0.7, "text": caption}
            for index, caption in enumerate(captions)
        ],
    }


def public_tools():
    return [{
        "id": TOOL_ID,
        "label": "按时间线剪辑本地视频",
        "role_suffix": "video-director",
        "sites": list(PLATFORMS),
        "input_example": _example("CN"),
        "input_examples": {site: _example(site) for site in PLATFORMS},
        "description": (
            "上传已获授权且属于同一商品的 MP4，按示例填写入出点与字幕。"
            "示例需上传 overview.mp4、detail.mp4、hero.mp4；仅在本机导出竖版 MP4、时间线与回执。"
        ),
        "max_media_files": MAX_MEDIA,
    }, {
        "id": VERSIONS_TOOL,
        "label": "抖音多版本剪辑与本机交付",
        "role_suffix": "video-director",
        "sites": ["CN"],
        "input_example": _version_example(),
        "description": "上传同一商品的本地素材，按用途与时区排期剪辑 15、30、60 秒版本。交付各版视频、字幕、时间线和来源回执。",
        "execution_scope": "仅本机交付包。素材授权、活动政策及下游签收需另行核实。",
        "max_media_files": MAX_MEDIA,
    }]


def _text(value, max_length):
    if not isinstance(value, str):
        return False
    if not 1 <= len(value) <= max_length or not value.strip():
        return False
    return not any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise WorkflowError(f"{label}字段不完整或含有未支持的字段，请参照输入示例。")


def _sequence(value, maximum, label):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise WorkflowError(f"{label}请填写 1 至 {maximum} 条。")
    return value


def _millis(value):
    if type(value) not in (int, float):
        raise WorkflowError("时间必须以数字填写，单位为秒。")
    try:
        ms = Decimal(str(value)) * 1000
        if not ms.is_finite() or not 0 <= ms <= 86_400_000 or ms != ms.to_integral_value():
            raise WorkflowError("时间须在 0 到 86400 秒内，最多保留三位小数。")
        return int(ms)
    except InvalidOperation as error:
        raise WorkflowError("时间必须为有效的非负秒数。") from error


def _srt_timestamp(ms):
    total_seconds, ms = divmod(ms, 1000)
    total_minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def _digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _plan(site, payload, uploads):
    required = {
        "site", "publish_product_id", "language", "synthetic_material", "assets", "cuts", "captions",
    }
    if isinstance(payload, dict) and "reference_product_id" in payload:
        required.add("reference_product_id")
    _object(payload, required, "剪辑需求")
    if payload["site"] != site or not isinstance(payload["language"], str) or payload["language"] not in LANGUAGES[site]:
        raise WorkflowError("请使用当前任务站点与其支持的字幕语言。")
    product = payload["publish_product_id"]
    reference = payload.get("reference_product_id")
    if not isinstance(product, str) or not PRODUCT_ID.fullmatch(product):
        raise WorkflowError("请填写我方商品编号，使用字母、数字、连字符或下划线。")
    if reference is not None and (
        not isinstance(reference, str) or not PRODUCT_ID.fullmatch(reference) or reference == product
    ):
        raise WorkflowError("参考商品编号须与我方商品编号分开，或填写 null。")
    if type(payload["synthetic_material"]) is not bool:
        raise WorkflowError("请注明是否使用合成素材。")

    manifest = {}
    for index, item in enumerate(_sequence(payload["assets"], MAX_MEDIA, "素材"), 1):
        _object(item, {"file", "rights", "product_id"}, "素材")
        name = item["file"]
        if (not isinstance(name, str) or not FLAT_MP4.fullmatch(name)
                or DRIVE_DEVICE.fullmatch(Path(name).stem) or name not in uploads
                or name.casefold() in {entry.casefold() for entry in manifest}):
            raise WorkflowError("素材必须是已上传、名称不重复的 MP4。文件名仅使用字母、数字、连字符或下划线。")
        if item["rights"] not in ("owned", "authorized") or item["product_id"] != product:
            raise WorkflowError("请确认每份素材的使用权，且素材与我方同一商品编号对应。")
        manifest[name] = {
            "asset_id": f"clip-{index:02}", "path": f"clip-{index:02}.mp4",
            "rights": item["rights"], "product_id": product,
        }
    if set(manifest) != set(uploads):
        raise WorkflowError("请在素材清单逐一列出上传的 MP4，或移除多余文件。")

    total_ms, cuts = 0, []
    for item in _sequence(payload["cuts"], 24, "剪辑镜头"):
        _object(item, {"file", "in_seconds", "out_seconds"}, "镜头")
        name = item["file"]
        if not isinstance(name, str) or name not in manifest:
            raise WorkflowError("剪辑镜头引用了未声明的素材。")
        start = _millis(item["in_seconds"])
        end = _millis(item["out_seconds"])
        if end - start < 100:
            raise WorkflowError("每个入出点片段至少为 0.1 秒，出点须在入点之后。")
        total_ms += end - start
        if total_ms > MAX_LENGTH_MS:
            raise WorkflowError("成片计划总长最多为 120 秒。")
        cuts.append({"source_asset_id": manifest[name]["asset_id"],
                     "in_seconds": start / 1000, "out_seconds": end / 1000})
    if {cut["source_asset_id"] for cut in cuts} != {item["asset_id"] for item in manifest.values()}:
        raise WorkflowError("请为每份声明的素材填写对应镜头，或移除未用素材。")

    captions, previous_end = [], 0
    for index, item in enumerate(_sequence(payload["captions"], 120, "字幕"), 1):
        _object(item, {"start_seconds", "end_seconds", "text"}, "字幕")
        start = _millis(item["start_seconds"])
        end = _millis(item["end_seconds"])
        if not previous_end <= start < end <= total_ms:
            raise WorkflowError("字幕须在成片时间内按序填写，区间不可重叠。")
        caption = item["text"]
        if not _text(caption, 80) or set(caption) & set("{}<>\\") or "-->" in caption:
            raise WorkflowError("字幕使用 1 至 80 个字符的单行纯文本，不填写控制符或排版标记。")
        captions.append(f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{caption}")
        previous_end = end
    job = {
        "production_route": "real_footage_auto_edit", "platform": PLATFORMS[site], "site": site,
        "publish_product_id": product, "reference_product_id": reference,
        "cuts": cuts, "subtitle_path": "captions.srt",
    }
    return job, manifest, "\n\n".join(captions) + "\n", previous_end


def _copy_assets(uploads, manifest, source_dir):
    for name, item in manifest.items():
        source = Path(uploads[name])
        if not source.is_file() or not 12 <= source.stat().st_size <= MAX_SOURCE_BYTES:
            raise WorkflowError("每份素材须为可读的本机 MP4，且不超过 24 MiB。")
        with source.open("rb") as stream:
            header = stream.read(12)
        if header[4:8] != b"ftyp":
            raise WorkflowError("素材内容无法识别为 MP4，请重新导出标准 MP4 后上传。")
        target = source_dir / item["path"]
        shutil.copyfile(source, target)
        item["sha256"] = _digest(target)


def _renderer_error(stderr):
    message = stderr.casefold()
    if "missing or sha-256 mismatch" in message or "permissionerror" in message or "winerror 5" in message:
        return "本机固定版本的 OpenMontage 或 FFmpeg 缺失、无读取权限或校验失败。请检查项目运行环境。"
    if "cut out of bounds" in message:
        return "镜头出点超过了原片时长，请核对对应秒数。"
    if any(code in message for code in ("ffprobe:", "invalid source duration", "expected local mp4")):
        return "素材无法读取为有效 MP4，请核对视频流与时长，必要时重新导出。"
    return "本机合成未通过。请核对视频能否播放、字幕与时间线，再查看可用磁盘空间。"


def _run_montage(project, site, tool_id, payload, task_dir, assets, progress):
    if site not in PLATFORMS or tool_id != TOOL_ID:
        raise WorkflowError("本地剪辑仅支持抖音中国和 TikTok 菲律宾任务。")
    job, manifest, srt, caption_end = _plan(site, payload, assets)
    project = Path(project).resolve()
    task_dir = Path(task_dir).resolve()
    renderer = project / "scripts" / "render_real_footage.py"
    runtime = project / "work" / "openmontage-runtime"
    try:
        ready = renderer.is_file() and (runtime / "bin" / "ffmpeg.exe").is_file()
    except PermissionError:
        ready = False
    if not ready:
        raise WorkflowError("找不到可读取的 D&T OpenMontage 运行环境，请检查配套项目或文件访问权限。")
    if "'" in str(task_dir):
        raise WorkflowError("当前剪辑引擎的本地工作目录不能含英文单引号，请选择其他工作目录。")
    source_dir, dest = task_dir / "montage-source", task_dir / "montage"
    try:
        source_dir.mkdir()
        _copy_assets(assets, manifest, source_dir)
        job["asset_manifest"] = list(manifest.values())
        (source_dir / "captions.srt").write_text(srt, encoding="utf-8")
        job_path = source_dir / "job.json"
        _write_json(job_path, job)
        progress("正在按时间线合成视频，并校验输出能否解码。")
        env_names = {"PATH", "PATHEXT", "SYSTEMDRIVE", "SYSTEMROOT", "WINDIR"}
        env = {key: value for key, value in os.environ.items() if key.upper() in env_names}
        env.update(TEMP=str(source_dir), TMP=str(source_dir), OPENMONTAGE_ASSET_CACHE="0")
        command = [sys.executable, "-I", "-B", "-X", "utf8", "-c",
                   "import runpy,sys; sys.path.insert(0,sys.argv[1]);"
                   "sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')",
                   str(renderer.parent), str(renderer), str(job_path), "--output-dir", str(dest)]
        checked = subprocess.run(command, cwd=source_dir, env=env,
                                 stdin=subprocess.DEVNULL, capture_output=True,
                                 encoding="utf-8", errors="replace", timeout=360,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise WorkflowError("本机合成超时。请缩短待剪视频，或减少本次镜头数量。") from None
    except OSError:
        raise WorkflowError("无法读取素材、启动本机剪辑或保存交付，请检查文件权限和剩余空间。") from None
    if checked.returncode:
        raise WorkflowError(_renderer_error(checked.stderr))

    paths = {
        "video": dest / "montage.mp4", "receipt": dest / "render_receipt.json",
        "srt": dest / "captions.srt", "timeline": task_dir / "timeline.json",
        "json": task_dir / "result.json",
    }
    try:
        receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
        duration_ms = int((Decimal(str(receipt["duration"])) * 1000).to_integral_value(ROUND_DOWN))
        if (receipt["site"] != site or receipt["platform"] != PLATFORMS[site]
                or receipt["decode"] != "passed" or receipt["size"] != [1080, 1920]
                or receipt["video_sha256"] != _digest(paths["video"])
                or receipt["subtitle_sha256"] != _digest(paths["srt"])):
            raise WorkflowError("输出回执与成片或字幕不符，请检查本机剪辑环境。")
        if caption_end > duration_ms:
            raise WorkflowError("末条字幕超出实际导出长度，请提前字幕结束点后再提交。")
        names = {item["asset_id"]: name for name, item in manifest.items()}
        timeline = [
            {**shot, "uploaded_file": names[shot["source_asset_id"]]}
            for shot in receipt["timeline"]
        ]
        _write_json(paths["timeline"], {"site": site, "cuts": timeline})
        review = ["人工核对镜头与商品是否一致、字幕及原声是否准确、素材使用权是否覆盖用途。"]
        if site == "PH":
            review.append("投用前由熟悉菲律宾语境的人员检查口播、字幕与场景。")
        result = {
            "site": site, "platform": PLATFORMS[site],
            "role": f"{PLATFORMS[site]}-video-director", "tool_id": tool_id,
            "publish_product_id": job["publish_product_id"],
            "reference_product_id": job["reference_product_id"],
            "language": payload["language"], "synthetic_material": payload["synthetic_material"],
            "technical_result": "RENDERED_AND_DECODED", "manual_review": "PENDING",
            "duration_seconds": receipt["duration"], "size": receipt["size"],
            "video_sha256": receipt["video_sha256"], "subtitle_sha256": receipt["subtitle_sha256"],
            "timeline": timeline, "next_steps": review,
        }
        _write_json(paths["json"], result)
    except (OSError, KeyError, TypeError, InvalidOperation, ArithmeticError, json.JSONDecodeError):
        raise WorkflowError("无法读回合成文件或验收回执，请检查本机剪辑环境。") from None
    return result, paths


def _version_example():
    return {
        "site": "CN",
        "publish_product_id": "DEMO-CN-DIRECTOR-VERSIONS",
        "language": "zh",
        "synthetic_material": True,
        "assets": [{
            "file": "product-demo.mp4",
            "rights": "owned",
            "product_id": "DEMO-CN-DIRECTOR-VERSIONS",
            "rights_evidence_ref": "LOCAL-SYNTHETIC-FIXTURE",
            "allowed_purposes": list(VERSION_DESTINATIONS),
        }],
        "versions": [
            {
                "duration_seconds": seconds,
                "purposes": purposes,
                "scheduled_at": f"2026-12-01T{hour}:00:00+08:00",
                "campaign_policy_ref": "SIMULATED-CASE" if "campaign_creative" in purposes else None,
                "cuts": [{"file": "product-demo.mp4", "in_seconds": 0, "out_seconds": seconds}],
                "captions": [{"start_seconds": 0, "end_seconds": 2, "text": "合成素材演示"}],
            }
            for seconds, purposes, hour in (
                (15, ["paid_creative", "livestream_teaser"], "12"),
                (30, ["paid_creative", "livestream_clip"], "16"),
                (60, ["paid_creative", "campaign_creative"], "20"),
            )
        ],
    }


def _plan_versions(site, payload, uploads):
    fields = {"site", "publish_product_id", "language", "synthetic_material", "assets", "versions"}
    if isinstance(payload, dict) and "reference_product_id" in payload:
        fields.add("reference_product_id")
    _object(payload, fields, "多版本任务")
    if site != "CN" or payload["site"] != "CN":
        raise WorkflowError("多版本剪辑入口仅支持抖音中国站。")
    assets = {}
    for row in _sequence(payload["assets"], MAX_MEDIA, "素材"):
        _object(row, {"file", "rights", "product_id", "rights_evidence_ref", "allowed_purposes"}, "素材")
        name = row["file"]
        if not isinstance(name, str) or name not in uploads or name.casefold() in assets:
            raise WorkflowError("素材名称必须与上传文件一一对应，不得重复。")
        if not _text(row["rights_evidence_ref"], 180) or row["rights"] not in ("owned", "authorized"):
            raise WorkflowError("素材须有明确的用途授权声明和可追溯来源编号。")
        allowed = _sequence(row["allowed_purposes"], len(VERSION_DESTINATIONS), "素材许可用途")
        if any(not isinstance(use, str) or use not in VERSION_DESTINATIONS for use in allowed):
            raise WorkflowError("素材许可用途包含不支持的值。")
        if len(set(allowed)) != len(allowed):
            raise WorkflowError("素材许可用途不得重复。")
        assets[name.casefold()] = row
    if {row["file"] for row in assets.values()} != set(uploads):
        raise WorkflowError("已上传素材必须全部列入源素材清单。")

    versions = _sequence(payload["versions"], 3, "剪辑版本")
    if len(versions) < 2:
        raise WorkflowError("多版本任务至少需要两个不同时长，可选 15、30 或 60 秒。")
    seen, planned, used = set(), [], set()
    common = {key: value for key, value in payload.items() if key not in {"assets", "versions"}}
    for row in versions:
        _object(row, {"duration_seconds", "purposes", "scheduled_at", "campaign_policy_ref",
                      "cuts", "captions"}, "剪辑版本")
        duration = row["duration_seconds"]
        if type(duration) is not int or duration not in (15, 30, 60) or duration in seen:
            raise WorkflowError("剪辑版本的时长只能是 15、30 或 60 秒，各时长只能出现一次。")
        seen.add(duration)
        purposes = _sequence(row["purposes"], len(VERSION_DESTINATIONS), "用途")
        if any(not isinstance(use, str) or use not in VERSION_DESTINATIONS for use in purposes):
            raise WorkflowError("版本用途不属于抖音中国站的本机交接范围。")
        if len(set(purposes)) != len(purposes):
            raise WorkflowError("版本用途不得重复。")
        schedule = row["scheduled_at"]
        if not isinstance(schedule, str) or not CN_SCHEDULE.fullmatch(schedule):
            raise WorkflowError("请输入带 +08:00 时区的本机排期时间。")
        try:
            datetime.fromisoformat(schedule)
        except ValueError:
            raise WorkflowError("本机排期时间无效。") from None
        policy = row["campaign_policy_ref"]
        if policy is not None and ("campaign_creative" not in purposes or not _text(policy, 180)):
            raise WorkflowError("活动规则来源只用于活动版本，须填写来源编号或 null。")

        names = []
        for cut in _sequence(row["cuts"], 24, "剪辑镜头"):
            _object(cut, {"file", "in_seconds", "out_seconds"}, "镜头")
            name = cut["file"]
            if not isinstance(name, str) or name not in uploads:
                raise WorkflowError("剪辑镜头引用了未上传素材。")
            if name not in names:
                names.append(name)
        allowed_assets = [assets[name.casefold()] for name in names]
        if any(set(purposes) - set(asset["allowed_purposes"]) for asset in allowed_assets):
            raise WorkflowError("剪辑素材原用途授权未覆盖该版本的全部用途。")
        used.update(names)
        selected = {name: uploads[name] for name in names}
        local_payload = {
            **common, "assets": [
                {field: asset[field] for field in ("file", "rights", "product_id")}
                for asset in allowed_assets
            ],
            "cuts": row["cuts"], "captions": row["captions"],
        }
        _plan(site, local_payload, selected)
        if sum(_millis(cut["out_seconds"]) - _millis(cut["in_seconds"]) for cut in row["cuts"]) != duration * 1000:
            raise WorkflowError("镜头总长必须等于该版本申报的时长。")
        planned.append((row, allowed_assets, local_payload, selected))
    if used != set(uploads):
        raise WorkflowError("上传素材清单含未使用文件。")
    return sorted(planned, key=lambda item: item[0]["duration_seconds"])


def _run_versions(project, site, payload, task_dir, uploads, progress):
    planned = _plan_versions(site, payload, uploads)
    root = Path(task_dir)
    paths = {kind: root / name for kind, name in VERSIONS_FILES.items()}
    versions, bundle_files = [], {}
    try:
        for row, sources, local_payload, selected in planned:
            seconds = row["duration_seconds"]
            version_id = f"v{seconds}"
            folder = root / version_id
            folder.mkdir()
            progress(f"正在生成 {seconds} 秒剪辑，核对实际时长和可解码性。")
            result, files = _run_montage(project, site, TOOL_ID, local_payload, folder, selected, progress)
            if abs(result["duration_seconds"] - seconds) > 0.1 + len(local_payload["cuts"]) * 0.08:
                raise WorkflowError(f"{seconds} 秒版本的成片时长超出渲染容差。")
            registered = json.loads(files["receipt"].read_text(encoding="utf-8"))["asset_manifest"]
            media = [{**asset, "sha256": pin["sha256"]} for asset, pin in zip(sources, registered, strict=True)]
            names = {"video": "video.mp4", "srt": "captions.srt", "timeline": "timeline.json", "receipt": "render-receipt.json"}
            entries = {f"{version_id}/{name}": files[key] for key, name in names.items()}
            artifacts = {name: _digest(path) for name, path in entries.items()}
            bundle_files.update(entries)
            versions.append({
                "version_id": version_id, "target_duration_seconds": seconds,
                "rendered_duration_seconds": result["duration_seconds"], "resolution": result["size"],
                "source_assets": media, "artifacts": artifacts,
                "scheduled_at": row["scheduled_at"], "campaign_policy_ref": row["campaign_policy_ref"],
                "handoff": [{"purpose": use, "recipient_role": VERSION_DESTINATIONS[use]}
                            for use in row["purposes"]],
                "campaign_policy_status": ("PENDING" if "campaign_creative" in row["purposes"] else "NOT_APPLICABLE"),
            })
        result = {
            "tool_id": VERSIONS_TOOL, "site": site, "platform": PLATFORMS[site],
            "publish_product_id": payload["publish_product_id"], "language": payload["language"],
            "reference_product_id": payload.get("reference_product_id"),
            "synthetic_material": payload["synthetic_material"], "versions": versions,
            "technical_result": "RENDERED_AND_DECODED", "manual_review": "PENDING",
            "handoff_status": "OFFLINE_PACKAGE_ONLY", "publication_status": "NOT_AUTHORIZED",
            "rights_verification": "DECLARATION_ONLY", "external_calls": 0,
        }
        with ZipFile(paths["director_bundle"], "x", ZIP_STORED) as archive:
            archive.writestr("manifest.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            for item in versions:
                for name, digest in item["artifacts"].items():
                    path = bundle_files[name]
                    if _digest(path) != digest:
                        raise WorkflowError("成片或源档在打包前发生变更。")
                    archive.write(path, name)
        result["bundle_sha256"] = _digest(paths["director_bundle"])
        _write_json(paths["json"], result)
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise WorkflowError("无法完成多版本交付包，请检查源素材和工作区。") from None
    return result, paths


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress):
    if tool_id == VERSIONS_TOOL:
        return _run_versions(project, site, payload, task_dir, assets, progress)
    return _run_montage(project, site, tool_id, payload, task_dir, assets, progress)
