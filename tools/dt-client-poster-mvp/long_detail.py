"""Render a site-specific product detail page with independently editable screen groups."""
from copy import deepcopy
from hashlib import sha256
import json
import re
from shutil import copyfile
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

from copy_constraints import CopyConstraintError, normalize_copy
from main_images import _id, _sha, _write
from poster import CATEGORIES, OUTPUTS, PosterError, _run_engine
from poster_workflow import WorkflowError, _run_gate
import visual_market
import visual_copy

TOOL_ID = "long-product-detail"
ENDPOINT = "/api/tasks/long-detail"
MAX_BODY = 28 * 1024 * 1024
WIDTH = 750
MOBILE_WIDTH = 375
DESCRIPTION_FILE = "product-description.en-PH.txt"
DESCRIPTION_WORDS = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
MODULES = {
    "fit": ("商品与适用", "商品和适用资料暂未提供", True, "#E9EFEA"),
    "quality": ("结构与品质", "未提供可核对的品质资料", True, "#F8F6EF"),
    "spec": ("尺寸与规格", "未提供可核对的规格", True, "#E7EFEF"),
    "use": ("使用方法", "未提供可核对的使用说明", True, "#F3ECE4"),
    "value": ("价格与选择", "当前售价与优惠暂未提供", False, "#EFF2EC"),
    "support": ("售后保障", "售后保障条款暂未提供", False, "#FAF7F1"),
    "delivery": ("配送与退换", "配送时效与退换条件暂未提供", False, "#ECF0EF"),
    "demand": ("销量与反馈", "销量与用户评价暂未提供", False, "#F6F0E7"),
}
PH_MODULES = {
    "fit": ("Product and fit", "Product information not provided", True, "#E9EFEA"),
    "quality": ("Product details", "Quality evidence not provided", True, "#F8F6EF"),
    "spec": ("Product dimensions", "Dimensions not provided", False, "#E7EFEF"),
    "use": ("How to use it", "Usage information not provided", True, "#F3ECE4"),
    "value": ("Price and options", "Price not provided", False, "#EFF2EC"),
    "support": ("After-sales support", "Support terms not provided", False, "#FAF7F1"),
    "delivery": ("Delivery and returns", "Delivery and returns not confirmed", False, "#ECF0EF"),
    "demand": ("Customer feedback", "Sales and reviews not provided", False, "#F6F0E7"),
    "size_choice": ("Choosing a size", "Size guidance not provided", False, "#E9EFEA"),
    "payment": ("Payment and COD", "COD availability not confirmed", False, "#F8F6EF"),
    "local_returns": ("Local returns and contact", "Return terms not confirmed\nSupport channel not confirmed", False, "#E7EFEF"),
    "dispatch": ("Dispatch and delivery timing", "Delivery timing not confirmed", False, "#F3ECE4"),
    "certification": ("Product certification", "Certification not confirmed", False, "#EFF2EC"),
}
PH_TYPED_MODULES = {"spec": ("dimensions", "Overall dimensions"), "value": ("price", "Listed test price")}
REFERENCES = (
    "templates/美工设计/详情页结构模板.md",
    "templates/美工设计/视觉验收清单.md",
    ".agents/skills/ecommerce-detail-page-generator-zh/SKILL.md",
    ".agents/skills/ecommerce-detail-page-generator-zh/references/buyer-decision-framework.md",
    ".agents/skills/ecommerce-detail-page-generator-zh/references/claim-evidence-rules.md",
    "docs/product-visual-flow.md",
)


class LongDetailError(ValueError):
    """The long-detail submission or export did not meet its local contract."""


def _product_description(item):
    if not isinstance(item, dict) or set(item) != {"text", "source_ref"} or not _id(item["source_ref"]):
        raise LongDetailError("Product description needs explicit text and a source reference.")
    text = item["text"]
    if (not isinstance(text, str)
            or not all(char.isprintable() or char == "\n" for char in text.replace("\r\n", "\n"))):
        raise LongDetailError("Product description must be plain text.")
    visual_market.check_ph_copy(text)
    word_count = len(DESCRIPTION_WORDS.findall(text))
    if word_count < 30:
        raise LongDetailError("Provide a product description of at least 30 words.")
    return {"text": text, "source_ref": item["source_ref"], "word_count": word_count}


def intake(payload, image_decoder, *, trusted_foreground=False):
    if trusted_foreground:
        from foreground_detail_outputs import renderer_intake_scope
        payload = renderer_intake_scope(payload, trusted_foreground)
    fields = {"site", "scope", "request_id", "product_id", "modules"}
    description_supplied = payload.get("site") == "PH" and "product_description" in payload
    if description_supplied:
        fields.add("product_description")
    try:
        listing = visual_market.listing_binding(payload, fields, _id)
        market = visual_market.ph_facts(payload, fields, _id)
        description = _product_description(payload["product_description"]) if description_supplied else None
    except ValueError as error:
        raise LongDetailError(str(error)) from error
    if not _id(payload["product_id"]):
        raise LongDetailError("请提供可核对的合成商品编号。")
    definitions = PH_MODULES if market else MODULES
    modules = payload["modules"]
    if not isinstance(modules, list) or len(modules) != len(definitions):
        raise LongDetailError(f"请提供本站全部 {len(definitions)} 个模块，并标明资料状态。")
    by_id = {}
    for row in modules:
        if not isinstance(row, dict) or "module" not in row:
            raise LongDetailError("每个模块需要模块名和资料状态。")
        key = row["module"]
        if not isinstance(key, str) or key not in definitions or key in by_id:
            raise LongDetailError("模块不存在或重复，请按八项疑虑核对。")
        by_id[key] = row

    accepted, images = [], []
    total = 0
    seen = set()
    for key, (label, placeholder, needs_image, _) in definitions.items():
        item = by_id[key]
        typed = PH_TYPED_MODULES.get(key) if market else None
        if typed:
            fact_key, fact_title = typed
            fact = market[fact_key]
            required = {"module"}
            can_use_image = key == "spec" and fact["status"] == "provided"
            if set(item) != required and (not can_use_image or set(item) != required | {"image", "image_ref"}):
                raise LongDetailError(f"{label} must use the structured market fact and may not override the wording.")
            item = {**item, "status": fact["status"], "title": fact_title, "body": visual_market.copy_for_fact(market, fact_key)}
            if fact["status"] == "provided":
                item["fact_ref"] = fact["source_ref"]
            else:
                accepted.append(item)
                continue
        if market and key == "size_choice" and item.get("status") == "not_applicable":
            if set(item) != {"module", "status", "fact_ref"} or not _id(item["fact_ref"]):
                raise LongDetailError("Size choice marked not applicable needs a source reference.")
            accepted.append({**item, "title": label, "body": "Wearable sizing does not apply"})
            continue
        if item.get("status") == "unknown":
            if needs_image or set(item) != {"module", "status"}:
                raise LongDetailError(f"{label}需要明确资料，或未知项包含未经确认的文案／素材。")
            accepted.append({"module": key, "status": "unknown", "title": label, "body": placeholder})
            continue
        if item.get("status") == "provided":
            item = {**item, **{field: None for field in ("title", "body") if visual_copy.blank(item.get(field))}}
        expected = {"module", "status", "title", "body", "fact_ref"}
        image_fields = {"image", "image_ref"}
        if (item.get("status") != "provided" or not _id(item.get("fact_ref"))
                or (set(item) != expected | image_fields and (needs_image or set(item) != expected))):
            raise LongDetailError(f"{label}缺可追溯资料、对应素材或明确文案。")
        for field, constraint in (("title", "title"), ("body", "subtitle")):
            if item[field] is None:
                continue
            try:
                if normalize_copy(item[field], constraint) != item[field]:
                    raise LongDetailError(f"{label}文案含多余首尾空白，请确认后重提。")
                if market and not typed:
                    visual_market.check_ph_copy(item[field])
            except (CopyConstraintError, ValueError) as error:
                raise LongDetailError(f"{label}：{error}") from error
        if "image" in item:
            if not _id(item["image_ref"]):
                raise LongDetailError(f"{label}素材来源编号无效。")
            data = image_decoder(item["image"])
            signature = sha256(data).hexdigest()
            if not trusted_foreground or signature not in seen:
                total += len(data)
            seen.add(signature)
            if total > 16 * 1024 * 1024:
                raise LongDetailError("标准化图片总大小不得超过 16 MiB。")
            images.append(({"name": key, "file": f"assets/{key}.png", "sha256": signature}, data))
        accepted.append({key: val for key, val in item.items() if key != "image"})
    supplied = {"product_id": payload["product_id"], "modules": accepted}
    if market:
        supplied["market"] = market
        if listing is not None:
            market["listing"] = listing
        if description is not None:
            supplied["product_description"] = description
    elif listing is not None:
        supplied["listing"] = listing
    return supplied, images


def _layout(task, parameters, assets, copy_plan):
    piece = "D01"
    elements, entries, sections = [], {}, []
    top = 0
    market = parameters.get("market")
    definitions = PH_MODULES if market else MODULES
    family, psd_font = ("Arial", "ArialMT") if market else ("Microsoft YaHei", "MicrosoftYaHei")
    for index, module in enumerate(parameters["modules"], 1):
        key = module["module"]
        label, _, _, background = definitions[key]
        asset = assets.get(key)
        height = 720 if key == "fit" else 672 if asset else 294
        title_y = 86
        image_y = 280
        if key == "fit":
            image_y, title_y = 84, 486
        text_layers = [
            ("T01", label, 34, 32, 41, 26, "#526B62"),
            ("T02", module["title"], 49, title_y, 104, 42, "#122B26"),
            ("T03", module["body"], 50, title_y + 110, 76, 28, "#29483F"),
        ]
        if module["status"] in {"unknown", "not_applicable"}:
            text_layers.pop(1)
            text_layers[1] = ("T02", module["body"], 49, 105, 92, 35, "#29483F")
        panel_elements = [
            ("B01", {"type": "rect", "role": "background", "x": 0, "y": top,
                     "width": WIDTH, "height": height, "fill": background}),
            ("M01", {"type": "rect", "role": "divider", "x": 50, "y": top + height - 16,
                     "width": 650, "height": 2, "fill": "#CAD9D2"}),
        ]
        if asset:
            panel_elements.append(("I01", {"type": "image", "role": "product", "x": 50, "y": top + image_y,
                                           "width": 650, "height": 366, "fit": "contain", "path": str(asset),
                                           "asset_sha256": _sha(asset), "provenance": module["image_ref"]}))
        for short, text, x, y, h, font, fill in text_layers:
            panel_elements.append((short, {"type": "text", "x": x, "y": top + y, "width": 650,
                                          "height": h, "fontSize": font, "fontFamily": family,
                                          "psdFontName": psd_font, "fontWeight": 600 if short == "T02" else 400,
                                          "textInsetY": 7, "padding": 2, "lineHeight": 1.15, "fill": fill, "text": text}))
        for short, element in panel_elements:
            screen = f"S{index:02d}"
            internal = f"DT-{piece}-{screen}-{short}"
            short_id = f"{short[0]}{(index-1)*3+int(short[1:]):02d}"
            element["id"] = internal
            elements.append(element)
            entries[internal] = {"internal_id": internal, "short_id": short_id, "qualified_id": f"{piece}/{short_id}",
                                 "category": CATEGORIES[short[0]], "active": True,
                                 "psd_layer_id": int(sha256(internal.encode()).hexdigest()[:7], 16)}
        sections.append({**module, "group": f"S{index:02d}", "buyer_question": label, "y": top, "height": height,
                         "asset_sha256": _sha(asset) if asset else None})
        top += height
    layout = {"schema": "dt-layout-v1", "id": piece, "site": task["site"], "width": WIDTH, "height": top,
              "task_id": task["id"], "sku": parameters["product_id"], "elements": elements,
              "copy_plan": copy_plan,
              "user_id_registry": {"schema": "dt-user-element-registry-v1", "pieces": {piece: {"entries": entries}}}}
    return layout, sections


def render(project, folder, task, parameters, assets, engine, node, progress, *, copy_execution=None):
    attempt = folder / "long-detail-attempt"
    attempt.mkdir()
    try:
        copy_hash = None
        if copy_execution is None:
            copy_body = json.dumps(parameters, ensure_ascii=False, sort_keys=True, allow_nan=False)
            user_copy = attempt / "user-copy.txt"
            user_copy.write_text(copy_body, encoding="utf-8", newline="\n")
            gate = _run_gate(project, attempt, attempt / "copy.sqlite", task["id"], "prepare", user_copy_file=user_copy)
            if gate["route"] != "USER" or not gate["layout_allowed"] or gate["copy"]["copy"]["body"] != copy_body:
                raise LongDetailError("长商详明确文案未通过来源核验。")
            copy_hash = gate["copy"]["sha256"]
        copy_plan = visual_copy.layout_plan(copy_execution, copy_hash)
        layout, sections = _layout(task, parameters, assets, copy_plan)
        source = attempt / "layout-input.json"
        _write(source, layout)
        output = attempt / "artwork"
        progress(f"正在生成 {len(sections)} 个模块和长页分组 PSD。")
        _run_engine(engine, node, ["render", source, output], attempt)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        psd = manifest["psd"]
        if (psd["status"] != "EXPORTED_AND_READ_BACK" or not manifest["exportIntegrity"]["pass"]
                or len(psd["groupTree"]) != len(sections) or psd["layerCount"] != len(layout["elements"])
                or psd["nativeTextDescriptorCount"] != sum(element["type"] == "text" for element in layout["elements"])):
            raise LongDetailError("长商详分屏、原生图层或字形完整性未通过核验。")

        portable = deepcopy(layout)
        sources = output / "source"
        slices = output / "sections"
        sources.mkdir()
        slices.mkdir()
        for element in portable["elements"]:
            if element["type"] == "image":
                path = next(path for path in assets.values() if str(path) == element["path"])
                target = sources / path.name
                copyfile(path, target)
                element["path"] = target.relative_to(output).as_posix()
        _write(output / "layout-source.json", portable)

        with Image.open(output / OUTPUTS["png"]) as page:
            if page.size != (layout["width"], layout["height"]):
                raise LongDetailError("长页实际尺寸与分屏计划不一致。")
            page.resize((MOBILE_WIDTH, layout["height"] // 2), Image.Resampling.LANCZOS).save(output / "preview.png")
            for section in sections:
                top = section["y"]
                name = f"{section['group']}-{section['module']}.png"
                page.crop((0, top, WIDTH, top + section["height"])).save(slices / name)
                section["export"] = f"sections/{name}"
        package_files = [output / name for name in (
            *OUTPUTS.values(), "preview.png", "layout-source.json", "review-numbered.json",
        )] + sorted(slices.iterdir()) + sorted(sources.iterdir())
        if task.get("source", {}).get("foreground_visual"):
            package_files.append(output / "manifest.json")
        market = parameters.get("market")
        description = parameters.get("product_description")
        description_info = {"status": "missing", "word_count": None, "export": None}
        if description is not None:
            description_file = output / DESCRIPTION_FILE
            with description_file.open("xb") as stream:
                stream.write(description["text"].encode("utf-8"))
            package_files.append(description_file)
            description_info = {"status": "provided", "copy_source": "explicit_input",
                                "source_ref": description["source_ref"], "word_count": description["word_count"],
                                "export": DESCRIPTION_FILE}
        role = visual_market.ROLE if market else "douyin-visual-designer"
        references = (*REFERENCES, *visual_market.REFERENCES) if market else REFERENCES
        report = {
            "task_id": task["id"], "role": role, "tool_id": TOOL_ID, "site": task["site"],
            "scope": "synthetic_offline", "product_id": parameters["product_id"], "copy_route": copy_plan["route"],
            "copy_sha256": copy_plan["sha256"], "reference_sha256": {ref: _sha(project / ref) for ref in references},
            "renderer_sha256": _sha(engine), "size": [layout["width"], layout["height"]],
            "preview_size": [MOBILE_WIDTH, layout["height"] // 2], "section_count": len(sections),
            "sections": sections, "native_text_count": psd["nativeTextDescriptorCount"], "layer_count": psd["layerCount"],
            "readback_sha256": _sha(output / "manifest.json"),
            "file_sha256": {path.relative_to(output).as_posix(): _sha(path) for path in package_files},
            "model_called": False, "business_execution_authorized": False, "photoshop_application_tested": False,
        }
        if copy_execution is not None:
            report["copy_execution"] = {"kind": visual_copy.TEST, "binding": copy_execution["binding"], "skill_executed": False}
        if market:
            report["market"] = market
            report["product_description"] = description_info
        elif "listing" in parameters:
            report["listing"] = parameters["listing"]
        if task.get("source", {}).get("foreground_visual"):
            from foreground_detail_outputs import renderer_provenance
            renderer_provenance(task, parameters, report, "scope")
        receipt = output / "receipt.json"
        _write(receipt, report)
        bundle_path = output / "long-detail.zip"
        with ZipFile(bundle_path, "x", compression=ZIP_DEFLATED) as bundle:
            for path in [*package_files, receipt]:
                bundle.write(path, path.relative_to(output).as_posix())
        with ZipFile(bundle_path) as bundle:
            if bundle.testzip() is not None:
                raise LongDetailError("长商详压缩包校验失败。")
        result = {**report, "bundle_sha256": _sha(bundle_path)}
        _write(output / "result.json", result)
        finished = folder / "long-detail"
        output.rename(finished)
    except (WorkflowError, PosterError, OSError, TypeError, ValueError, KeyError) as error:
        if isinstance(error, LongDetailError):
            raise
        raise LongDetailError("长商详未完成，请核对已提供材料、文案边界与渲染回执。") from error
    return result, {kind: finished / name for kind, name in (
        ("json", "result.json"), ("receipt", "receipt.json"), ("preview", "preview.png"), ("detail_bundle", "long-detail.zip"),
    )}

