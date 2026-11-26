"""Receive one pinned competitor channel report as offline ad research."""
from hashlib import sha256
import json
import re

from role_tools import ROUTES, SYNTHETIC
from workflow_channel_alignment import COMPETITOR, IDENTITY
from workflow_lead_weekly import encode
from workflow_live_rehearsal import check, identify, task_id

TOOL = "ad-competitor-channel-intake"
TOOLS = (TOOL,)
FILES = {"json": "ad-channel-intake.json", "report": "ad-channel-intake.md", "receipt": "ad-channel-intake-receipt.json"}
RECEIVERS = {"CN": "douyin-ad-buyer", "PH": "tiktok-ad-buyer"}
CHANNELS = {"product_card": "商品卡", "shoppable_video": "挂链短视频", "live": "直播"}


class WorkflowError(ValueError):
    pass


def bind(site, source, fetch):
    check(source, "site scope job_id receiver owner target alignment_task_id source_result_sha256", "竞品分路情报收件")
    if site not in ROUTES or (source["site"], source["scope"], source["receiver"]) != (site, SYNTHETIC, RECEIVERS[site]):
        raise WorkflowError("请选择与原情报同站的投放测试岗位及合成范围。")
    identify(source["job_id"], "任务标识")
    identify(source["owner"], "收件测试身份")
    check(source["target"], IDENTITY, "同口径来源范围")
    task_id(source["alignment_task_id"], "竞品分析任务号")
    digest = source["source_result_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise WorkflowError("请从原竞品分析回执填写结果 SHA-256。")
    upstream, pin = fetch(source["alignment_task_id"], site, COMPETITOR)
    if upstream["scope"] != SYNTHETIC or source["target"] != upstream["target"] or digest != pin["result_sha256"]:
        raise WorkflowError("竞品情报范围或结果版本不匹配，请使用同站、同 SKU、同统计窗口及原回执。")
    return {"alignment": pin}, upstream


def receipt(result, files):
    return {
        **{key: result[key] for key in ("task_id", "site", "tool_id", "job_id", "target", "receiver",
                                         "upstream", "state", "execution_authorized")},
        "result_sha256": sha256(files["json"]).hexdigest(),
        "report_sha256": sha256(files["report"]).hexdigest(),
    }


def run_workflow(project, site, tool, source, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("此次收件只接受已核实的本机竞品情报任务。")
    pins, upstream = linked
    view = upstream["views"]["surface"]
    channels = {}
    for name, bucket in view["buckets"].items():
        channels[name] = {
            "source_refs": [row["source_ref"] for row in bucket["observations"]],
            "values": bucket["values"],
            "issues": bucket["issues"],
            "ratios": bucket["ratios"],
            "ad_spend_share": view["ad_spend_share_by_surface"][name],
        }
    has_gap = upstream["state"] != "ALIGNED_FIXTURE"
    result = {
        **{key: source[key] for key in ("site", "scope", "job_id", "receiver", "owner", "target")},
        "task_id": folder.name, "tool_id": tool, "upstream": pins,
        "source_state": upstream["state"],
        "state": "RECEIVED_WITH_METRIC_GAP" if has_gap else "RECEIVED_FOR_RESEARCH",
        "decision": "仅保留已知分路指标并待核缺失口径。" if has_gap else "保留分路指标供本机研究对照。",
        "source_cutoff": upstream["cutoff"], "partition": view["partition"],
        "reconciled": upstream["reconciled"], "channels": channels,
        "excluded_observations": upstream["excluded"],
        "cross_view_sum_performed": False,
        "causal_increment": None, "commercial_reuse_rights": "unverified",
        "research_only": True, "actual_channel_or_ad_action": False,
        "execution_authorized": False, "provider_called": False, "model_called": False,
    }
    lines = [f"# {source['job_id']} · {source['receiver']}", "",
             "已收合成竞品情报。指标保持原来源、分子与分母；转化因果和商业素材使用权待核。",
             result["decision"], f"原任务 {pins['alignment']['task_id']} · {pins['alignment']['result_sha256']}"]
    for name, channel in channels.items():
        value = channel["ratios"]["click_through"]
        if value is not None:
            ctr = f"{value['numerator']}/{value['denominator']}"
        elif channel["issues"].get("clicks"):
            ctr = "未知（点击与曝光口径需核对）"
        else:
            ctr = "未知（无可用分母）"
        lines.append(f"- {CHANNELS[name]}点击率 {ctr}。来源 {'、'.join(channel['source_refs']) or '缺'}。")
    data = {"json": encode(result), "report": ("\n".join(lines) + "\n").encode("utf-8")}
    data["receipt"] = encode(receipt(result, data))
    paths = {kind: folder / name for kind, name in FILES.items()}
    for kind, path in paths.items():
        with path.open("xb") as stream:
            stream.write(data[kind])
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    if (task["local_files"] != FILES or result != json.loads(files["json"])
            or result["upstream"] != task["source"]["role_packet_pins"]
            or (result["task_id"], result["site"], result["tool_id"]) !=
               (task["id"], task["site"], task["source"]["tool_id"])
            or json.loads(files["receipt"]) != receipt(result, files)):
        raise WorkflowError("投放情报收件回执无法复核。")


def public_tools():
    return [{
        "id": TOOL, "sites": list(ROUTES), "role_suffix": "ad-buyer",
        "label": "投放收件 · 竞品分路情报（合成本机）",
        "description": "用竞品分析任务号、原结果 SHA-256 和原 target 领用同版情报。保留各路分数和来源，仅用于本机研究。",
        "input_examples": {
            site: {"site": site, "scope": SYNTHETIC, "job_id": f"{site}-AD-RESEARCH-DEMO",
                   "receiver": receiver, "owner": f"{site}-AD-TEST",
                   "alignment_task_id": "填入竞品分析任务号",
                   "source_result_sha256": "填入原回执结果 SHA-256", "target": "填写原结果的 target 对象"}
            for site, receiver in RECEIVERS.items()
        },
    }]

