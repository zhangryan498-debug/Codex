"""Compose an offline five-card set from explicit copy and local assets."""
from hashlib import file_digest, sha256
from io import BytesIO
import json
import re
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

from copy_constraints import CopyConstraintError, normalize_copy
from poster import CATEGORIES, OUTPUTS, PosterError, _run_engine
from poster_workflow import WorkflowError, _run_gate
import visual_market
import visual_copy

TOOL_ID = "main-image-set"
ENDPOINT = "/api/tasks/main-images"
SIZE = 1600
THUMB = 360
MIN_ADDITIONAL_EDGE = 720
MAX_BODY = 28 * 1024 * 1024
SLOTS = ("feature", "advantage", "benefit", "proof", "scene")
REFERENCES = ("templates/美工设计/主图需求单.md", "templates/美工设计/视觉验收清单.md",
              "docs/product-visual-flow.md", "runtime-agents/douyin-visual-designer/agent.md",
              ".agents/skills/douyin-visual-designer-playbook/SKILL.md")
COMPOSITIONS = {
    "feature": ("#FFFFFF", (100, 80, 1400, 1030), (128, 1160, 1344)),
    "advantage": ("#EDF3F1", (780, 240, 720, 1100), (100, 535, 650)),
    "benefit": ("#F9F0E6", (120, 485, 1360, 1000), (128, 110, 1344)),
    "proof": ("#F0F1F7", (105, 235, 770, 1120), (920, 540, 580)),
    "scene": ("#ECEFE6", (100, 190, 1400, 960), (150, 1205, 1300)),
}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
BUNDLE_FILES = (*OUTPUTS.values(), "thumbnail.png")
COVER_EDGE = 1472
COVER_FILE = "cover.png"
VIEWS = {"front", "side", "back", "detail", "scene", "unknown"}


class MainImageError(ValueError):
    """An offline main-image request or render could not be completed."""


def _sha(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _write(path, data):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _id(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def _cover_bounds(image):
    bounds = image.getchannel("A").getbbox()
    if (not bounds or bounds[0] == 0 or bounds[1] == 0
            or bounds[2] == image.width or bounds[3] == image.height):
        raise MainImageError("Cover needs an isolated transparent front view with space on all four sides.")
    return bounds


def _export_cover(path, source, source_ref):
    with Image.open(source) as image:
        cutout = image.crop(_cover_bounds(image))
    factor = COVER_EDGE / max(cutout.size)
    target = cutout.resize(tuple(round(side * factor) for side in cutout.size), Image.Resampling.LANCZOS)
    out = Image.new("RGB", (SIZE, SIZE), "white")
    out.paste(target.convert("RGB"), tuple((SIZE - side) // 2 for side in target.size), target.getchannel("A"))
    out.save(path, "PNG")
    alpha = target.getchannel("A")
    pixels = sum(alpha.histogram()[128:])
    return {"file": COVER_FILE, "sha256": _sha(path), "source_sha256": _sha(source), "source_ref": source_ref,
            "declared_view": "front", "size": [SIZE, SIZE], "foreground_box_size": list(target.size),
            "foreground_area_ratio": pixels / (SIZE * SIZE), "foreground_alpha_cutoff": 128,
            "background": "#FFFFFF", "text_overlay_added": False}


def intake(payload, image_decoder, *, trusted_foreground=False):
    if trusted_foreground:
        from foreground_detail_outputs import renderer_intake_scope
        payload = renderer_intake_scope(payload, trusted_foreground)
    fields = {"site", "scope", "request_id", "product_id", "cards"}
    additional_output = None
    if "additional_output" in payload:
        fields.add("additional_output")
        item = payload["additional_output"]
        if (not isinstance(item, dict) or set(item) != {"edge", "spec_ref"}
                or type(item["edge"]) is not int or not MIN_ADDITIONAL_EDGE <= item["edge"] < SIZE
                or not _id(item["spec_ref"])):
            raise MainImageError("附加方图须填写 720–1599 像素的整数边长及有效规格来源编号。")
        additional_output = item
    has_cover = payload.get("site") == "PH" and "cover" in payload
    if has_cover:
        fields.add("cover")
    try:
        listing = visual_market.listing_binding(payload, fields, _id)
        market = visual_market.ph_facts(payload, fields, _id)
    except ValueError as error:
        raise MainImageError(str(error)) from error
    if not _id(payload["product_id"]):
        raise MainImageError("请提供可核对的合成商品编号。")
    cards = payload["cards"]
    if not isinstance(cards, list) or len(cards) != len(SLOTS):
        raise MainImageError("请提供五张主图的图片及素材来源编号。")
    by_slot = {}
    for card in cards:
        card_fields = {"slot", "title", "subtitle", "source_ref", "image"}
        if market and isinstance(card, dict) and "view" in card:
            card_fields.add("view")
            if not isinstance(card["view"], str) or card["view"] not in VIEWS:
                raise MainImageError("Unknown source-view label.")
        if isinstance(card, dict):
            card = {**card, **{field: None for field in ("title", "subtitle") if visual_copy.blank(card.get(field))}}
        if not isinstance(card, dict) or set(card) != card_fields:
            raise MainImageError("每张主图须有槽位、标题、副标题、素材来源编号和图片。")
        slot = card["slot"]
        if not isinstance(slot, str) or slot not in SLOTS or slot in by_slot or not _id(card["source_ref"]):
            raise MainImageError("请检查五个槽位是否齐全、互不重复，以及素材来源编号是否有效。")
        for field in ("title", "subtitle"):
            if card[field] is None:
                continue
            try:
                if normalize_copy(card[field], field) != card[field]:
                    raise MainImageError("显式文案中有多余首尾空白，请确认后重提。")
                if market:
                    visual_market.check_ph_copy(card[field])
            except (CopyConstraintError, ValueError) as error:
                raise MainImageError(str(error)) from error
        by_slot[slot] = card

    clean_cards, images = [], []
    total = 0
    seen = set()
    for slot in SLOTS:
        card = by_slot[slot]
        raw = image_decoder(card["image"])
        signature = sha256(raw).hexdigest()
        if not trusted_foreground or signature not in seen:
            total += len(raw)
        seen.add(signature)
        if total > 16 * 1024 * 1024:
            raise MainImageError("五张规范化图片合计不得超过 16 MiB。")
        meta = {"name": slot, "file": f"assets/{slot}.png", "sha256": signature}
        images.append((meta, raw))
        clean_cards.append({key: value for key, value in card.items() if key != "image"})
    supplied = {"product_id": payload["product_id"], "scope": payload["scope"], "cards": clean_cards}
    if additional_output is not None:
        supplied["additional_output"] = additional_output
    if market:
        supplied["market"] = market
        if listing is not None:
            market["listing"] = listing
    elif listing is not None:
        supplied["listing"] = listing
    if has_cover:
        cover = payload["cover"]
        if (not isinstance(cover, dict) or set(cover) != {"source_ref", "view", "image"}
                or not _id(cover["source_ref"]) or cover["view"] != "front"):
            raise MainImageError("Cover needs explicit source evidence and a declared front view.")
        raw = image_decoder(cover["image"])
        if total + len(raw) > 16 * 1024 * 1024:
            raise MainImageError("规范化主图和封面合计不得超过 16 MiB。")
        with Image.open(BytesIO(raw)) as image:
            _cover_bounds(image)
        images.append(({"name": "cover", "file": "assets/cover.png", "sha256": sha256(raw).hexdigest()}, raw))
        supplied["cover_source_ref"] = cover["source_ref"]
    return supplied, images


def _layout(task, product_id, card, asset, index, copy_plan, market, edge):
    background, box, anchor = COMPOSITIONS[card["slot"]]
    x, y, width, height = box
    tx, ty, tw = anchor
    piece = f"P{index:02d}"
    title_font, subtitle_font = (68, 46) if tw < 800 else (78, 47)
    elements = [
        {"type": "rect", "role": "background", "x": 0, "y": 0, "width": SIZE, "height": SIZE, "fill": background},
        {"type": "image", "role": "product", "x": x, "y": y, "width": width, "height": height, "fit": "contain",
         "path": str(asset), "asset_sha256": _sha(asset), "provenance": card["source_ref"]},
        {"type": "text", "label": "主文案", "x": tx, "y": ty, "width": tw, "height": 204,
         "fontSize": title_font, "fill": "#173430", "text": card["title"]},
        {"type": "text", "label": "说明", "x": tx, "y": ty + 206, "width": tw, "height": 148,
         "fontSize": subtitle_font, "fill": "#3B504B", "text": card["subtitle"]},
    ]
    short_ids = ["B01", "I01", "T01", "T02"]
    fact_key = {"benefit": "price", "proof": "dimensions"}.get(card["slot"]) if market else None
    if fact_key:
        short_ids.append("T03")
        elements.append({"type": "text", "label": "Local product fact", "x": 128, "y": 1492, "width": 1344,
                         "height": 104, "fontSize": 46, "fill": "#29483F",
                         "text": visual_market.copy_for_fact(market, fact_key)})
    entries = {}
    for element, short in zip(elements, short_ids, strict=True):
        internal = f"DT-{piece}-{short}"
        element["id"] = internal
        entries[internal] = {"internal_id": internal, "psd_layer_id": int(sha256(internal.encode()).hexdigest()[:7], 16),
                             "short_id": short, "qualified_id": f"{piece}/{short}",
                             "category": CATEGORIES[short[0]], "active": True}
        if element["type"] == "text":
            font, psd_font = ("Arial", "ArialMT") if market else ("Microsoft YaHei", "MicrosoftYaHei")
            element.update(fontFamily=font, psdFontName=psd_font, padding=12, textInsetY=18, lineHeight=1.12)
    if edge != SIZE:
        for element in elements:
            for key in ("x", "y", "width", "height", "fontSize", "padding", "textInsetY"):
                if key in element:
                    element[key] = round(element[key] * edge / SIZE)
    return {"schema": "dt-layout-v1", "id": piece, "width": edge, "height": edge, "site": task["site"],
            "sku": product_id, "task_id": task["id"], "scope": TOOL_ID, "copy_plan": copy_plan,
            "elements": elements,
            "user_id_registry": {"schema": "dt-user-element-registry-v1", "pieces": {piece: {"entries": entries}}}}


def _export_card(engine, node, draft, task, parameters, card, asset, index, copy_plan, edge=SIZE):
    output = draft if edge == SIZE else draft / "sizes" / f"{edge}x{edge}"
    output.mkdir(parents=True, exist_ok=True)
    piece = output / f"{index:02d}-{card['slot']}"
    layout = _layout(task, parameters["product_id"], card, asset, index, copy_plan, parameters.get("market"), edge)
    spec = output / (piece.name + ".json")
    _write(spec, layout)
    diagnostics = output / f"engine-{index:02d}"
    diagnostics.mkdir()
    _run_engine(engine, node, ["render", spec, piece], diagnostics)
    manifest = json.loads((piece / "manifest.json").read_text(encoding="utf-8"))
    native = sum(e["type"] == "text" for e in layout["elements"])
    if (manifest["psd"]["status"] != "EXPORTED_AND_READ_BACK" or manifest["nativeText"]["count"] != native
            or manifest["exportIntegrity"]["pass"] is not True):
        raise MainImageError("主图 PNG／PSD 的边界、原生文字或读回未通过。")
    with Image.open(piece / OUTPUTS["png"]) as picture:
        if picture.size != (edge, edge):
            raise MainImageError("主图导出尺寸不符合所选方图规格。")
        thumbnail = picture.convert("RGB").resize((THUMB, THUMB), Image.Resampling.LANCZOS)
        thumbnail.save(piece / "thumbnail.png")
    bundle_files = (*BUNDLE_FILES, "manifest.json") if task.get("source", {}).get("foreground_visual") else BUNDLE_FILES
    files = {(piece / name).relative_to(draft).as_posix(): _sha(piece / name) for name in bundle_files}
    return ({"slot": card["slot"], "piece": layout["id"], "asset_sha256": _sha(asset),
             "source_ref": card["source_ref"], "text": {"title": card["title"], "subtitle": card["subtitle"]},
             "size": [edge, edge], "thumbnail_size": [THUMB, THUMB], "file_sha256": files,
             "engine_manifest_sha256": _sha(piece / "manifest.json"), "integrity_checked": True}, thumbnail)


def render(project, task_dir, task, parameters, assets, engine, node, progress, *, copy_execution=None):
    draft = task_dir / "main-image-attempt"
    draft.mkdir()
    try:
        copy_hash = None
        if copy_execution is None:
            copy_body = json.dumps(parameters, ensure_ascii=False, sort_keys=True, allow_nan=False)
            copy_file = draft / "user-copy.txt"
            with copy_file.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(copy_body)
            gate = _run_gate(project, draft, draft / "copy.sqlite", task["id"], "prepare", user_copy_file=copy_file)
            if (gate["route"] != "USER" or not gate["layout_allowed"] or gate["copy"]["copy"]["body"] != copy_body):
                raise MainImageError("主图显式文案回执与输入不一致。")
            copy_hash = gate["copy"]["sha256"]
        copy_plan = visual_copy.layout_plan(copy_execution, copy_hash)
        cards = []
        preview = Image.new("RGB", (3 * THUMB, 2 * THUMB), "#E5E9E7")
        for index, card in enumerate(parameters["cards"], 1):
            progress(f"正在渲染主图 {index}/{len(SLOTS)}，并核对可编辑 PSD。")
            item, thumbnail = _export_card(engine, node, draft, task, parameters, card, assets[card["slot"]], index,
                                           copy_plan)
            if "view" in card:
                item["view"] = card["view"]
            cards.append(item)
            preview.paste(thumbnail, (((index - 1) % 3) * THUMB, ((index - 1) // 3) * THUMB))
        additional = parameters.get("additional_output")
        extra_cards = []
        if additional is not None:
            for index, card in enumerate(parameters["cards"], 1):
                progress(f"正在渲染附加方图 {index}/{len(SLOTS)}，并核对可编辑 PSD。")
                item, _ = _export_card(engine, node, draft, task, parameters, card, assets[card["slot"]], index,
                                       copy_plan, edge=additional["edge"])
                if "view" in card:
                    item["view"] = card["view"]
                extra_cards.append(item)
        preview.save(draft / "preview.png")
        market = parameters.get("market")
        role = visual_market.ROLE if market else "douyin-visual-designer"
        references = (*REFERENCES[:3], *visual_market.REFERENCES) if market else REFERENCES
        report = {"tool_id": TOOL_ID, "task_id": task["id"], "role": role, "site": task["site"],
                  "mode": "synthetic_offline", "product_id": parameters["product_id"], "copy_route": copy_plan["route"],
                  "copy_sha256": copy_plan["sha256"], "renderer_sha256": _sha(engine),
                  "reference_sha256": {ref: _sha(project / ref) for ref in references},
                  "preview_sha256": _sha(draft / "preview.png"), "cards": cards, "model_called": False,
                  "business_execution_authorized": False, "size_is_offline_preset": True}
        if copy_execution is not None:
            report["copy_execution"] = {"kind": visual_copy.TEST, "binding": copy_execution["binding"], "skill_executed": False}
        if market:
            report["market"] = market
        elif "listing" in parameters:
            report["listing"] = parameters["listing"]
        if additional is not None:
            report["additional_output"] = {**additional, "cards": extra_cards,
                                           "scope": "offline_render", "platform_approval": False}
        if "cover" in assets:
            report["cover"] = _export_cover(draft / COVER_FILE, assets["cover"], parameters["cover_source_ref"])
        if task.get("source", {}).get("foreground_visual"):
            from foreground_detail_outputs import renderer_provenance
            renderer_provenance(task, parameters, report, "mode")
        _write(draft / "receipt.json", report)
        with ZipFile(draft / "main-images.zip", "x", compression=ZIP_DEFLATED) as bundle:
            bundle.write(draft / "receipt.json", "receipt.json")
            for card in (*cards, *extra_cards):
                for relative in card["file_sha256"]:
                    bundle.write(draft / relative, relative)
            bundle.write(draft / "preview.png", "preview.png")
            if "cover" in report:
                bundle.write(draft / COVER_FILE, COVER_FILE)
        with ZipFile(draft / "main-images.zip") as bundle:
            if bundle.testzip() is not None:
                raise MainImageError("主图归档未通过读回核验。")
        result = {**report, "bundle_sha256": _sha(draft / "main-images.zip"),
                  "preview_url": f"/api/tasks/{task['id']}/files/preview",
                  "download_url": f"/api/tasks/{task['id']}/files/bundle"}
        _write(draft / "result.json", result)
        completed = task_dir / "main-images"
        draft.rename(completed)
    except (WorkflowError, PosterError, OSError, ValueError, KeyError) as error:
        if isinstance(error, MainImageError):
            raise
        raise MainImageError("主图套组未完成。请检查文案边界、素材和本机渲染回执。") from error
    files = {kind: completed / name for kind, name in (
        ("json", "result.json"), ("receipt", "receipt.json"), ("preview", "preview.png"), ("bundle", "main-images.zip")
    )}
    if "cover" in result:
        files["cover"] = completed / COVER_FILE
    return result, files
