"""Package one existing poster version, retaining its artwork and stable identifiers."""
from hashlib import sha256
from io import BytesIO
import json
from struct import unpack
from zipfile import ZIP_DEFLATED, ZipFile

from poster_copy_executor import WORKBENCH_MOCK_KINDS
from workflow_lead_weekly import encode
from workflow_live_rehearsal import amount, check, identify, task_id

TOOL = "visual-poster-version-delivery"
SHOP_RECEIVE = "cn-shop-poster-version-receive"
SHOP_TAKEOUT = "ph-shop-poster-archive-takeout"
TOOLS = (TOOL, SHOP_RECEIVE, SHOP_TAKEOUT)
SITES = {"CN": "P01", "PH": "P02"}
FILES = {"json": "delivery.json", "report": "delivery.md", "receipt": "delivery-receipt.json", "bundle": "poster-source.zip"}
ARTWORK = ("original.png", "layered.psd", "review-numbered.png", "review-numbered.json",
           "layout.json", "manifest.json", "export-integrity.json")
REVISION = ("revision-complete.json", "edit-check.json")


class WorkflowError(ValueError):
    pass


def digest(data):
    return sha256(data).hexdigest()


def dimensions(raw, *, psd=False):
    if psd:
        if raw[:6] != b"8BPS\0\1" or len(raw) < 26:
            raise WorkflowError("PSD 原件头不可识别。")
        height, width = unpack(">II", raw[14:22])
        return width, height
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR" or len(raw) < 24:
        raise WorkflowError("PNG 原件头不可识别。")
    return unpack(">II", raw[16:24])


def prepare(site, p, entry, source, raw):
    piece, version = SITES[site], p["version"]
    layout = json.loads(raw["layout.json"])
    manifest = json.loads(raw["manifest.json"])
    index = json.loads(raw["review-numbered.json"])
    integrity = json.loads(raw["export-integrity.json"])
    if version > 1:
        revision = json.loads(raw["revision-complete.json"])
        delta = json.loads(raw["edit-check.json"])
        if (revision["version"] != f"v{version}" or revision["manifest_sha256"] != digest(raw["manifest.json"])
                or revision["edit_check_sha256"] != digest(raw["edit-check.json"])
                or delta["pass"] is not True or delta["unselectedChanged"] or delta["idsStable"] is not True):
            raise WorkflowError("所选版本的修改/保存回执不匹配。")
    canvas = (layout["width"], layout["height"])
    if ((layout["task_id"], layout["site"], layout["id"], layout["scope"]) !=
            (p["poster_task_id"], site, piece, "local_poster")
            or entry["piece"] != piece or manifest["layoutId"] != piece or index["piece"] != piece
            or canvas != (manifest["width"], manifest["height"])
            or canvas != (index["artwork"]["width"], index["artwork"]["height"])
            or canvas != dimensions(raw["original.png"]) or canvas != dimensions(raw["layered.psd"], psd=True)
            or manifest["original_png_sha256"] != digest(raw["original.png"])
            or manifest["psd"]["sha256"] != digest(raw["layered.psd"])
            or manifest["psd"]["status"] != "EXPORTED_AND_READ_BACK"
            or integrity["pass"] is not True or manifest["exportIntegrity"]["pass"] is not True):
        raise WorkflowError("作品编号、尺寸、PNG、PSD 或完整度回执不匹配。")
    dimensions(raw["review-numbered.png"])
    dimensions(raw["product.png"])
    elements = layout["elements"]
    manifest_layers = manifest["layers"]
    ids = [e["id"] for e in elements]
    if (len(set(ids)) != len(ids) or ids != manifest["elementIds"]
            or ids != [r["internalId"] for r in index["entries"]]
            or ids != [r["internalId"] for r in manifest_layers]):
        raise WorkflowError("布局、编号图及分层记录的元素身份不一致。")
    assets = [e for e in elements if e["type"] == "image"]
    image, = assets
    if (image["role"] != "product" or image["asset_sha256"] != source["sha256"]
            or digest(raw["product.png"]) != source["sha256"]
            or next(e for e in manifest_layers if e["internalId"] == image["id"])["asset"]["sha256"] != source["sha256"]):
        raise WorkflowError("布局与归档的商品素材不一致。")
    copies = [{"id": m["qualifiedReference"], "text": e["text"], "font": e["psdFontName"], "size": e["fontSize"]}
              for e,m in zip(elements, manifest_layers, strict=True) if e["type"] == "text"]
    if ([{k: c[k] for k in ("id", "text")} for c in copies] != entry["selected_elements"]
            or [c["text"] for c in copies] != [r["text"] for r in manifest["nativeText"]["texts"]]
            or [c["font"] for c in copies] != [r["style"]["font"]["name"] for r in manifest["nativeText"]["texts"]]):
        raise WorkflowError("文案与所选版本的原生文字回读不一致。")
    image["path"] = "product.png"
    contents = {n: raw[n] for n in ("original.png", "layered.psd", "review-numbered.png", "review-numbered.json", "product.png")}
    contents.update({"layout.json": encode(layout), "copy.json": encode(copies)})
    manifest_out = {"site": site, "poster_task_id": p["poster_task_id"], "piece": piece, "version": version,
                    "dimensions_px": list(canvas), "source_manifest_sha256": digest(raw["manifest.json"]),
                    "photoshop_reopen_verified_here": False,
                    "native_text_readback_from_original": len(copies),
                    "inventory": {n: {"sha256": digest(b), "bytes": len(b)} for n,b in contents.items()}}
    contents["inventory.json"] = encode(manifest_out)
    return manifest_out, contents


def bind(site, p, load, *, tool=TOOL):
    receiving = tool == SHOP_RECEIVE
    fields = "site job_id poster_task_id piece version"
    check(p, fields + (" product_ref" if receiving else ""), "海报源档交付")
    if site not in SITES or p["site"] != site or p["piece"] != SITES[site] or (receiving and site != "CN"):
        raise WorkflowError("站点与海报作品号不一致。")
    if receiving:
        identify(p["product_ref"], "商品编号")
    identify(p["job_id"], "源档交付编号")
    task_id(p["poster_task_id"], "原海报任务编号")
    amount(p["version"], "精确版本", 1, 10**6, False)
    if receiving:
        entry, source, raw = load(site, p["poster_task_id"], p["version"], reviewed=True)
        if p["product_ref"] != source.get("sku"):
            raise WorkflowError("商城收件的商品编号与上游海报不一致。")
    else:
        entry, source, raw = load(site, p["poster_task_id"], p["version"])
    try:
        result, contents = prepare(site, p, entry, source, raw)
        if receiving:
            plan_release = entry["plan_release"]
            result.update(receiver_role="douyin-shop-ops", product_ref=p["product_ref"],
                          source_plan_sha256=plan_release["initial_plan_sha256"],
                          source_layout_sha256=plan_release["layout_sha256"],
                          source_release_sha256=digest(raw["poster-chain-release.json"]),
                          human_signoff_recorded=False)
            contents["poster-chain-release.json"] = raw["poster-chain-release.json"]
            result["inventory"]["poster-chain-release.json"] = {
                "sha256": result["source_release_sha256"], "bytes": len(raw["poster-chain-release.json"])}
            scope = plan_release.get("copy_plan", {}).get("execution_kind")
            if scope in WORKBENCH_MOCK_KINDS:
                result.update(evidence_scope=scope, upstream_cloud_provider_called=False,
                              upstream_native_skill_execution_verified=False)
            contents["inventory.json"] = encode(result)
    except WorkflowError:
        raise
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        raise WorkflowError("原版源档、图层、商品素材或版本回执无法核对。") from error
    pins = {n: digest(b) for n,b in raw.items()}
    identity = {"poster_task_id": p["poster_task_id"], "version": p["version"], "source_hashes": pins}
    if result.get("evidence_scope") in WORKBENCH_MOCK_KINDS:
        identity["evidence_scope"] = result["evidence_scope"]
    return identity, (result, contents)


def receipt(r, files):
    audit = {"task_id": r["task_id"], "site": r["site"], "tool_id": r["tool_id"], "upstream": r["upstream"],
             "result_sha256": digest(files["json"]), "report_sha256": digest(files["report"]),
             "bundle_sha256": digest(files["bundle"])}
    if r["tool_id"] == SHOP_RECEIVE:
        audit.update({key: r[key] for key in ("receiver_role", "product_ref", "source_plan_sha256",
                                              "source_layout_sha256", "source_release_sha256", "human_signoff_recorded")})
    elif r["tool_id"] == SHOP_TAKEOUT:
        audit.update({key:r[key] for key in ("receiver", "receiver_role", "delivery_task_id", "piece",
                     "version", "evidence_scope", "human_signoff_recorded", "execution_authorized")})
    if r.get("evidence_scope") in WORKBENCH_MOCK_KINDS:
        audit.update(evidence_scope=r["evidence_scope"], upstream_cloud_provider_called=False,
                     upstream_native_skill_execution_verified=False)
    return audit


def run_workflow(project, site, tool, p, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("源档交付仅引用已保存版本。")
    if tool == SHOP_TAKEOUT:
        return run_shop_takeout(p,folder,linked)
    pins, (source, contents) = linked
    paths = {kind: folder / name for kind, name in FILES.items()}
    with ZipFile(paths["bundle"], "x", ZIP_DEFLATED) as z:
        for name, data in contents.items():
            z.writestr(name, data)
    result = {**source, "task_id": folder.name, "tool_id": tool, "job_id": p["job_id"], "upstream": pins,
              "state": "POSTER_SOURCE_BUNDLE_READY", "execution_authorized": False,
              "model_called": False, "provider_called": False}
    result["bundle_sha256"] = digest(paths["bundle"].read_bytes())
    report = (f"# {site} · {source['piece']} v{source['version']}\n\n"
              "本机源档包含固定版本 PNG、PSD、编号图及编号表、文案、布局和该任务素材。\n"
              "按清单核对尺寸与哈希。字体二进制文件未包含，Photoshop 应用内兼容性未在本次打包测试复测。\n")
    if result.get("evidence_scope") in WORKBENCH_MOCK_KINDS:
        report += "\n本机兼容接口模拟。没有访问云服务或完成原生 Skill，未有人签收。\n"
    files = {"json": encode(result), "report": report.encode(), "bundle": paths["bundle"].read_bytes()}
    for kind in ("json","report","receipt"):
        with paths[kind].open("xb") as stream:
            stream.write(encode(receipt(result,files)) if kind == "receipt" else files[kind])
    return result, paths


def verify_saved(task, files):
    r = task["result"]
    if (FILES != task["local_files"] or json.loads(files["json"]) != r
            or (r["task_id"], r["site"], r["tool_id"]) != (task["id"], task["site"], task["source"]["tool_id"])
            or r["upstream"] != task["source"]["role_packet_pins"]
            or (r.get("evidence_scope") in WORKBENCH_MOCK_KINDS or r["upstream"].get("evidence_scope") in WORKBENCH_MOCK_KINDS)
                and r.get("evidence_scope") != r["upstream"].get("evidence_scope")
            or r["bundle_sha256"] != digest(files["bundle"])
            or json.loads(files["receipt"]) != receipt(r, files)):
        raise WorkflowError("交付 ZIP、报告或版本回执无法核验。")


def public_tools():
    example = {site: {"site": site, "job_id": "", "poster_task_id": "", "piece": piece, "version": 1}
               for site,piece in SITES.items()}
    return [{"id": TOOL, "sites": list(SITES), "role_suffix": "visual-designer", "label": "海报固定版本源档交付",
             "description": "引用已保存的精确海报版本打包，带尺寸、相对路径布局和文案表。",
             "input_example": example["CN"], "input_examples": example, "max_media_files": 0},
            {"id": SHOP_RECEIVE, "sites": ["CN"], "role_suffix": "shop-ops",
             "label": "商城运营 · 海报固定版收件",
             "description": "引用抖音中国本机已审校海报，核对商品编号、原任务和固定版本，下载同版源档与接收回执。",
             "input_example": {**example["CN"], "product_ref": ""}, "max_media_files": 0},
            shop_public_tool()]


def bind_shop_takeout(site, p, load):
    check(p, "site job_id receiver delivery_task_id poster_task_id piece version expected_hashes",
          "PH poster archive receipt")
    if site != "PH" or p["site"] != "PH" or p["piece"] != SITES["PH"]:
        raise WorkflowError("The archive receipt only accepts PH P02 for a PH shop test role.")
    identify(p["job_id"], "shop receipt job ID")
    identify(p["receiver"], "PH shop test identity", r"SYN-PH-SHOP-[A-Za-z0-9_-]{1,40}")
    task_id(p["delivery_task_id"], "issued archive task")
    task_id(p["poster_task_id"], "original poster task")
    amount(p["version"], "exact archive version", 1, 10**6, False)
    expected = p["expected_hashes"]
    check(expected, "bundle_sha256 original_png_sha256 layered_psd_sha256", "issued archive checksums")
    for value in expected.values():
        identify(value, "requested archive checksum", r"[a-f0-9]{64}")
    source, files = load(p["delivery_task_id"], site, TOOL)
    archive = files["bundle"]
    inventory = source["inventory"]
    if ((source["poster_task_id"], source["piece"], source["version"]) != (p["poster_task_id"], p["piece"], p["version"])
            or source["state"] != "POSTER_SOURCE_BUNDLE_READY" or source["execution_authorized"] is not False
            or expected != {"bundle_sha256":digest(archive), "original_png_sha256":inventory["original.png"]["sha256"],
                            "layered_psd_sha256":inventory["layered.psd"]["sha256"]}):
        raise WorkflowError("The archive belongs to another task, piece, version, or checksum.")
    with ZipFile(BytesIO(archive)) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or set(names) != {*inventory, "inventory.json"}:
            raise WorkflowError("The archive member list differs from the source inventory.")
        for name, item in inventory.items():
            member = bundle.getinfo(name)
            if member.file_size != item["bytes"] or digest(bundle.read(name)) != item["sha256"]:
                raise WorkflowError("An archive member differs from its issued checksum.")
        shape = [dimensions(bundle.read("original.png")), dimensions(bundle.read("layered.psd"), psd=True)]
        if any(list(value) != source["dimensions_px"] for value in shape):
            raise WorkflowError("The source PNG and PSD dimensions disagree.")
    pins = {"delivery_task_id":p["delivery_task_id"], "poster_task_id":p["poster_task_id"],
            "version":p["version"], "source_delivery_result_sha256":digest(files["json"]),
            "source_delivery_receipt_sha256":digest(files["receipt"]), **expected}
    result = {**{key:source[key] for key in ("site", "piece", "version", "poster_task_id", "dimensions_px",
              "inventory", "native_text_readback_from_original", "source_manifest_sha256")},
              "job_id":p["job_id"], "delivery_task_id":p["delivery_task_id"], "receiver":p["receiver"],
              "receiver_role":"tiktok-shop-ops", "evidence_scope":"offline_archived_delivery_only",
              "state":"EXACT_POSTER_ARCHIVE_RECEIVED", "bundle_sha256":digest(archive),
              "photoshop_reopen_verified_here":False, "human_signoff_recorded":False,
              "execution_authorized":False, "model_called":False, "provider_called":False}
    return pins, (result, archive)


def run_shop_takeout(p, folder, linked):
    pins, (source, archive) = linked
    result = {**source, "task_id":folder.name, "tool_id":SHOP_TAKEOUT, "upstream":pins}
    report = (f"# PH P02 v{p['version']}｜商城收件\n\n"
              f"测试收件岗位 {p['receiver']}，源交付 {p['delivery_task_id']}，原海报 {p['poster_task_id']}。\n"
              f"原 ZIP SHA-256 {pins['bundle_sha256']}；原 PNG 和 PSD 随包逐项核验，720×880。\n"
              "沿用原源档。真人签收、商用授权和本轮 Photoshop 复开待独立核实。\n").encode()
    files = {"json":encode(result), "report":report, "bundle":archive}
    files["receipt"] = encode(receipt(result, files))
    paths = {key:folder/name for key,name in FILES.items()}
    for key, path in paths.items():
        with path.open("xb") as stream:
            stream.write(files[key])
    return result, paths


def shop_public_tool():
    return {"id":SHOP_TAKEOUT, "sites":["PH"], "role_suffix":"shop-ops",
            "label":"菲律宾商城：领取海报源档固定版",
            "description":"商城测试岗领取已签发的 P02 原 ZIP，并核对海报、交付任务、版本及 PNG／PSD 指纹。",
            "input_example":{"site":"PH", "job_id":"SYN-PH-SHOP-ARCHIVE", "receiver":"SYN-PH-SHOP-REVIEW",
                             "delivery_task_id":"0"*32, "poster_task_id":"0"*32, "piece":"P02", "version":2,
                             "expected_hashes":dict.fromkeys(("bundle_sha256","original_png_sha256","layered_psd_sha256"),"0"*64)},
            "max_media_files":0}

