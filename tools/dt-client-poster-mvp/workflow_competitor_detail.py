"""Compare explicitly bound local SKU detail images and synthetic review evidence."""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile

import workflow_competitor as base

TOOL = "competitor-detail-image-evidence"
MAX_IMAGES = 8
MAX_ALL_BYTES = 16 * 1024 * 1024
OCR_HOST = "ocr_detail_winrt.exe"
OCR_EXPECTED_SHA256 = "03c0bb4789554aa75c5f081cabe2cce6fc198d084627f65471b097218c9a8355"
SKU_LINE = re.compile(r"^(?i:SKU)[:：]?([A-Za-z0-9_-]{1,64})$")
NET_WEIGHT = re.compile(r"^净重[:：]?([0-9]{1,6}(?:\.[0-9]{1,3})?)(?:G|克)$")
SCOPE = "synthetic_offline"
LIMITS = [
    "图片是归属待核的商家说明，评价是买家反馈。实际材质、安全及瑕疵率无法由此证实。",
    "仅比较抖音合成小型解压玩具样本。仅对明确标注且同 SKU 的净重进行克数比对。",
    "词频只纳入同商品同 SKU、已购且明确未激励的样本。排除项单独留痕，不能据此推断作假。",
]


class WorkflowError(ValueError):
    """Submitted image metadata is not safe to consume."""


def public_tools():
    return [{
        "id": TOOL, "label": "抖音商详图与评价证据", "role_suffix": "competitor-analyst", "sites": ["CN"],
        "description": "在已有合成竞品评价 JSON 上增加 detail_images，保存明确 SKU 的图片、OCR 原文和评价证据。",
        "execution_scope": "本机合成 · 不采集、不对接店铺或模型", "max_media_files": 0,
        "input_example": {**base._example("CN"), "detail_images": []},
    }]


def _digest(data):
    return sha256(data).hexdigest()


def _validate(site, payload):
    base._fields(payload, "site synthetic category products detail_images", "商详图片与评价输入")
    if site != "CN":
        raise WorkflowError("仅接收抖音中国本机合成材料。")
    original = {key: value for key, value in payload.items() if key != "detail_images"}
    base._validate(site, original)
    subjects = {p["product_id"]: {sku["sku_id"] for sku in p["skus"]} for p in original["products"]}
    if sum(len(skus) for skus in subjects.values()) > 12:
        raise WorkflowError("图片专项最多接受两个商品合计 12 个 SKU。")
    rows = payload["detail_images"]
    if not isinstance(rows, list) or len(rows) > MAX_IMAGES:
        raise WorkflowError("最多接受 8 张有来源的 PNG 或 JPEG 商详图。")
    return subjects, rows


def intake(site, payload, decode):
    subjects, rows = _validate(site, payload)
    clean, files, used, image_hash_to_subject = [], [], set(), {}
    total = 0
    for index, row in enumerate(rows, 1):
        base._fields(row, "image_id product_id sku_id source_ref captured_utc image", "商详图片")
        for field in ("image_id", "product_id", "sku_id", "source_ref"):
            base._id(row[field], field)
        base._date(row["captured_utc"], "图片保存时间", utc=True)
        subject = (row["product_id"], row["sku_id"])
        if subject[1] not in subjects.get(subject[0], ()):
            raise WorkflowError("商详图片引用了该商品不存在的 SKU。")
        if row["image_id"] in used:
            raise WorkflowError("商详图片引用重复。")
        used.add(row["image_id"])
        data = decode(row["image"])
        total += len(data)
        if total > MAX_ALL_BYTES:
            raise WorkflowError("商详 PNG 的规范化总大小不得超过 16 MiB。")
        digest = _digest(data)
        prior = image_hash_to_subject.setdefault(digest, subject)
        if prior != subject:
            raise WorkflowError("同一图片字节被分配给不同 SKU，需核对归属。")
        files.append(({"name": row["image_id"], "file": f"assets/detail-{index:02d}.png", "sha256": digest}, data))
        clean.append({**{key: v for key, v in row.items() if key != "image"},
                      "normalized_png_sha256": digest, "asset_name": f"detail-{index:02d}.png"})
    return {**payload, "detail_images": clean}, files


def _norm(text):
    return re.sub(r"\s+", "", text).upper()


def _extract_weight(text):
    match = NET_WEIGHT.fullmatch(_norm(text))
    return (format(Decimal(match[1]).normalize(), "f")
            if match and Decimal(match[1]) > 0 else None)


def _ocr(path, executable):
    engine = "Windows.Media.Ocr"
    if not executable.is_file():
        return {"status": "pending", "reason": "local_ocr_host_not_available"}
    env = {k: v for k, v in os.environ.items()
           if k.upper() in {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE"}}
    try:
        if _digest(executable.read_bytes()) != OCR_EXPECTED_SHA256:
            return {"status": "pending", "reason": "local_ocr_host_sha256_mismatch"}
        run = subprocess.run([str(executable), str(path)], capture_output=True, env=env, cwd=path.parent,
                             timeout=18, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if run.returncode:
            return {"status": "pending", "reason": "local_ocr_process_failed", "exit_code": run.returncode}
        if len(run.stdout) > 48 * 1024:
            return {"status": "pending", "reason": "local_ocr_output_too_large"}
        lines = run.stdout.decode("utf-8-sig").splitlines()
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return {"status": "pending", "reason": "local_ocr_process_unavailable"}
    if not lines:
        return {"status": "pending", "reason": "no_ocr_text"}
    return {"status": "extracted", "raw_lines": lines, "engine": engine, "language": "zh-Hans-CN",
            "engine_binary_sha256": OCR_EXPECTED_SHA256, "extracted_utc": datetime.now(timezone.utc).isoformat()}


def _image_observations(site, payload, assets):
    subjects, rows = _validate(site, payload)
    if {row["image_id"] for row in rows} != assets.keys():
        raise WorkflowError("任务商详图片不完整或与绑定资料不符。")
    executable = Path(__file__).with_name(OCR_HOST)
    result = []
    for row in rows:
        path = assets[row["image_id"]]
        if (_digest(path.read_bytes()) != row["normalized_png_sha256"] or path.name != row["asset_name"]
                or row["sku_id"] not in subjects.get(row["product_id"], ())):
            raise WorkflowError("已保存的图片证据或 SKU 发生变化。")
        extracted = _ocr(path, executable)
        if _digest(path.read_bytes()) != row["normalized_png_sha256"]:
            raise WorkflowError("OCR 期间商详图已变化。")
        lines = extracted.get("raw_lines", [])
        anchors = sorted({value.group(1) for line in lines if (value := SKU_LINE.fullmatch(re.sub(r"\s+", "", line)))})
        # A visible token only corroborates the submitted SKU binding. It is not seller authenticity.
        expected = row["sku_id"]
        status = "SKU_MISMATCH" if anchors and anchors != [expected] else "SKU_CORROBORATED" if anchors else "PENDING"
        weights = [{"line_number": index, "raw_line": line, "value_g": amount}
                   for index, line in enumerate(lines, 1) if (amount := _extract_weight(line)) is not None]
        result.append({**row, "site": "CN", "ocr": extracted, "visible_sku_tokens": anchors,
                       "sku_binding": status, "seller_image_weight_candidates": weights,
                       "usable_for_sku_comparison": status == "SKU_CORROBORATED"})
    return result


def _admissible(review, sku_id):
    reasons = []
    if not review["text"].strip():
        reasons.append("empty_text")
    if review["sku_id"] != sku_id:
        reasons.append("sku_not_equal_or_unknown")
    if review["is_incentivized_review"] is not False:
        reasons.append("incentive_true_or_unknown")
    if review["is_verified_purchase"] is not True:
        reasons.append("purchase_false_or_unknown")
    return reasons


def _wordcloud(scoped, frequencies, engine, font_path):
    from PIL import Image, ImageDraw, ImageFont
    from wordcloud import WordCloud

    counts = {row["keyword"]: row["review_count"] for row in frequencies}
    colors = {row["keyword"]: engine["COLORS"][row["polarity"]] for row in frequencies}
    canvas = Image.new("RGB", (1520, 940), "white")
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype(str(font_path), 30)
    body = ImageFont.truetype(str(font_path), 25)
    draw.text((44, 38), scoped["display_name"], fill="#14283a", font=title)
    draw.text((44, 94), f"纳入 {scoped['sample_statistics']['unique_reviews']} 条已购且明确未激励的同 SKU 合成评价。",
              fill="#4e5c69", font=body)
    draw.text((44, 134), "字号仅供浏览，词频按评价条数计，不代表真实瑕疵率。", fill="#a24b27", font=body)
    cloud = WordCloud(font_path=str(font_path), width=1432, height=510, background_color="white", random_state=41,
                      max_words=40, prefer_horizontal=1, relative_scaling=.7, repeat=False,
                      color_func=lambda word, **_: colors[word]).generate_from_frequencies(counts)
    canvas.paste(cloud.to_image(), (44, 192))
    draw.text((44, 731), "红：风险反馈    绿：正向反馈    蓝：买家描述", fill="#263e52", font=body)
    for index, row in enumerate(frequencies[:8]):
        draw.text((44 + index % 2 * 716, 780 + index // 2 * 34), f"{row['keyword']}  {row['review_count']} 条",
                  fill=colors[row["keyword"]], font=body)
    return canvas


def _one_product(item, images, engine, task_dir, archive):
    unique = {review["review_key"]: review for review in item["reviews"]}
    scopes = []
    for sku in item["skus"]:
        sku_id = sku["sku_id"]
        sku_images = [row for row in images if row["sku_id"] == sku_id]
        expected = _extract_weight(sku["specification"]) if sku["specification"] else None
        recognized = [{
            "image_id": image["image_id"], "source_ref": image["source_ref"],
            "image_sha256": image["normalized_png_sha256"], **value,
        } for image in sku_images if image["usable_for_sku_comparison"] for value in image["seller_image_weight_candidates"]]
        amounts = {row["value_g"] for row in recognized}
        if len(amounts) > 1 or (amounts and expected is not None and amounts != {expected}):
            status = "CONFLICT"
        elif amounts and expected is not None and all(row["sku_binding"] == "SKU_CORROBORATED" for row in sku_images):
            status = "MATCHED_SELLER_CLAIMS"
        else:
            status = "PENDING"
        rows = []
        admitted = []
        for review in unique.values():
            if review["sku_id"] not in (sku_id, None):
                continue
            reasons = _admissible(review, sku_id)
            rows.append({**review, "excluded_reasons": reasons})
            if not reasons:
                admitted.append({**review, "time": review["review_date"]})
        scoped = engine["analyze_product"]({
            "platform": "douyin", "product_id": item["product_id"], "display_name": f"{item['product_id']} / {sku_id}",
            "unique_reviews": admitted, "description_blocks": [],
        }, [])
        frequencies = scoped["keyword_frequencies"]
        cloud = {"status": "no_eligible_keywords"}
        if frequencies:
            name = f"cloud-{len(archive)+1:02d}.png"
            path = task_dir / name
            try:
                fonts = Path(os.environ["WINDIR"]) / "Fonts/msyh.ttc"
                _wordcloud(scoped, frequencies, engine, fonts).save(path)
                archive[name] = path
                cloud = {"status": "rendered", "file": name, "sha256": _digest(path.read_bytes())}
            except (ImportError, OSError, ValueError, KeyError):
                cloud = {"status": "pending", "reason": "local_wordcloud_runtime_unavailable"}
        scopes.append({
            "sku": sku, "detail_text_weight_g": expected, "detail_text_reference": item["source"]["reference"],
            "sku_image_refs": [row["image_id"] for row in sku_images],
            "image_weight_candidates": recognized, "weight_comparison_status": status,
            "weight_fact_verified": False, "visual_gaps": ([] if sku_images else ["no_detail_image"])
                + (["ocr_or_visible_sku_unconfirmed"] if any(row["sku_binding"] != "SKU_CORROBORATED" for row in sku_images) else []),
            "reviews_for_this_or_unknown_sku": rows, "admissible_review_count": len(admitted),
            "excluded_review_count": len(rows)-len(admitted), "review_evidence": scoped["review_evidence"],
            "keyword_frequencies": frequencies if admitted else None, "wordcloud": cloud,
        })
    product_copy = [{"kind": "merchant_text", "source_ref": item["source"]["reference"],
                     "line": index, "raw_text": line, "sku_id": None}
                    for index, line in enumerate(item["description_blocks"], 1)]
    image_copy = [{"kind": "merchant_image_ocr", "source_ref": image["source_ref"], "line": index, "raw_text": line,
                   "image_id": image["image_id"], "sha256": image["normalized_png_sha256"],
                   "sku_id": image["sku_id"] if image["usable_for_sku_comparison"] else None,
                   "sku_binding": image["sku_binding"]}
                  for image in images for index, line in enumerate(image["ocr"].get("raw_lines", []), 1)]
    material_leads = [{**row, "material_label": label, "verified": False}
                      for rows in (product_copy, image_copy) for row in rows for label, pattern in engine["MATERIALS"]
                      if re.search(pattern, _norm(row["raw_text"]), re.I)]
    return {
        "product_id": item["product_id"], "shop_id": item["shop_id"], "source": item["source"],
        "merchant_material_leads": material_leads,
        "merchant_description_blocks": item["description_blocks"], "images": images, "sku_evidence": scopes,
        "review_input_rows": len(item["reviews"]), "deduplicated_review_rows": len(unique),
    }


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress):
    if tool_id != TOOL or site != "CN":
        raise WorkflowError("仅支持抖音中国商详本机证据。")
    task_dir, project = Path(task_dir), Path(project)
    analyzer = project / base.ANALYZER
    try:
        engine = runpy.run_path(str(analyzer))
        progress("正在核对图片字节、SKU 绑定及本机 OCR。")
        images = _image_observations(site, payload, assets)
        archive = {f"images/{row['asset_name']}": assets[row["image_id"]] for row in images}
        progress("正在按 SKU 去重评价、区分可信范围和否定语境。")
        products = [_one_product(item, [row for row in images if row["product_id"] == item["product_id"]],
                                 engine, task_dir, archive) for item in payload["products"]]
        all_states = Counter(row["weight_comparison_status"] for item in products for row in item["sku_evidence"])
        result = {"tool_id": TOOL, "task_id": task_dir.name, "site": site, "role": "douyin-competitor-analyst",
                  "scope": SCOPE, "source_authenticity_verified": False, "quality_winner": None,
                  "comparison": "same_use_only", "sku_state_counts": dict(all_states), "products": products,
                  "analysis_script_sha256": _digest(analyzer.read_bytes()), "limitations": LIMITS,
                  "external_request_count": 0, "model_called": False, "real_business_authorized": False}
        paths = {"json": task_dir/"detail-result.json", "receipt": task_dir/"detail-receipt.json",
                 "evidence_bundle": task_dir/"detail-evidence.zip"}
        paths["json"].write_bytes(base._json_bytes(result))
        archive["detail-result.json"] = paths["json"]
        with ZipFile(paths["evidence_bundle"], "x", compression=ZIP_DEFLATED) as zipped:
            for name, path in sorted(archive.items()):
                zipped.write(path, name)
        with ZipFile(paths["evidence_bundle"]) as zipped:
            if zipped.testzip() is not None:
                raise WorkflowError("商详证据压缩包读回未通过。")
        receipt = {"task_id": task_dir.name, "site": site, "scope": SCOPE, "tool_id": TOOL,
                   "payload_sha256": _digest(base._json_bytes(payload)), "result_sha256": _digest(paths["json"].read_bytes()),
                   "bundle_sha256": _digest(paths["evidence_bundle"].read_bytes()),
                   "images": {row["image_id"]: row["normalized_png_sha256"] for row in images},
                   "external_request_count": 0, "model_called": False}
        paths["receipt"].write_bytes(base._json_bytes(receipt))
    except (OSError, KeyError, ImportError, SyntaxError) as error:
        raise WorkflowError("无法读取或保存商详证据与本机分析依赖。") from error
    return result, paths

