"""Read-only competitor comparison readiness from frozen archive projections."""

from hashlib import sha256
import json

from role_tools import PROVIDER
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import check, identify, stamp

TOOL = "competitor-two-shop-compare-readiness"
TOOLS = (TOOL,)
FILES = {"json": "comparison-readiness.json", "report": "comparison-readiness.md", "receipt": "comparison-receipt.json"}
SOURCES = {
    "CN": {
        "cn_historical": ("41498f466ffaf743f4eca4645f7cda32f075ad28bd09b62f659da4f94c32ec33",
                          "3ad2f4914964e632d2f53c1521b5d174268ec8e3f37f808065c7e4f1dbd68d0a"),
        "cn_nov16_projection": ("3147982467a2aa161019971a58437532cafaf4de2305f6c473ebfbaa42baa18c",
                                "5cb3b3c7fda2f4c362bb120e750b534a0689a1b3dd1313988b473089dd37da65"),
        "cn_nov16_overlap_index": ("55fa2d02bb6c07ce3d851765e1cc190537d3250cc59cd3a198c7d50a3f27213c",
                                  "305754073fff90318038b4061fd0ac8115b1e25f006157cd4323a4317503b7d6"),
    },
    "PH": {
        "ph_recent_7_28_projection": ("296ce6596fb712ad695d5e58716549314edf9acc000e74a31ad978552b876757",
                                      "f9b6ef029591f96f9def8be3a55d59afab3c23e6fb35a31c3cccf1fc5074dc5d"),
    }
}
CHANNELS = ("product_card", "linked_short_video", "live")


class WorkflowError(ValueError):
    pass


def _fingerprint(data):
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def bind(site, payload):
    check(payload, "site scope currency job_id requested_end requested_shops analyst archives", "两店比较预检")
    if (payload["site"], payload["scope"], payload["currency"]) != (site, PROVIDER, {"CN":"CNY", "PH":"PHP"}[site]):
        raise WorkflowError("站点、币种与历史观测范围必须一致。")
    identify(payload["job_id"], "比较任务编号")
    stamp(payload["requested_end"])
    term(payload["analyst"], "竞品分析测试角色")
    shops = payload["requested_shops"]
    if not isinstance(shops, list) or len(shops) != 2:
        raise WorkflowError("必须保留两个比较位置。")
    for shop in shops:
        if shop is not None:
            identify(shop, "店铺标识")
    if shops[0] is not None and shops[0] == shops[1]:
        raise WorkflowError("不能以同一家店代替两店比较。")
    check(payload["archives"], " ".join(SOURCES[site]), "本站获准档案")
    pins = {}
    for source_id, (canonical, file_hash) in SOURCES[site].items():
        source = payload["archives"][source_id]
        check(source, "file_sha256 data", "档案与原文件哈希")
        if source["file_sha256"] != file_hash or _fingerprint(source["data"]) != canonical:
            raise WorkflowError("原件或档案内容哈希不匹配。")
        pins[source_id] = {"archive_file_sha256": file_hash, "canonical_projection_sha256": canonical}
    return pins, None


def _reported_cn(data):
    search_rows = []
    for source_id in ("cn_historical", "cn_nov16_projection"):
        source = data[source_id]["data"]
        for row in source["records"]:
            search_rows.append({
                "archive": source_id, "product_id": row["product_id"], "promotion_id": row["promotion_id"],
                "display_price_as_received": row["price_raw"],
                "price_basis": "provider_display_only" if row["price_raw"] is not None else "unknown",
                "shop_id": None, "sku_id": None,
            })
    overlap = data["cn_nov16_overlap_index"]["data"]
    return {"projected_observations": search_rows,
            "search_overlap_index": {key:overlap[key] for key in (
                "first_response_records", "local_later_success_records", "shared_product_ids",
                "distinct_product_ids", "sku_identity_verified"
            )}}


def _reported_ph(data):
    rows = data["ph_recent_7_28_projection"]["data"]["records"]
    measures = ("day7_units_sold","day7_gmv","day28_units_sold","day28_gmv")
    return {"projected_observations": [{
        "product_id": row["identity"]["product_id"]["value"],
        "shop_id": row["seller_id"], "sku_id": row["sku_id"],
        "currency": row["identity"]["currency"]["value"],
        "nominal_estimates": {key:row["metrics"][key] for key in measures},
    } for row in rows]}


def receipt(result, files):
    return {key:result[key] for key in ("task_id", "site", "job_id", "state", "upstream", "input_sha256",
                                       "comparison_release_allowed", "execution_authorized")} | {
        "result_sha256": sha256(files["json"]).hexdigest(), "report_sha256": sha256(files["report"]).hexdigest()}


def run_workflow(project, site, tool, payload, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("此预检使用已归档来源。")
    pins, _ = linked
    observed = (_reported_cn if site == "CN" else _reported_ph)(payload["archives"])
    result = {key:payload[key] for key in ("site","currency","scope","job_id","requested_end","requested_shops","analyst")}
    result.update(tool_id=tool, task_id=folder.name, state="PENDING_COMPARABLE_EVIDENCE",
                  upstream=pins, input_sha256=sha256(encode(payload)).hexdigest(), requested_window_days=30,
                  observed=observed, verified_shop_ids=[], actual_thirty_day_window_verified=False,
                  shop_type_and_official_tier=None, shop_catalogue_size=None, transaction_count=None,
                  confirmed_revenue=None, actual_revenue_per_unit=None, actual_order_aov=None,
                  channels={channel:dict.fromkeys(("impressions","clicks","conversions","orders","attributed_revenue","ad_roi"))
                            for channel in CHANNELS},
                  later_consumer_ready=False, comparison_release_allowed=False,
                  execution_authorized=False, model_called=False, provider_called=False)
    lines = [f"# {site} 两店近 30 日比较预检 | {result['job_id']}", "",
             "状态：待核。当前档案未证实两家独立店铺和同一 30 日统计窗。",
             f"请求截止 {result['requested_end']}；所附历史档案以哈希绑定，独立保存观测和请求。",
             "店铺主体、官方层级、目录规模、交易、件均、订单客单、三渠道与广告 ROI 保持未知。",
             ("PH 仅保留历史产品级第三方 7/28 日估算，原精确窗口和时区未知。" if site == "PH"
              else "CN 仅保留历史搜索展示和去重索引；展示价格及商品 ID 不等于成交或店铺身份。"),
             "需获准的同站两店身份和同窗同口径资料，之后才能接 C03/C07 比较。"]
    for name, pin in pins.items():
        lines.append(f"- {name} · 档案 SHA-256 {pin['archive_file_sha256']}")
    binary = {"json":encode(result), "report": ("\n".join(lines)+"\n").encode("utf-8")}
    binary["receipt"] = encode(receipt(result,binary))
    paths = {key:folder/name for key,name in FILES.items()}
    for key,path in paths.items():
        with path.open("xb") as out:
            out.write(binary[key])
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    if (task["local_files"] != FILES or result != json.loads(files["json"])
            or result["upstream"] != task["source"]["role_packet_pins"]
            or (result["task_id"],result["site"],result["tool_id"]) !=
            (task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(files["receipt"]) != receipt(result,files)):
        raise WorkflowError("比较预检档案或回执无法复核。")


def public_tools():
    return [{"id":TOOL,"sites":["CN","PH"],"role_suffix":"competitor-analyst",
             "label":"两店近 30 日比较预检",
             "description":"登记本站获准快照，保留店铺、分母与统计窗未匹配状态。",
             "input_example":{"site":"CN","scope":PROVIDER,"currency":"CNY","job_id":"CN-C02-QA",
                              "requested_end":"2026-11-21T08:00:00+08:00","requested_shops":[None,None],
                              "analyst":"CN-COMP-QA","archives":{}}}]

