"""Local fixture audit for competitor surfaces and shop funnel views."""

from collections import defaultdict
from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, ratio, term
from workflow_live_rehearsal import amount, check, identify, stamp


COMPETITOR = "competitor-surface-alignment"
SHOP = "shop-funnel-alignment"
TOOLS = (COMPETITOR, SHOP)
FILES = {"json": "channel-alignment.json", "report": "channel-alignment.md", "receipt": "channel-alignment-receipt.json"}
VIEWS = {
    COMPETITOR: {"surface": ("product_card", "shoppable_video", "live")},
    "CN": {
        "entry_source": ("mall", "search", "shop_window", "other", "content_referral"),
        "transaction_surface": ("product_card", "non_product_card"),
    },
    "PH": {"entry_source": ("recommended_shoppable_video", "direct_listing", "affiliate", "search_hashtag", "live")},
}
COMMON = {
    "impressions": "item_impression_events",
    "clicks": "item_click_events",
    "paid_orders": "buyer_paid_order_ids",
    "paid_gmv_minor": "buyer_paid_goods_minor_before_refunds",
}
METRICS = {
    COMPETITOR: {**COMMON, "ad_spend_minor": "booked_sku_ad_spend_minor"},
    SHOP: {**COMMON, "visits": "linked_product_page_visit_events", "placed_orders": "placed_order_ids",
           "refund_orders": "paid_cohort_refunded_order_ids_at_cutoff"},
}
RATIOS = {
    COMPETITOR: {"click_through": ("clicks", "impressions"), "spend_to_paid_goods": ("ad_spend_minor", "paid_gmv_minor")},
    SHOP: {"click_through": ("clicks", "impressions"), "linked_visit_per_click": ("visits", "clicks"),
           "orders_per_visit": ("placed_orders", "visits"), "payment_per_order": ("paid_orders", "placed_orders"),
           "refunded_per_paid_order": ("refund_orders", "paid_orders")},
}
IDENTITY = "site platform currency shop_id product_id sku_id cohort_id attribution_id start end"
SOURCE = "id view bucket source_ref captured_at refund_as_of measures " + IDENTITY
SHOP_ACCOUNT = "account_id listing_revision"


class WorkflowError(ValueError):
    pass


def _views(site, tool):
    return VIEWS[COMPETITOR if tool == COMPETITOR else site]


def _identity(row):
    for key in ("shop_id", "product_id", "sku_id", "cohort_id", "attribution_id"):
        identify(row[key], key)
    if "account_id" in row:
        identify(row["account_id"],"站内登记账号")
        amount(row["listing_revision"],"商品修订",1,10**6,False)
    start, end = stamp(row["start"]), stamp(row["end"])
    if not start < end or end - start > timedelta(days=90):
        raise WorkflowError("统计窗口须按本站时区填写，起止有序且不超过 90 天。")
    return start, end


def _validate(site, tool, p):
    check(p, "scope job_id cutoff target partitions observations", "渠道口径输入")
    if p["scope"] != SYNTHETIC:
        raise WorkflowError("此入口只接受明确标注的本机合成快照。")
    identify(p["job_id"], "任务编号")
    target = p["target"]
    account_scoped=tool==SHOP and isinstance(target,dict) and SHOP_ACCOUNT.split()[0] in target
    suffix=" "+SHOP_ACCOUNT if account_scoped else ""
    check(target, IDENTITY+suffix, "目标范围")
    platform, currency = ROUTES[site]
    if (target["site"], target["platform"], target["currency"]) != (site, platform, currency):
        raise WorkflowError("目标范围须匹配当前站点、平台与原币。")
    _, end = _identity(target)
    cutoff = stamp(p["cutoff"])
    if end > cutoff:
        raise WorkflowError("只检查已结束的统计窗口。")
    views = _views(site, tool)
    check(p["partitions"], " ".join(views), "视角归属")
    for proof in p["partitions"].values():
        check(proof, "classification source_ref", "分组归属")
        if proof["classification"] not in ("exclusive_complete", "overlapping", "unknown"):
            raise WorkflowError("需明确每个视角的归属是否互斥且完整。")
        term(proof["source_ref"], "互斥依据", True)
    rows = p["observations"]
    if type(rows) is not list or len(rows) > 100:
        raise WorkflowError("渠道证据须为不超过 100 行的列表。")
    for row in rows:
        check(row, SOURCE+suffix, "观察记录")
        identify(row["id"], "观察编号")
        _identity(row)
        if not isinstance(row["view"], str) or row["bucket"] not in views.get(row["view"], ()):
            raise WorkflowError("来源视角和分组必须匹配所选站点及岗位。")
        term(row["source_ref"], "数值证据来源", True)
        stamp(row["captured_at"])
        if row["refund_as_of"] is not None:
            stamp(row["refund_as_of"])
        check(row["measures"], " ".join(METRICS[tool]), "统计字段")
        for metric, entry in row["measures"].items():
            check(entry, "definition value", "单项统计")
            term(entry["definition"], metric + "口径", True)
            amount(entry["value"], metric)
    return end, cutoff


def _row_scope(row, target, end, cutoff):
    if any(row[key] != value for key,value in target.items()):
        return "identity_window_or_attribution_mismatch"
    if not end <= stamp(row["captured_at"]) <= cutoff:
        return "capture_outside_closed_window"
    if row["source_ref"] is None:
        return "source_missing"
    return None


def _row_values(row, tool, cutoff):
    values, issues = {}, {}
    for key, canonical in METRICS[tool].items():
        entry = row["measures"][key]
        reason = None
        if entry["definition"] != canonical:
            reason = "definition_missing_or_mismatch"
        elif entry["value"] is None:
            reason = "value_unknown"
        elif key == "refund_orders" and (row["refund_as_of"] is None or stamp(row["refund_as_of"]) != cutoff):
            reason = "different_or_unknown_refund_cutoff"
        elif key == "refund_orders" and stamp(row["refund_as_of"]) > stamp(row["captured_at"]):
            reason = "refund_cutoff_after_capture"
        values[key] = None if reason else entry["value"]
        if reason:
            issues[key] = reason
    links = [("clicks", "impressions")]
    if tool == SHOP:
        links += [("visits", "clicks"), ("paid_orders", "placed_orders"), ("refund_orders", "paid_orders")]
    conflicts = {child for child, parent in links if None not in (values[child], values[parent])
                 and values[child] > values[parent]}
    for key in conflicts:
        values[key] = None
        issues[key] = "numerator_conflicts_with_same_cohort_denominator"
    return values, issues


def _ratios(values, tool):
    return {key: ratio(values[n], values[d]) for key, (n, d) in RATIOS[tool].items()}


def _analyze(site, tool, p):
    end, cutoff = _validate(site, tool, p)
    by_id, groups = defaultdict(dict), defaultdict(list)
    for row in p["observations"]:
        by_id[row["id"]][encode(row)] = row
    excluded, conflict_keys, duplicates = [], set(), len(p["observations"]) - sum(map(len, by_id.values()))
    for row_id, variants in by_id.items():
        if len(variants) > 1:
            for row in variants.values():
                if not _row_scope(row, p["target"], end, cutoff):
                    conflict_keys.add((row["view"], row["bucket"]))
                excluded.append({"observation": row, "reason": "same_observation_id_conflict"})
            continue
        row = next(iter(variants.values()))
        reason = _row_scope(row, p["target"], end, cutoff)
        if reason:
            excluded.append({"observation": row, "reason": reason})
        else:
            groups[row["view"], row["bucket"]].append(row)
    output = {}
    keys = METRICS[tool]
    for view, labels in _views(site, tool).items():
        buckets = {}
        for label in labels:
            group = groups[view, label]
            if len(group) == 1 and (view, label) not in conflict_keys:
                values, issues = _row_values(group[0], tool, cutoff)
            else:
                reason = "competing_observations" if group or (view, label) in conflict_keys else "missing_bucket"
                values, issues = dict.fromkeys(keys), dict.fromkeys(keys, reason)
            buckets[label] = {"observations": group, "values": values, "issues": issues, "ratios": _ratios(values, tool)}
        partition = p["partitions"][view]
        summable = partition["classification"] == "exclusive_complete" and partition["source_ref"] is not None
        totals, coverage = {}, {}
        for key in keys:
            available = [bucket["values"][key] for bucket in buckets.values() if bucket["values"][key] is not None]
            totals[key] = sum(available) if summable and len(available) == len(labels) else None
            coverage[key] = {"known_buckets": len(available), "expected_buckets": len(labels)}
        out = {"partition": partition, "buckets": buckets, "coverage": coverage, "totals": totals,
               "ratios": _ratios(totals, tool)}
        if tool == COMPETITOR:
            out["ad_spend_share_by_surface"] = {
                label: ratio(row["values"]["ad_spend_minor"], totals["ad_spend_minor"])
                for label, row in buckets.items()}
        output[view] = out
    reconciled = {}
    for key in keys:
        totals = [view["totals"][key] for view in output.values()]
        match = all(total is not None for total in totals) and len(set(totals)) == 1
        status = "matched" if match else "pending" if None in totals else "conflict"
        reconciled[key] = {"status": status, "value": totals[0] if match else None}
    return {"target": p["target"], "cutoff": p["cutoff"], "views": output, "reconciled": reconciled,
            "reconciliation_basis": "one_declared_partition_only" if len(output) == 1 else "separate_complete_views_must_agree",
            "excluded": excluded, "identical_duplicates": duplicates, "different_views_summed": False,
            "state": "ALIGNED_FIXTURE" if not excluded and all(x["status"] == "matched" for x in reconciled.values()) else "PENDING_EVIDENCE",
            "actual_attribution_verified": False, "commercial_reuse_verified": False,
            "real_funnel_verified": False, "execution_authorized": False}


def bind(site, tool, source):
    _validate(site, tool, source)
    return {"fixture_input_sha256": sha256(encode(source)).hexdigest()}, None


def _receipt(result, raw, report):
    return {**{k: result[k] for k in ("task_id", "site", "tool_id", "scope", "input_sha256", "job_id", "state")},
            "result_sha256": sha256(raw).hexdigest(), "report_sha256": sha256(report).hexdigest(),
            "execution_authorized": False}


def run_workflow(project, site, tool, source, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("此入口仅核对结构化合成口径。")
    result = {**_analyze(site, tool, source), "task_id": folder.name, "site": site, "tool_id": tool,
              "scope": SYNTHETIC, "job_id": source["job_id"], "input_sha256": linked[0]["fixture_input_sha256"],
              "provider_called": False, "model_called": False}
    raw = encode(result)
    report = (f"# {site} · {source['job_id']} · {result['state']}\n\n"
              "仅复核本机合成口径。未核验实际入账、商业素材权利或平台归因与漏斗。各视角只核对汇总，不相加。\n\n"
              + raw.decode()).encode()
    paths = {k: folder / v for k, v in FILES.items()}
    for key, data in {"json": raw, "report": report, "receipt": encode(_receipt(result, raw, report))}.items():
        with paths[key].open("xb") as handle:
            handle.write(data)
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    if (task["local_files"] != FILES or result != json.loads(files["json"])
            or (result["task_id"], result["site"], result["tool_id"]) != (task["id"], task["site"], task["source"]["tool_id"])
            or result["input_sha256"] != task["source"]["role_packet_pins"]["fixture_input_sha256"]
            or json.loads(files["receipt"]) != _receipt(result, files["json"], files["report"])):
        raise WorkflowError("渠道报告、合成输入或取件回执未通过核验。")


def _example(site, tool):
    platform, currency = ROUTES[site]
    end = "2026-11-09T00:00:00+08:00"
    return {"scope": SYNTHETIC, "job_id": f"DEMO-{site}", "cutoff": "2026-11-12T00:00:00+08:00",
            "target": {"site": site, "platform": platform, "currency": currency, "shop_id": f"SYN-{site}-SHOP",
                       "product_id": f"SYN-{site}-PRODUCT", "sku_id": f"SYN-{site}-SKU", "cohort_id": "SYN-WEEK",
                       "attribution_id": "SYN-SINGLE-ASSIGNMENT", "start": "2026-11-02T00:00:00+08:00", "end": end},
            "partitions": {name: {"classification": "unknown", "source_ref": None} for name in _views(site, tool)},
            "observations": []}


def public_tools():
    return [{"id": tool, "label": label, "role_suffix": role, "sites": list(ROUTES),
             "input_example": _example("CN", tool),
             "input_examples": {site: _example(site, tool) for site in ROUTES},
             "description": "本机合成口径核对。对齐站点、SKU、时间和统计定义，来源不全待核，分视角独立计算。",
             "max_media_files": 0}
            for tool, label, role in ((COMPETITOR, "竞品三路数据口径核对", "competitor-analyst"),
                                      (SHOP, "商城漏斗多视角口径核对", "shop-ops"))]
