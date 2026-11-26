"""Analyze explicitly synthetic competitor review samples using the D&T keyword engine."""

from datetime import date, datetime, timezone
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import runpy


class WorkflowError(ValueError):
    """An input or local runtime error the customer can act on."""


TOOL_ID = "competitor-review-evidence"
SITES = {"CN": ("douyin", "CNY"), "PH": ("tiktok", "PHP")}
ANALYZER = "scripts/analyze_review_evidence.py"
TEMPLATE = "templates/竞品分析/双平台竞店竞品对比报告模板.md"
METHOD_DOCS = ["docs/双平台竞店与竞品对比规范.md", "docs/商详材质与评价词云工作流.md"]
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", re.ASCII)
MODES = ("sampled", "not_returned", "unknown")
METHODS = ("recent_pages", "display_sample", "other_sample")
COVERAGE_LABELS = {"sampled": "已提供样本", "not_returned": "未取回", "unknown": "未知"}
SAMPLING_LABELS = {"recent_pages": "最近若干页", "display_sample": "页面展示样本", "other_sample": "其他抽样"}
COMPARISON_BOUNDARY = "同用途模拟候选。规格相近、共用文本或外观相似不足以确认同款。"
LIMITS = [
    "只分析提交的捏捏乐、小型解压玩具评价和商详文字。输入必须为合成资料。",
    "评价来源为非随机样本；每条评价对每个词最多计一次，不同词可以重叠。频次不能推算真实瑕疵率、退货率或整店表现。",
    "商详文本与买家评价分别计算。材质词命中需结合上下文核对，只提供文字线索；实际成分、安全、耐用性均未知。",
    "未执行采集、图像/OCR、时序趋势或词云。累计总评与抽样条数分列，评价日期的来源时区未知。经营、视觉、价格、库存及供应商均未核实。",
]


def _example(site):
    platform, currency = SITES[site]
    reviews = {
        "CN": ["没有异味，手感很好。", "虽然捏爆了，还是挺好玩。", "颜色挺好，摸起来很软。"],
        "PH": ["Walang amoy at malambot.", "It leaked after one day, pero maganda.", "Maganda ang kulay."],
    }[site]
    products = []
    for index in (1, 2):
        prefix = f"DEMO-{site}-{index}"
        row = {
            "review_key": f"{prefix}-R1", "sku_id": f"{prefix}-SKU",
            "text": reviews[2 if index == 2 else 0], "review_date": "2026-11-09", "rating": 4,
            "is_incentivized_review": None, "is_verified_purchase": True,
        }
        rows = [row]
        if index == 1:
            rows += [
                row.copy(),
                {**row, "review_key": f"{prefix}-R2", "review_date": "2026-11-10",
                 "text": reviews[1], "rating": 5, "is_incentivized_review": True},
                {**row, "review_key": f"{prefix}-R3", "sku_id": None, "text": "",
                 "rating": None, "is_incentivized_review": False, "is_verified_purchase": None},
            ]
        products.append({
            "product_id": f"{prefix}-PRODUCT", "shop_id": f"{prefix}-SHOP",
            "source": {
                "reference": f"{prefix}-SOURCE", "site": site, "platform": platform,
                "currency": currency, "captured_utc": "2026-11-12T12:00:00Z",
                "window_from": "2026-11-08", "window_to": "2026-11-11",
                "coverage": "sampled", "sampling_method": "recent_pages" if site == "CN" else "display_sample",
                "has_more": True if index == 1 else None, "lifetime_review_total": 19 if index == 1 else None,
            },
            "skus": [{"sku_id": f"{prefix}-SKU", "unit_count": 1 if index == 1 else 2,
                      "specification": "蓝色、单只" if index == 1 else "蓝色、两只套装"}],
            "description_blocks": (
                ["商家文案：外壳 TPR，具体成分待核。"] if index == 1 and site == "CN"
                else ["Seller copy: silicone shell."] if index == 1
                else ["慢回弹，成分未知。"] if site == "CN"
                else ["Slow-rising toy; composition unspecified."]
            ),
            "reviews": rows,
        })
    return {"site": site, "synthetic": True, "category": "squishy-toy", "products": products}


def public_tools():
    return [{
        "id": TOOL_ID, "label": "比较竞品评价样本", "role_suffix": "competitor-analyst",
        "sites": list(SITES), "input_example": _example("CN"),
        "input_examples": {site: _example(site) for site in SITES},
        "description": (
            "分析两个不同模拟店铺的捏捏乐样本，按评价去重并保存 SKU、来源、时间窗和激励标记。"
            "中国样本可计算中文词频；菲律宾样本可核样本覆盖与星级，菲律宾语、Taglish 和英语语义待人工复核。"
        ),
        "execution_scope": "本地统计 · 合成样本 · 不访问店铺或模型",
        "max_media_files": 0,
    }]


def _fields(obj, names, where):
    if not isinstance(obj, dict) or set(obj) != set(names.split()):
        raise WorkflowError(f"{where}字段与示例不符，请检查漏填或多填的字段。")


def _text(value, limit, empty=False):
    return (isinstance(value, str) and len(value) <= limit and (empty or value.strip())
            and all(ord(char) >= 32 and not 0xD800 <= ord(char) <= 0xDFFF for char in value))


def _id(value, where):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise WorkflowError(f"{where}须为 1–64 位字母、数字、连字符或下划线。")
    return value


def _date(value, where, utc=False):
    pattern = ISO_UTC if utc else ISO_DAY
    try:
        if isinstance(value, str) and pattern.fullmatch(value):
            return datetime.fromisoformat(value.replace("Z", "+00:00")) if utc else date.fromisoformat(value)
    except ValueError:
        pass
    raise WorkflowError(f"{where}须为有效日期，按示例填写{' UTC 时间' if utc else ' YYYY-MM-DD'}。")


def _count(value, limit):
    return type(value) is int and 0 <= value <= limit


def _nullable_flag(value):
    return value is None or type(value) is bool


def _validate_source(source, platform, currency, site, product_number):
    _fields(source, "reference site platform currency captured_utc window_from window_to "
            "coverage sampling_method has_more lifetime_review_total", f"商品 {product_number} 来源")
    if source["site"] != site or source["platform"] != platform or source["currency"] != currency:
        raise WorkflowError("来源站点、平台及币种须与当前任务一致（CN/douyin/CNY 或 PH/tiktok/PHP）。")
    _id(source["reference"], "来源引用")
    captured = _date(source["captured_utc"], "样本快照时间", utc=True)
    if source["coverage"] not in MODES or not _nullable_flag(source["has_more"]):
        raise WorkflowError("请明确填写来源覆盖状态；是否还有更多评价可填写 null。")
    if source["lifetime_review_total"] is not None and not _count(source["lifetime_review_total"], 1_000_000_000):
        raise WorkflowError("累计总评条数须为非负整数或 null。")
    start, end = source["window_from"], source["window_to"]
    if source["coverage"] == "sampled":
        if source["sampling_method"] not in METHODS:
            raise WorkflowError("有评价样本时，请填写实际抽样方法。")
        start, end = _date(start, "窗口开始"), _date(end, "窗口结束")
        if start > end or end > captured.date():
            raise WorkflowError("评价窗口起止顺序有误，或结束日超过快照 UTC 日期。")
    elif any(value is not None for value in (start, end, source["sampling_method"])):
        raise WorkflowError("评价未取回或覆盖未知时，样本窗口和抽样方法须留空（null）。")
    return start, end


def _validate_product(product, site, index):
    _fields(product, "product_id shop_id source skus description_blocks reviews", f"商品 {index}")
    _id(product["product_id"], "商品 ID")
    _id(product["shop_id"], "店铺 ID")
    source = product["source"]
    start, end = _validate_source(source, *SITES[site], site, index)

    sku_index = {}
    if not isinstance(product["skus"], list) or len(product["skus"]) > 30:
        raise WorkflowError("每件商品最多可填写 30 个 SKU，未知 SKU 清单可留空。")
    for item in product["skus"]:
        _fields(item, "sku_id unit_count specification", "SKU")
        sku_id = _id(item["sku_id"], "SKU ID")
        if sku_id in sku_index:
            raise WorkflowError("同一商品内 SKU ID 重复，请核对所属规格。")
        for value, valid in (
            (item["unit_count"], lambda number: _count(number, 1000) and number > 0),
            (item["specification"], lambda label: _text(label, 160)),
        ):
            if value is not None and not valid(value):
                raise WorkflowError("规格或件数无效。件数须为正整数，未知值请用 null。")
        sku_index[sku_id] = item

    descriptions = product["description_blocks"]
    if (not isinstance(descriptions, list) or len(descriptions) > 20
            or any(not _text(item, 600) for item in descriptions)):
        raise WorkflowError("商详可留空，或填写最多 20 条单行文本，每条不超过 600 字。")

    rows = product["reviews"]
    if not isinstance(rows, list) or len(rows) > 150:
        raise WorkflowError("每件商品最多可填写 150 条评价。")
    if bool(rows) != (source["coverage"] == "sampled"):
        raise WorkflowError("评价行数与声明的样本覆盖矛盾；取不到评价时请填写 not_returned 或 unknown。")

    review_index = {}
    for row in rows:
        _fields(row, "review_key sku_id text review_date rating is_incentivized_review is_verified_purchase", "评价")
        key = _id(row["review_key"], "评价匿名引用")
        if row["sku_id"] is not None and _id(row["sku_id"], "评价 SKU ID") not in sku_index:
            raise WorkflowError("评价引用了不属于该商品的 SKU；所属变体未知请填写 null。")
        review_day = _date(row["review_date"], "评价日期")
        if not start <= review_day <= end:
            raise WorkflowError("评价日期超出声明的样本窗口。")
        if not _text(row["text"], 800, empty=True):
            raise WorkflowError("评价正文应为不超过 800 字的单行文本，空评可填写空字符串。")
        if row["rating"] is not None and not (_count(row["rating"], 5) and row["rating"] > 0):
            raise WorkflowError("星级须为 1–5 的整数或 null。")
        if not all(_nullable_flag(row[name]) for name in ("is_incentivized_review", "is_verified_purchase")):
            raise WorkflowError("激励及已购标记请填写 true、false 或 null。")
        if key in review_index and row != review_index[key]:
            raise WorkflowError(f"相同评价引用 {key} 的原文、日期、SKU 或标记互相冲突，请查对后再提交。")
        review_index[key] = row

    total = source["lifetime_review_total"]
    if total is not None and total < len(review_index):
        raise WorkflowError("累计总评少于去重样本数，请核对累计口径与快照时间。")
    return set(review_index)


def _validate(site, payload):
    _fields(payload, "site synthetic category products", "竞品分析输入")
    if payload["site"] != site or payload["synthetic"] is not True or payload["category"] != "squishy-toy":
        raise WorkflowError("请按当前站点提交明确标记 synthetic=true 的 squishy-toy 合成样本。")
    products = payload["products"]
    if not isinstance(products, list) or len(products) != 2:
        raise WorkflowError("请输入两个不同店铺各一个同用途样本商品。")
    keys = [_validate_product(product, site, index) for index, product in enumerate(products, 1)]
    if any(products[0][field] == products[1][field] for field in ("product_id", "shop_id")):
        raise WorkflowError("两件商品及其店铺 ID 必须各自不同。")
    if keys[0] & keys[1]:
        raise WorkflowError("同一评价引用出现在两个商品下，请核对商品归属。")


def _analyze(analyze_product, site, product):
    source_program = {
        "product_id": product["product_id"],
        "description_blocks": product["description_blocks"],
        "unique_reviews": [{**row, "time": row["review_date"]} for row in product["reviews"]],
    }
    analyzed = analyze_product(source_program, [])
    rows = analyzed["review_evidence"]
    stats = analyzed["sample_statistics"]
    if site == "CN":
        mode = "CN_SQUISHY_KEYWORDS"
    else:
        mode = "PH_SEMANTIC_ANALYSIS_PENDING"
        # The project's vocabulary targets Chinese and Indonesian squishy toy phrases.
        # Suppress semantic metrics completely for Filipino, Taglish and English input.
        for metric in ("usable_text", "quality_keyword_reviews", "risk_keyword_reviews"):
            stats[metric] = None
        for row in rows:
            row["usable_text"] = row["keywords"] = None
    if product["source"]["coverage"] != "sampled":
        mode = "REVIEW_TEXT_UNAVAILABLE"
        for metric in ("usable_text", "quality_keyword_reviews", "risk_keyword_reviews"):
            stats[metric] = None
    stats.update({
        "input_rows": len(product["reviews"]),
        "duplicates_removed": len(product["reviews"]) - stats["unique_reviews"],
        "empty_text": stats["unique_reviews"] - stats["nonempty_text"],
        "has_unresolved_sku": sum(row["sku_id"] is None for row in rows),
        "incentive_flag_unknown_reviews": stats["unique_reviews"] - stats["incentive_flag_known_reviews"],
        "rating_unknown_reviews": stats["sample_star_distribution"].pop("None", 0),
        "verified_purchase_reviews": sum(row["is_verified_purchase"] is True for row in rows),
        "verified_purchase_flag_unknown_reviews": sum(row["is_verified_purchase"] is None for row in rows),
    })
    for row in rows:
        row.pop("time")
    analysis = {
        "product_id": product["product_id"], "shop_id": product["shop_id"],
        "source": product["source"], "skus": product["skus"],
        "sample_statistics": stats, "keyword_analysis_mode": mode,
        "review_count_by_known_sku": {
            sku["sku_id"]: sum(row["sku_id"] == sku["sku_id"] for row in rows)
            for sku in product["skus"]
        },
        "keyword_frequencies": analyzed["keyword_frequencies"] if mode == "CN_SQUISHY_KEYWORDS" else None,
        "material_mentions": [
            {**mention, "evidence_status": "merchant_text_keyword_match_only"}
            for mention in analyzed["material_mentions"]
        ],
        "review_evidence": rows,
    }
    return analysis


def _comparison(products):
    a, b = products
    coverage = (a["source"], b["source"])
    count_sets = [{item["unit_count"] for item in product["skus"]} for product in products]
    if all(len(items) == 1 and None not in items for items in count_sets) and count_sets[0] != count_sets[1]:
        pack_basis = "PACK_COUNT_DIFFERS"
    elif all(len(items) == 1 and None not in items for items in count_sets):
        pack_basis = "PACK_COUNT_MATCHES_ONLY"
    else:
        pack_basis = "PACK_COUNT_UNCONFIRMED"
    same_window = (
        all(source["coverage"] == "sampled" for source in coverage)
        and all(coverage[0][key] == coverage[1][key] for key in ("window_from", "window_to", "sampling_method"))
    )
    return {
        "product_relationship": "SAME_USE_ONLY",
        "relationship_statement": COMPARISON_BOUNDARY,
        "pack_comparison": pack_basis, "same_sampling_window_and_method": same_window,
        "quality_winner": None, "shop_benchmark_winner": None, "commercial_priority_product": None,
        "confidence": "SYNTHETIC_DIRECTION_ONLY",
        "recommendation": "先按同件数和可确认规格补采同窗样本。质量、选品与投放结论待可比证据。",
    }


def _next_steps(analysis, site):
    steps = []
    for product in analysis:
        product_id = product["product_id"]
        if product["keyword_frequencies"]:
            risks = [term for term in product["keyword_frequencies"] if term["polarity"] == "risk"]
            if risks:
                first = risks[0]
                steps.append({
                    "role_suffix": "selection", "product_id": product_id,
                    "review_keys": [example["review_key"] for example in first["examples"]],
                    "action": f"选品前优先回读“{first['keyword']}”风险线索的原文，核对 SKU 与发生条件，补充同规格样本。",
                })
        if product["keyword_analysis_mode"] == "REVIEW_TEXT_UNAVAILABLE":
            steps.append({
                "role_suffix": "competitor-analyst", "product_id": product_id, "review_keys": [],
                "action": "下一轮补交可回查的评价样本与对应抽样窗口；当前无法判断评价方向。",
            })
    if site == "PH":
        steps.append({
            "role_suffix": "competitor-analyst", "product_id": None, "review_keys": [],
            "action": "解释菲律宾语、Taglish 或英语评价前，逐条人工核对原文、SKU、否定和混合描述。",
        })
    steps.append({
        "role_suffix": "visual-designer", "product_id": None, "review_keys": [],
        "action": "出素材前核对目标商品资料和图片；仅将这些样本反馈用作待验证问题，不承继材质或功效宣称。",
    })
    return steps


def _md(value):
    # Escape field content rendered in downloadable Markdown, including links and HTML.
    return re.sub(r"([\\|~*_\x60\[\]!#])", r"\\\1", escape(str(value)))


def _display(value):
    if value is None:
        return "未知或未测"
    if value is True:
        return "是"
    if value is False:
        return "否"
    return _md(value)


def _report(result):
    site = result["site"]
    lines = [
        f"# 竞品评价样本比较 · {site} · 合成测试",
        "",
        result["comparison"]["recommendation"],
        COMPARISON_BOUNDARY,
        "店铺、商业机会和质量胜负均未判定。置信度仅限合成样本方向。",
        "",
        "## 对象与样本",
        "",
    ]
    for product in result["products"]:
        source, stats = product["source"], product["sample_statistics"]
        lines += [
            f"### 商品 {_md(product['product_id'])}，店铺 {_md(product['shop_id'])}",
            "",
            f"- 来源 {_md(source['reference'])} · {site}/{result['platform']}/{source['currency']} · "
            f"快照 {_md(source['captured_utc'])}；样本窗口 {_display(source['window_from'])} 至 {_display(source['window_to'])}。",
            f"- 覆盖 {COVERAGE_LABELS[source['coverage']]}，抽样方法 {SAMPLING_LABELS.get(source['sampling_method'], '未知')}，"
            f"来源仍有更多评价 {_display(source['has_more'])}。",
            f"- 累计总评 {_display(source['lifetime_review_total'])}；取回行 {stats['input_rows']}，"
            f"去重 {stats['unique_reviews']}，非空 {stats['nonempty_text']}，空评 {stats['empty_text']}，"
            f"有效文本 {_display(stats['usable_text'])}，激励 {stats['incentivized_reviews']}，"
            f"激励未知 {stats['incentive_flag_unknown_reviews']}，未归属 SKU {stats['has_unresolved_sku']}。",
            f"- 样本星级 {_md(json.dumps(stats['sample_star_distribution'], ensure_ascii=False))}，"
            f"未知星级 {stats['rating_unknown_reviews']}。",
        ]
        if stats["unique_reviews"] < 30:
            lines.append("- 样本少于 30 条，仅供方向参考，不作经营结论依据。")
        for sku in product["skus"]:
            count = product["review_count_by_known_sku"][sku["sku_id"]]
            lines.append(f"- SKU {_md(sku['sku_id'])}，件数 {_display(sku['unit_count'])}，"
                         f"规格 {_display(sku['specification'])}，样本归属 {count} 条。")
        lines += ["", "#### 商品描述词与评价词频", ""]
        for mention in product["material_mentions"]:
            lines.append(f"- 商家文字命中 {_md(mention['material_label'])}，来源 {_md(mention['source_path'])}，原文「{_md(mention['text'])}」。")
        if not product["material_mentions"]:
            lines.append("- 所提交商详未命中项目材质词表，实际成分未知。")
        terms = product["keyword_frequencies"]
        if terms is None:
            reason = "菲律宾语、Taglish 及英语评价语义待人工复核。" if product[
                "keyword_analysis_mode"] == "PH_SEMANTIC_ANALYSIS_PENDING" else "未取回评价正文。"
            lines.append("- " + reason)
        elif not terms:
            lines.append("- 当前词表没有命中评价正文，仍需结合原文核查。")
        else:
            for term in terms[:12]:
                example = term["examples"][0]
                lines.append(
                    f"- {_md(term['keyword'])}（{term['polarity']}） {term['review_count']} 条。"
                    f"原文示例 {_md(example['review_key'])}「{_md(example['quote'])}」。"
                )
            lines.append("- 下载计算 JSON 查看全部命中、完整原文、SKU 与激励标记。")
        lines.append("")
    lines += ["## 岗位交接建议", ""]
    lines += [f"- {_md(step['role_suffix'])} · {_display(step['product_id'])} · {_md(step['action'])}"
              for step in result["next_steps"]]
    lines += ["", "## 计算依据与边界", ""]
    lines += [f"- {limit}" for limit in LIMITS]
    return "\n".join(lines) + "\n"


def _json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress):
    if site not in SITES or tool_id != TOOL_ID:
        raise WorkflowError("竞品评价入口仅支持中国抖音和菲律宾 TikTok。")
    if assets:
        raise WorkflowError("此入口只处理合成评价 JSON，请移除上传文件。")
    _validate(site, payload)
    platform, _currency = SITES[site]
    engine = Path(project) / ANALYZER
    progress("已核对站点、SKU 与抽样窗口，正在计算去重与评价证据。")
    try:
        engine_bytes = engine.read_bytes()
        analyze_product = runpy.run_path(str(engine))["analyze_product"]
    except (OSError, KeyError, ImportError, SyntaxError):
        raise WorkflowError("找不到可运行的 D&T 评价分析脚本，请检查本机配套项目。") from None
    analyzed = [_analyze(analyze_product, site, item) for item in payload["products"]]
    method = {
        "engine": ANALYZER, "engine_sha256": sha256(engine_bytes).hexdigest(),
        "method_docs": METHOD_DOCS, "template_reference": TEMPLATE,
        "role_skill": f".agents/skills/{platform}-competitor-analyst-playbook/SKILL.md",
    }
    result = {
        "schema_version": "1.0", "site": site, "platform": platform,
        "role": f"{platform}-competitor-analyst", "tool_id": tool_id,
        "mode": "SYNTHETIC_OFFLINE", "category": payload["category"],
        "method": method, "comparison": _comparison(analyzed), "products": analyzed,
        "next_steps": _next_steps(analyzed, site), "limitations": LIMITS,
    }
    dest = Path(task_dir)
    paths = {"json": dest / "result.json", "receipt": dest / "receipt.json", "report": dest / "report.md"}
    result_bytes = _json_bytes({**result, "source_input": payload})
    report_bytes = _report(result).encode("utf-8")
    receipt = {
        "site": site, "role": result["role"], "tool_id": tool_id,
        "executed_utc": datetime.now(timezone.utc).isoformat(), "data_request_count": 0,
        "mode": result["mode"], "method": method, "status": "ANALYZED_LOCAL",
        "source_input_serialized_sha256": sha256(_json_bytes(payload)).hexdigest(),
        "result_sha256": sha256(result_bytes).hexdigest(),
        "report_sha256": sha256(report_bytes).hexdigest(),
    }
    try:
        paths["json"].write_bytes(result_bytes)
        paths["report"].write_bytes(report_bytes)
        paths["receipt"].write_bytes(_json_bytes(receipt))
    except OSError:
        raise WorkflowError("无法保存本地评价分析，检查任务目录权限和剩余空间。") from None
    return result, paths
