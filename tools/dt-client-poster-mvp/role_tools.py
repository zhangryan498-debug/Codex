"""Fixed, offline bridges to established D&T role checks."""
from copy import deepcopy
from datetime import datetime
from importlib import import_module
import json
import math
from pathlib import Path
import re
import sys
from threading import Lock


class RoleToolError(ValueError):
    """An input or local dependency prevented this check from running."""


ROUTES = {"CN": ("douyin", "CNY"), "PH": ("tiktok", "PHP")}
MODULES = {
    "ad_decision_contract", "campaign_handoff", "normalize_justone_product_search",
    "reconcile_fastmoss_sku_stock", "strict_handoff_trace", "strict_price_review",
}
IMPORT_LOCK = Lock()
SYNTHETIC = "synthetic_offline"
PROVIDER = "local_sanitized_snapshot"
DEMO_SKU = "DT-DEMO-SKU"
DEMO_START = "2026-11-18T00:00:00Z"
DEMO_END = "2026-11-19T00:00:00Z"

AD_FLAGS = {
    "hard_risk", "product_changed_unverified", "paused", "restart_checks_verified",
    "same_basis_verified", "budget_constraint_evidence_verified", "marginal_evidence_verified",
    "control_limits_verified", "cooldown_satisfied", "trial_or_scale_approval_verified",
}
AD_AMOUNTS = {
    "stock", "stop_stock", "resume_stock", "absolute_increment_cap", "available_balance",
    "balance_fraction", "risk_spend_cap", "proposed_increment",
}
DATA_STATES = {"complete", "partial", "missing", "stale", "zero"}
PRIVATE_KEYS = {
    "authorization", "accesstoken", "apikey", "clientsecret", "cookie", "cookies",
    "credential", "credentials", "password", "refreshtoken", "secret", "setcookie", "token",
}


def _payload(site, **fields):
    return {"site": site, "scope": SYNTHETIC, **fields}


def _price_example():
    return _payload(
        "CN", receiver="shop-ops", evaluation_time="2026-11-18T12:00:00Z",
        source={
            "platform": "douyin", "country": "CN", "currency": "CNY", "sku": DEMO_SKU,
            "revision": 1, "scope": SYNTHETIC, "fixture_type": SYNTHETIC,
            "release_status": "not_authorized", "cost_unit": "order_total_do_not_multiply_by_quantity",
            "quantity": 1, "floor_basis": "buyer_goods_order", "floor_scope": "order", "floor_cents": 7500,
            "amounts": {
                "list_cents": 9900, "merchant_coupon_cents": 900, "platform_coupon_cents": 1000,
                "confirmed_platform_reimbursement_cents": 1000,
                "seller_costs_cents": {"goods": 3500, "packaging": 300, "shipping": 600, "returns_reserve": 200},
                "fees": {
                    "ad_cost_cents": 0, "creator_cost_cents": 0, "tax_delta_cents": 0,
                    "other_variable_cost_cents": 0, "platform_service_fee_rate": "0.10",
                },
            },
        },
        evidence={
            "source_id": "DT-DEMO-MOCK", "version": 1,
            "valid_from_inclusive": DEMO_START, "valid_until_exclusive": DEMO_END,
        },
    )


def _campaign_example():
    terms = {
        "currency": "CNY", "timezone": "Asia/Shanghai", "list_price_cents": 9900,
        "valid_from_inclusive": "2026-11-18T08:00:00+08:00",
        "valid_until_exclusive": "2026-11-19T08:00:00+08:00",
        "coupon": "模拟商家券 19 元", "shipping": "运费另计",
    }
    identity = {"sku": DEMO_SKU, "revision": 1, "payable_cents": 8000}
    return _payload("CN", receiver="shop-ops", authority={**identity, **terms}, packets=[{
        "packet_id": "DT-DEMO-01", "payload": {
            **identity, "role": "douyin-shop-ops", "terms": {**terms, "evidence_scope": SYNTHETIC},
        },
    }])


def public_tools():
    tools = [
        {
            "id": "ad-decision", "role_suffix": "ad-buyer", "label": "广告离线复盘与调参检查", "sites": ["CN", "PH"],
            "description": "复算毛投产、结算回报和投后贡献，并按素材、库存、齐数和预算约束检查模拟调参建议。",
            "input_example": _payload(
                "CN", currency="CNY", decision={"data_state": "complete", "requested_action": "hold",
                                                 "stock": 60, "stop_stock": 25},
                ledger={"gross": 1050, "spend": 210, "settled": 735, "variable_cost": 575,
                        "same_basis_verified": True, "state": "complete"},
            ),
        },
        {
            "id": "price-review", "role_suffix": "selection", "label": "选品货盘贡献与底价复核", "sites": ["CN", "PH"],
            "description": "按订单原始金额独立复算。权限仅限内置 DT-DEMO-SKU 模拟样例，时间按样例固定区间。",
            "input_example": _price_example(),
        },
        {
            "id": "campaign-handoff", "role_suffix": "campaign-ops", "label": "抖音活动交接字段对照", "sites": ["CN"],
            "description": "检查模拟活动交接的 SKU、版本、到手价、有效期、优惠和运费，逐条标出缺项或冲突。",
            "input_example": _campaign_example(),
        },
        {
            "id": "justone-search-projection", "role_suffix": "competitor-analyst", "label": "抖音竞品搜索快照整理", "sites": ["CN"],
            "description": "整理合成样例或已脱敏的本地 Just One 搜索回执，按商品与推广 ID 精确比对。",
            "input_example": _payload("CN", snapshot={
                "code": 0,
                "data": {
                    "promotions": [{"product_id": "10001", "promotion_id": "20001"}],
                    "summary_promotions": [{"product_id": "10001", "promotion_id": "20001"}],
                },
            }),
        },
        {
            "id": "fastmoss-stock-reconciliation", "role_suffix": "competitor-analyst", "label": "菲律宾竞品 SKU 库存视图核对", "sites": ["PH"],
            "description": "保留 FastMoss 来源冲突。合成范围可另填 warehouse_readback 收件，并独立核算预留扣减。",
            "input_example": _payload("PH", snapshot={
                "product_id": "DT-DEMO-PRODUCT",
                "sku_variants": [{"sku_id": "DT-DEMO-A", "stock_units": 80},
                                 {"sku_id": "DT-DEMO-B", "stock_units": None}],
            }, warehouse_readback={
                "target_sku": "DT-DEMO-A", "evaluation_time": "2026-11-20T15:30:00+08:00",
                "receipt": {"site":"PH", "product_id":"DT-DEMO-PRODUCT", "sku_id":"DT-DEMO-A",
                            "warehouse_id":None, "source_ref":None, "observed_at":None,
                            "quantity_basis":None, "reported_units":None, "reserved_units":None},
            }),
        },
    ]
    for tool in tools:
        example = tool["input_example"]
        tool["input_examples"] = {example["site"]: example}
        if len(tool["sites"]) == 2:
            philippines = deepcopy(example)
            philippines["site"] = "PH"
            if tool["id"] == "ad-decision":
                philippines["currency"] = "PHP"
            else:
                philippines["source"].update(platform="tiktok", country="PH", currency="PHP")
            tool["input_examples"]["PH"] = philippines
    return tools


def _trusted_module(project, name):
    """Resolve fixed project-owned filenames; never import from a submitted path."""
    scripts = (project / "scripts").resolve()
    if name not in MODULES:
        raise RoleToolError("本地岗位脚本未登记。")
    expected = scripts / (name + ".py")
    if not expected.is_file() or expected.resolve().parent != scripts:
        raise RoleToolError("本地 D&T 项目脚本缺失。")
    with IMPORT_LOCK:
        sys.path.insert(0, str(scripts))
        try:
            module = import_module(name)
        except ImportError:
            raise RoleToolError("本地 D&T 项目脚本依赖缺失。") from None
        finally:
            sys.path.remove(str(scripts))
        if Path(getattr(module, "__file__", "")).resolve() != expected:
            raise RoleToolError("本地 D&T 项目脚本来源冲突。")
    return module


def _object(value, label, allowed=None):
    if type(value) is not dict:
        raise RoleToolError(f"{label} 必须是 JSON 对象。")
    if allowed is not None and set(value) - allowed:
        raise RoleToolError(f"{label} 含本入口不支持的字段。")
    return value


def _validate_json(payload):
    pending = [(payload, 0)]
    visited = 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if depth > 32 or visited > 100_000:
            raise RoleToolError("离线输入过大或嵌套过深。")
        if type(node) is dict:
            for key, value in node.items():
                if type(key) is not str or len(key) > 256:
                    raise RoleToolError("离线输入含无效字段名。")
                if re.sub(r"[^a-z0-9]", "", key.lower()) in PRIVATE_KEYS:
                    raise RoleToolError("请使用脱敏快照，移除凭据字段。")
                pending.append((value, depth + 1))
        elif type(node) is list:
            if len(node) > 10_000:
                raise RoleToolError("离线输入列表过大。")
            pending.extend((value, depth + 1) for value in node)
        elif type(node) is str:
            if len(node) > 100_000 or re.match(r"\s*Bearer\s+", node, re.IGNORECASE):
                raise RoleToolError("离线输入含超长文字或鉴权值。")
        elif type(node) is int:
            if abs(node) > 10 ** 24:
                raise RoleToolError("离线输入数字过大。")
        elif type(node) is float:
            if not math.isfinite(node) or abs(node) > 10 ** 24:
                raise RoleToolError("离线输入含无效数字。")
        elif node is not None and type(node) is not bool:
            raise RoleToolError("离线输入含无效 JSON 值。")


def _check_snapshot_site(snapshot, site):
    known_site = {"CN": ("CN", "China", "Douyin-CN"), "PH": ("PH", "Philippines", "TikTok-PH")}[site]
    platform, _ = ROUTES[site]
    for identity in (snapshot, snapshot.get("data"), snapshot.get("requestParams"),
                     (snapshot.get("requestParams") or {}).get("query")
                     if type(snapshot.get("requestParams")) is dict else None):
        if not isinstance(identity, dict):
            continue
        for field in ("site", "country", "country_code", "region", "market"):
            if field in identity and identity[field] is not None and identity[field] not in known_site:
                raise RoleToolError("快照的站点与所选站点不符。")
        if "platform" in identity and identity["platform"] is not None and identity["platform"] != platform:
            raise RoleToolError("快照的平台与所选站点不符。")


def _ad(project, site, payload):
    _object(payload, "广告输入", {"scope", "site", "currency", "decision", "ledger"})
    if payload.get("currency") != ROUTES[site][1]:
        raise RoleToolError("广告币种与所选站点不符。")
    decision = _object(payload.get("decision"), "decision",
                       AD_FLAGS | AD_AMOUNTS | {"data_state", "requested_action"})
    if decision.get("data_state") not in DATA_STATES:
        raise RoleToolError("decision.data_state 必须标明 complete、partial、missing、stale 或 zero。")
    if decision.get("requested_action") not in ("hold", "increase"):
        raise RoleToolError("requested_action 仅支持 hold 或 increase。")
    if any(key in decision and type(decision[key]) is not bool for key in AD_FLAGS):
        raise RoleToolError("模拟检查标记必须是真或假。")
    ad = _trusted_module(project, "ad_decision_contract")
    for key in AD_AMOUNTS & decision.keys():
        if decision[key] is not None:
            ad.number(decision[key])
    result = {"decision_check": ad.review(decision), "ledger": None,
              "ledger_status": "not_supplied", "causal_incrementality_verified": False,
              "decision_input_evidence_verified": False}
    if "ledger" in payload:
        keys = ("gross", "spend", "settled", "variable_cost")
        ledger = _object(payload["ledger"], "ledger", {*keys, "state", "same_basis_verified"})
        for key in keys:
            if ledger.get(key) is not None:
                ad.number(ledger[key])
        if ledger.get("state") not in DATA_STATES:
            raise RoleToolError("ledger.state 必须标明账目状态。")
        if "same_basis_verified" in ledger and type(ledger["same_basis_verified"]) is not bool:
            raise RoleToolError("账目同口径标记必须是真或假。")
        result["ledger_status"] = "incomplete_or_incomparable"
        if (ledger["state"] in ("complete", "zero") and ledger.get("same_basis_verified") is True
                and all(ledger.get(key) is not None for key in keys)):
            if ledger["state"] == "zero" and any(ledger[key] != 0 for key in keys):
                raise RoleToolError("零值状态须与账目数值一致。")
            result["ledger"] = ad.ledger(*(ledger[key] for key in keys))
            result["ledger_status"] = "computed_from_caller_declared_matching_synthetic_basis"
    return result


def _price(project, site, payload):
    _object(payload, "价审输入", {"scope", "site", "receiver", "source", "evidence", "evaluation_time"})
    source = _object(payload.get("source"), "source")
    evidence = _object(payload.get("evidence"), "evidence")
    receiver = payload.get("receiver")
    platform, currency = ROUTES[site]
    if (source.get("platform"), source.get("country"), source.get("currency")) != (platform, site, currency):
        raise RoleToolError("价审平台、站点或币种与所选站点不符。")
    price = _trusted_module(project, "strict_price_review")
    trace = _trusted_module(project, "strict_handoff_trace")
    if receiver not in price.SUFFIXES:
        raise RoleToolError("价审接收岗位仅支持 shop-ops、campaign-ops 和 ad-buyer。")
    rate = source.get("amounts")
    rate = rate.get("fees") if isinstance(rate, dict) else None
    rate = rate.get("platform_service_fee_rate") if isinstance(rate, dict) else None
    if rate is not None and (type(rate) is not str or re.fullmatch(r"[01](?:\.[0-9]{1,12})?", rate) is None):
        raise RoleToolError("平台费率须使用 0 到 1 的小数字符串，最多 12 位小数。")
    evaluation_time = payload.get("evaluation_time")
    price.at(evaluation_time)
    principal = "dt-client-fixed-offline-demo"
    policy = {
        "policy_id": "dt-client-fixed-price-review", "version": 1, "mode": price.MARKER,
        "principals": [{"principal_id": principal, "grants": [{
            "action": "offline_price_review", "receiver_roles": [platform + "-" + suffix for suffix in price.SUFFIXES],
            "platform": platform, "country": site, "currency": currency, "skus": [DEMO_SKU],
            "valid_from_inclusive": DEMO_START, "valid_until_exclusive": DEMO_END,
        }]}],
    }
    context = price.make_simulated_context(policy, trace.digest(policy), principal)
    return price.review_source(source, evidence, context, platform + "-" + receiver, evaluation_time)


def _campaign(project, site, payload):
    _object(payload, "活动输入", {"scope", "site", "receiver", "authority", "packets"})
    authority = _object(payload.get("authority"), "authority", {
        "sku", "revision", "payable_cents", "currency", "timezone", "list_price_cents", "coupon",
        "shipping", "valid_from_inclusive", "valid_until_exclusive",
    })
    if authority.get("currency") != ROUTES[site][1] or authority.get("timezone") != "Asia/Shanghai":
        raise RoleToolError("抖音活动使用 CNY 与 Asia/Shanghai。")
    first = datetime.fromisoformat(authority.get("valid_from_inclusive", ""))
    last = datetime.fromisoformat(authority.get("valid_until_exclusive", ""))
    if (first.utcoffset() is None or last.utcoffset() is None
            or first.utcoffset().total_seconds() != 28800 or last.utcoffset().total_seconds() != 28800
            or first >= last):
        raise RoleToolError("活动有效期须使用递增的 ISO 8601 时间，并带 +08:00。")
    receiver = payload.get("receiver")
    if type(receiver) is not str:
        raise RoleToolError("请填写活动交接的接收岗位。")
    packets = payload.get("packets")
    if not isinstance(packets, list) or not packets or len(packets) > 1000:
        raise RoleToolError("请提供 1 到 1000 条活动交接。")
    contract = _trusted_module(project, "campaign_handoff")
    result = contract.validate(authority, packets, "douyin-" + receiver)
    for packet, record in zip(packets, result["records"]):
        body = packet.get("payload")
        terms = body.get("terms") if isinstance(body, dict) else None
        if isinstance(terms, dict) and terms.get("evidence_scope") != SYNTHETIC:
            record["errors"].append({"field": "terms.evidence_scope", "kind": "offline_scope_mismatch"})
            record["field_gate"] = "fail"
    result["eligibility_verified"] = False
    result["price_floor_verified"] = False
    return result


WAREHOUSE_FIELDS = ("site", "product_id", "sku_id", "warehouse_id", "source_ref", "observed_at",
                    "quantity_basis", "reported_units", "reserved_units")
WAREHOUSE_BASIS = ("on_hand_before_reserve", "net_after_reserve")


def _warehouse_timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+08:00", value):
        raise RoleToolError("盘点和复核时点须用明确的菲律宾 +08:00 时间。")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise RoleToolError("盘点或复核时点不是有效时间。") from None


def _warehouse_readback(query, snapshot):
    _object(query, "合成仓库收件", {"target_sku", "evaluation_time", "receipt"})
    target = query.get("target_sku")
    if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", target):
        raise RoleToolError("请填明确的本次待核 SKU。")
    at = _warehouse_timestamp(query.get("evaluation_time"))
    raw = query.get("receipt")
    if raw is not None:
        _object(raw, "合成仓库记录", set(WAREHOUSE_FIELDS))
    reference = raw or {}
    missing = [key for key in WAREHOUSE_FIELDS if reference.get(key) is None]
    issues = []
    subjects = {"site":"PH", "product_id":snapshot["product_id"], "sku_id":target}
    for key, expected in subjects.items():
        if reference.get(key) is not None and reference[key] != expected:
            issues.append(key+"_mismatch")
    if target not in {sku["sku_id"] for sku in snapshot["skus"]}:
        issues.append("target_sku_missing_in_returned_snapshot")
    for key in ("warehouse_id","source_ref"):
        value = reference.get(key)
        if value is not None and (not isinstance(value, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value)):
            raise RoleToolError("仓库或可核来源编号无效。")
    if reference.get("observed_at") is not None and _warehouse_timestamp(reference["observed_at"]) > at:
        issues.append("warehouse_observation_after_evaluation")
    for key in ("reported_units","reserved_units"):
        count = reference.get(key)
        if count is not None and (type(count) is not int or not 0 <= count <= 10**9):
            raise RoleToolError("盘点及预留数量须为非负整数或未知。")
    basis, units, reserved = (reference.get(key) for key in ("quantity_basis","reported_units","reserved_units"))
    if basis is not None and basis not in WAREHOUSE_BASIS:
        raise RoleToolError("须明确盘点是否已扣除预留；未知可留空。")
    if basis == WAREHOUSE_BASIS[0] and units is not None and reserved is not None and reserved > units:
        issues.append("reservation_exceeds_gross")
    net, applied = None, None
    if not missing and not issues:
        applied = basis == WAREHOUSE_BASIS[0]
        net = units - reserved if applied else units
    return {"scope": SYNTHETIC, "status": "received_synthetic" if net is not None else "pending",
            "subject": subjects, "evaluation_time": query["evaluation_time"], "source_fields": raw,
            "missing_fields": missing, "conflicts": issues, "net_sellable_units_synthetic": net,
            "reservation_subtraction_applied": applied,
            "physical_stock_verified": False, "provider_values_modified": False,
            "requires_synthetic_followup": bool(missing or issues)}


def _snapshot(project, site, payload, tool_id):
    allowed = {"scope", "site", "snapshot"}
    if tool_id == "fastmoss-stock-reconciliation" and payload.get("scope") == SYNTHETIC:
        allowed.add("warehouse_readback")
    _object(payload, "快照输入", allowed)
    snapshot = _object(payload.get("snapshot"), "snapshot")
    _check_snapshot_site(snapshot, site)
    if tool_id == "justone-search-projection":
        data = snapshot.get("data")
        if not isinstance(data, dict):
            raise RoleToolError("需要带 data 的 Just One 商品搜索回执。")
        for key in ("promotions", "summary_promotions"):
            if isinstance(data.get(key), list) and len(data[key]) > 500:
                raise RoleToolError("每次最多检查每个分支 500 条搜索回执。")
        module = _trusted_module(project, "normalize_justone_product_search")
        return module.normalize(snapshot, source={"kind": payload["scope"]})
    module = _trusted_module(project, "reconcile_fastmoss_sku_stock")
    result = module.reconcile(snapshot)
    # The original input belongs to the local task store; avoid duplicating it.
    result["source"].pop("provider_payload", None)
    if "warehouse_readback" in payload:
        result["warehouse_readback"] = _warehouse_readback(payload["warehouse_readback"], result)
    return result


def run_role_tool(project: Path, site: str, tool_id: str, payload: dict, task_id: str) -> dict:
    if type(site) is not str or type(tool_id) is not str:
        raise RoleToolError("需要有效的站点与岗位。")
    tool = next((tool for tool in public_tools() if tool["id"] == tool_id), None)
    if tool is None or site not in tool["sites"]:
        raise RoleToolError("所选岗位检查在本站点不可用。")
    _object(payload, "离线输入")
    _validate_json(payload)
    if payload.get("site") != site:
        raise RoleToolError("输入站点与所选站点不符；请查看本站点示例。")
    snapshot_tool = tool_id in {"justone-search-projection", "fastmoss-stock-reconciliation"}
    allowed_scopes = {SYNTHETIC, PROVIDER} if snapshot_tool else {SYNTHETIC}
    if type(payload.get("scope")) is not str or payload["scope"] not in allowed_scopes:
        raise RoleToolError("请明确标注输入来源与离线范围。")
    try:
        if snapshot_tool:
            result = _snapshot(project, site, payload, tool_id)
        else:
            result = {"ad-decision": _ad, "price-review": _price,
                      "campaign-handoff": _campaign}[tool_id](project, site, payload)
        json.dumps(result, ensure_ascii=True, allow_nan=False)
    except RoleToolError:
        raise
    except (ValueError, TypeError, ArithmeticError, KeyError, RecursionError):
        raise RoleToolError("输入未满足岗位检查契约。请逐项核对示例、必填字段和数值类型。") from None
    return {
        "task_id": task_id, "site": site, "tool_id": tool_id, "scope": payload["scope"],
        "execution_mode": "deterministic_local_check", "business_execution_authorized": False,
        "model_called": False, "provider_called": False, "result": result,
    }

