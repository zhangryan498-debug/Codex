"""Deterministic, local selection-to-shop exercises using the established D&T gates."""
from hashlib import sha256
import json
from pathlib import Path
import re

from role_tools import (
    DEMO_END, DEMO_SKU, DEMO_START, ROUTES, SYNTHETIC, RoleToolError,
    _price_example, _trusted_module, _validate_json,
)
from role_tools_extra import _load


class WorkflowError(ValueError):
    pass


TOOL_ID = "selection-shop-intake"
LABEL = "选品价审与商城接收"
UTC = "2026-11-18T12:00:00Z"
MONEY = {
    "buyer_paid": ("买家商品实付", "buyer_paid_cents"),
    "seller_revenue": ("商家确认收入", "seller_revenue_cents"),
    "contribution": ("订单贡献利润", "contribution_profit_cents"),
}
LIMITS = [
    "仅固定演练 SKU DT-DEMO-SKU 和固定日期的模拟商城收件权限。",
    "成本、底价、运费说明、库存及图片编号均为调用方提供的模拟输入。",
    "草稿不包含素材审阅、平台资格、实店授权或发布；未调用岗位模型，未运行正式商城价格签收。",
]


def _example(site):
    payload = _price_example()
    platform, currency = ROUTES[site]
    source = payload["source"]
    source.update(platform=platform, country=site, currency=currency)
    return {
        "site": site, "scope": SYNTHETIC, "evaluation_time": UTC,
        "source": source, "evidence": payload["evidence"],
        "catalog": {
            "product_id": f"DT-DEMO-CUP-{site}", "sku_id": DEMO_SKU, "version": 1,
            "image_ids": [f"MOCK-{site}-COVER", f"MOCK-{site}-SIZE"],
            "title": "轻行运动水杯 500 mL" if site == "CN" else "LightMove 500 mL Sports Bottle",
            "shipping_note": "模拟运费另计" if site == "CN" else "Shipping charged separately in this exercise",
        },
        "stock": {
            "min_sellable_orders": 4,
            "observations": [{
                "source_id": "MOCK-STOCK-LOCAL-A", "site": site,
                "sku_id": DEMO_SKU, "version": 1, "as_of": "2026-11-18T11:00:00Z",
                "sellable_orders": 24,
            }],
        },
    }


def public_tools():
    return [{
        "id": TOOL_ID, "label": LABEL, "role_suffix": "selection", "sites": list(ROUTES),
        "input_example": _example("CN"), "input_examples": {site: _example(site) for site in ROUTES},
        "description": (
            "从原始费用计算模拟价格与利润，商城按 SKU、版本、图片编号和可售订单库存接收。"
            "输入缺失或库存冲突时下载复核回执，条件满足时下载商城草稿。"
            "仅使用站点示例的演练 SKU 和日期授权。"
        ),
        "max_media_files": 0,
    }]


def _shape(value, fields, label, required=None):
    if type(value) is not dict or set(value) - fields or not (fields if required is None else required) <= set(value):
        raise WorkflowError(f"{label} 字段不符合演练格式，请对照站点示例。")


def _id(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is not None


def _utc(value, price):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise WorkflowError("演练时间须填写 UTC 时间（以 Z 结尾）。")
    try:
        return price.at(value)
    except (ValueError, TypeError) as error:
        raise WorkflowError("演练时间格式错误。") from error


def _validate_inputs(payload, site, price, shop):
    _shape(payload, {"site", "scope", "evaluation_time", "source", "evidence", "catalog", "stock"}, "输入")
    if payload["site"] != site or payload["scope"] != SYNTHETIC:
        raise WorkflowError("请选择相同站点并明确标记 synthetic_offline。")
    _utc(payload["evaluation_time"], price)
    source = payload["source"]
    identity = {"platform", "country", "currency", "sku", "revision", "fixture_type",
                "scope", "release_status", "cost_unit", "quantity", "floor_basis", "floor_scope"}
    _shape(source, identity | {"floor_cents", "amounts"}, "选品原始金额", identity)
    platform, currency = ROUTES[site]
    if (source["platform"], source["country"], source["currency"]) != (platform, site, currency):
        raise WorkflowError("选品原始金额的站点与币种必须和商城站点匹配。")
    if not _id(source["sku"]) or type(source["revision"]) is not int or not 1 <= source["revision"] <= 10**9:
        raise WorkflowError("选品 SKU 和整数版本格式错误。")
    amounts = source.get("amounts")
    list_amount = amounts.get("list_cents") if type(amounts) is dict else None
    if type(list_amount) is int and list_amount > 10**18:
        raise WorkflowError("原标价超出商城事实包可表示的最小货币单位范围。")
    fees = amounts.get("fees") if type(amounts) is dict else None
    rate = fees.get("platform_service_fee_rate") if type(fees) is dict else None
    if rate is not None and (type(rate) is not str or re.fullmatch(r"(?:0(?:\.\d{1,12})?|1(?:\.0{1,12})?)", rate) is None):
        raise WorkflowError("费率须填写 0 到 1 的十进制字符串，最多 12 位小数。")
    evidence = payload["evidence"]
    _shape(evidence, {"source_id", "version", "valid_from_inclusive", "valid_until_exclusive"},
           "价格证据")
    if not _id(evidence["source_id"]) or type(evidence["version"]) is not int or not 1 <= evidence["version"] <= 10**9:
        raise WorkflowError("价格证据源编号和整数版本格式错误。")
    for key in ("valid_from_inclusive", "valid_until_exclusive"):
        if evidence[key] is not None:
            _utc(evidence[key], price)
    catalog = payload["catalog"]
    _shape(catalog, {"product_id", "sku_id", "version", "image_ids", "title", "shipping_note"}, "商城目录")
    if (not _id(catalog["product_id"]) or not _id(catalog["sku_id"])
            or type(catalog["version"]) is not int or not 1 <= catalog["version"] <= 10**9):
        raise WorkflowError("商城商品号、SKU 或整数版本格式错误。")
    images = catalog["image_ids"]
    if type(images) is not list or len(images) > 12 or any(not _id(value) for value in images) or len(set(images)) != len(images):
        raise WorkflowError("请提供最多 12 个互不重复的模拟图片编号。")
    for key in ("title", "shipping_note"):
        value = catalog[key]
        if value is not None and (type(value) is not str or not value.strip() or len(value) > 200
                                  or any(ord(char) < 32 for char in value)):
            raise WorkflowError("标题及运费说明须为 200 字内单行文字或 null。")
        if value and any(marker in value for marker in (shop.BEGIN, shop.END, shop.HASH)):
            raise WorkflowError("标题及运费说明含系统保留的事实包标记，请移除后重试。")
    _shape(payload["stock"], {"min_sellable_orders", "observations"}, "商城库存")


def _context(site, price, trace):
    platform, currency = ROUTES[site]
    receiver = platform + "-shop-ops"
    principal = "dt-client-selection-shop-fixture"
    policy = {
        "policy_id": "dt-client-selection-shop", "version": 1, "mode": price.MARKER,
        "principals": [{"principal_id": principal, "grants": [{
            "action": "offline_price_review", "receiver_roles": [receiver],
            "platform": platform, "country": site, "currency": currency, "skus": [DEMO_SKU],
            "valid_from_inclusive": DEMO_START, "valid_until_exclusive": DEMO_END,
        }]}],
    }
    return receiver, price.make_simulated_context(policy, trace.digest(policy), principal)


def _stock(payload, price):
    stock = payload["stock"]
    limit = stock["min_sellable_orders"]
    if type(limit) is not int or not 1 <= limit <= 10**6:
        raise WorkflowError("演练最低可售订单数须为 1 至 1,000,000 的整数。")
    observations = stock["observations"]
    if type(observations) is not list or len(observations) > 12:
        raise WorkflowError("请填写最多 12 条模拟库存观察；空列表表示未知。")
    instant = _utc(payload["evaluation_time"], price)
    start, end = _utc(DEMO_START, price), _utc(DEMO_END, price)
    catalog = payload["catalog"]
    rows = []
    seen_sources = set()
    for row in observations:
        _shape(row, {"source_id", "site", "sku_id", "version", "as_of", "sellable_orders"}, "库存观察")
        if not _id(row["source_id"]) or row["source_id"] in seen_sources:
            raise WorkflowError("库存证据须有互不重复的来源编号。")
        seen_sources.add(row["source_id"])
        if not _id(row["sku_id"]) or type(row["version"]) is not int or not 1 <= row["version"] <= 10**9:
            raise WorkflowError("库存观察的 SKU 或整数版本格式错误。")
        value = row["sellable_orders"]
        if value is not None and (type(value) is not int or not 0 <= value <= 10**9):
            raise WorkflowError("可售订单数须为非负整数或 null。")
        observed = _utc(row["as_of"], price)
        if (row["site"], row["sku_id"], row["version"]) != (payload["site"], catalog["sku_id"], catalog["version"]):
            state = "wrong_site_sku_or_version"
        elif observed > instant:
            state = "future"
        elif not start <= observed < end:
            state = "outside_mock_window"
        else:
            state = "unknown" if value is None else "usable"
        rows.append({**row, "status": state})
    values = {row["sellable_orders"] for row in rows if row["status"] == "usable"}
    blocked = any(row["status"] == "wrong_site_sku_or_version" for row in rows)
    unresolved = any(row["status"] not in {"usable", "unknown"} for row in rows)
    reason = ("identity_mismatch" if blocked else "conflict" if len(values) > 1 else
              "incomplete_observations" if unresolved else "unknown" if not values else "known")
    return {"state": reason, "sellable_orders": next(iter(values)) if reason == "known" else None,
            "min_sellable_orders": limit, "observations": rows}


def _price_handoff(payload, receiver, review, trace, case_id):
    source, evidence = payload["source"], payload["evidence"]
    numbers = review["host_calculation"]["numbers"]
    document = trace.bind_document("selection-host-price", review, case_id, source["sku"])
    return {
        "scope": SYNTHETIC, "release_status": "not_authorized", "decision": review["decision"],
        "sender_role": source["platform"] + "-selection", "receiver_role": receiver,
        "site": source["country"], "currency": source["currency"], "sku_id": source["sku"],
        "version": source["revision"], "source_sha256": trace.digest(source),
        "evidence_sha256": trace.digest(evidence), "review_sha256": document.document_sha256,
        "money": {name: numbers[field] for name, (_, field) in MONEY.items()},
        "references": {name: trace.reference(document, f"/host_calculation/numbers/{field}")
                       for name, (_, field) in MONEY.items()},
    }


def _fact(fact_id, subject, kind, value, source_id):
    return {"fact_id": fact_id, "subject": subject, "kind": kind,
            "status": "unknown" if value is None else "known", "value": value,
            "source_ids": [] if value is None else [source_id], "alternatives": []}


def _consume(payload, task_dir, handoff, review, receiver, stock, trace, shop):
    source, evidence, catalog = payload["source"], payload["evidence"], payload["catalog"]
    files = {}
    shop_record = {"stage": "shop_local_consumer", "receiver_role": receiver,
                   "listing_draft": None, "shop_facts_bundle_readback": False,
                   "stock": stock, "issues": []}
    issues = shop_record["issues"]
    if (handoff["site"], handoff["sku_id"], handoff["version"], handoff["receiver_role"]) != (
            payload["site"], catalog["sku_id"], catalog["version"], receiver):
        issues.append("selection_shop_context_mismatch")
    if (handoff["source_sha256"], handoff["evidence_sha256"]) != (trace.digest(source), trace.digest(evidence)):
        raise WorkflowError("选品交接的原始输入哈希不符。")
    document = trace.bind_document("selection-host-price", review, task_dir.name, source["sku"])
    if handoff["review_sha256"] != document.document_sha256 or handoff["decision"] != review["decision"]:
        raise WorkflowError("选品交接的价审回执不符。")
    for name, (_, field) in MONEY.items():
        ref = handoff["references"][name]
        result = trace.validate_leaf_reference(ref, {document.document_id: document}, task_dir.name, source["sku"])
        if not result["valid"] or ref["path"] != f"/host_calculation/numbers/{field}" or not trace.same(ref["value"], handoff["money"][name]):
            raise WorkflowError("选品交接的金额未绑定到原计算字段。")
    if review["decision"] != "PASS":
        issues.append("selection_price_" + review["decision"].lower())
    if not catalog["image_ids"]:
        issues.append("catalog_image_ids_missing")
    if catalog["title"] is None:
        issues.append("catalog_title_missing")
    if catalog["shipping_note"] is None:
        issues.append("catalog_shipping_note_missing")
    count = stock["sellable_orders"]
    if stock["state"] != "known":
        issues.append("stock_" + stock["state"])
    elif count < stock["min_sellable_orders"]:
        issues.append("stock_below_mock_minimum")

    if issues:
        definite = {"selection_shop_context_mismatch", "selection_price_block", "stock_identity_mismatch",
                    "stock_below_mock_minimum"}
        shop_record["status"] = "BLOCK" if definite.intersection(issues) else "PENDING"
        return shop_record, files

    price_id = "SELECTION-PRICE-" + evidence["source_id"]
    catalog_id = "CATALOG-MOCK"
    bundle_from = evidence["valid_from_inclusive"]
    bundle_until = evidence["valid_until_exclusive"]
    source_ids = [price_id, catalog_id]
    versions = {price_id: evidence["version"], catalog_id: catalog["version"]}
    pack = {
        "schema_version": "shop_product_facts/1.0", "pack_id": "SIM-" + catalog["product_id"],
        "version": catalog["version"], "mode": "simulated", "product_id": catalog["product_id"],
        "sku_id": catalog["sku_id"], "as_of": bundle_from, "expires_at": bundle_until,
        "image_ids": catalog["image_ids"],
        "sources": [{"source_id": key, "version": versions[key], "trust_category": "synthetic",
                     "applicable_sku_ids": [catalog["sku_id"]], "as_of": bundle_from, "expires_at": bundle_until}
                    for key in source_ids],
        "facts": [
            _fact("title", "商城标题", "text", catalog["title"], catalog_id),
            _fact("shipping_note", "运费说明", "text", catalog["shipping_note"], catalog_id),
            _fact("buyer_paid", MONEY["buyer_paid"][0], "money",
                  {"minor": handoff["money"]["buyer_paid"], "currency": handoff["currency"], "scale": 2}, price_id),
        ],
    }
    facts = task_dir / "shop-facts.json"
    facts_sha = _write(facts, pack)
    instructions = "仅用于本地模拟商城草稿；引用数据和图片编号不代表现场核验或发布授权。"
    task = shop.build_task(facts, instructions).encode("utf-8")
    raw = shop.extract_task_facts(task, catalog["sku_id"])
    if sha256(raw).hexdigest() != facts_sha:
        raise WorkflowError("商城原生事实包的回读字节不一致。")
    decoded = shop.parse_bytes(raw)
    verdict = shop.validate_facts(
        decoded, expected_sku_id=catalog["sku_id"], expected_version=catalog["version"],
        expected_image_ids=catalog["image_ids"], now=payload["evaluation_time"],
        expected_source_versions=versions,
    )
    native_task = task_dir / "shop-native-input.txt"
    _write_bytes(native_task, task)
    files.update(shop_facts_sha256=facts_sha, native_shop_task_sha256=sha256(task).hexdigest())
    visible = {fact["fact_id"]: fact["value"] for fact in decoded["facts"]}
    shop_record.update(status="DRAFT_READY", shop_facts_bundle_readback=True, native_validation=verdict,
                       received_facts=decoded,
                       listing_draft={"site": payload["site"], "product_id": catalog["product_id"],
                                      "sku_id": decoded["sku_id"], "version": decoded["version"],
                                      "title": visible["title"], "buyer_goods_payment": visible["buyer_paid"],
                                      "shipping_note": visible["shipping_note"], "image_ids": decoded["image_ids"],
                                      "sellable_orders": count, "status": "synthetic_offline_draft",
                                      "publish_authorized": False})
    return shop_record, files


ACTIONS = {
    "selection_shop_context_mismatch": "核对商城侧 SKU、版本和站点，重新提交同一货品的输入。",
    "selection_price_block": "按价审拒绝明细修正输入，禁止绕过底价或模拟授权范围。",
    "selection_price_pending": "补全原始成本、返还、费率或价格有效期后再跑价审。",
    "catalog_image_ids_missing": "补齐模拟素材编号；真实图片的内容与授权需另审。",
    "catalog_title_missing": "补齐本站点商城标题。",
    "catalog_shipping_note_missing": "补齐买家可见运费条件，保留未知状态。",
    "stock_below_mock_minimum": "复核可售订单库存及本次演练库存门槛。",
    "stock_identity_mismatch": "隔离错误站点、SKU 或版本的库存观察。",
    "stock_conflict": "保留冲突来源，核实同一 SKU/版本的可售订单数。",
    "stock_incomplete_observations": "补齐演练有效时窗内的库存观察；保留越界原值。",
    "stock_unknown": "补充可追溯的可售订单数。没有库存观测不能填 0。",
}


def _write_bytes(path, data):
    with path.open("xb") as stream:
        stream.write(data)


def _write(path, document):
    raw = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    _write_bytes(path, raw)
    return sha256(raw).hexdigest()


def _report(result):
    price = result["selection_price_review"]
    shop = result["shop_intake"]
    lines = [
        f"# {LABEL}", "",
        f'站点 {result["site"]} · {result["currency"]} · 模拟价审 {price["decision"]} · 商城接收 {shop["status"]}',
        "", "金额使用币种最小单位，未知保留。", "",
    ]
    for label, field in MONEY.values():
        value = price["host_calculation"]["numbers"][field]
        lines.append(f'- {label} {"未知" if value is None else value}')
    count = shop["stock"]["sellable_orders"]
    lines.extend([f'- 可售订单数 {"未知" if count is None else count}', "",
                  "## 后续动作", "", *[f'- {action}' for action in result["next_actions"]], "",
                  "## 验证边界", "", *[f'- {limit}' for limit in LIMITS], ""])
    return "\n".join(lines)


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress):
    if tool_id != TOOL_ID or site not in ROUTES:
        raise WorkflowError("该收件演练仅支持中国抖音与 TikTok 菲律宾。")
    if assets:
        raise WorkflowError("本演练只接收模拟图片编号，不能提交图片。")
    task_dir = Path(task_dir)
    project = Path(project).resolve()
    try:
        _validate_json(payload)
        price = _trusted_module(project, "strict_price_review")
        trace = _trusted_module(project, "strict_handoff_trace")
        shop = _load(project, "shop_product_contract")
    except RoleToolError as error:
        raise WorkflowError(str(error)) from error
    _validate_inputs(payload, site, price, shop)
    stock = _stock(payload, price)
    paths = {key: task_dir / name for key, name in (
        ("json", "selection-shop-result.json"), ("receipt", "selection-shop-receipt.json"),
        ("report", "selection-shop-report.md"),
    )}
    # Transport and receipts are preserved per task; never overwrite an existing run.
    input_sha = _write(task_dir / "selection-shop-original.json", payload)
    progress("正在按原始成本计算订单金额并核对模拟权限。")
    receiver, context = _context(site, price, trace)
    review = price.review_source(payload["source"], payload["evidence"], context, receiver, payload["evaluation_time"])
    review_path = task_dir / "selection-price-review.json"
    review_sha = _write(review_path, review)
    handoff = _price_handoff(payload, receiver, review, trace, task_dir.name)
    handoff_path = task_dir / "selection-shop-handoff.json"
    handoff_sha = _write(handoff_path, handoff)
    review_raw, handoff_raw = review_path.read_bytes(), handoff_path.read_bytes()
    if (sha256(review_raw).hexdigest(), sha256(handoff_raw).hexdigest()) != (review_sha, handoff_sha):
        raise WorkflowError("商城收到的选品价审或交接文件已变化。")
    progress("商城正在读取选品交接，核对 SKU 版本、库存及事实包。")
    shop_record, extra = _consume(
        payload, task_dir, trace.parse_json(handoff_raw), trace.parse_json(review_raw), receiver, stock, trace, shop,
    )
    result = {
        "tool_id": TOOL_ID, "site": site, "currency": ROUTES[site][1], "scope": SYNTHETIC,
        "status": shop_record["status"], "selection_price_review": review, "shop_intake": shop_record,
        "handoff": handoff, "release_status": "not_authorized", "model_called": False,
        "shop_api_called": False, "business_execution_authorized": False,
        "next_actions": [ACTIONS[issue] for issue in shop_record["issues"]] or
                        ["核对模拟商品草稿；实际素材、平台规则与实店权限须另行审查。"],
        "limitations": LIMITS,
    }
    result_sha = _write(paths["json"], result)
    report_raw = _report(result).encode("utf-8")
    _write_bytes(paths["report"], report_raw)
    _write(paths["receipt"], {
        "tool_id": TOOL_ID, "site": site, "task_id": task_dir.name, "status": result["status"],
        "scope": SYNTHETIC, "release_status": "not_authorized", "business_execution_authorized": False,
        "real_source_verified": False,
        "stages": ["strict_host_price_review", "saved_price_handoff", "local_shop_intake"],
        "runs": {"model": 0, "API": 0}, "price_contract_version": price.VERSION, "trace_version": trace.VERSION,
        "input_sha256": input_sha, "price_review_sha256": review_sha, "handoff_sha256": handoff_sha,
        "result_sha256": result_sha, "report_sha256": sha256(report_raw).hexdigest(), **extra,
    })
    return result, paths

