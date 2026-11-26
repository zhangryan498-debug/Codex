"""Replay local synthetic ad receipts using the D&T advertising decision contract."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from types import ModuleType


class WorkflowError(ValueError):
    pass


TOOL_ID = "ad-period-review"
LABEL = "广告多窗口复盘与预算核账"
CONTRACT_PATH = "scripts/ad_decision_contract.py"
CONTRACT_SHA = "b901261a5706415c86ad112c809c91ba39ba599caa210a0bbee574429887c5c0"
METHOD_PATH = "docs/双平台广告复盘口径与增量验证规范.md"
TEMPLATE_PATH = "templates/广告投手/复盘口径与增量验证表.md"
SITES = {
    "CN": ("CNY", "Asia/Shanghai", "douyin", {"qianchuan_product", "qianchuan_live", "qianchuan_multiplication"}),
    "PH": ("PHP", "Asia/Manila", "tiktok", {"product_gmv_max", "live_gmv_max_target_roi", "live_gmv_max_max_delivery", "tiktok_purchase_ads"}),
}
AD_FIELDS = ("gross_minor", "spend_minor", "settled_minor", "variable_cost_minor")
RISK_FIELDS = ("product_binding", "material_permission", "floor_price", "fulfillment", "cash_flow")
STATES = ("complete", "partial", "missing", "stale", "zero")
ID_RE = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
TIME_RE = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00"


def _example(site):
    currency, timezone_name, _, _ = SITES[site]
    basis = {
        "attribution_id": f"{site}-SANDBOX-ATTR-V1",
        "maturity_id": f"{site}-SANDBOX-REFUND-V1",
        "shop_definition_id": f"{site}-SANDBOX-SHOP-DEDUP-V1",
    }

    def window(day, gross, spend, settled, cost, shop_sales, spent, commitments):
        return {
            "start": f"2026-11-{day:02d}T00:00:00+08:00",
            "end": f"2026-11-{day + 1:02d}T00:00:00+08:00",
            "captured_at": f"2026-11-{day + 3:02d}T09:00:00+08:00",
            "source_id": f"{site}-SIM-AD-{day}",
            "measurement_basis": dict(basis),
            "state": "complete",
            "finance_maturity": "mature",
            "gross_minor": gross,
            "spend_minor": spend,
            "settled_minor": settled,
            "variable_cost_minor": cost,
            "budget": {
                "receipt_id": f"{site}-SIM-CAP-{day}",
                "as_of": f"2026-11-{day + 1:02d}T00:00:00+08:00",
                "spent_cumulative_minor": spent,
                "committed_unbooked_minor": commitments,
            },
            "shop": {"receipt_id": f"{site}-SIM-SHOP-{day}", "state": "complete", "dedup_sales_minor": shop_sales},
            "risks": {**dict.fromkeys(RISK_FIELDS, "clear"), "stock_units": 38, "product_changed_unverified": False},
        }

    return {
        "site": site,
        "currency": currency,
        "timezone": timezone_name,
        "data_origin": "synthetic_offline",
        "account_id": f"{site}-SIM-ACCOUNT",
        "shop_id": f"{site}-SIM-SHOP",
        "plan_id": f"{site}-SIM-PLAN",
        "product_id": f"{site}-SIM-PRODUCT",
        "channel": "qianchuan_live" if site == "CN" else "product_gmv_max",
        "budget_cycle": {
            "id": f"{site}-SIM-CYCLE",
            "start": "2026-11-08T00:00:00+08:00",
            "end": "2026-11-17T00:00:00+08:00",
            "cap_minor": 10000,
            "opening_spent_minor": 250,
        },
        "stock_stop_units": 20,
        "windows": [
            window(8, 5000, 1000, 4100, 2500, 9000, 1250, 500),
            window(9, 9500, 2000, 8500, 5800, 12000, 3250, 400),
        ],
    }


def public_tools():
    return [{
        "id": TOOL_ID,
        "label": LABEL,
        "role_suffix": "ad-buyer",
        "sites": list(SITES),
        "description": "按原投手契约复算 2–14 个本地模拟窗口，区分成熟财务、平台归因、店铺总额、周期预算与硬停止。金额用分，未知填 null。",
        "input_example": _example("CN"),
        "input_examples": {site: _example(site) for site in SITES},
        "max_media_files": 0,
    }]


def _fields(obj, names, where):
    if not isinstance(obj, dict) or set(obj) != set(names.split()):
        raise WorkflowError(f"{where} 字段不匹配，请参照本站示例。")


def _id(value, where):
    if not isinstance(value, str) or re.fullmatch(ID_RE, value) is None:
        raise WorkflowError(f"{where} 须为 1–64 字符的本地标识，仅支持英文字母、数字、点、下划线及连字符。")


def _timestamp(value, where):
    if not isinstance(value, str) or re.fullmatch(TIME_RE, value) is None:
        raise WorkflowError(f"{where} 须为本站 ISO 时间，含 +08:00 时差及秒。")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise WorkflowError(f"{where} 日期或时间无效。") from None


def _number(value, where):
    if value is not None and (type(value) is not int or not 0 <= value <= 10**12):
        raise WorkflowError(f"{where} 须为 0 到 1000000000000 的整数或 null，金额单位为分。")


def _source_id(value, used_ids, site, where):
    _id(value, where)
    if not value.startswith(site + "-"):
        raise WorkflowError(f"{where} 必须以本站简码 {site}- 开头。")
    if value in used_ids:
        raise WorkflowError("同一任务内的来源回执编号不可重复。")
    used_ids.add(value)


def _load_contract(project):
    project = Path(project).resolve()
    path = (project / CONTRACT_PATH).resolve()
    if not path.is_relative_to(project):
        raise WorkflowError("广告决策契约路径超出项目范围。")
    try:
        blob = path.read_bytes()
    except OSError:
        raise WorkflowError("缺少 D&T 广告决策契约，请检查项目目录。") from None
    if hashlib.sha256(blob).hexdigest() != CONTRACT_SHA:
        raise WorkflowError("D&T 广告决策契约版本与复盘入口不匹配，请同步已审版本。")
    module = ModuleType(path.stem)
    module.__file__ = str(path)
    exec(compile(blob, str(path), "exec"), module.__dict__)
    return module


def _basis(value, site):
    _fields(value, "attribution_id maturity_id shop_definition_id", "measurement_basis")
    for field, identifier in value.items():
        _id(identifier, field)
        if not identifier.startswith(site + "-"):
            raise WorkflowError(f"{field} 必须以本站简码 {site}- 开头。")


def _validate_window(window, index, site, used_ids):
    prefix = f"windows[{index}]"
    _fields(window, "start end captured_at source_id measurement_basis state finance_maturity gross_minor "
            "spend_minor settled_minor variable_cost_minor budget shop risks", prefix)
    start, end, captured = (_timestamp(window[key], prefix + "." + key)
                            for key in ("start", "end", "captured_at"))
    if not start < end <= captured:
        raise WorkflowError(f"{prefix} 必须满足开始早于结束、截点不早于结束。")
    _source_id(window["source_id"], used_ids, site, prefix + ".source_id")
    _basis(window["measurement_basis"], site)
    for key in AD_FIELDS:
        _number(window[key], prefix + "." + key)
    state = window["state"]
    maturity = window["finance_maturity"]
    gross, spend, settled, cost = (window[key] for key in AD_FIELDS)
    if state not in STATES or maturity not in ("mature", "pending", "unknown"):
        raise WorkflowError(f"{prefix} 状态或结算成熟度无效。")
    if state in ("missing", "stale") and any(window[key] is not None for key in AD_FIELDS):
        raise WorkflowError(f"{prefix} 缺失/过期窗口的广告金额应填 null。")
    if state == "zero" and (gross, spend) != (0, 0):
        raise WorkflowError(f"{prefix} 零窗口必须有明确的零成交和零花费。")
    if state == "complete" and (gross is None or spend is None):
        raise WorkflowError(f"{prefix} 已齐窗必须含成交和花费。")
    if maturity == "mature" and (state not in ("complete", "zero") or settled is None or cost is None):
        raise WorkflowError(f"{prefix} 成熟结算须有已齐/明确零窗口、退款后结算及同批变动成本。")
    budget = window["budget"]
    if budget is not None:
        _fields(budget, "receipt_id as_of spent_cumulative_minor committed_unbooked_minor", prefix + ".budget")
        _source_id(budget["receipt_id"], used_ids, site, prefix + ".budget.receipt_id")
        if _timestamp(budget["as_of"], prefix + ".budget.as_of") != end:
            raise WorkflowError(f"{prefix} 预算回执须对应同窗口的结束时刻，不能混用后来余额。")
        for key in ("spent_cumulative_minor", "committed_unbooked_minor"):
            _number(budget[key], prefix + ".budget." + key)
    shop = window["shop"]
    if shop is not None:
        _fields(shop, "receipt_id state dedup_sales_minor", prefix + ".shop")
        _source_id(shop["receipt_id"], used_ids, site, prefix + ".shop.receipt_id")
        _number(shop["dedup_sales_minor"], prefix + ".shop.dedup_sales_minor")
        if (shop["state"] not in STATES or
                (shop["state"] in ("missing", "stale") and shop["dedup_sales_minor"] is not None) or
                (shop["state"] == "zero" and shop["dedup_sales_minor"] != 0) or
                (shop["state"] == "complete" and shop["dedup_sales_minor"] is None)):
            raise WorkflowError(f"{prefix} 店铺去重额与覆盖状态不符。")
    risks = window["risks"]
    _fields(risks, " ".join((*RISK_FIELDS, "stock_units", "product_changed_unverified")), prefix + ".risks")
    _number(risks["stock_units"], prefix + ".risks.stock_units")
    if (any(risks[name] not in ("clear", "failed", "unknown") for name in RISK_FIELDS) or
            risks["product_changed_unverified"] is not None and type(risks["product_changed_unverified"]) is not bool):
        raise WorkflowError(f"{prefix} 风险状态无效。")
    return start, end, captured


def _budget_review(ad, source, cycle, prior_spent, last_known_spent):
    receipt = source["budget"]
    spent = receipt["spent_cumulative_minor"] if receipt is not None else None
    committed = receipt["committed_unbooked_minor"] if receipt is not None else None
    cap = cycle["cap_minor"]
    window_complete = source["state"] in ("complete", "zero")
    reset = spent is not None and last_known_spent is not None and spent < last_known_spent
    comparable = spent is not None and prior_spent is not None and window_complete
    spend_delta = spent - prior_spent if comparable else None
    conflict = reset or comparable and spend_delta != source["spend_minor"]
    available = ad.remaining(cap, spent, committed) if not conflict else None
    cap_exceeded = spent is not None and cap is not None and (
        spent > cap or committed is not None and spent + committed > cap)
    return {
        "cycle_id": cycle["id"],
        "receipt_id": receipt["receipt_id"] if receipt is not None else None,
        "as_of": receipt["as_of"] if receipt is not None else None,
        "cap_minor": cap,
        "spent_cumulative_minor": spent,
        "committed_unbooked_minor": committed,
        "available_minor": available,
        "cumulative_spend_delta_minor": spend_delta,
        "spend_reconciled": not conflict if comparable or reset else None,
        "receipt_conflict": conflict,
        "cap_exceeded": cap_exceeded,
    }


def _review_window(ad, source, cycle, stop_stock, prior_spent, last_known_spent):
    gross, spend, settled, cost = (source[name] for name in AD_FIELDS)
    observed = ad.window(gross, spend, source["state"])
    mature = source["finance_maturity"] == "mature"
    finance = ad.ledger(gross, spend, settled, cost) if mature else None
    budget = _budget_review(ad, source, cycle, prior_spent, last_known_spent)
    risks = source["risks"]
    hard_stops = [field for field in RISK_FIELDS if risks[field] == "failed"]
    if budget["cap_exceeded"]:
        hard_stops.append("budget_cap_exceeded")
    if budget["receipt_conflict"]:
        hard_stops.append("budget_receipt_conflict")
    if finance is not None and finance["post_ad"] < 0:
        hard_stops.append("mature_post_ad_loss")
    if risks["product_changed_unverified"] is True:
        hard_stops.append("product_changed_unverified")
    stock = risks["stock_units"]
    if stock is not None and stop_stock is not None and stock < stop_stock:
        hard_stops.append("inventory_below_stop")
    gaps = [field for field in RISK_FIELDS if risks[field] == "unknown"]
    if budget["available_minor"] is None:
        gaps.append("available_budget")
    if budget["spend_reconciled"] is not True:
        gaps.append("budget_spend_reconciliation")
    if stock is None or stop_stock is None:
        gaps.append("stock_or_approved_stop")
    if risks["product_changed_unverified"] is None:
        gaps.append("product_change_status")
    if not mature:
        gaps.append("mature_financial_ledger")
    if not observed["decision_eligible"]:
        gaps.append("eligible_observation")
    decision = ad.review({
        "requested_action": "hold",
        "data_state": "partial" if gaps else source["state"],
        "hard_risk": bool(hard_stops),
        "product_changed_unverified": risks["product_changed_unverified"],
        "stock": stock,
        "stop_stock": stop_stock,
    })
    shop = source["shop"]
    shop_visible = shop is not None and shop["state"] in ("complete", "zero")
    return {
        "source_id": source["source_id"],
        "budget": budget,
        "hard_stops": hard_stops,
        "decision_gaps": gaps,
        "decision": decision,
        "report_scope_roi": observed["roi"],
        "observation_state": source["state"],
        "observation_eligible": observed["decision_eligible"],
        "finance_minor": finance,
        "shop_dedup_sales_minor": shop["dedup_sales_minor"] if shop_visible else None,
    }


def _summarize(ad, sources, rows, periods):
    state_eligible = [source["state"] in ("complete", "zero") for source in sources]
    basis = [source["measurement_basis"] for source in sources]
    continuous = all(previous[1] == current[0] for previous, current in zip(periods, periods[1:]))
    equal_length = len({end - start for start, end, _ in periods}) == 1
    ad_basis_match = len({(item["attribution_id"], item["maturity_id"]) for item in basis}) == 1
    shop_basis_match = len({item["shop_definition_id"] for item in basis}) == 1
    ad_comparable = continuous and equal_length and ad_basis_match and all(state_eligible)
    shop_values = [row["shop_dedup_sales_minor"] for row in rows]
    shop_comparable = continuous and equal_length and shop_basis_match and None not in shop_values
    ad_totals = {field: sum(source[field] for source in sources) for field in AD_FIELDS[:2]} if ad_comparable else None
    mature = [row["finance_minor"] is not None for row in rows]
    cohort_finance = None
    if ad_comparable and all(mature):
        totals = {field: sum(source[field] for source in sources) for field in AD_FIELDS}
        cohort_finance = {"totals_minor": totals, "ledger": ad.ledger(*(totals[field] for field in AD_FIELDS))}

    return {
        "coverage_and_gaps": {
            "windows_total": len(rows),
            "report_scope_complete_or_zero": sum(state_eligible),
            "actual_zero_windows": sum(source["state"] == "zero" for source in sources),
            "mature_financial_windows": sum(mature),
            "shop_independent_complete_or_zero": sum(value is not None for value in shop_values),
            "adjacent_windows": continuous,
            "equal_length_windows": equal_length,
            "ad_attribution_and_maturity_basis_match": ad_basis_match,
            "shop_dedup_definition_match": shop_basis_match,
            "cross_source_order_mapping": None,
        },
        "findings": {
            "ad_comparable_window_totals_minor": ad_totals,
            "ad_weighted_report_scope_roi": ad.ratio(ad_totals["gross_minor"], ad_totals["spend_minor"]) if ad_totals else None,
            "mature_financial_rollup": cohort_finance,
            "shop_independent_window_total_minor": sum(shop_values) if shop_comparable else None,
            "latest_vs_prior_ad_gross_change_minor": sources[-1]["gross_minor"] - sources[-2]["gross_minor"] if ad_comparable else None,
            "latest_vs_prior_shop_dedup_sales_change_minor": shop_values[-1] - shop_values[-2] if shop_comparable else None,
            "causal_increment_minor": None,
            "incremental_roi": None,
            "incremental_buyers": None,
        }
    }


def _write_json(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _report(result):
    latest = result["windows"][-1]
    findings = result["findings"]
    coverage = result["coverage_and_gaps"]
    label = {"hold": "维持观察", "freeze": "冻结常规调参", "pause": "建议暂停对应推广及追加"}
    action = label[latest["decision"]["action"]]
    def display(value):
        return "未知" if value is None else str(round(value, 6))
    lines = [
        f"# {LABEL}", "",
        f"**{action}**。{result['site']} 模拟回放，金额单位为 {result['currency']} 分。",
        "", f"最近窗口风险项：{', '.join(latest['hard_stops']) or '无已记录硬停止'}。",
        f"最近窗口待补：{', '.join(latest['decision_gaps']) or '无本工具定义的数据缺口'}。",
        "", "| 广告来源 | 状态 | 平台口径 ROI | 成熟投后贡献（分） | 可用预算（分） | 建议 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in result["windows"]:
        finance = row["finance_minor"] or {}
        lines.append(f"| {row['source_id']} | {row['observation_state']} | {display(row['report_scope_roi'])} "
                     f"| {display(finance.get('post_ad'))} | {display(row['budget']['available_minor'])} "
                     f"| {label[row['decision']['action']]} |")
    lines += [
        "", "## 覆盖与结论", "",
        f"- 成熟财务窗口 {coverage['mature_financial_windows']}/{coverage['windows_total']}。平台口径加权回报 {display(findings['ad_weighted_report_scope_roi'])}。",
        f"- 店铺独立去重总额 {display(findings['shop_independent_window_total_minor'])}。缺少跨来源订单映射，分开报告店铺和平台总额。",
        "- 无可信对照，未估计广告增量。未执行任何真实调参、追加或暂停。",
        "", "## 下一步", "",
        *(f"- {step['owner']}：{step['action']}" for step in result["next_actions"]),
        "", "## 增量验证建议", "",
        *(f"- {point}" for point in result["experiment_plan"]["conditions"]),
        "",
    ]
    return "\n".join(lines)


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress):
    if tool_id != TOOL_ID or site not in SITES:
        raise WorkflowError("本广告复盘仅支持抖音中国 CN 与 TikTok 菲律宾 PH。")
    if assets:
        raise WorkflowError("本广告复盘使用结构化模拟 JSON，无需上传文件。")
    _fields(payload, "site currency timezone data_origin account_id shop_id plan_id product_id channel "
            "budget_cycle stock_stop_units windows", "请求")
    currency, timezone_name, platform, channels = SITES[site]
    if (payload["site"], payload["currency"], payload["timezone"], payload["data_origin"]) != (
            site, currency, timezone_name, "synthetic_offline"):
        raise WorkflowError("仅支持本站本币、本站时区及明确标注的模拟数据。")
    if not isinstance(payload["channel"], str) or payload["channel"] not in channels:
        raise WorkflowError("channel 不属于本站复盘口径。")
    for key in ("account_id", "shop_id", "plan_id", "product_id"):
        _id(payload[key], key)
        if not payload[key].startswith(site + "-"):
            raise WorkflowError(f"{key} 必须以本站简码 {site}- 开头。")
    _number(payload["stock_stop_units"], "stock_stop_units")
    cycle = payload["budget_cycle"]
    _fields(cycle, "id start end cap_minor opening_spent_minor", "budget_cycle")
    _id(cycle["id"], "budget_cycle.id")
    if not cycle["id"].startswith(site + "-"):
        raise WorkflowError("budget_cycle.id 必须绑定本站。")
    _number(cycle["cap_minor"], "budget_cycle.cap_minor")
    _number(cycle["opening_spent_minor"], "budget_cycle.opening_spent_minor")
    cycle_start, cycle_end = (_timestamp(cycle[key], "budget_cycle." + key) for key in ("start", "end"))
    windows = payload["windows"]
    if not isinstance(windows, list) or not 2 <= len(windows) <= 14:
        raise WorkflowError("windows 须含 2 至 14 个按时间排序的窗口。")
    used_ids = {cycle["id"]}
    periods = [_validate_window(item, index, site, used_ids) for index, item in enumerate(windows)]
    if any(start < cycle_start or end > cycle_end for start, end, _ in periods):
        raise WorkflowError("窗口须属于所声明的审批周期。")
    if any(current[0] < prior[1] or current[2] < prior[2] for prior, current in zip(periods, periods[1:])):
        raise WorkflowError("窗口不得重叠或乱序，回执截点不可倒退。")
    progress("核对站点、归因和预算回执，独立计算广告窗口与成熟财务。")
    ad = _load_contract(project)
    prior_spent = last_known_spent = cycle["opening_spent_minor"]
    prior_end = cycle_start
    rows = []
    for source, (start, end, _) in zip(windows, periods):
        if start != prior_end:
            prior_spent = None
        row = _review_window(ad, source, cycle, payload["stock_stop_units"], prior_spent, last_known_spent)
        rows.append(row)
        prior_end = end
        prior_spent = None if row["budget"]["receipt_conflict"] else row["budget"]["spent_cumulative_minor"]
        if prior_spent is not None:
            last_known_spent = max(last_known_spent, prior_spent) if last_known_spent is not None else prior_spent
    summary = _summarize(ad, windows, rows, periods)
    latest = rows[-1]
    latest_state = {"pause": "BLOCKED", "freeze": "PENDING", "hold": "RESEARCH_ONLY"}[latest["decision"]["action"]]
    result = {
        "tool_id": TOOL_ID,
        "site": site,
        "currency": currency,
        "data_origin": "synthetic_offline",
        "execution_authorized": False,
        "current_research_state": latest_state,
        "facts": deepcopy(windows),
        "measurement_basis": {
            "platform": platform,
            **{key: payload[key] for key in ("site", "currency", "timezone", "channel",
                                             "account_id", "shop_id", "plan_id", "product_id")},
            "budget_cycle": dict(cycle),
            "stock_stop_units": payload["stock_stop_units"],
            "money_unit": "minor",
            "advertising_scope": "purchase_event_reporting" if payload["channel"] == "tiktok_purchase_ads" else "platform_optimization_scope",
            "shop_scope": "separate_deduplicated_sales_observation",
            "formula": "gross/spend; settled/spend; contribution=settled-variable_cost; post_ad=contribution-spend",
        },
        **summary,
        "windows": rows,
        "next_actions": [
            {"owner": "广告投手", "action": "按当前建议复核原报表、审批总额、累计入账和未入账承诺；预算卡量及边际回报另行送审。"},
            {"owner": "商城与财务", "action": "核对同批可售实点库存、价格底线、退款成熟和已扣成本。"},
            *([{"owner": "菲律宾履约", "action": "分开核签收、拒收、实际履约支出和回款对现金流的影响。"}]
              if site == "PH" else []),
        ],
        "experiment_plan": {
            "executed": False,
            "conditions": [
                "比较当前策略与预先定义的另一项投放策略，先确认平台可支持的独立随机化单元与同期对照。",
                "预注册去重订单、退款后贡献主指标及样本量、最小可检测差异和观察窗。",
                "先明确审批预算、损失上限、观察与止损／回退；校验退款成熟、价格券和同期活动、污染及溢出。",
                "无法随机化时另行评估平行趋势及未控混杂。当前模拟不执行实验。",
            ],
        },
        "limits": ["用户输入的模拟风险与回执不代表外部真实核验。", "不会建议扩量或复启；需另有可信成本、控制权限与实验依据。"],
    }
    task_dir = Path(task_dir)
    files = {
        "json": task_dir / "ad-period-review.json",
        "receipt": task_dir / "ad-period-review-receipt.json",
        "report": task_dir / "ad-period-review.md",
    }
    _write_json(files["json"], result)
    files["report"].write_text(_report(result), encoding="utf-8")
    _write_json(files["receipt"], {
        "tool_id": TOOL_ID,
        "site": site,
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": result["data_origin"],
        "execution_authorized": False,
        "formula_source_and_sha256": {CONTRACT_PATH: CONTRACT_SHA},
        "process_references": [METHOD_PATH, TEMPLATE_PATH],
        "input_sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                                  separators=(",", ":")).encode("utf-8")).hexdigest(),
        "outputs_sha256": {files[key].name: hashlib.sha256(files[key].read_bytes()).hexdigest()
                           for key in ("json", "report")},
        "calls": {"model": 0, "shop": 0, "external_api": 0},
    })
    progress("已保存广告窗口复盘、预算核账与来源回执。")
    return result, files

