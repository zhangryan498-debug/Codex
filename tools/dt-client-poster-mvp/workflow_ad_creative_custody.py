"""Offline custody of a verified test creative and its canonical fixture anchor."""
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, term
from workflow_live_paid_slot import TOOL as PAID_SLOT
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

SEND = "creative-offline-custody-dispatch"
RECEIVE = "ad-creative-offline-custody-intake"
TOOLS = (SEND, RECEIVE)
FILES = {"json": "creative-custody.json", "receipt": "creative-custody-receipt.json"}
BASE = "site scope job_id pack_id version actor as_of account_id sku_id sku_revision creative_sha256 canonical_anchor"
SOURCE = "upstream_task_id upstream_receipt_sha256"
EXTRA = {SEND: "grant", RECEIVE: "rights_mode"}
ROLES = {SEND: "video-director", RECEIVE: "ad-buyer"}
PH_RIGHTS = ("spark", "affiliate")


class WorkflowError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def digest(raw):
    return sha256(raw).hexdigest()


def anchor(site, sku, revision):
    return f"QA-ANCHOR-{site}-{sku}-V{revision}"


def actor_for(site, tool):
    return f"{ROUTES[site][0]}-{ROLES[tool]}-qa"


def identity(p):
    return {name: p[name] for name in ("site", "scope", "pack_id", "version", "account_id", "sku_id",
                                       "sku_revision", "creative_sha256", "canonical_anchor")}


def request_key(p):
    return p["job_id"]


def bind(site, tool, p, artifact):
    check(p, BASE + " " + SOURCE + " " + EXTRA[tool], "广告素材证据交接")
    require((p["site"], p["scope"], p["actor"]) == (site, SYNTHETIC, actor_for(site, tool)),
            "须选择本机同站测试岗位。")
    for name in ("job_id", "pack_id", "account_id", "sku_id"):
        identify(p[name], name)
    for name in ("version", "sku_revision"):
        amount(p[name], name, 1, 10**6, False)
    for name in ("creative_sha256", "upstream_receipt_sha256"):
        identify(p[name], name, r"[a-f0-9]{64}")
    require(p["canonical_anchor"] == anchor(site, p["sku_id"], p["sku_revision"]),
            "仅接受该站同 SKU 修订的本地规范锚点。")
    at = stamp(p["as_of"])
    ident = p["upstream_task_id"]
    task_id(ident, "上游任务")
    try:
        data, receipt_bytes = (artifact(ident, kind) for kind in ("json", "receipt"))
        source = json.loads(data)
    except (KeyError, TypeError, OSError, ValueError):
        raise WorkflowError("上游证据回执无法核实。") from None
    expected_tool = PAID_SLOT if tool == SEND else SEND
    require((source.get("tool_id"), source.get("site"), source.get("scope")) == (expected_tool, site, SYNTHETIC),
            "上游来源阶段、站点或范围不匹配。")
    require(p["upstream_receipt_sha256"] == digest(receipt_bytes), "上游回执 SHA-256 不匹配。")
    require(stamp(source["as_of"]) < at, "交接时间须晚于上游证据。")
    if tool == SEND:
        binding, clip = source["binding"], source["clip"]
        require((binding["account_id"], binding["sku_id"], binding["revision"], clip["sha256"]) ==
                (p["account_id"], p["sku_id"], p["sku_revision"], p["creative_sha256"]),
                "素材、账户、SKU 或版本与上游原件不符。")
        require(source["state"] == "ALIGNED_OFFLINE" and clip["local_synthetic_only"] is True
                and clip["claim"]["use_scope"] == "synthetic_offline_reference",
                "只接收已验证的本地校准合成素材。")
        try:
            media = artifact(ident, "video")
        except (ValueError, OSError, KeyError, TypeError):
            raise WorkflowError("上游本地素材字节无法核实。") from None
        require(digest(media) == p["creative_sha256"], "上游素材字节 SHA-256 不匹配。")
        grant = p["grant"]
        check(grant, "source_ref use start end", "本机批准记录")
        term(grant["source_ref"], "本机批准来源")
        require(grant["use"] == "local_custody_only" and stamp(grant["start"]) <= at < stamp(grant["end"]),
                "本机批准仅可用于有效期内的合成交接。")
        specific = {"state": "LOCAL_FIXTURE_SENT", "grant": grant, "rights_mode": "owned_fixture"}
    else:
        require(source["identity"] == identity(p), "接件必须对应同包、同版、同 SKU 和同素材锚点。")
        mode = p["rights_mode"]
        allowed = ("owned_fixture", *PH_RIGHTS) if site == "PH" else ("owned_fixture",)
        require(isinstance(mode, str) and mode in allowed, "授权路径与当前站点不匹配。")
        grant = source["grant"]
        if mode in PH_RIGHTS:
            state, relevant = "PENDING_DISTINCT_RIGHTS", None
        elif not stamp(grant["start"]) <= at < stamp(grant["end"]):
            state, relevant = "LOCAL_FIXTURE_RIGHTS_EXPIRED", grant
        else:
            state, relevant = "OWNED_FIXTURE_CUSTODY_RECEIVED", grant
        specific = {"state": state, "grant": relevant, "rights_mode": mode}
    pin = {"task_id": ident, "tool_id": expected_tool, "result_sha256": digest(data),
           "receipt_sha256": digest(receipt_bytes)}
    return {"source": pin}, specific


def receipt(result, raw):
    return {**{key: result[key] for key in ("identity", "site", "actor", "task_id", "tool_id", "upstream",
               "input_sha256", "state", "rights_mode", "actual_authority", "placement_authorized")},
            "result_sha256": digest(raw)}


def run_workflow(project, site, tool, p, folder, assets, progress, *, linked):
    pins, specific = linked
    result = {**specific, "site": site, "scope": SYNTHETIC, "job_id": p["job_id"], "identity": identity(p),
              "actor": p["actor"], "as_of": p["as_of"], "task_id": folder.name, "tool_id": tool,
              "upstream": pins, "input_sha256": digest(encode(p)),
              "actual_anchor_id": None, "actual_authority": "PENDING_REAL_PLATFORM_RIGHTS",
              "placement_authorized": False, "model_called": False, "provider_called": False}
    raw = encode(result)
    data = {"json": raw, "receipt": encode(receipt(result, raw))}
    paths = {kind: folder / name for kind, name in FILES.items()}
    for kind, path in paths.items():
        with path.open("xb") as stream:
            stream.write(data[kind])
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    require(task["local_files"] == FILES and json.loads(files["json"]) == result
            and (result["task_id"], result["site"], result["tool_id"]) ==
            (task["id"], task["site"], task["source"]["tool_id"])
            and result["upstream"] == task["source"]["role_packet_pins"]
            and json.loads(files["receipt"]) == receipt(result, files["json"]),
            "广告素材本地交接回执不一致。")


def public_tools():
    def example(site, tool):
        return {"site": site, "scope": SYNTHETIC, "job_id": "", "pack_id": "", "version": 1,
                "actor": actor_for(site, tool), "as_of": "", "account_id": "", "sku_id": "",
                "sku_revision": 1, "creative_sha256": "", "canonical_anchor": "", "upstream_task_id": "",
                "upstream_receipt_sha256": "", **({"grant": {"source_ref": "", "use": "local_custody_only",
                "start": "", "end": ""}} if tool == SEND else {"rights_mode": "owned_fixture"})}
    return [{"id": tool, "sites": list(ROUTES), "role_suffix": ROLES[tool], "label": label,
             "description": "保存同素材、同 SKU 修订及本地锚点的合成授权交接。真实授权保持待核。",
             "input_example": example("CN", tool), "input_examples": {site: example(site, tool) for site in ROUTES},
             "max_media_files": 0}
            for tool, label in ((SEND, "编导｜发出素材权利证据"), (RECEIVE, "投手｜领取素材权利证据"))]
