"""Offline input and copy boundaries shared by the Philippines visual routes."""
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import re
import unicodedata
from zipfile import ZipFile

NUMBER = re.compile(r"(0|[1-9][0-9]{0,6})(\.[0-9]{1,2})?", re.ASCII)
UNBOUND_CURRENCY = re.compile(r"\b(?:cny|rmb|yuan|renminbi|php|pesos?|usd|idr|thb|vnd|myr)\b", re.I)
ROLE = "tiktok-visual-designer"
REFERENCES = ("runtime-agents/tiktok-visual-designer/agent.md",
              ".agents/skills/tiktok-visual-designer-playbook/SKILL.md")


def _number(value):
    if not isinstance(value, str) or not NUMBER.fullmatch(value):
        raise ValueError("Use an exact decimal string for the test amount or dimension.")
    return Decimal(value)


def _fact(row, required, valid_id):
    if row == {"status": "unknown"}:
        return None
    if (not isinstance(row, dict) or set(row) != {"status", "source_ref"} | required
            or row["status"] != "provided" or not valid_id(row["source_ref"])):
        raise ValueError("A missing fact must be unknown. A supplied fact needs its own source reference.")
    return row


def ph_facts(payload, fields, valid_id):
    if payload.get("scope") != "synthetic_offline":
        raise ValueError("Only local synthetic visual tasks are supported.")
    site = payload.get("site")
    if site == "CN" and set(payload) == fields:
        return None
    if site != "PH" or set(payload) != fields | {"locale", "currency", "price", "dimensions"}:
        raise ValueError("Use the matching CN or PH request contract. PH requires locale, currency, price and dimensions.")
    if payload["locale"] != "en-PH" or payload["currency"] != "PHP":
        raise ValueError("This offline Philippines preset requires en-PH and PHP.")
    price = _fact(payload["price"], {"amount"}, valid_id)
    if price:
        price = {**price, "amount": format(_number(price["amount"]), ".2f")}
    fields = ("width", "depth", "height")
    dimensions = _fact(payload["dimensions"], {*fields, "unit"}, valid_id)
    if dimensions:
        values = {key: _number(dimensions[key]) for key in fields}
        if dimensions["unit"] != "cm" or any(value <= 0 for value in values.values()):
            raise ValueError("Supply positive width, depth and height in cm, or leave dimensions unknown.")
        dimensions = {**dimensions, **{key: format(value.normalize(), "f") for key, value in values.items()}}
    return {"locale": "en-PH", "currency": "PHP", "price": price or {"status": "unknown"},
            "dimensions": dimensions or {"status": "unknown"}}


def listing_binding(payload, fields, valid_id):
    if payload.get("site") not in ("CN", "PH") or "listing" not in payload:
        return None
    row = payload["listing"]
    if (not isinstance(row, dict) or set(row) != {"sku_id", "version"} or not valid_id(row["sku_id"])
            or type(row["version"]) is not int or not 1 <= row["version"] <= 1_000_000_000):
        raise ValueError("Provide a distinct SKU reference and a valid listing version.")
    fields.add("listing")
    return row


def listing_files(report, bundle_bytes, manifest):
    bundle_hash = sha256(bundle_bytes).hexdigest()
    if bundle_hash != report["bundle_sha256"]:
        raise ValueError("Visual archive does not match the completed task.")
    confirmed = []
    with ZipFile(BytesIO(bundle_bytes)) as bundle:
        names = bundle.namelist()
        if len(set(names)) != len(names):
            raise ValueError("Duplicate paths in the visual archive.")
        for label, name, expected in manifest:
            info = bundle.getinfo(name)
            if info.is_dir() or not 0 < info.file_size <= 10_000_000:
                raise ValueError("Visual file missing or oversized.")
            data = bundle.read(info)
            signature = sha256(data).hexdigest()
            if signature != expected or (name.endswith(".png") and not data.startswith(b"\x89PNG\r\n\x1a\n")):
                raise ValueError("Visual file mismatches its recorded type or digest.")
            confirmed.append({"id": label, "file": name, "sha256": signature, "bytes": len(data)})
    return {"task_id": report["task_id"], "bundle_sha256": bundle_hash, "files": confirmed}


def shop_visual_delivery(main, main_bundle, detail, detail_bundle, slots, modules):
    cards, sections = main["cards"], detail["sections"]
    if [card["slot"] for card in cards] != list(slots) or [row["module"] for row in sections] != list(modules):
        raise ValueError("Unexpected main-image or detail-section order.")
    site = main["site"]
    if detail["site"] != site or site not in ("CN", "PH"):
        raise ValueError("Visual archives have different or unsupported sites.")
    images = []
    for index, card in enumerate(cards, 1):
        if site == "PH" and index == 1:
            cover = main["cover"]
            if cover["declared_view"] != "front" or cover["file"] != "cover.png":
                raise ValueError("Dedicated front cover missing.")
            images.append(("P01", "cover.png", cover["sha256"]))
        else:
            part = f"{index:02d}-{card['slot']}/original.png"
            images.append((f"P{index:02d}", part, card["file_sha256"][part]))
    parts = []
    for index, section in enumerate(sections, 1):
        label = f"S{index:02d}"
        part = f"sections/{label}-{section['module']}.png"
        if (section["group"], section["export"]) != (label, part):
            raise ValueError("Unexpected detail export order.")
        parts.append((label, part, detail["file_sha256"][part]))
    if site == "CN":
        return {"status": "received_for_local_review", "listing": main["listing"],
                "main": listing_files(main, main_bundle, images),
                "detail": listing_files(detail, detail_bundle, parts), "upload_authorized": False}
    description = detail["product_description"]
    path = "product-description.en-PH.txt"
    if (description.get("status") != "provided" or description.get("copy_source") != "explicit_input"
            or not description.get("source_ref") or description.get("export") != path
            or description.get("word_count", 0) < 30):
        raise ValueError("A source-linked product description is missing.")
    detail_group = listing_files(detail, detail_bundle, [*parts, ("description", path, detail["file_sha256"][path])])
    missing_views = sorted({"side", "back", "detail"} - {card.get("view") for card in cards[1:]})
    return {"status": "received_for_local_review", "listing": main["market"]["listing"],
            "main": listing_files(main, main_bundle, images), "detail": detail_group,
            "description_source_ref": description["source_ref"], "description_words": description["word_count"],
            "missing_declared_views": missing_views, "actual_angles_verified": False,
            "upload_authorized": False}


def check_ph_copy(text):
    if (UNBOUND_CURRENCY.search(text)
            or any(unicodedata.category(ch) == "Sc" or "CJK" in unicodedata.name(ch, "") for ch in text)):
        raise ValueError("Use explicit Philippines copy without Han characters or inline currency; put price in the PHP fact.")


def copy_for_fact(market, key):
    fact = market[key]
    if key == "price":
        return f"PHP {fact['amount']}" if fact["status"] == "provided" else "Price not provided"
    return (" × ".join(fact[field] for field in ("width", "depth", "height")) + " cm"
            if fact["status"] == "provided" else "Dimensions not provided")

