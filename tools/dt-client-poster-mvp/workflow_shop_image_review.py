"""Offline image inspection with immutable numbered review receipts."""
from hashlib import sha256
from io import BytesIO
import json
import re
from zipfile import ZipFile

from PIL import Image
from role_tools import SYNTHETIC

TOOL = "ph-shop-image-review"
TOOLS = {"CN": "cn-shop-image-review", "PH": TOOL}
PNG = b"\x89PNG\r\n\x1a\n"
DETAIL_COUNT = {"CN": 8, "PH": 13}
ASSET_IDS = {site: {*(f"P{i:02}" for i in range(1, 6)), *(f"S{i:02}" for i in range(1, count + 1))}
             for site, count in DETAIL_COUNT.items()}
CHECKS = ("appearance", "size_copy", "certification_copy", "crop", "occlusion")
REVIEW_ITEMS = {"CN": (*CHECKS, "language", "copy_consistency"), "PH": CHECKS}
OUTPUTS = {"json": "image-review/result.json", "report": "image-review/report.md",
           "receipt": "image-review/receipt.json", "preview": "image-review/selected.png"}
REFERENCES = {"CN": (".agents/skills/douyin-shop-ops-playbook/SKILL.md", "scripts/shop_image_recheck_v12.py"),
              "PH": (".agents/skills/tiktok-shop-ops-playbook/SKILL.md", "scripts/shop_image_recheck_v12.py")}


class WorkflowError(ValueError):
    pass


class MissingImage(WorkflowError):
    pass


def _matches(pattern, text):
    return isinstance(text, str) and re.fullmatch(pattern, text, re.ASCII)


def _note(value, limit):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and len(value) <= limit and value.isprintable()


def _region(box):
    return isinstance(box, list) and len(box) == 4 and all(type(n) is int and 0 <= n <= 1000 for n in box) \
        and box[0] < box[2] and box[1] < box[3]


def validate(site, payload):
    if not isinstance(payload, dict):
        raise WorkflowError("请提供逐图审核的 JSON 资料。")
    mode = payload.get("mode")
    common = {"mode", "scope", "sku_id", "version", "image_id"}
    if (payload.get("scope") != SYNTHETIC or not _matches(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,83}", payload.get("sku_id"))
            or not isinstance(payload.get("image_id"), str) or payload["image_id"] not in ASSET_IDS[site]
            or type(payload.get("version")) is not int or not 1 <= payload["version"] <= 1_000_000_000):
        raise WorkflowError(f"请填写合成资料标记、SKU、有效版本和 P01–P05 或 S01–S{DETAIL_COUNT[site]:02} 编号。")
    if mode == "open":
        required = common | {"intake_task_id"}
        if not required <= payload.keys() or payload.keys() - required - {"revises_review_task_id"}:
            raise WorkflowError("打开原图需要商城收件任务号；返修请提供上一张退回回执号。")
        for k in ("intake_task_id", "revises_review_task_id"):
            if k in payload and not _matches(r"[0-9a-f]{32}", payload[k]):
                raise WorkflowError("收件或返修回执须为本工作台的有效任务号。")
    elif mode == "review":
        required = common | {"inspection_task_id", "image_sha256", "reviewer", "checks"}
        if (payload.keys() != required or not _matches(r"[0-9a-f]{32}", payload["inspection_task_id"])
                or not _matches(r"[0-9a-f]{64}", payload["image_sha256"]) or not _note(payload["reviewer"], 80)):
            raise WorkflowError("审核须填写原图任务号、图片哈希和审阅标识。")
        checks = payload["checks"]
        if not isinstance(checks, dict) or set(checks) - set(REVIEW_ITEMS[site]):
            raise WorkflowError("审核项与所选站点的清单不符。")
        for entry in checks.values():
            if (not isinstance(entry, dict) or set(entry) - {"status", "note", "region"}
                    or entry.get("status") not in ("ok", "defect", "unknown")
                    or ("note" in entry and not _note(entry["note"], 300))
                    or ("region" in entry and not _region(entry["region"]))):
                raise WorkflowError("审核证据须包含有效状态；位置以图内千分比矩形表示。")
    else:
        raise WorkflowError("请选择 open 打开原图或 review 提交目视审核。")


def asset_from_manifest(delivery, payload, package):
    bucket = "main" if payload["image_id"].startswith("P") else "detail"
    group = delivery[bucket]
    matches = [item for item in group["files"] if item["id"] == payload["image_id"]]
    if len(matches) != 1:
        raise MissingImage("收件清单缺少该编号图片或出现重复。")
    item = matches[0]
    with ZipFile(BytesIO(package)) as archive:
        picks = [entry for entry in archive.infolist() if entry.filename == item["file"]]
        if len(picks) != 1 or not 0 < picks[0].file_size <= 10_000_000:
            raise MissingImage("资料包缺图、重复或成员超限。")
        if sha256(package).hexdigest() != group["bundle_sha256"]:
            raise WorkflowError("上游资料包与商城收件回执不一致。")
        data = archive.read(picks[0])
    if not data.startswith(PNG) or sha256(data).hexdigest() != item["sha256"] or len(data) != item["bytes"]:
        raise WorkflowError("图片内容与收件回执不一致。")
    with Image.open(BytesIO(data)) as image:
        w, h = image.size
        if image.format != "PNG" or w * h > 18_000_000:
            raise WorkflowError("图片格式或画布不受支持。")
        image.verify()
    return {"image_id": item["id"], "sha256": item["sha256"], "bytes": len(data),
            "pixel_width": w, "pixel_height": h, "group": bucket, "visual_task_id": group["task_id"]}, data


def same_listing_asset(record, payload, *, version=True):
    keys = ("sku_id", "image_id", "version") if version else ("sku_id", "image_id")
    return all(record[key] == payload[key] for key in keys)


def check_revision(prior, current, payload):
    if (prior["status"] != "returned_for_revision" or not same_listing_asset(prior, payload, version=False)
            or prior["product_id"] != current["product_id"] or prior["version"] >= payload["version"]
            or prior["asset"]["sha256"] == current["asset"]["sha256"]):
        raise WorkflowError("返修须使用同站、同商品、同 SKU 和同编号的新版本，目标图片必须有实际更新。")
    old = prior["manifest_sha256_by_id"]
    new = current["manifest_sha256_by_id"]
    return {"previous_review_task_id": prior["task_id"], "previous_version": prior["version"],
            "previous_image_sha256": prior["asset"]["sha256"],
            "previous_defects": [k for k, v in prior["checks"].items() if v["status"] == "defect"],
            "changed_image_ids": [k for k in old if new.get(k) != old[k]],
            "unchanged_image_count": sum(new.get(k) == v for k, v in old.items())}


def assessment(site, payload, linked):
    checks = {}
    for k in REVIEW_ITEMS[site]:
        item = payload["checks"].get(k, {})
        outcome = item.get("status", "unknown")
        if outcome != "unknown" and not (item.get("note") and item.get("region")):
            outcome = "unknown"
        checks[k] = {"status": outcome, "note": item.get("note"), "region": item.get("region")}
    defects = [k for k, row in checks.items() if row["status"] == "defect"]
    missing = [k for k, row in checks.items() if row["status"] == "unknown"]
    prior = (linked.get("revision") or {}).get("previous_defects", [])
    status = "returned_for_revision" if defects else ("pending_evidence" if missing else "visual_review_passed")
    return {"status": status, "reviewer": payload["reviewer"], "checks": checks,
            "returned_checks": defects, "missing_evidence_checks": missing,
            "revision_resolution": {k: {"ok":"closed","defect":"open","unknown":"pending"}[checks[k]["status"]]
                                    for k in prior}}


def _json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def receipt_for(result, result_raw, report_raw):
    return {"task_id": result["task_id"], "site": result["site"], "sku_id": result["sku_id"],
            "version": result["version"], "image_id": result["image_id"], "status": result["status"],
            "source_task_id": result["intake_task_id"], "image_sha256": result["asset"]["sha256"],
            "result_sha256": sha256(result_raw).hexdigest(), "report_sha256": sha256(report_raw).hexdigest(),
            "physical_facts_verified": False, "upload_authorized": False, "model_called": False}


def save_review(project, site, folder, payload, linked, data):
    if payload["mode"] == "review":
        decision = assessment(site, payload, linked)
    else:
        decision = {"status": "awaiting_visual_observation"}
    result = {"workflow": TOOLS[site], "task_id": folder.name, "site": site, "scope": SYNTHETIC,
              "sku_id": payload["sku_id"], "version": payload["version"], "image_id": payload["image_id"],
              "mode": payload["mode"], **linked, **decision,
              "fact_verification": {"physical_appearance": "unknown", "physical_dimensions": "unknown",
                                    "certification": "unknown"},
              "observations_are_locally_supplied": payload["mode"] == "review",
              "upload_authorized": False, "physical_facts_verified": False,
              "model_called": False, "provider_called": False,
              "method_sources": [{"path": p, "sha256": sha256((project/p).read_bytes()).hexdigest()}
                                 for p in REFERENCES[site]]}
    result_raw = _json_bytes(result)
    report = [f"{site} 合成资料逐图审阅 · {result['image_id']} · v{result['version']}",
              f"SKU {result['sku_id']}", f"图片 SHA-256 {result['asset']['sha256']}",
              f"状态 {result['status']}", "可见文字与实际尺码、实物和认证分别记录。后者均待证。"]
    for key, row in result.get("checks", {}).items():
        report.append(f"{key} {row['status']} | {row['region']} | {row['note'] or '缺证'}")
    if result.get("revision"):
        report.append(f"返修来源 v{result['revision']['previous_version']} {result['revision']['previous_review_task_id']}")
        report.append(f"原问题复查 {result.get('revision_resolution', {})}")
    report_raw = ("\n".join(report)+"\n").encode("utf-8")
    (folder/"image-review").mkdir()
    outputs={key:folder/name for key,name in OUTPUTS.items()}
    for k,v in {"json":result_raw,"report":report_raw,"preview":data,
                "receipt":_json_bytes(receipt_for(result,result_raw,report_raw))}.items():
        with outputs[k].open("xb") as stream:
            stream.write(v)
    return result, outputs


def result_artifacts(task, reads):
    saved=task["result"]
    if (task["local_files"] != OUTPUTS or saved != json.loads(reads["json"])
            or saved["task_id"] != task["id"] or saved["site"] != task["site"]
            or saved["workflow"] != TOOLS[task["site"]] or sha256(reads["preview"]).hexdigest() != saved["asset"]["sha256"]
            or json.loads(reads["receipt"]) != receipt_for(saved, reads["json"], reads["report"])):
        raise WorkflowError("逐图审核的版本、文件或回执与保存记录不一致。")
    return saved


def public_tools():
    return [{"id": tool, "role_suffix": "shop-ops", "sites": [site], "label": "逐图审核与返修复审",
             "description": "先以 open 打开已收件的原图，再用 review 提交实际目视记录。"
                            "两步均填同一 SKU、版本、P/S 编号。新版打开时带退回审核任务号。"
                            "备注和图内千分比位置均有记录的检查才可判定。",
             "execution_scope": "本机合成资料及目视证据记录。不会证明实物尺寸、认证或可上架。",
             "input_example": {"mode":"open","scope":SYNTHETIC,"sku_id":f"{site}-DEMO-01","version":1,
                               "image_id":"S01" if site=="CN" else "S09","intake_task_id":"0"*32},
             "max_media_files": 0} for site,tool in TOOLS.items()]

