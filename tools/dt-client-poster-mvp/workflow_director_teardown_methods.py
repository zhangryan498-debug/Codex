"""Receive approved, locally reviewed directing methods without source media."""

from hashlib import sha256
import json

from workflow_lead_weekly import encode
from workflow_live_rehearsal import check, identify, task_id
from workflow_teardown_report import SCOPE as REPORT_SCOPE, TOOL as REPORT_TOOL

TOOL = "cn-director-teardown-method-intake"
TOOLS = (TOOL,)
FILES = {"json": "director-teardown-methods.json", "report": "director-teardown-methods.md",
         "receipt": "director-teardown-methods-receipt.json"}
RECIPIENT = "douyin-video-director"
SCOPE = "authorized_local_methods_only"
REVISION = "archived-original"
PURPOSE = "internal_director_reference"
STATE = "RECEIVED_METHODS_ONLY"

# Each approval covers these rewritten methods and these exact local report bytes.
APPROVED_REPORTS = {
    "4e3798dae9f848909132b98ead2b4d87": (
        "12ee755cf131bdce2c2e55ccc730454af55ad8e3a4c1d7c0445b6c46670e24b0",
        "97391c291efb0e33ca4dccf92fba5e1ab85ef1df57927f5233dbfed922d8c03e",
        (
            ("opening", "用自有样品开场，先让观众辨认商品类型，再切到细节。"),
            ("continuity", "固定机位和同一件样品，连续拍摄操作前、操作中与结束后的变化。"),
            ("comparison", "如另获测试许可，只改变先看全貌还是先看动作，对齐观察窗口再比较早段留存。"),
        ),
    ),
    "ad845dbc7ead40b6b4c38372ed519e35": (
        "80a5c669a0cf60239cfad1f6b229003bb612fd6bcf51167e77fa9e55d4ce5967",
        "9c8c888279d6bda2ab42438c4930961e434f9e37e0739b1e3e7cec1824adb3af",
        (
            ("opening", "开头让手与自有样品同框，交代尺度和操作方式。"),
            ("continuity", "从外包装到取出展示始终跟拍同一件样品，用连续画面交代规格范围。"),
            ("comparison", "如另获测试许可，用同批自有素材对比静态开场与提前出现动作，统一时长和拍摄条件。"),
        ),
    ),
}
FLAGS = {
    "source_media_included": False,
    "original_media_reuse_authorized": False,
    "external_release_authorized": False,
    "execution_authorized": False,
    "model_called": False,
    "provider_called": False,
    "product_anchor_verified": False,
    "attribution_verified": False,
}


class WorkflowError(ValueError):
    pass


def binding_for(ident):
    approved = APPROVED_REPORTS.get(ident)
    if approved is None:
        raise WorkflowError("此报告任务未登记本机方法交接。")
    return {"task_id": ident, "artifact_revision": REVISION, "result_sha256": approved[0],
            "receipt_sha256": approved[1]}


def bind(site, p, load_report):
    check(p, "site scope job_id report_task_id report_revision report_sha256 recipient purpose "
             "include_original_media publish_externally", "编导拆解方法收件")
    if (site, p["site"], p["scope"], p["recipient"], p["purpose"]) != ("CN", "CN", SCOPE, RECIPIENT, PURPOSE):
        raise WorkflowError("只允许中国站编导接收本机内部方法。")
    if p["include_original_media"] is not False or p["publish_externally"] is not False:
        raise WorkflowError("仅接收通用方法，原音画、截图、下载链接及外部发布均未授权。")
    identify(p["job_id"], "编导收件编号")
    task_id(p["report_task_id"], "上游八段报告任务")
    pin = binding_for(p["report_task_id"])
    if p["report_revision"] != pin["artifact_revision"] or p["report_sha256"] != pin["result_sha256"]:
        raise WorkflowError("提交的报告版本或结果 SHA-256 不匹配。")
    result, observed = load_report(pin["task_id"], site, REPORT_TOOL)
    if (observed != {key: pin[key] for key in ("task_id", "result_sha256", "receipt_sha256")}
            or result.get("scope") != REPORT_SCOPE or result.get("state") != "REVIEW_REPORT_READY"
            or result.get("commercial_media_reuse_authorized") is not False):
        raise WorkflowError("本机报告、回执或原授权范围变化，不能收件。")
    return {"report": pin}, None


def compose(ident, job_id, upstream, input_sha256):
    pin = upstream["report"]
    methods = [{"use": stage, "instruction": text} for stage, text in APPROVED_REPORTS[pin["task_id"]][2]]
    return {"task_id": ident, "job_id": job_id, "site": "CN", "tool_id": TOOL, "state": STATE,
            "scope": SCOPE, "recipient": RECIPIENT, "purpose": PURPOSE, "upstream": upstream,
            "input_sha256": input_sha256, "methods": methods, **FLAGS}


def render(result):
    source = result["upstream"]["report"]
    lines = ["# 抖音编导 · 拆解方法收件", "", f"收件任务 {result['task_id']}",
             f"原报告任务 {source['task_id']}", f"报告结果 SHA-256 {source['result_sha256']}",
             "", "以下为供本机编导参考的通用方法。", "",
             *(f"- {row['instruction']}" for row in result["methods"]), "",
             "使用自有或另获授权的素材。原片、原音、截图和发布均未获本次授权。",
             "商品挂链、实际转化和因果归因仍待独立验证。", ""]
    return "\n".join(lines).encode("utf-8")


def receipt(result, raw, report):
    fields = ("task_id", "job_id", "site", "tool_id", "state", "scope", "recipient",
              "purpose", "upstream", "input_sha256", *FLAGS)
    return {**{k: result[k] for k in fields},
            "result_sha256": sha256(raw).hexdigest(), "report_sha256": sha256(report).hexdigest()}


def run_workflow(project, site, tool, p, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("方法收件不接收媒体附件。")
    pins, _ = linked
    result = compose(folder.name, p["job_id"], pins, sha256(encode(p)).hexdigest())
    raw, report = encode(result), render(result)
    content = {"json": raw, "report": report, "receipt": encode(receipt(result, raw, report))}
    paths = {kind: folder / name for kind, name in FILES.items()}
    for kind, path in paths.items():
        with path.open("xb") as stream:
            stream.write(content[kind])
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    pin = result["upstream"]["report"]
    if (task["site"] != "CN" or task["source"]["tool_id"] != TOOL or task["local_files"] != FILES
            or pin != binding_for(pin["task_id"])
            or result != compose(task["id"], result["job_id"], task["source"]["role_packet_pins"], result["input_sha256"])
            or json.loads(files["json"]) != result or files["report"] != render(result)
            or json.loads(files["receipt"]) != receipt(result, files["json"], files["report"])):
        raise WorkflowError("编导方法、绑定版本或收件回执不一致。")


def public_tools():
    return [{"id": TOOL, "sites": ["CN"], "role_suffix": "video-director", "label": "抖音编导接收拆解方法",
             "description": "按本机原报告任务及结果哈希接收已整理的方法。只提供方法和溯源回执。",
             "input_example": {"site": "CN", "scope": SCOPE, "job_id": "", "report_task_id": "",
                               "report_revision": REVISION, "report_sha256": "", "recipient": RECIPIENT,
                               "purpose": PURPOSE, "include_original_media": False, "publish_externally": False},
             "max_media_files": 0}]
