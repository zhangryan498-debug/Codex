"""Receive one verified cut of an offline CN director delivery."""

from hashlib import sha256
from io import BytesIO
import json
from zipfile import BadZipFile, ZIP_STORED, ZipFile

from role_tools import SYNTHETIC
from workflow_director import VERSION_DESTINATIONS, VERSIONS_TOOL
from workflow_lead_weekly import encode
from workflow_live_rehearsal import check, identify, task_id

ROLES = {
    "cn-ads-director-version-intake": ("ad-buyer", "抖音广告接收剪辑版本"),
    "cn-live-director-version-intake": ("live-ops", "抖音直播接收剪辑版本"),
    "cn-campaign-director-version-intake": ("campaign-ops", "抖音活动接收剪辑版本"),
}
TOOLS = tuple(ROLES)
TARGETS = {"v15": 15, "v30": 30, "v60": 60}
MEMBERS = {"video": "video.mp4", "srt": "captions.srt", "timeline": "timeline.json"}
RENDER_RECEIPT = "render-receipt.json"
FILES = {"json": "director-version-intake.json", "receipt": "director-version-intake-receipt.json", **MEMBERS}
STATE = "RECEIVED_OFFLINE_VERSION"
FLAGS = {"independent_recipient_confirmation": False, "actual_rights_verified": False,
         "platform_publication_authorized": False, "real_spend_authorized": False,
         "execution_authorized": False, "provider_called": False, "model_called": False}


class WorkflowError(ValueError):
    pass


def digest(blob):
    return sha256(blob).hexdigest()


def bind(site, tool, p, load_source):
    check(p, "site scope job_id source_task_id product_id version_id purpose recipient_role "
             "bundle_sha256 video_sha256", "短视频版本收件")
    recipient = "douyin-" + ROLES[tool][0]
    if (site, p["site"], p["scope"], p["recipient_role"]) != ("CN", "CN", SYNTHETIC, recipient):
        raise WorkflowError("此入口仅允许本站对应岗位接收离线合成版本。")
    if (not isinstance(p["purpose"], str) or VERSION_DESTINATIONS.get(p["purpose"]) != recipient
            or not isinstance(p["version_id"], str) or p["version_id"] not in TARGETS):
        raise WorkflowError("用途、岗位或版本不匹配。")
    task_id(p["source_task_id"], "编导原交付任务")
    identify(p["job_id"], "收件编号")
    identify(p["product_id"], "原商品编号")
    result_raw, bundle = load_source(p["source_task_id"], site)
    try:
        source = json.loads(result_raw)
        if (source["site"], source["tool_id"], source["publish_product_id"], source["synthetic_material"],
                source["handoff_status"], source["publication_status"]) != (
                "CN", VERSIONS_TOOL, p["product_id"], True, "OFFLINE_PACKAGE_ONLY", "NOT_AUTHORIZED"):
            raise WorkflowError("原任务、商品或本机合成授权范围不匹配。")
        if source["bundle_sha256"] != p["bundle_sha256"]:
            raise WorkflowError("原交付包 SHA-256 不匹配。")
        versions = {row["version_id"]: row for row in source["versions"]}
        if set(versions) != set(TARGETS) or len(source["versions"]) != len(TARGETS):
            raise WorkflowError("原交付包的版本清单不完整。")
        version_id = p["version_id"]
        row = versions[version_id]
        if (row["target_duration_seconds"] != TARGETS[version_id]
                or {"purpose": p["purpose"], "recipient_role": recipient} not in row["handoff"]):
            raise WorkflowError("本版未约定该用途或接收岗位。")
        files = (*MEMBERS.values(), RENDER_RECEIPT)
        selected = {name: f"{version_id}/{name}" for name in files}
        if (set(row["artifacts"]) != set(selected.values())
                or row["artifacts"][selected["video.mp4"]] != p["video_sha256"]):
            raise WorkflowError("指定版本与视频 SHA-256 不匹配。")
        expected = {"manifest.json", *(f"{v}/{name}" for v in TARGETS for name in files)}
        with ZipFile(BytesIO(bundle)) as zipped:
            entries = zipped.infolist()
            if (len(entries) != len(expected) or {e.filename for e in entries} != expected
                    or any(e.compress_type != ZIP_STORED or e.is_dir() for e in entries)):
                raise WorkflowError("原交付包缺文件或包含重复、异常条目。")
            if json.loads(zipped.read("manifest.json")) != {k:v for k,v in source.items() if k != "bundle_sha256"}:
                raise WorkflowError("原交付包清单与工作台结果不符。")
            content = {name: zipped.read(entry) for name, entry in selected.items()}
        if any(digest(content[name]) != row["artifacts"][entry] for name, entry in selected.items()):
            raise WorkflowError("本版原件与出件哈希不匹配。")
    except (KeyError, TypeError, OSError, UnicodeError, json.JSONDecodeError, BadZipFile) as error:
        raise WorkflowError("原工作台包或本版文件缺失、损坏或无法核验。") from error
    binding = {"task_id": p["source_task_id"], "result_sha256": digest(result_raw),
               "bundle_sha256": source["bundle_sha256"], "product_id": p["product_id"],
               "version_id": version_id, "purpose": p["purpose"], "recipient_role": recipient,
               "artifact_sha256": row["artifacts"]}
    context = {"scheduled_at": row["scheduled_at"], "target_duration_seconds": TARGETS[version_id],
               "rendered_duration_seconds": row["rendered_duration_seconds"],
               "source_rights_status": source["rights_verification"], "source_manual_review": source["manual_review"],
               "campaign_policy_ref": row["campaign_policy_ref"], "campaign_policy_status": row["campaign_policy_status"]}
    return {"director": binding}, (context, content)


def request_key(p):
    return ":".join(p[k] for k in ("source_task_id", "version_id", "purpose", "recipient_role"))


def receipt(result, raw):
    return {**{k:result[k] for k in ("task_id", "tool_id", "site", "scope", "state", "upstream",
                                    "input_sha256", *FLAGS)}, "result_sha256": digest(raw)}


def run_workflow(project, site, tool, p, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("只从已经保存的原交付包收件。")
    pins, (context, contents) = linked
    result = {**context, "site":site, "scope":SYNTHETIC, "state":STATE, "task_id":folder.name,
              "tool_id":tool, "job_id":p["job_id"], "upstream":pins, "input_sha256":digest(encode(p)), **FLAGS}
    raw = encode(result)
    files = {"json":raw, "receipt":encode(receipt(result, raw)),
             **{kind:contents[name] for kind,name in MEMBERS.items()}}
    paths = {kind:folder/name for kind,name in FILES.items()}
    for kind,path in paths.items():
        with path.open("xb") as stream:
            stream.write(files[kind])
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    pin = result["upstream"]["director"]
    if (task["site"] != "CN" or task["local_files"] != FILES or json.loads(files["json"]) != result
            or (result["task_id"],result["site"],result["tool_id"]) != (task["id"],task["site"],task["source"]["tool_id"])
            or result["upstream"] != task["source"]["role_packet_pins"]
            or any(result[k] is not False for k in FLAGS)
            or json.loads(files["receipt"]) != receipt(result, files["json"])
            or any(digest(files[k]) != pin["artifact_sha256"][f"{pin['version_id']}/{name}"]
                   for k,name in MEMBERS.items())):
        raise WorkflowError("版本文件或收件回执与冻结源不符。")


def public_tools():
    return [{"id":tool, "sites":["CN"], "role_suffix":role, "label":label,
             "description":"核对指定完成任务、版号、用途与双 SHA-256，只下载该版本；目前支持合成本机交付。",
             "input_example":{"site":"CN","scope":SYNTHETIC,"job_id":"","source_task_id":"","product_id":"",
                              "version_id":"v15","purpose":"","recipient_role":"douyin-"+role,
                              "bundle_sha256":"","video_sha256":""}, "max_media_files":0}
            for tool,(role,label) in ROLES.items()]
