"""Deterministic, simulated replay of the live-ops financial review for CN and PH."""

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re

from role_tools import ROUTES, SYNTHETIC
from role_tools_extra import _load


class WorkflowError(ValueError):
    """The local live review could not consume the given observations."""


TOOL = "live-window-replay"
MONEY = (
    "buyer_paid_minor", "refunds_minor", "reimbursement_minor", "clawback_minor",
    "fees_minor", "fee_credits_minor", "costs_minor", "recoveries_minor", "ad_spend_minor",
)
INVENTORY = ("on_hand", "reserved", "safety_stock", "stop_below", "max_age_seconds", "snapshot_time")
BUDGET = ("cap_minor", "spent_minor", "committed_minor")
PH_CONTROLS = ("fulfillment_state", "cashflow_state")
SOURCE = ("event_id", "site", "session_id", "sku_id", "currency", "kind", "maturity",
          "state", "start", "end", "costs_through", "metrics")
DISCLAIMER = "合成观察离线复盘。金额使用最小货币单位。结果供人工复核，未取得正式价审或执行授权。"


def _example(site):
    _, currency = ROUTES[site]
    start, mid, end = ("2026-11-18T12:00:00+08:00", "2026-11-18T12:05:00+08:00",
                       "2026-11-18T12:10:00+08:00")
    common = {"site": site, "currency": currency, "session_id": f"DEMO-{site}-LIVE",
              "sku_id": "DEMO-CUP", "kind": "window", "maturity": "mature", "state": "complete"}
    if site == "PH":
        common["settlement_basis"] = "confirmed_collected"
    return {
        "site": site, "scope": SYNTHETIC, "currency": currency, "session_id": common["session_id"],
        "sku_id": common["sku_id"], "evaluation_time": end,
        "expected_windows": [{"start": start, "end": mid}, {"start": mid, "end": end}],
        "records": [
            {**common, "event_id": f"DEMO-{site}-{index}", "start": a, "end": b,
             "costs_through": b, "metrics": dict(zip(MONEY, amounts))}
            for index, a, b, amounts in (
                (1, start, mid, (5000, 500, 100, 0, 400, 0, 2000, 0, 500)),
                (2, mid, end, (6000, 600, 0, 0, 500, 0, 2500, 0, 750)),
            )
        ],
        "inventory": {"on_hand": 35, "reserved": 5, "safety_stock": 4, "stop_below": 10,
                      "max_age_seconds": 600, "snapshot_time": end},
        "budget": {"cap_minor": 2000, "spent_minor": 1250, "committed_minor": 350},
        **({"ph_controls": {"fulfillment_state": "safe", "cashflow_state": "safe"}} if site == "PH" else {}),
    }


def public_tools():
    return [{
        "id": TOOL, "label": "直播场次逐窗复盘", "role_suffix": "live-ops", "sites": ["CN", "PH"],
        "description": "按场次、SKU 和时间窗核对合成观察，复算退款后结算和投后贡献，报告库存、预算与复核动作。菲律宾区分已实收和待收。",
        "input_example": _example("CN"), "input_examples": {site: _example(site) for site in ("CN", "PH")},
        "max_media_files": 0,
    }]


def _object(value, allowed, name):
    if not isinstance(value, dict) or value.keys() - set(allowed):
        raise WorkflowError(f"{name} 文件结构或字段不符，请参照本站示例。")
    return value


def _text_identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,72}", value):
        raise WorkflowError(f"{name} 需要 1 至 72 位字母、数字、下划线或连字符。")
    return value


def _time(value, name):
    if not isinstance(value, str) or len(value) > 40:
        raise WorkflowError(f"{name} 需要包含时区的 ISO 8601 时间。")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        raise WorkflowError(f"{name} 需要包含时区的 ISO 8601 时间。") from None
    if stamp.utcoffset() is None:
        raise WorkflowError(f"{name} 需要显式时区。")
    return stamp


def _nullable_units(value, name):
    if value is not None and (type(value) is not int or not 0 <= value <= 10**12):
        raise WorkflowError(f"{name} 需要 0 到 10^12 的整数或 null。")
    return value


def _input(site, payload):
    allowed = {"site", "scope", "currency", "session_id", "sku_id", "evaluation_time",
               "expected_windows", "records", "inventory", "budget"}
    if site == "PH":
        allowed.add("ph_controls")
    _object(payload, allowed, "直播复盘")
    platform, currency = ROUTES[site]
    if (payload.get("site"), payload.get("currency")) != (site, currency) or payload.get("scope") != SYNTHETIC:
        raise WorkflowError("请输入所选站点与币种，并标记为 synthetic_offline。")
    evaluated = _time(payload.get("evaluation_time"), "评估时点")
    session = _text_identifier(payload.get("session_id"), "场次编号")
    sku = _text_identifier(payload.get("sku_id"), "SKU")
    windows = payload.get("expected_windows")
    if not isinstance(windows, list) or not 1 <= len(windows) <= 12:
        raise WorkflowError("请填写 1 至 12 个待核对的时段。")
    for window in windows:
        _object(window, {"start", "end"}, "时段")
        if not _time(window.get("start"), "时段开始") < _time(window.get("end"), "时段截止") <= evaluated:
            raise WorkflowError("时段开始必须早于截止，截止不能晚于评估时点。")

    records = payload.get("records")
    if not isinstance(records, list) or len(records) > 48:
        raise WorkflowError("观测请填写为列表，每次最多 48 条。没有观测可填写空列表。")
    for record in records:
        _object(record, SOURCE + (("settlement_basis",) if site == "PH" else ()), "窗口观测")
        if (record.get("site"), record.get("session_id"), record.get("sku_id"), record.get("currency")) != (
            site, session, sku, currency,
        ):
            raise WorkflowError("观测的站点、场次、SKU 或币种不符，请分开复盘。")
        _text_identifier(record.get("event_id"), "观测编号")
        if (record.get("kind") not in ("window", "cumulative")
                or record.get("maturity") not in ("mature", "estimated")
                or record.get("state") not in ("complete", "partial", "missing")):
            raise WorkflowError("观测所用类型、成熟度或完整性标记超出示例契约。")
        end = _time(record.get("end"), "观测截止")
        if record["kind"] == "window" and _time(record.get("start"), "观测开始") >= end:
            raise WorkflowError("窗口观测的起止时点顺序有误。")
        if record["kind"] == "cumulative" and record.get("start") is not None:
            raise WorkflowError("累计观测使用截止时点，start 留空。")
        if record.get("costs_through") is not None:
            _time(record["costs_through"], "成本截止")
        if site == "PH" and record.get("settlement_basis") not in (
            None, "confirmed_collected", "pending_order_value",
        ):
            raise WorkflowError("菲律宾观测使用已实收、待收订单额或 null。")
        metrics = _object(record.get("metrics"), MONEY, "金额观测")
        for field, value in metrics.items():
            _nullable_units(value, field)

    inventory = _object(payload.get("inventory", {}), INVENTORY, "库存快照")
    for field in INVENTORY[:-1]:
        _nullable_units(inventory.get(field), field)
    if inventory.get("snapshot_time") is not None:
        _time(inventory["snapshot_time"], "库存快照时间")
    budget = _object(payload.get("budget", {}), BUDGET, "预算")
    for field in BUDGET:
        _nullable_units(budget.get(field), field)
    controls = _object(payload.get("ph_controls", {}), PH_CONTROLS, "菲律宾场次条件")
    if any(value not in (None, "safe", "unsafe", "unknown") for value in controls.values()):
        raise WorkflowError("履约和现金流使用 safe、unsafe、unknown 或 null。")
    return {
        "case_id": session, "session_id": session, "platform": platform, "country": site,
        "currency": currency, "current_sku": sku, "target_sku": sku,
        "evaluation_time": payload["evaluation_time"], "expected_windows": windows, "records": records,
        "inventory": {**inventory, "paused": None}, "budget": {**budget, "requested_increment_minor": 0},
        "requested_action": "continue", "hard_risk": None, "ph_controls": {**controls, "site": site},
    }


def _review(project, site, case):
    module_name = "live_window_review_v6" if site == "CN" else "live_window_ph_v3"
    module = _load(project, module_name)
    evaluator = module.review_cn if site == "CN" else module.review_ph

    def price_unverified(_case):
        return {"proven": False, "reason": "no_trusted_price_evidence_in_client_replay"}

    result = evaluator(case, price_unverified)
    if result.get("execution_authorized") is not False or result.get("release_status") != "not_authorized":
        raise WorkflowError("项目复盘的离线授权状态不符，未出具结果。")
    source = Path(module.__file__)
    scripts = (project / "scripts").resolve()
    dependencies = {source, scripts / "live_window_review_v6.py", scripts / "ad_decision_contract.py"}
    return result, {
        file.relative_to(project.resolve()).as_posix(): sha256(file.read_bytes()).hexdigest()
        for file in sorted(dependencies)
    }


def _findings(case, review):
    findings = [{
        "code": "formal_price_review_absent", "status": "unknown",
        "evidence": {"price_proven": review["price_proven"]},
        "action": "开播、改价或切品前，按同站同 SKU 和生效修订取得正式价审及人工授权。",
    }]
    for window in review["windows"]:
        if window["mature"] is None:
            findings.append({
                "code": "mature_window_incomplete", "status": "unknown",
                "evidence": {"start": window["start"], "end": window["end"],
                             "reasons": window["blocking_reasons"]},
                "action": "由直播运营核对原始观察和精确窗口，补齐同口径的退款、成本及截止，冲突须回源确认。",
            })
    if review["data_errors"]:
        findings.append({
            "code": "observation_integrity", "status": "review",
            "evidence": review["data_errors"],
            "action": "校对冲突事件编号、重叠时间窗或未来记录；有冲突时保留原始各候选。",
        })
    if case["country"] == "PH":
        basis = review["ph_record_basis"]
        if basis["unproven_records"] or basis["estimated_basis_conflict"]:
            findings.append({
                "code": "ph_settlement_basis_unproven", "status": "unknown",
                "evidence": basis,
                "action": "按菲律宾已实收和待收订单分账并回源核对；待收订单额不能填补实收。",
            })
        for field in PH_CONTROLS:
            state = case["ph_controls"].get(field)
            if state != "safe":
                findings.append({
                    "code": field, "status": "review" if state == "unsafe" else "unknown",
                    "evidence": state,
                    "action": "由菲律宾站运营核对本地履约与回款资料，确认前暂停讨论扩大规模。",
                })
    inventory = case["inventory"]
    available = review["inventory_available"]
    stop = inventory.get("stop_below")
    stamp, max_age = inventory.get("snapshot_time"), inventory.get("max_age_seconds")
    age = (_time(case["evaluation_time"], "评估时点") - _time(stamp, "库存快照时间")).total_seconds() if stamp else None
    current = age is not None and max_age is not None and 0 <= age <= max_age
    if available is None or stop is None or not current or available < stop:
        findings.append({
            "code": "inventory_needs_review",
            "status": "review" if current and available is not None and stop is not None and available < stop else "unknown",
            "evidence": {"snapshot_available_units": available, "age_seconds": age,
                         "max_age_seconds": max_age, "simulated_stop_below": stop},
            "action": "核对同 SKU 的在手、预留和安全库存及快照时间，确认缺货或超卖风险。",
        })
    remaining = review["budget_remaining_minor"]
    if remaining is None or remaining <= 0:
        findings.append({
            "code": "budget_needs_review", "status": "unknown" if remaining is None else "review",
            "evidence": {"budget_remaining_minor": remaining},
            "action": "由投手或负责人对账预算上限、已消耗和已承诺支出，确认前不建议加投。",
        })
    # Cross-window integrity errors forbid treating the aggregate as settled in
    # the client even if the source engine could reconstruct individual rows.
    aggregate = review["cumulative"]["mature"]
    if review["current_windows_complete"] and aggregate and aggregate["post_ad_profit_minor"] < 0:
        findings.append({
            "code": "negative_post_ad_profit", "status": "review",
            "evidence": {"post_ad_profit_minor": aggregate["post_ad_profit_minor"]},
            "action": "联合投手回看同场成熟退款、费用及投流消耗；目前变化不能证明广告增量。",
        })
    return findings


def _report(result):
    lines = [
        "# 直播场次逐窗复盘", "", DISCLAIMER, "",
        f"站点 {result['site']} · 场次 {result['session_id']} · SKU {result['sku_id']} · 币种 {result['currency']}",
        "", "## 分窗表现", "",
    ]
    for row in result["windows"]:
        mature = row["mature"]
        summary = (f"成熟留存收入 {mature['retained_revenue_minor']}，投后贡献 {mature['post_ad_profit_minor']}"
                   if mature is not None else "同口径成熟账未知")
        lines.append(f"- {row['start']} → {row['end']}　{summary}")
    lines += ["", "## 场次经营复核", ""]
    aggregate = result["mature_total"]
    if aggregate is None:
        lines.append("场次成熟汇总未知。已知窗口及候选观察在复盘 JSON 中留存。")
    else:
        lines.append(f"所列窗口成熟留存收入 {aggregate['retained_revenue_minor']}，"
                     f"投后贡献 {aggregate['post_ad_profit_minor']}。")
    if result["estimated_total"] is not None:
        lines.append("暂估合计另存于复盘 JSON，保持与成熟账分离。")
    available = result["inventory_snapshot_available"]
    remaining = result["budget_remaining_minor"]
    lines.extend([
        f"库存快照可用量 {available if available is not None else '未知'}。",
        f"扣除已用和承诺后的预算 {remaining if remaining is not None else '未知'}。",
        "投后贡献仅涵盖表内成本与支出。跨窗表现变化有待核对流量、话术与时段，不能据此认定广告增量。",
    ])
    lines += ["", "## 需人工复核", ""]
    lines += [f"- {finding['action']}" for finding in result["findings"]]
    return "\n".join(lines) + "\n"


def _save_json(path, value):
    with path.open("x", encoding="utf8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return sha256(path.read_bytes()).hexdigest()


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress):
    if tool_id != TOOL or site not in ("CN", "PH") or assets:
        raise WorkflowError("直播复盘仅支持抖音中国、TikTok 菲律宾的结构化合成观察。")
    project = Path(project).resolve()
    case = _input(site, payload)
    if progress is not None:
        progress("正在复算直播窗口和检查证据")
    try:
        review, dependency_hashes = _review(project, site, case)
    except (ValueError, OSError, ImportError) as error:
        raise WorkflowError("项目离线直播复盘器无法执行，请检查项目依赖和输入。") from error
    result = {
        "site": site, "platform": case["platform"], "currency": case["currency"],
        "session_id": case["session_id"], "sku_id": case["current_sku"],
        "scope": SYNTHETIC, "release_status": "not_authorized", "execution_authorized": False,
        "model_called": False, "provider_called": False, "disclaimer": DISCLAIMER,
        "workflow_status": "human_review_required", "aggregate_basis": "sum_of_expected_windows_only",
        "windows": review["windows"], "mature_total": (
            review["cumulative"]["mature"] if review["current_windows_complete"] else None
        ),
        "estimated_total": review["cumulative"]["estimated"] if not review["data_errors"] else None,
        "inventory_snapshot_available": review["inventory_available"],
        "budget_remaining_minor": review["budget_remaining_minor"],
        "duplicate_event_count": review["duplicate_event_count"],
        "conflicting_event_evidence": review["conflicting_event_evidence"],
        "findings": _findings(case, review), "raw_engine_result": review,
    }

    output = Path(task_dir) / "live-window-replay"
    try:
        output.mkdir()
    except FileExistsError:
        raise WorkflowError("此任务已有直播复盘记录。请从任务历史取件，或另建任务复盘。") from None
    paths = {name: output / filename for name, filename in (
        ("input", "input.json"), ("json", "review.json"), ("receipt", "receipt.json"), ("report", "review.md"),
    )}
    input_hash = _save_json(paths["input"], payload)
    review_hash = _save_json(paths["json"], result)
    with paths["report"].open("x", encoding="utf8", newline="\n") as stream:
        stream.write(_report(result))
    _save_json(paths["receipt"], {
        "workflow": TOOL, "engine_version": review["version"], "role": case["platform"] + "-live-ops",
        "scope": SYNTHETIC, "execution_mode": "deterministic_local_replay",
        "model_called": False, "provider_called": False, "execution_authorized": False,
        "input_sha256": input_hash, "review_sha256": review_hash,
        "report_sha256": sha256(paths["report"].read_bytes()).hexdigest(),
        "local_engine_sources": dependency_hashes,
        "method_sources": (
            f".agents/skills/{case['platform']}-live-ops-playbook/SKILL.md",
            "templates/直播运营/直播复盘表.md", "docs/直播逐窗离线复盘与换品工作流.md",
        ),
    })
    return result, paths

