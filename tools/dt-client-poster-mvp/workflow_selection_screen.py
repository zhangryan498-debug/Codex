"""Evidence-bound, synthetic candidate screening for the local selection workbench."""

from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_competitor_supplier import TOOL as SUPPLIER_TOOL
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

TOOL = "selection-offline-screen"
TOOLS = (TOOL,)
FILES = {"json": "selection-screening.json", "report": "selection-screening.md", "receipt": "selection-screening-receipt.json"}
DIMENSIONS = {
    "CN": {"hook": "内容爆点", "qianchuan": "千川承接", "price_fit": "客单适配",
           "refund_risk": "退货风险", "review_risk": "差评与宣称风险"},
    "PH": {"logistics": "跨境物流", "creator_demo": "达人可讲性", "certification": "类目认证",
           "cod_risk": "COD 退拒", "localization": "本地化", "site_fees": "菲律宾费率"},
}
BOOLEAN_GATES = {
    "CN": ("unsupported_claim", "forced_bundle_or_review", "unstable_supply", "ad_eligibility"),
    "PH": ("unsupported_claim", "forced_bundle_or_review", "unstable_supply", "certification_and_fulfillment"),
}
RATE_GATE = {"CN": "refund_profit_erosion", "PH": "cod_terminal_loss"}


class WorkflowError(ValueError):
    pass


def identity(value):
    if not isinstance(value, list) or len(value) != 3:
        raise WorkflowError("对象须含商家、商品和 SKU 三个标识。")
    for part in value:
        identify(part, "商品身份")
    return tuple(value)


def validate(site, p):
    check(p, "site currency scope screening_id as_of research_task_id rubric candidates owner next_review_at", "候选初筛")
    if (p["site"], p["currency"], p["scope"]) != (site, ROUTES[site][1], SYNTHETIC):
        raise WorkflowError("仅支持本站和本币的合成筛选。")
    identify(p["screening_id"], "评估编号")
    task_id(p["research_task_id"], "同近款来源")
    term(p["owner"], "评审人")
    cutoff = stamp(p["as_of"])
    if stamp(p["next_review_at"]) < cutoff:
        raise WorkflowError("补证截止不得早于评估时间。")
    dims = DIMENSIONS[site]
    r = p["rubric"]
    check(r, "source_ref score_floor rate_limit_bps rate_comparison", "合成评分与红线依据")
    term(r["source_ref"], "合成口径出处")
    amount(r["score_floor"], "合成总分线", len(dims), len(dims) * 5, False)
    amount(r["rate_limit_bps"], "合成红线", 0, 10000, False)
    if r["rate_comparison"] not in ("gt", "gte"):
        raise WorkflowError("须冻结合成红线的阈值和是否包含边界。")
    if not isinstance(p["candidates"], list) or not 1 <= len(p["candidates"]) <= 5:
        raise WorkflowError("提交一至五个可辨认候选。")
    keys = set()
    for c in p["candidates"]:
        check(c, "identity sku_revision dimensions boolean_gates rate_gate evidence counterevidence", "候选")
        amount(c["sku_revision"], "SKU 证据修订", 1, 10**6, False)
        key = identity(c["identity"])
        if key in keys:
            raise WorkflowError("同一对象在本批只评分一次。")
        keys.add(key)
        ev = c["evidence"]
        if not isinstance(ev, dict) or len(ev) > 36:
            raise WorkflowError("每个候选最多携带 36 条具名合成证据。")
        for eid, item in ev.items():
            identify(eid, "证据编号")
            check(item, "site currency identity sku_revision scope captured_at statement source_ref", "证据出处")
            amount(item["sku_revision"], "证据 SKU 修订", 1, 10**6, False)
            if item["sku_revision"] != c["sku_revision"]:
                raise WorkflowError("证据与被评估 SKU 的修订不一致。")
            if (item["site"], item["currency"], item["scope"], identity(item["identity"])) != (site, p["currency"], SYNTHETIC, key):
                raise WorkflowError("证据站点、币种或 SKU 不匹配。")
            if stamp(item["captured_at"]) > cutoff:
                raise WorkflowError("证据晚于本次评估截点。")
            term(item["statement"], "合成观察事实")
            term(item["source_ref"], "证据来源")
        check(c["dimensions"], " ".join(dims), "岗位维度")
        for row in c["dimensions"].values():
            check(row, "rating reason evidence_id", "维度判断")
            amount(row["rating"], "评分", 1, 5)
            term(row["reason"], "评分理由", True)
            term(row["evidence_id"], "评分依据", True)
        check(c["boolean_gates"], " ".join(BOOLEAN_GATES[site]), "红线项目")
        for row in c["boolean_gates"].values():
            check(row, "hit evidence_id", "红线证据")
            if row["hit"] is not None and type(row["hit"]) is not bool:
                raise WorkflowError("红线须标明确命中、证据支持未命中或未知。")
            term(row["evidence_id"], "红线依据", True)
        row = c["rate_gate"]
        check(row, "numerator denominator evidence_id", "红线样本")
        amount(row["numerator"], "损失分子")
        amount(row["denominator"], "损失分母")
        term(row["evidence_id"], "样本依据", True)
        counter = c["counterevidence"]
        if not isinstance(counter, list) or len(counter) > 12:
            raise WorkflowError("使用具名反证。")
        for row in counter:
            check(row, "against evidence_id reason resolution_evidence_id resolution", "反证")
            if row["against"] not in (*dims, *BOOLEAN_GATES[site], RATE_GATE[site], "match"):
                raise WorkflowError("反证需关联维度、匹配或门禁。")
            term(row["reason"], "反证理由")
            for k in ("evidence_id", "resolution_evidence_id", "resolution"):
                term(row[k], k, True)


def bind(site, tool, p, fetch):
    validate(site, p)
    upstream, pin = fetch(p["research_task_id"], site, SUPPLIER_TOOL)
    if (upstream["site"], upstream["currency"], upstream["scope"]) != (site, p["currency"], SYNTHETIC):
        raise WorkflowError("同近款回执须同站、本币和合成范围。")
    if stamp(upstream["evaluated_at"]) > stamp(p["as_of"]):
        raise WorkflowError("同近款回执晚于本次评估截点。")
    by_key = {tuple(row["identity"]): row for row in upstream["products"]}
    if any(tuple(c["identity"]) not in by_key for c in p["candidates"]):
        raise WorkflowError("候选商家、商品、SKU 必须与上游回执一致。")
    return {"research": pin}, upstream


def request_key(p):
    return p["screening_id"]


def gate_status(hit, source):
    if hit is None or source is None:
        return "PENDING"
    return "HIT" if hit else "CLEARED_FIXTURE"


def classify_match(row, source_current):
    if not source_current or row["match"].startswith("PENDING"):
        return "PENDING_MATCH"
    if row["match"] == "EXACT_PRODUCT":
        if row["exact_sku"] and any(row["target_candidate_spec_differences"].values()):
            return "PENDING_MATCH"
        return {True: "EXACT_SKU", False: "SKU_MISMATCH", None: "PENDING_MATCH"}[row["exact_sku"]]
    return {"NEAR_SAME_USE": "NEAR_REFERENCE_ONLY", "NO_MATCH": "NO_MATCH"}.get(row["match"], "PENDING_MATCH")


def review_candidate(site, c, match, rubric, current):
    evidence = c["evidence"]
    dims = {}
    for k, row in c["dimensions"].items():
        source = evidence.get(row["evidence_id"])
        rating = row["rating"] if source and row["reason"] else None
        dims[k] = {"label": DIMENSIONS[site][k], "rating": rating, "reported_rating": row["rating"],
                   "reason": row["reason"], "evidence_id": row["evidence_id"],
                   "review_state": "RATED_FIXTURE" if rating is not None else "PENDING"}
    gates = {k: {**v, "review_state": gate_status(v["hit"], evidence.get(v["evidence_id"]))}
             for k, v in c["boolean_gates"].items()}
    rate = c["rate_gate"]
    n, d = rate["numerator"], rate["denominator"]
    rate_valid = n is not None and d is not None and 0 < d and (site == "CN" or n <= d)
    delta = n * 10000 - rubric["rate_limit_bps"] * d if rate_valid else None
    hit = None if delta is None else delta > 0 or (delta == 0 and rubric["rate_comparison"] == "gte")
    gates[RATE_GATE[site]] = {**rate, "limit_bps": rubric["rate_limit_bps"], "comparison": rubric["rate_comparison"],
                             "review_state": gate_status(hit, evidence.get(rate["evidence_id"]))}
    counters = []
    unresolved = set()
    for counter in c["counterevidence"]:
        ref, resolution = counter["evidence_id"], counter["resolution_evidence_id"]
        closed = bool(counter["resolution"] and resolution != ref and ref in evidence and resolution in evidence
                      and stamp(evidence[resolution]["captured_at"]) >= stamp(evidence[ref]["captured_at"]))
        if not closed:
            unresolved.add(counter["against"])
        counters.append({**counter, "state": "RESOLVED_FIXTURE" if closed else "PENDING_REVIEW"})
    for k in unresolved.intersection(gates):
        gates[k]["initial_state"] = gates[k]["review_state"]
        gates[k]["review_state"] = "PENDING_CONFLICT"
    matches = classify_match(match, current)
    positives = [k for k, v in gates.items() if v["review_state"] == "HIT"]
    unknowns = [k for k, v in gates.items() if v["review_state"].startswith("PENDING")]
    missing = [k for k, v in dims.items() if v["rating"] is None]
    state = "READY_FOR_OFFLINE_TRIAL_REVIEW"
    points = None
    # A sourced hard stop precedes match or score eligibility.
    if positives:
        state = "ELIMINATE_RED_LINE"
    elif matches in ("NEAR_REFERENCE_ONLY", "NO_MATCH", "SKU_MISMATCH"):
        state = matches
    elif matches == "PENDING_MATCH" or unresolved or unknowns or missing:
        state = "PENDING_EVIDENCE"
    else:
        points = sum(r["rating"] for r in dims.values())
        if points < rubric["score_floor"]:
            state = "REVIEW_BELOW_FIXTURE_SCORE"
    return {"identity": c["identity"], "sku_revision": c["sku_revision"], "state": state,
            "match_state": matches, "source_current": current, "sku_revision_inherited_from_research": False,
            "match_comparison": {k: match[k] for k in (
                "match", "exact_sku", "target_candidate_spec_differences", "matched_candidates"
            )}, "score": points, "max_score": len(dims) * 5, "scoring_basis": "equal_weight_synthetic_review_annotations",
            "dimensions": dims, "missing_dimensions": missing, "gates": gates,
            "hit_gates": positives, "pending_gates": unknowns, "evidence": evidence, "counterevidence": counters,
            "candidate_facts_inherited_from_near_match": False,
            "proposed_action": {
                "READY_FOR_OFFLINE_TRIAL_REVIEW": "送选品员审阅合成测品方案。",
                "REVIEW_BELOW_FIXTURE_SCORE": "复核低分维及证据后另拟方案。",
                "ELIMINATE_RED_LINE": "按本次合成红线淘汰。",
            }.get(state, "补本款本 SKU 证据，保留现有初筛结果。")}


def receipt(r, b):
    return {"task_id": r["task_id"], "site": r["site"], "input_sha256": r["input_sha256"], "upstream": r["upstream"],
            "result_sha256": sha256(b["json"]).hexdigest(), "report_sha256": sha256(b["report"]).hexdigest(),
            "execution_authorized": False}


def run_workflow(project, site, tool, p, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("候选筛选使用本机已保存合成证据及具名观察。")
    pins, source = linked
    by_key = {tuple(row["identity"]): row for row in source["products"]}
    cutoff = stamp(p["as_of"])
    src = source["candidates"]
    current = stamp(src["captured_at"]) <= cutoff < stamp(src["valid_until"])
    rows = [review_candidate(site, c, by_key[tuple(c["identity"])], p["rubric"], current) for c in p["candidates"]]
    tiers = {}
    for row in rows:
        if row["state"] == "READY_FOR_OFFLINE_TRIAL_REVIEW":
            tiers.setdefault(row["score"], []).append(row["identity"])
    ranking = [{"score": n, "identities": sorted(tiers[n])} for n in sorted(tiers, reverse=True)]
    r = {**{k: p[k] for k in ("site", "currency", "scope", "screening_id", "as_of", "rubric", "owner", "next_review_at")},
         "state": "SCREENING_RECORDED", "tool_id": tool, "task_id": folder.name, "upstream": pins,
         "input_sha256": sha256(encode(p)).hexdigest(), "candidates": rows, "ranking": ranking,
         "execution_authorized": False, "actual_platform_policy_verified": False,
         "sample_order_or_outreach_executed": False, "model_called": False}
    lines = [f"# {site} 选品初筛 · {p['screening_id']}", "",
             "合成证据与规则，只供本机离线评审。", "",
             f"评审人：{p['owner']} · 补证截止：{p['next_review_at']}"]
    for row in rows:
        lines += ["", "## " + " / ".join(row["identity"]), "",
                  f"{row['state']} · {row['match_state']} · 综合分 {row['score'] if row['score'] is not None else '待核或不计'}",
                  f"处置：{row['proposed_action']}"]
        for d in row["dimensions"].values():
            lines.append(f"- {d['label']}：{d['rating'] if d['rating'] is not None else '待核'}，"
                         f"{d['reason'] or '待补理由'}（证据 {d['evidence_id'] or '待补'}）")
        for k, v in row["gates"].items():
            lines.append(f"- 红线 {k}：{v['review_state']}（证据 {v['evidence_id'] or '待补'}）")
        for item in row["counterevidence"]:
            lines.append(f"- 反证：{item['reason']}（{item['evidence_id'] or '待补'}，{item['state']}）")
    raw = {"json": encode(r), "report": ("\n".join(lines) + "\n").encode("utf-8")}
    raw["receipt"] = encode(receipt(r, raw))
    paths = {k: folder / n for k, n in FILES.items()}
    for k, path in paths.items():
        with path.open("xb") as stream:
            stream.write(raw[k])
    return r, paths


def verify_saved(task, files):
    r = task["result"]
    if (task["local_files"] != FILES or r != json.loads(files["json"])
            or r["upstream"] != task["source"]["role_packet_pins"]
            or (r["task_id"], r["site"], r["tool_id"]) != (task["id"], task["site"], task["source"]["tool_id"])
            or json.loads(files["receipt"]) != receipt(r, files)):
        raise WorkflowError("选品初筛产物、来源或回执无法复核。")


def public_tools():
    return [{"id": TOOL, "sites": ["CN", "PH"], "role_suffix": "selection", "label": "候选五维／六维初筛",
             "description": "结合本机同近款回执与具名合成证据，核对红线和评估依据。", "input_example": {}}]

