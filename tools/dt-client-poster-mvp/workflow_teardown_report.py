"""Deliver reviewer-authored eight-section reports bound to existing local video tasks."""
from hashlib import sha256
import json
import re

from role_tools import SYNTHETIC
from workflow_lead_weekly import encode
from workflow_live_rehearsal import amount, check, identify, task_id
from workflow_teardown import TOOL_ID as CLIP

TOOL = "cn-teardown-eight-section-report"
PH_REPORT = "ph-teardown-eight-section-report"
PH_INTAKE = "ph-director-eight-section-intake"
PH_RECIPIENT = "tiktok-video-director"
TOOLS = (TOOL, PH_REPORT, PH_INTAKE)
FILES = {"json": "teardown-review.json", "report": "teardown-review.md", "receipt": "teardown-review-receipt.json"}
SCOPE = "authorized_local_review"
HEADINGS = {
    "hook": "前 3 秒钩子", "structure": "内容结构与节奏", "elements": "情绪及可复用要素",
    "conversion": "搜索与商品卡承接", "attribution": "归因与置信度",
    "reuse": "三可借、两不可借及测试物料", "risk": "风险和证据缺口", "archive": "归档",
}
CATEGORIES = {"hook", "selling_point", "demonstration", "trust", "cta", "emotion"}
METRICS = ("views", "completion_rate", "click_rate", "conversion_rate", "gmv", "causal_increment")


class WorkflowError(ValueError):
    pass


def text(value, name):
    if (not isinstance(value, str) or not 1 <= len(value) <= 500
            or value != value.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise WorkflowError(f"{name}须为非空单行审核文字，最多 500 字。")
    return value


def list_of(value, minimum, maximum, name):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise WorkflowError(f"{name}需要 {minimum} 至 {maximum} 条。")
    return value


def notes_and_lines(parts, frames, duration):
    """Parse user annotations without certifying their interpretation."""
    check(parts, " ".join(HEADINGS), "八段审阅记录")
    used = set()
    lines = {}

    def cites(ids, start=None, end=None):
        seen = set()
        output = []
        for ident in list_of(ids, 1, 8, "画面帧"):
            if not isinstance(ident, str) or ident not in frames or ident in seen:
                raise WorkflowError("引用的帧须属于该原片，且同项不可重复。")
            seconds = frames[ident]["actual_seconds"]
            if start is not None and not start <= seconds * 1000 < end:
                raise WorkflowError("引用帧不在所填时段内。")
            used.add(ident)
            seen.add(ident)
            output.append(f"{ident} @ {seconds:.3f}s")
        return "〔" + "、".join(output) + "〕"

    def plain(value, label):
        value = text(value, label)
        return re.sub(r"([\\<>\[\]*_#\x60])", r"\\\1", value)

    def note(row, start=None, end=None):
        check(row, "text frame_ids", "绑定画面的审阅记录")
        return plain(row["text"], "审阅记录") + " " + cites(row["frame_ids"], start, end)

    hook = parts["hook"]
    check(hook, "observation hypothesis", "开头")
    lines["hook"] = ["审阅者画面记录 · " + note(hook["observation"], 0, min(3000, duration)),
                     "待验证机制 · " + plain(hook["hypothesis"], "钩子假设"), "可核实原话 · 未确认。"]

    structure = []
    last_end = 0
    for row in list_of(parts["structure"], 1, 12, "结构段"):
        check(row, "start_ms end_ms observation intended_role", "结构段")
        start, end = row["start_ms"], row["end_ms"]
        amount(start, "起点毫秒", 0, duration, False)
        amount(end, "终点毫秒", 1, duration, False)
        if start != last_end or end <= start:
            raise WorkflowError("结构段须从 0 开始、相接且无重叠。")
        structure.append(f"- {start/1000:g}–{end/1000:g}s · {note(row['observation'],start,end)}；用途假设 · "
                         + plain(row["intended_role"], "段落用途假设"))
        last_end = end
    if last_end != duration:
        raise WorkflowError("最后一段须覆盖原片末端。")
    lines["structure"] = structure

    elements = []
    categories = set()
    for row in list_of(parts["elements"], 3, 6, "要素"):
        check(row, "category observation mechanism condition", "拆解要素")
        category = row["category"]
        if not isinstance(category, str) or category not in CATEGORIES or category in categories:
            raise WorkflowError("要素类别须已定义且不可重复。")
        categories.add(category)
        elements.append(f"- {category} · {note(row['observation'])}；机制假设 · "
                        + plain(row["mechanism"], "机制假设") + "；借鉴前提 · " + plain(row["condition"], "借鉴前提"))
    if not {"hook", "emotion"} <= categories:
        raise WorkflowError("要素须覆盖钩子和情绪线索。")
    lines["elements"] = elements

    conversion = parts["conversion"]
    check(conversion, "frame_ids search_proposal product_card_proposal", "承接设计")
    lines["conversion"] = ["承接草案依据 " + cites(conversion["frame_ids"]),
        "搜索 · " + plain(conversion["search_proposal"], "搜索承接草案"),
        "商品卡 · " + plain(conversion["product_card_proposal"], "商品卡承接草案"),
        "原片真实入口、商品 ID、渠道和支付状态 · 待核。"]
    attribution = parts["attribution"]
    check(attribution, "frame_ids limitation comparison_needed", "归因")
    lines["attribution"] = ["结构参照 " + cites(attribution["frame_ids"]),
        "局限 · " + plain(attribution["limitation"], "缺口说明"),
        "需补 · " + plain(attribution["comparison_needed"], "补证或对照"),
        "置信度低。播放、完播、点击、转化、成交额和因果增量均未知。"]
    reuse = parts["reuse"]
    check(reuse, "borrow avoid test", "借鉴")
    borrow = list_of(reuse["borrow"], 3, 3, "可借结构")
    avoid = list_of(reuse["avoid"], 2, 2, "不可借事项")
    reuse_lines = [*("- 可借结构 · " + note(r) for r in borrow), *("- 不可借 · " + note(r) for r in avoid)]
    if len({r["text"] for r in borrow + avoid}) != 5:
        raise WorkflowError("三项可借与两项不可借不能重复。")
    test = reuse["test"]
    check(test, "frame_ids variable control variant metric materials", "对照草案")
    proposal = {k: plain(test[k], "对照草案的 " + k) for k in ("variable", "control", "variant", "metric")}
    if proposal["control"] == proposal["variant"]:
        raise WorkflowError("测试的对照组和变体需明确区别。")
    materials = [plain(x, "待准备物料") for x in list_of(test["materials"], 1, 10, "测试物料")]
    lines["reuse"] = [*reuse_lines, "测试草案 " + cites(test["frame_ids"]),
        f"- 唯一变量 · {proposal['variable']}；对照 · {proposal['control']}；变体 · {proposal['variant']}",
        f"- 观察指标 · {proposal['metric']}；测试未执行。", "- 待准备物料 · " + "、".join(materials)]

    lines["risk"] = ["- " + note(r) for r in list_of(parts["risk"], 1, 8, "风险记录")]
    archive = parts["archive"]
    check(archive, "frame_ids reference", "归档")
    lines["archive"] = [
        "审阅者档案标识 · " + plain(archive["reference"], "历史审阅回执标识") + " " + cites(archive["frame_ids"]),
        "用途 · 本机内部研究；商业复用仍待授权。",
    ]
    return lines, used


def bind(site, p, load, verify_frame_files, *, tool=TOOL):
    is_ph = tool == PH_REPORT
    fields = "site scope job_id owner source_task_id source_sha256 sections"
    check(p, fields + (" revision localization cod_risks" if is_ph else ""), "八段拆解报告")
    expected_site, expected_scope = ("PH", SYNTHETIC) if is_ph else ("CN", SCOPE)
    if (site, p["site"], p["scope"]) != (expected_site, expected_site, expected_scope):
        raise WorkflowError("原片站点与报告不符，或超出入口支持的本机用途。")
    identify(p["job_id"], "报告编号")
    owner = text(p["owner"], "审核提交人")
    task_id(p["source_task_id"], "已保存的抽帧任务")
    source, source_pin = load(p["source_task_id"], CLIP, site)
    if (source["source_sha256"] != p["source_sha256"]
            or source["synthetic_material"] is not is_ph
            or source["source_kind_declared"] != ("local_clip" if is_ph else "published_work")):
        raise WorkflowError("来源的原片 SHA-256、类型或合成标记与本入口不符。")
    frames = {frame["id"]: frame for frame in source["frames"]}
    if len(frames) != len(source["frames"]):
        raise WorkflowError("原片帧号重复，不能固定来源。")
    duration = round(source["duration_seconds"] * 1000)
    section_lines, used = notes_and_lines(p["sections"], frames, duration)
    frame_pins = verify_frame_files(p["source_task_id"], [frames[ident] for ident in sorted(used)])
    details = ph_details(p, section_lines) if is_ph else {}
    return {"clip": source_pin, "frames": frame_pins}, {
        **details, "scope": expected_scope, "job_id": p["job_id"], "owner": owner, "source_name": source["source_name"],
        "duration_ms": duration, "sections": p["sections"], "section_lines": section_lines,
        "reviewer_note_kind": "reviewer_supplied_interpretation",
        "verified_spoken_quote": None, "real_product_anchor": None, "real_product_id": None,
        "real_metrics": dict.fromkeys(METRICS), "mechanism_confidence": "LOW_HYPOTHESIS_ONLY",
        "commercial_media_reuse_authorized": False, "experiment_executed": False,
        "source_publication_independently_verified": False,
    }


def render(result):
    if result["tool_id"] == PH_INTAKE:
        return render_ph_intake(result)
    clip = result["upstream"]["clip"]
    title = "菲律宾八段拆解（本机合成）" if result["tool_id"] == PH_REPORT else "抖音八段拆解"
    lines = [f"# {title} · {result['job_id']}", "",
             f"来源任务 · {clip['task_id']}",
             f"原片 SHA-256 · {clip['media_sha256']}；抽帧清单 SHA-256 · {clip['json_sha256']}",
             "来源绑定覆盖文件和时间戳。正文为审阅者记录和待验证假设。"]
    for i, (key, title) in enumerate(HEADINGS.items(), 1):
        lines.extend(["", f"## {i}. {title}", "", *result["section_lines"][key]])
    lines.extend(["", "帧号 / 实测时间 / SHA-256", *(
        f"- {ident} / {frame['actual_seconds']:.6f}s / {frame['sha256']}"
        for ident, frame in result["upstream"]["frames"].items())])
    return ("\n".join(lines) + "\n").encode("utf-8")


def receipt(result, raw, report):
    return {k: result[k] for k in ("site", "task_id", "job_id", "tool_id", "upstream", "input_sha256",
                                   "execution_authorized", "provider_called", "model_called")} | {
        "result_sha256": sha256(raw).hexdigest(), "report_sha256": sha256(report).hexdigest()}


def run_workflow(project, site, tool, p, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("此入口只引用已完成的本机抽帧。")
    pins, material = linked
    state = "RECEIVED_OFFLINE_METHODS" if tool == PH_INTAKE else "REVIEW_REPORT_READY"
    result = {**material, "site": site, "tool_id": tool, "task_id": folder.name, "upstream": pins,
              "state": state, "input_sha256": sha256(encode(p)).hexdigest(),
              "execution_authorized": False, "provider_called": False, "model_called": False}
    raw, report = encode(result), render(result)
    contents = {"json": raw, "report": report, "receipt": encode(receipt(result, raw, report))}
    paths = {kind: folder / name for kind, name in FILES.items()}
    for kind, path in paths.items():
        with path.open("xb") as stream:
            stream.write(contents[kind])
    return result, paths


def verify_saved(task, files):
    r = task["result"]
    if (task["local_files"] != FILES or json.loads(files["json"]) != r
            or (r["task_id"], r["site"], r["tool_id"]) != (task["id"], task["site"], task["source"]["tool_id"])
            or r["upstream"] != task["source"]["role_packet_pins"]
            or (r["site"] == "PH" and files["report"] != render(r))
            or json.loads(files["receipt"]) != receipt(r, files["json"], files["report"])):
        raise WorkflowError("八段报告文件、来源与回执不一致。")


def public_tools():
    cn = [{"id": TOOL, "label": "抖音八段拆解报告", "sites": ["CN"], "role_suffix": "video-teardown",
             "description": "用已保存的抽帧任务、原片哈希及八段审阅记录生成本机报告。",
             "input_example": {"site": "CN", "scope": SCOPE, "job_id": "", "owner": "", "source_task_id": "",
                               "source_sha256": "", "sections": {
            "hook": {"observation": {"text": "", "frame_ids": []}, "hypothesis": ""},
            "structure": [], "elements": [], "conversion": {"frame_ids": [], "search_proposal": "", "product_card_proposal": ""},
            "attribution": {"frame_ids": [], "limitation": "", "comparison_needed": ""},
            "reuse": {"borrow": [], "avoid": [], "test": {"frame_ids": [], "variable": "", "control": "", "variant": "",
                                                          "metric": "", "materials": []}},
            "risk": [], "archive": {"frame_ids": [], "reference": ""}}}, "max_media_files": 0}]


    ph = {"site": "PH", "scope": SYNTHETIC, "job_id": "", "report_task_id": "",
          "report_revision": 1, "report_sha256": "", "receipt_sha256": "",
          "recipient": PH_RECIPIENT, "purpose": "internal_director_reference"}
    return [*cn,
            {**cn[0], "id": PH_REPORT, "sites": ["PH"], "label": "菲律宾八段拆解报告（本机合成）",
             "description": "按本站合成原片作八段报告；商品、COD、母语签核和归因保留待核。",
             "input_example": {**cn[0]["input_example"], "site": "PH", "scope": SYNTHETIC,
                               "revision": 1, "localization": [], "cod_risks": []}},
            {"id": PH_INTAKE, "sites": ["PH"], "role_suffix": "video-director",
             "label": "菲律宾编导接收八段拆解（本机合成）",
             "description": "按报告任务、版本和 SHA-256 接收内部方法；源片和业务资格未获授权。",
             "input_example": ph, "max_media_files": 0}]


PH_DIFFICULTY = {"direct_with_review": "需本地复核", "replace_language": "需改写语言",
                 "replace_culture": "需替换文化场景", "structure_only_reshoot": "仅可借结构并重拍"}
PH_CULTURE = {"local_wording": "核本地措辞与上下文", "audience_expectation": "核受众场景预期",
              "item_identity": "核本地同商品身份", "none_observed": "原片未确认文化前提"}
COD_RISKS = {"eligibility": "货到付款资格", "coverage": "地区覆盖", "fulfillment": "履约",
             "returns": "退换", "payment_trust": "支付信任"}
PH_METHODS = {
    "hook": "用自有获准同商品素材对比开头是否先交代产品身份。",
    "demonstration": "用获准同款实物连拍操作，避免通过切换主体制造效果。",
    "trust": "先核商品和规格证据，再决定是否展示说明或保证。",
    "emotion": "用另获准的本地场景测试情绪假设，结果保留待核。",
    "cta": "只拟内部行动提示，另核商品卡同款、COD 条件和地区。",
    "selling_point": "只选有同商品证据的卖点草案，未知事实留空。",
}


def ph_details(p, lines):
    amount(p["revision"], "报告版本", 1, 1000000, False)
    expected = {row["category"] for row in p["sections"]["elements"]}
    localization = {}
    for row in list_of(p["localization"], len(expected), len(expected), "菲律宾逐要素本地化"):
        check(row, "category difficulty cultural_precondition", "本地化")
        category, difficulty, culture = (row[k] for k in ("category", "difficulty", "cultural_precondition"))
        if (not all(isinstance(x, str) for x in (category, difficulty, culture))
                or category not in expected or category in localization
                or difficulty not in PH_DIFFICULTY or culture not in PH_CULTURE):
            raise WorkflowError("须逐一标注本地化难度和文化前提。")
        localization[category] = {"difficulty": difficulty, "cultural_precondition": culture}
    risks = list_of(p["cod_risks"], 1, len(COD_RISKS), "COD 待核因素")
    if any(not isinstance(k, str) or k not in COD_RISKS for k in risks) or len(set(risks)) != len(risks):
        raise WorkflowError("COD 因素须选有效且不重复的待核分类。")
    lines["elements"].extend(f"- {category} · {PH_DIFFICULTY[item['difficulty']]}；{PH_CULTURE[item['cultural_precondition']]}。"
                             for category, item in localization.items())
    lines["conversion"].extend(("COD 条件 · 未知；仅列待核 " + "、".join(COD_RISKS[risk] for risk in risks),
                                "菲律宾本地语言独立签核 · 未完成。"))
    lines["archive"].append("该原片明确为合成测试素材，未验证发布、母语、商品、商业许可或转化。")
    return {"revision": p["revision"], "localization": localization, "cod_risks": risks,
            "cod_eligibility": None, "cod_conditions": None, "native_review_verified": False,
            "synthetic_material": True}


def bind_ph_intake(site, p, load):
    check(p, "site scope job_id report_task_id report_revision report_sha256 receipt_sha256 recipient purpose",
          "菲律宾编导八段拆解收件")
    if (site, p["site"], p["scope"], p["recipient"], p["purpose"]) != (
            "PH", "PH", SYNTHETIC, PH_RECIPIENT, "internal_director_reference"):
        raise WorkflowError("仅本站编导可接收本机合成方法。")
    identify(p["job_id"], "编导收件编号")
    task_id(p["report_task_id"], "原八段报告")
    amount(p["report_revision"], "报告版本", 1, 1000000, False)
    report, pin = load(p["report_task_id"], site, PH_REPORT)
    if (p["report_revision"], p["report_sha256"], p["receipt_sha256"]) != (
            report["revision"], pin["result_sha256"], pin["receipt_sha256"]):
        raise WorkflowError("报告版号、文件或回执哈希不符。")
    if report["state"] != "REVIEW_REPORT_READY" or report["scope"] != SYNTHETIC:
        raise WorkflowError("仅可接收已完成的合成报告。")
    source = {**pin, "revision": report["revision"]}
    return {"report": source}, {
        "job_id": p["job_id"], "scope": SYNTHETIC, "recipient": PH_RECIPIENT,
        "purpose": p["purpose"], "synthetic_material": True,
        "methods": {category: PH_METHODS[category] for category in report["localization"]},
        "cod_risks": report["cod_risks"], "localization": report["localization"],
        "cod_eligibility": None, "cod_conditions": None, "real_product_id": None,
        "real_product_anchor": None, "real_metrics": dict.fromkeys(METRICS),
        "native_review_verified": False, "actual_recipient_confirmation": False,
        "source_media_included": False, "commercial_media_reuse_authorized": False,
    }


def render_ph_intake(result):
    pin = result["upstream"]["report"]
    lines = ["# 菲律宾编导 · 八段拆解接件（本机合成）", "",
             f"报告任务 {pin['task_id']}，版本 {pin['revision']}",
             f"报告 JSON SHA-256 {pin['result_sha256']}",
             f"报告回执 SHA-256 {pin['receipt_sha256']}", "",
             *(f"- {method} {PH_DIFFICULTY[result['localization'][key]['difficulty']]}，"
               f"{PH_CULTURE[result['localization'][key]['cultural_precondition']]}。"
               for key, method in result["methods"].items()), "",
             "COD 待核 · " + "、".join(COD_RISKS[risk] for risk in result["cod_risks"]),
             "本机测试角色回执。原音画、商品关联、实际 COD、成交、母语或发布均未确认。"]
    return ("\n".join(lines) + "\n").encode("utf-8")
