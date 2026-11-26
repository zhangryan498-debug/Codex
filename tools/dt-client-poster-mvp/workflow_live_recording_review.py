"""Bind a bounded local recording to review cues without determining violations."""

from decimal import Decimal, InvalidOperation
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import subprocess

from role_tools import ROUTES, SYNTHETIC, RoleToolError, _object, _validate_json
from role_tools_extra import _load


class WorkflowError(ValueError):
    """An invalid or unverifiable local recording intake."""


TOOL = "live-recorded-evidence"
FILE = "recording-review.json"
LOCAL_RECORDING = "declared_local_review"
MAX_BYTES = 16 * 1024 * 1024
MAX_DURATION = Decimal("180")
PROBE_SHA256 = "f0d36ecbbdd3bcfac3efa078c96c7271c2e68b3810595552ac3b7f17e9a65c52"
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
SHA = re.compile(r"[a-f0-9]{64}")
SCOPES = {"brand_identity_authorization", "price", "performance_claim", "off_platform", "other"}
SPEAKERS = {"host", "viewer", "unknown"}
CONTEXTS = {"affirmed", "quoted", "negated", "promotional", "unknown"}


def _example(site):
    platform, _ = ROUTES[site]
    return {
        "site": site, "scope": SYNTHETIC,
        "source": {"reference": "FIXTURE-001", "platform": platform, "country": site,
                   "recording_sha256": "0" * 64, "local_use": "synthetic_fixture"},
        "segment": {"start_seconds": 0, "end_seconds": 1},
        "observation": {"visual_observed": True, "speech_observed": False,
                        "visual_status": "insufficient", "relation": "unknown"},
        "alerts": [{"id": "CUE-001", "scope": "brand_identity_authorization", "independent_risk": False,
                    "speaker": "unknown", "context": "unknown"}],
    }


def public_tools():
    return [{
        "id": TOOL, "sites": list(ROUTES), "role_suffix": "live-ops", "label": "直播录屏片段与风险线索归档",
        "description": "保存短片段的文件、时间范围和人工整理的线索。只用有本机审核许可的无敏感片段，统一命名 recording.mp4。保留人工复核，不判断平台违规。",
        "max_media_files": 1, "media_required": True, "max_media_bytes": MAX_BYTES,
        "input_example": _example("CN"), "input_examples": {site: _example(site) for site in ROUTES},
    }]


def _fields(value, keys, label):
    try:
        _object(value, label, keys)
    except ValueError:
        raise WorkflowError(f"{label}字段不符合合约。") from None
    if set(value) != keys:
        raise WorkflowError(f"{label}字段不完整。")
    return value


def _ref(value):
    return type(value) is str and bool(REF.fullmatch(value))


def _duration(raw):
    try:
        value = Decimal(str(raw))
    except (ValueError, InvalidOperation):
        raise WorkflowError("录屏时间无法核验。") from None
    if not value.is_finite() or not 0 <= value <= MAX_DURATION:
        raise WorkflowError("仅支持最长 180 秒的本机短片段。长录屏请先在本机截取许可区间。")
    return value


def _probe(project, asset):
    binary = Path(project) / "work" / "openmontage-runtime" / "bin" / "ffprobe.exe"
    try:
        with binary.open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != PROBE_SHA256:
                raise WorkflowError("项目固定版本的视频探测工具校验失败。")
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE"}}
        completed = subprocess.run([
            str(binary), "-v", "error", "-protocol_whitelist", "file",
            "-enable_drefs", "0", "-use_absolute_path", "0",
            "-f", "mov", "-show_entries", "format=duration:stream=codec_type,width,height",
            "-of", "json", "-i", str(asset),
        ], stdin=subprocess.DEVNULL, capture_output=True, timeout=10, env=env, check=True)
        decoded = json.loads(completed.stdout)
        streams = decoded["streams"]
        videos = [r for r in streams if r.get("codec_type") == "video" and r.get("width", 0) > 0 and r.get("height", 0) > 0]
        if not videos or not _duration(decoded["format"]["duration"]):
            raise WorkflowError("文件缺少有效视频流或时长。")
        return {"duration_seconds": float(_duration(decoded["format"]["duration"])),
                "video_streams": len(videos),
                "audio_streams": sum(item.get("codec_type") == "audio" for item in streams)}
    except (FileNotFoundError, PermissionError):
        raise WorkflowError("本机固定版本的视频探测工具缺失或不可读。") from None
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError, KeyError, TypeError) as error:
        if isinstance(error, WorkflowError):
            raise
        raise WorkflowError("无法在本机读取 MP4 的视频流及完整时长。") from None


def _review(payload, site):
    platform, _ = ROUTES[site]
    _fields(payload, {"site", "scope", "source", "segment", "observation", "alerts"}, "录屏检查输入")
    if payload["site"] != site or payload["scope"] not in {SYNTHETIC, LOCAL_RECORDING}:
        raise WorkflowError("工作台站点与输入站点须一致，并选择本机合成或声明本机审核范围。")
    source = _fields(payload["source"], {"reference", "platform", "country", "recording_sha256", "local_use"}, "录屏来源")
    if (source["platform"], source["country"]) != (platform, site):
        raise WorkflowError("录屏来源的平台或站点与任务不一致。")
    if not _ref(source["reference"]) or type(source["recording_sha256"]) is not str or not SHA.fullmatch(source["recording_sha256"]):
        raise WorkflowError("请填写来源编号和原文件 SHA-256。来源编号仅使用字母、数字、减号或下划线。")
    expected_use = "synthetic_fixture" if payload["scope"] == SYNTHETIC else "permission_declared_deidentified_local_only"
    if source["local_use"] != expected_use:
        raise WorkflowError("缺少符合范围的许可和脱敏声明，暂不分析。")
    span = _fields(payload["segment"], {"start_seconds", "end_seconds"}, "时段")
    if any(type(value) not in (int, float) for value in span.values()):
        raise WorkflowError("起止时间须填写视频内的秒数。")
    if _duration(span["start_seconds"]) >= _duration(span["end_seconds"]):
        raise WorkflowError("录屏终点必须晚于起点。")
    obs = payload["observation"]
    if obs is not None:
        _fields(obs, {"visual_observed", "speech_observed", "visual_status", "relation"}, "结构化人工线索")
        if (type(obs["visual_observed"]) is not bool or type(obs["speech_observed"]) is not bool
                or obs["visual_status"] not in {"sufficient", "insufficient", "conflicting"}
                or obs["relation"] not in {"consistent", "inconsistent", "unknown"}):
            raise WorkflowError("请使用约定的画面、口播和品牌关系枚举。")
    alerts = payload["alerts"]
    if type(alerts) is not list or len(alerts) > 60:
        raise WorkflowError("请提供最多 60 条结构化线索。")
    seen = set()
    for item in alerts:
        _fields(item, {"id", "scope", "independent_risk", "speaker", "context"}, "线索")
        if (not _ref(item["id"]) or item["id"] in seen or item["scope"] not in SCOPES
                or item["speaker"] not in SPEAKERS or item["context"] not in CONTEXTS
                or (item["independent_risk"] is not None and type(item["independent_risk"]) is not bool)):
            raise WorkflowError("线索编号须唯一，并使用约定的角色、语境和风险枚举。")
        seen.add(item["id"])
    return source, span, obs, alerts


def validate_submission(payload, site, selected_media):
    """Reject cross-site, metadata and hash mismatches before persisting attachments."""
    try:
        _validate_json(payload)
        source, _, _, _ = _review(payload, site)
        if len(selected_media) != 1 or selected_media[0]["name"] != "recording.mp4":
            raise WorkflowError("请选择一份命名为 recording.mp4 的本机片段。")
        if selected_media[0]["sha256"] != source["recording_sha256"]:
            raise WorkflowError("上传文件与录屏来源的 SHA-256 不一致。")
    except RoleToolError as error:
        raise WorkflowError(str(error)) from None
    except (TypeError, KeyError) as error:
        raise WorkflowError("录屏或线索字段类型不符合合约。") from error


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress):
    if tool_id != TOOL:
        raise WorkflowError("当前工具无法处理此任务。")
    try:
        source, span, obs, alerts = _review(payload, site)
        asset = assets.get("recording.mp4")
        if not asset or asset.stat().st_size > MAX_BYTES:
            raise WorkflowError("缺少符合大小限制的本机录屏片段。")
        with asset.open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != source["recording_sha256"]:
                raise WorkflowError("本机录屏与申请时的来源哈希不一致。")
        progress("正在本机核对录屏容器与指明的时段。")
        probe = _probe(project, asset)
        if _duration(span["end_seconds"]) > _duration(probe["duration_seconds"]):
            raise WorkflowError("所填直播时段超出了实际录屏时长。")
        if obs and obs["speech_observed"] and not probe["audio_streams"]:
            raise WorkflowError("文件没有音轨，口播观察尚无法绑定此证据。")
        scope = {"platform": source["platform"], "country": site,
                 "recording_sha256": source["recording_sha256"], **span}
        simulated = payload["scope"] == SYNTHETIC
        evidence = ({**scope, **obs, "origin": "human_local"} if simulated and obs else None)
        policy = _load(Path(project), "live_brand_visual_silence_v2")
        policy_alerts = [
            ({**item, "independent_risk": None}
             if item["independent_risk"] is False and (item["speaker"], item["context"]) != ("host", "affirmed")
             else item)
            for item in alerts
        ]
        checked = policy.evaluate(scope, evidence, policy_alerts)
        result = {
            "task_id": Path(task_dir).name, "tool_id": TOOL, "site": site, "scope": payload["scope"],
            "source_reference": source["reference"], "source_identity": scope, "container": probe,
            "file_sha256_verified": True, "observation_is_simulated": simulated,
            "observation_status": "submitted_for_review" if obs else "not_supplied",
            "declared_observation": obs, "declared_cues": alerts,
            "rights_independently_verified": False, "semantic_accuracy_verified": False,
            "human_review_required": True, "official_enforcement_evaluated": False,
            "external_calls": 0, "media_exported": False, "model_called": False,
            "result": checked,
        }
        if not simulated:
            result["observation_status"] = "awaiting_independent_review"
        output = Path(task_dir) / FILE
        with output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        return result, {"json": output}
    except WorkflowError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as error:
        raise WorkflowError("录屏素材或线索无法核验。") from error


def verify_saved(task, data):
    saved = json.loads(data)
    if (saved != task["result"] or saved["task_id"] != task["id"]
            or saved["site"] != task["site"] or saved["tool_id"] != TOOL
            or saved["source_identity"]["recording_sha256"] != task["source"]["assets"][0]["sha256"]):
        raise WorkflowError("本机录屏审核文件无法核验。")
