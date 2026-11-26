"""Version-bound CN product main images and long detail in one foreground session."""
from hashlib import sha256
from io import BytesIO
import json
import os
import re
from tempfile import NamedTemporaryFile
from zipfile import ZipFile

from copy_constraints import normalize_copy
import long_detail
import main_images
from poster_workflow import _RESTRICTED
from workflow_poster_delivery import dimensions

MODULES = ("fit", "quality", "spec", "use")
OPTIONS = {"fit": "商品与适配", "quality": "结构与品质", "spec": "尺寸与规格", "use": "使用说明"}
CARD_OPTIONS = {"feature": "商品主图", "advantage": "商品特点", "benefit": "适配说明",
                "proof": "结构说明", "scene": "使用说明"}
ROLE_CARD = {"benefit": "fit", "proof": "quality", "scene": "use"}
ASSETS = "foreground-detail-assets"
SKILL = "ecommerce-detail-page-generator-zh"
SCOPE = "cn_customer_foreground_visual"
HASH = re.compile(r"[0-9a-f]{64}")
ID = re.compile(r"[0-9a-f]{32}")
CALL = re.compile(r"[A-Za-z0-9_-]{1,96}")


class DetailError(ValueError):
    """Foreground product visual evidence, plan, or export did not match."""


def digest(data):
    return sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def require(check, code):
    if not check:
        raise DetailError(code)


def _asset(folder, file, expected):
    require(isinstance(expected, str) and HASH.fullmatch(expected), "detail_asset_binding_invalid")
    target = folder / file
    try:
        require(target.resolve(strict=True).relative_to(folder.resolve(strict=True)).as_posix() == file,
                "detail_asset_path_changed")
        raw = target.read_bytes()
    except (OSError, ValueError) as error:
        raise DetailError("detail_asset_missing") from error
    require(sha256(raw).hexdigest() == expected, "detail_asset_changed")
    return raw


def _cache(folder, signature, raw):
    target = folder / ASSETS / (signature + ".png")
    target.parent.mkdir(exist_ok=True)
    require(target.parent.resolve().parent == folder.resolve(), "detail_asset_path_changed")
    if target.exists():
        _asset(folder, target.relative_to(folder).as_posix(), signature)
        return
    with NamedTemporaryFile(dir=target.parent, prefix=".asset-", delete=False) as temporary:
        temporary.write(raw)
        temp_path = target.parent / os.path.basename(temporary.name)
    try:
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def capture_inputs(payload, task, folder, decode_image):
    """Store optional evidence locally, returning only hashes, text, and missing field names."""
    require(task.get("site") == "CN" and task.get("source", {}).get("kind") == "local_upload",
            "detail_wrong_task")
    require(isinstance(payload, dict) and set(payload) == set(MODULES), "detail_input_shape")
    primary = task["source"]["sha256"]
    _asset(folder, "product.png", primary)
    previous = task.get("foreground_detail", {}).get("fields", {})
    fields, missing, images = {}, [], {}
    for key in MODULES:
        item = payload[key]
        require(isinstance(item, dict) and set(item) == {"fact", "image"}, "detail_input_shape")
        fact, image = item["fact"], item["image"]
        require(isinstance(fact, str) and len(fact) <= 160 and all(ch.isprintable() for ch in fact)
                and not _RESTRICTED.search(fact), "detail_fact_rejected")
        fact = fact.strip()
        if fact:
            _short_fact(fact)
        if image == "original":
            image_kind, signature = "original", primary
        elif image == "keep":
            require(key in previous, "detail_no_prior_evidence")
            old = previous[key]
            image_kind, signature = old["image_kind"], old["asset_sha256"]
            if image_kind == "evidence":
                _asset(folder, f"{ASSETS}/{signature}.png", signature)
            else:
                require((image_kind, signature) in {(None, None), ("original", primary)},
                        "detail_asset_binding_invalid")
        elif image is None:
            image_kind, signature = None, None
        else:
            require(isinstance(image, dict) and set(image) == {"name", "data"}
                    and isinstance(image["name"], str) and 1 <= len(image["name"]) <= 200,
                    "detail_image_input_shape")
            raw = decode_image(image)
            signature, image_kind = sha256(raw).hexdigest(), "evidence"
            images[signature] = raw
        fields[key] = {"fact": fact, "asset_sha256": signature, "image_kind": image_kind}
        if not fact:
            missing.append({"module": key, "field": "fact"})
        if not signature:
            missing.append({"module": key, "field": "image"})
    distinct = {}
    for row in fields.values():
        signature = row["asset_sha256"]
        if not signature or signature in distinct:
            continue
        if row["image_kind"] == "original":
            raw = _asset(folder,"product.png",primary)
        elif signature in images:
            raw = images[signature]
        else:
            raw = _asset(folder,f"{ASSETS}/{signature}.png",signature)
        distinct[signature] = len(raw)
    require(sum(distinct.values())<=16*1024*1024,"商详证据转换为本机 PNG 后合计须不超过 16 MiB。请缩小尺寸再传。")
    for signature, raw in images.items():
        _cache(folder, signature, raw)
    return {"fields": fields, "missing": missing}


def model_evidence(captured):
    """Text-only project evidence approved for the customer's interactive model."""
    fields = captured["fields"]
    return {"facts": {key: _short_fact(fields[key]["fact"]) for key in MODULES if fields[key]["fact"]},
            "module_titles": OPTIONS, "card_titles": CARD_OPTIONS, "missing": captured["missing"]}


def _short_fact(fact):
    # Preserve the supplied meaning, including numbers and negation. The only optional
    # edit is a line break inserted to fit the existing visual copy limit.
    for index in (0, *range(1, len(fact))):
        left, right = fact[:index], fact[index:]
        if len(left) > 1 and left[-1].isascii() and left[-1].isalnum() and right[:1].isascii() and right[:1].isalnum():
            continue
        candidate = fact if index == 0 else left.rstrip() + "\n" + right.lstrip()
        try:
            if normalize_copy(candidate, "subtitle") == candidate:
                return candidate
        except ValueError:
            continue
    raise DetailError("detail_fact_too_long")


def _as_exact_fact(text):
    return {text, text.replace("\n", ""), text.replace("\n", " ")}


def _allowed_copy(selected, style, evidence, labels=()):
    try:
        require(normalize_copy(selected, style) == selected and not _RESTRICTED.search(selected),
                "detail_unbound_copy")
    except ValueError as error:
        raise DetailError("detail_unbound_copy") from error
    require(selected in labels or any(_as_exact_fact(selected) & _as_exact_fact(fact)
                                        for fact in evidence if fact), "detail_unbound_copy")
    return selected


def _binding(binding):
    require(isinstance(binding, dict) and ID.fullmatch(str(binding.get("task_id", "")))
            and ID.fullmatch(str(binding.get("session_id", ""))) and type(binding.get("version")) is int
            and binding["version"] == 1, "detail_wrong_binding")
    return {key: binding[key] for key in ("session_id", "task_id", "version")}


def build_outputs(bench, task, binding, captured, plan, *, call_id, simulated):
    """Validate the model-selected plan, then queue the Workbench's two actual local artwork jobs."""
    pin = _binding(binding)
    require(task["id"] == pin["task_id"] and task["site"] == "CN"
            and task.get("agent_session", {}).get("session_id") == pin["session_id"]
            and task.get("source", {}).get("kind") == "local_upload"
            and task.get("foreground_detail") == captured and type(simulated) is bool
            and isinstance(call_id, str) and CALL.fullmatch(call_id), "detail_wrong_binding")
    require(not captured["missing"] and set(captured["fields"]) == set(MODULES), "detail_evidence_missing")
    require(isinstance(plan, dict) and set(plan) == {"cards", "modules"}
            and isinstance(plan["cards"], dict) and set(plan["cards"]) == set(main_images.SLOTS)
            and isinstance(plan["modules"], dict) and set(plan["modules"]) == set(MODULES), "detail_plan_shape")
    folder = bench.tasks_dir / task["id"]
    source = task["source"]
    primary = _asset(folder, "product.png", source["sha256"])
    sku = source.get("sku")
    require(main_images._id(sku), "detail_wrong_binding")
    product_id = sku
    copy = task.get("confirmed_copy", {})
    require(set(copy) == {"title", "subtitle"} and all(isinstance(val, str) and val for val in copy.values()),
            "detail_selling_points_missing")
    skill_file = bench.project / ".agents" / "skills" / SKILL / "SKILL.md"
    require(skill_file.is_file() and skill_file.stat().st_size <= 128_000, "detail_skill_missing")
    skill_sha = sha256(skill_file.read_bytes()).hexdigest()
    pin.update(product_id=product_id, sku_id=sku, asset_sha256=source["sha256"], tool_call_id=call_id,
               plan_sha256=digest(plan), evidence_sha256=digest(captured), skill=SKILL, skill_sha256=skill_sha,
               model_mode="local_fixture" if simulated else "customer_foreground")
    cards, detail = [], []
    evidence = captured["fields"]
    for slot in main_images.SLOTS:
        row = plan["cards"][slot]
        require(isinstance(row, dict) and set(row) == {"title", "subtitle"}, "detail_plan_shape")
        key = ROLE_CARD.get(slot)
        fact = evidence[key]["fact"] if key else None
        pinned = (fact,) if key else (task["title"], copy["title"], copy["subtitle"], evidence["fit"]["fact"])
        title = _allowed_copy(row["title"], "title", pinned, (CARD_OPTIONS[slot],))
        subtitle = _allowed_copy(row["subtitle"], "subtitle", pinned)
        cards.append({"slot": slot, "title": title, "subtitle": subtitle, "source_ref": "customer-primary",
                      "image": primary})
    for key in long_detail.MODULES:
        if key not in MODULES:
            detail.append({"module": key, "status": "unknown"})
            continue
        proof = evidence[key]
        row = plan["modules"][key]
        require(isinstance(row, dict) and set(row) == {"title", "body"}, "detail_plan_shape")
        fact = proof["fact"]
        require(bool(fact) and isinstance(fact, str), "detail_evidence_missing")
        title = _allowed_copy(row["title"], "title", (fact,), (OPTIONS[key],))
        body = _allowed_copy(row["body"], "subtitle", (fact,))
        signature = proof["asset_sha256"]
        if proof["image_kind"] == "original":
            require(signature == source["sha256"], "detail_asset_changed")
            image, ref = primary, "customer-primary"
        else:
            require(proof["image_kind"] == "evidence", "detail_evidence_missing")
            image, ref = _asset(folder, f"{ASSETS}/{signature}.png", signature), f"customer-{key}"
        detail.append({"module": key, "status": "provided", "title": title, "body": body,
                       "fact_ref": f"customer-fact-{key}", "image_ref": ref, "image": image})
    common = {"site": "CN", "scope": SCOPE, "product_id": product_id,
              "listing": {"sku_id": sku, "version": binding["version"]}}
    work = {}
    for tag, renderer, values in (("main", main_images, {"cards": cards}),
                                   ("detail", long_detail, {"modules": detail})):
        submission = {**common, **values, "request_id": f"fg-{tag}-{pin['session_id']}"}
        supplied, images = renderer.intake(submission, lambda data: data, trusted_foreground=True)
        work[tag] = submission, renderer, supplied, images
    selectors = {}
    for tag, (submission, renderer, supplied, images) in work.items():
        upstream = {"tool_id": renderer.TOOL_ID, "assets": [meta for meta, _ in images], "foreground_visual": pin}
        label = "抖音中国 · 客户会话商品主图" if tag == "main" else "抖音中国 · 客户会话商品详情页"
        child = bench.create_local(submission, "role_tool", label, upstream, role_input=supplied, media_files=images)
        selectors[tag] = child["id"]
    return selectors


def renderer_intake_scope(payload, trusted):
    """Check an internal-only scope before applying the original CN shape validation."""
    if not trusted:
        return payload
    require(payload.get("site") == "CN" and payload.get("scope") == SCOPE and "listing" in payload,
            "detail_wrong_scope")
    return {**payload, "scope": "synthetic_offline"}


def renderer_provenance(task, parameters, rendered, marker_field):
    """Label model-selected content separately from the local explicit-copy render gate."""
    if marker := task.get("source", {}).get("foreground_visual"):
        require(task["site"] == "CN" and marker["product_id"] == parameters["product_id"]
                and parameters["listing"] == {"sku_id": marker["sku_id"], "version": marker["version"]},
                "detail_wrong_binding")
        rendered.update({marker_field: SCOPE, "foreground_visual": marker,
                         "foreground_model_selected_plan": True,
                         "layout_renderer_model_called": False, "image_generation_used": False,
                         "product_image_use": "customer provided; primary image used for identification",
                         "evidence_basis": "customer_supplied_not_independently_verified"})


def verify_outputs(bench, binding, selectors, *, expected_context=None):
    """Reopen completed artwork tasks and their original PNG/PSD packages by exact parent binding."""
    pin = _binding(binding)
    require(isinstance(selectors, dict) and set(selectors) == {"main", "detail"}
            and all(isinstance(value, str) and ID.fullmatch(value) for value in selectors.values())
            and selectors["main"] != selectors["detail"], "detail_wrong_selectors")
    artifacts = {}
    marker = None
    for tag, renderer, bundle_kind in (("main", main_images, "bundle"), ("detail", long_detail, "detail_bundle")):
        child = bench.task(selectors[tag])
        saved = child.get("source", {}).get("foreground_visual")
        require(child["site"] == "CN" and child["status"] == "ready" and child.get("kind") == "role_tool"
                and child["source"]["tool_id"] == renderer.TOOL_ID and isinstance(saved, dict)
                and all(saved.get(k) == value for k, value in pin.items())
                and (marker is None or marker == saved), "detail_wrong_binding")
        marker = saved
        require(expected_context is None or all(marker.get(k) == v for k, v in expected_context.items()),
                "detail_wrong_binding")
        report = child["result"]
        receipt = json.loads(bench.local_artifact_bytes(child["id"], "receipt"))
        require(report["foreground_visual"] == receipt["foreground_visual"] == marker
                and report["product_id"] == marker["product_id"]
                and report["listing"] == {"sku_id": marker["sku_id"], "version": marker["version"]},
                "detail_wrong_binding")
        package = bench.local_artifact_bytes(child["id"], bundle_kind)
        require(sha256(package).hexdigest() == report["bundle_sha256"], "detail_export_changed")
        summaries = []
        with ZipFile(BytesIO(package)) as archive:
            require(json.loads(archive.read("receipt.json")) == receipt, "detail_export_changed")
            pieces = ([(f"{index:02d}-{row['slot']}/", row["file_sha256"], tuple(row["size"]))
                        for index, row in enumerate(report["cards"], 1)] if tag == "main" else
                      [("", report["file_sha256"], tuple(report["size"]))])
            for prefix, files, size in pieces:
                png, psd = (archive.read(prefix + filename) for filename in ("original.png", "layered.psd"))
                manifest = archive.read(prefix + "manifest.json")
                m = json.loads(manifest)
                manifest_sha = (report["cards"][len(summaries)]["engine_manifest_sha256"]
                                if tag == "main" else report["readback_sha256"])
                require(sha256(png).hexdigest() == files[prefix + "original.png"]
                        and sha256(psd).hexdigest() == files[prefix + "layered.psd"]
                        and sha256(manifest).hexdigest() == manifest_sha
                        and dimensions(png) == dimensions(psd, psd=True) == size
                        and m["psd"]["status"] == "EXPORTED_AND_READ_BACK"
                        and m["psd"]["nativeTextDescriptorCount"] >= 2 and m["exportIntegrity"]["pass"] is True,
                        "detail_export_changed")
                summaries.append({"png_sha256": sha256(png).hexdigest(), "psd_sha256": sha256(psd).hexdigest(),
                                  "size": list(size), "native_text_layers": m["psd"]["nativeTextDescriptorCount"]})
        artifacts[tag] = {"task_id": child["id"], "bundle_kind": bundle_kind,
                          "bundle_sha256": report["bundle_sha256"], "pieces": summaries}
    return {"binding": marker, "artifacts": artifacts, "local_render": True, "upload_authorized": False}

