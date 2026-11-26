"""Offline same-version shooting checklist handoff."""
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

SEND = "director-shoot-checklist-dispatch"
CLAIM = "onsite-shoot-checklist-intake"
RETURN = "onsite-shoot-evidence-return"
REVIEW = "director-shoot-evidence-intake"
TOOLS = (SEND, CLAIM, RETURN, REVIEW)
FILES = {"json": "shoot-handoff.json", "receipt": "shoot-receipt.json"}
LABELS = {
    SEND: ("video-director", "编导｜发出拍摄清单"),
    CLAIM: ("shooting-operator", "现场测试岗｜领取拍摄清单"),
    RETURN: ("shooting-operator", "现场测试岗｜按镜号回交"),
    REVIEW: ("video-director", "编导｜领取现场回交"),
}
BASE = "site scope job_id shoot_id sku version actor as_of"
EXTRA = {
    SEND: "planned_shots checklist_sha256 shoot_not_before",
    CLAIM: "upstream_task_id",
    RETURN: "upstream_task_id shots",
    REVIEW: "upstream_task_id",
}
PRIOR = {CLAIM: SEND, RETURN: CLAIM, REVIEW: RETURN}
QC = ("product_visible", "composition", "hook_first_three_seconds",
      "caption_safe", "rights_cleared")
STATUS = {
    "indexed_synthetic_frame": "已有合成索引帧",
    "missing": "缺该镜号证据",
    "rights_unknown": "用权待核，原片未收",
}


class WorkflowError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def digest(raw):
    return sha256(raw).hexdigest()


def parse_sha(value, label):
    identify(value, label, r"[0-9a-f]{64}")


def actor_for(site, tool):
    return f"{ROUTES[site][0]}-{LABELS[tool][0]}-qa"


def identity(p):
    return {key: p[key] for key in ("site", "scope", "shoot_id", "sku", "version")}


def shot_ids(rows):
    require(isinstance(rows, list) and 1 <= len(rows) <= 24, "镜号清单须有 1 至 24 项。")
    require(all(isinstance(row, dict) for row in rows), "每个镜号用一条结构化记录。")
    ids = [row.get("shot_id") for row in rows]
    for value in ids:
        identify(value, "镜号")
    require(len(set(ids)) == len(ids), "镜号不得重复。")
    return ids


def plan_of(p, at):
    parse_sha(p["checklist_sha256"], "拍摄清单校验值")
    require(stamp(p["shoot_not_before"]) > at, "拍摄开始时间须晚于清单发出。")
    shot_ids(p["planned_shots"])
    end = 0
    for shot in p["planned_shots"]:
        check(shot, "shot_id start_ms end_ms direction", "计划镜号")
        start, stop = shot["start_ms"], shot["end_ms"]
        amount(start, "开始毫秒", 0, 300000, False)
        amount(stop, "结束毫秒", 1, 300000, False)
        require(end <= start < stop, "镜头时段须顺序排列且不重叠。")
        term(shot["direction"], "拍摄指令")
        end = stop
    return {k: p[k] for k in ("checklist_sha256", "shoot_not_before", "planned_shots")}


def normalize_return(p, plan):
    rows = p["shots"]
    ids = shot_ids(rows)
    planned = {row["shot_id"]: row for row in plan["planned_shots"]}
    require(set(ids) == set(planned), "所有计划镜号必须明确标记，缺证镜号也需保留。")
    by_id = dict(zip(ids, rows))
    normalized = []
    for shot, target in planned.items():
        row = by_id[shot]
        check(row, "shot_id state frame qc", "回交镜号")
        state = row["state"]
        require(isinstance(state, str) and state in STATUS, "未知的镜头证据状态。")
        check(row["qc"], " ".join(QC), "现场质检")
        frame = row["frame"]
        if state == "indexed_synthetic_frame":
            check(frame, "site sku source_task_id frame_id sha256 frame_ms", "合成帧索引")
            require((frame["site"], frame["sku"]) == (p["site"], p["sku"]),
                    "镜头证据必须同站、同 SKU。")
            task_id(frame["source_task_id"], "合成索引来源任务")
            identify(frame["frame_id"], "合成帧编号")
            parse_sha(frame["sha256"], "合成帧校验值")
            offset = frame["frame_ms"]
            amount(offset, "合成帧毫秒", 0, 300000, False)
            require(target["start_ms"] <= offset < target["end_ms"], "索引帧不在该镜号时段。")
            require(all(type(row["qc"][field]) is bool or row["qc"][field] is None
                        for field in ("product_visible", "composition")),
                    "画面可见性与构图使用布尔值或未知。")
        else:
            require(frame is None and all(row["qc"][field] is None for field in QC),
                    "缺失或用权待核镜头必须保留未知，不得带入原片或标记质检通过。")
        require(all(row["qc"][field] is None for field in QC[2:]),
                "单独索引帧无法确认前三秒表现、字幕安全或原片用权。")
        normalized.append({**row, "state_label": STATUS[state]})
    return normalized


def bind(site, tool, p, artifact):
    check(p, BASE + " " + EXTRA[tool], "拍摄现场交接")
    require((p["site"], p["scope"], p["actor"]) == (site, SYNTHETIC, actor_for(site, tool)),
            "站点、合成范围和当前测试岗位须匹配。")
    for key in ("job_id", "shoot_id", "sku"):
        identify(p[key], key)
    amount(p["version"], "清单版本", 1, 10**6, False)
    at = stamp(p["as_of"])
    if tool == SEND:
        return {}, (plan_of(p, at), None)
    ident = p["upstream_task_id"]
    task_id(ident, "上游任务编号")
    try:
        result_bytes = artifact(ident, "json")
        receipt_bytes = artifact(ident, "receipt")
        prior = json.loads(result_bytes)
        same = (prior["tool_id"] == PRIOR[tool] and prior["identity"] == identity(p)
                and prior["actor"] == actor_for(site, PRIOR[tool]))
    except (ValueError, KeyError, OSError, TypeError):
        raise WorkflowError("上游任务同版回执缺失或无法核实。") from None
    require(same, "同站、同 SKU、同拍摄单版本和上游岗位阶段须匹配。")
    require(at > stamp(prior["as_of"]), "当前步骤须晚于上游回执。")
    plan = prior["plan"]
    if tool == CLAIM:
        require(at < stamp(plan["shoot_not_before"]), "拍前领取须早于模拟拍摄开始。")
    if tool == RETURN:
        require(at >= stamp(plan["shoot_not_before"]), "回交不得早于模拟拍摄开始。")
    shots = normalize_return(p, plan) if tool == RETURN else prior["shots"]
    pin = {"task_id": ident, "tool_id": prior["tool_id"], "result_sha256": digest(result_bytes),
           "receipt_sha256": digest(receipt_bytes)}
    return {"previous": pin}, (plan, shots)


def request_key(p):
    return f"{p['shoot_id']}:{p['sku']}:{p['version']}"


def receipt(result, raw):
    return {**{k: result[k] for k in ("identity", "task_id", "tool_id", "as_of", "actor",
               "upstream", "input_sha256", "actual_crew_signoff", "publication_authorized")},
            "result_sha256": digest(raw)}


def run_workflow(project, site, tool, p, folder, assets, progress, *, linked):
    pins, (plan, shots) = linked
    result = {"identity": identity(p), "task_id": folder.name, "tool_id": tool, "job_id": p["job_id"],
              "as_of": p["as_of"], "actor": p["actor"], "plan": plan, "shots": shots, "upstream": pins,
              "input_sha256": digest(encode(p)), "actual_crew_signoff": None,
              "ph_native_review": None, "publication_authorized": False}
    raw = encode(result)
    artifacts = {"json": raw, "receipt": encode(receipt(result, raw))}
    files = {kind: folder / name for kind, name in FILES.items()}
    for kind, path in files.items():
        with path.open("xb") as stream:
            stream.write(artifacts[kind])
    return result, files


def verify_saved(task, files):
    result = task["result"]
    require(task["local_files"] == FILES and json.loads(files["json"]) == result
            and result["identity"]["site"] == task["site"]
            and result["task_id"] == task["id"] and result["tool_id"] == task["source"]["tool_id"]
            and result["upstream"] == task["source"]["role_packet_pins"]
            and json.loads(files["receipt"]) == receipt(result, files["json"]),
            "本地拍摄交接回执与任务不一致。")


def public_tools():
    return [{"id": tool, "sites": list(ROUTES), "role_suffix": role, "label": label,
             "description": "本机合成走查。按同站、同 SKU、同清单版核对拍前领取和逐镜回交。",
             "input_example": {
                 "site": "CN", "scope": SYNTHETIC, "job_id": "", "shoot_id": "", "sku": "",
                 "version": 1, "actor": actor_for("CN", tool), "as_of": "",
                 **({"planned_shots": [], "checklist_sha256": "", "shoot_not_before": ""}
                    if tool == SEND else {"upstream_task_id": ""}),
                 **({"shots": []} if tool == RETURN else {}),
             }, "max_media_files": 0} for tool, (role, label) in LABELS.items()]
