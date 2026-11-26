"""Synthetic offline store and live placements with operator briefs and receipts."""
import json
from hashlib import sha256
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageChops
from poster import CATEGORIES, OUTPUTS as POSTER_FILES, _run_engine
from role_tools import ROUTES, SYNTHETIC
import workflow_media_library as media

WorkflowError = media.WorkflowError
RENDER = "visual-surface-render"
DESKS = {"shop":"shop-ops", "campaign":"campaign-ops", "live":"live-ops"}
BRIEFS = {f"{desk}-visual-brief":desk for desk in DESKS}
RECEIPTS = {f"{desk}-visual-receive":desk for desk in DESKS}
TOOLS = (*BRIEFS, RENDER, *RECEIPTS)
SLOTS = {"store_home":"shop", "campaign_banner":"campaign",
         "live_overlay":"live", "live_cover":"live"}
SIZE = {"store_home":(1200,480), "campaign_banner":(1080,720),
        "live_overlay":(1080,1920), "live_cover":(1080,1920)}
SLOT_CN = {"store_home":"店铺首页横幅", "campaign_banner":"活动页横幅",
           "live_overlay":"直播贴片", "live_cover":"直播封面"}
PURPOSE = {"shop":"store_campaign_static", "campaign":"store_campaign_static",
           "live":"live_graphic"}
COMMON = "site scope key version product_id"
BRIEF_FIELDS = "slot previous_task_id asset_task_id as_of title line copy_ref language currency session_ref"
RENDER_FIELDS = "brief_task_id previous_art_task_id"
RECEIPT_FIELDS = "art_task_id"
UUID = r"[0-9a-f]{32}"
SAVE = {"json":"surface-work/result.json", "report":"surface-work/report.md",
        "receipt":"surface-work/receipt.json"}
ASSETS = {"preview":"surface-work/render/original.png", "bundle":"surface-work/visual-surface.zip"}
CHANGE = "P01/T02"


def validate(site, tool, v):
    if (not isinstance(v,dict) or v.get("site")!=site or v.get("scope")!=SYNTHETIC
            or site not in ROUTES or tool not in TOOLS):
        raise WorkflowError("仅接受选中站点的离线合成任务。")
    fields=BRIEF_FIELDS if tool in BRIEFS else RENDER_FIELDS if tool==RENDER else RECEIPT_FIELDS
    media._shape(v, COMMON+" "+fields)
    for key in ("key","product_id"):media._id(v[key])
    if type(v["version"]) is not int or not 1<=v["version"]<=1_000_000:
        raise WorkflowError("须提供有效的连续版本。")
    if tool in BRIEFS:
        desk=BRIEFS[tool]
        allowed_slots={"shop":{"store_home"}, "campaign":{"campaign_banner"},
                       "live":{"live_overlay"} if site=="CN" else {"live_cover"}}
        if v["slot"] not in allowed_slots[desk]:
            raise WorkflowError("展示位置与本站运营岗位不匹配。")
        if (v["language"],v["currency"])!= (("zh-CN","CNY") if site=="CN" else ("en-PH","PHP")):
            raise WorkflowError("语言或币种与站点不符。")
        if desk=="live":media._id(v["session_ref"])
        elif v["session_ref"] is not None:raise WorkflowError("店铺或活动图不引用直播场次。")
        media._id(v["asset_task_id"],UUID)
        media._id(v["copy_ref"])
        media._time(v["as_of"])
        media._text(v["title"],26)
        media._text(v["line"],36)
        text=v["title"]+" "+v["line"]
        if (site=="PH" and any("\u3400"<=c<="\u9fff" or c in "¥￥" for c in text)
                or site=="CN" and "₱" in text):
            raise WorkflowError("文案出现与本站不符的语言或货币符号。")
        prior=v["previous_task_id"]
    elif tool==RENDER:
        media._id(v["brief_task_id"],UUID)
        prior=v["previous_art_task_id"]
    else:
        media._id(v["art_task_id"],UUID)
        return
    if (v["version"]==1)!=(prior is None):
        raise WorkflowError("续版须引用上一版的已完成任务。")
    if prior is not None:media._id(prior,UUID)


def localization(v):
    if v["site"]=="CN":return None
    text=v["title"]+" "+v["line"]
    return {
        "language":{"declared":v["language"],"scope":"fixture_text_and_visual_review"},
        "currency_and_units":{"declared":v["currency"],"displayed_currency":"PHP" if "₱" in text else None,
                              "conversion_performed":False,"units_inferred":False},
        "scene_and_models":{"scope":"synthetic product only","actors_required":False},
        "culture":{"scope":"no cultural cue is added by this layout","market_review":"unverified"}}


def approved_fields(v):
    return {key:v[key] for key in (COMMON+" slot as_of title line copy_ref language currency session_ref").split()}


def matching(approved,v):
    return all(approved[k]==v[k] for k in ("site","key","version","product_id"))


def layouts(v, image, ident, asset):
    width,height=SIZE[v["slot"]]
    color="#2E4058" if v["site"]=="CN" else "#233B51"
    paper="#F7F0E8" if v["site"]=="CN" else "#EAF4F7"
    accent="#D77A57" if v["site"]=="CN" else "#197B78"
    font,psd=("Microsoft YaHei","MicrosoftYaHei") if v["site"]=="CN" else ("Arial","ArialMT")
    def rect(role, box, fill, label):
        return dict(type="rect",role=role,x=box[0],y=box[1],width=box[2],height=box[3],fill=fill,label=label)
    def text(box, value, size, label):
        layer=rect(None,box,color,label)
        layer.update(type="text",text=value,fontSize=size,fontFamily=font,psdFontName=psd,
                     textInsetY=10,padding=9,lineHeight=1.15)
        return layer
    if v["slot"]=="store_home":
        zone=(40,40,1120,400)
        bg=(0,0,width,height)
        line=(70,260,510,84); title=(70,155,520,90); product=(625,90,515,300); module=(78,99,72,10)
        title_size,line_size=50,30
    elif v["slot"]=="campaign_banner":
        zone=(48,48,984,624)
        bg=(0,0,width,height)
        line=(68,410,462,83); title=(68,238,470,130); product=(548,188,464,355); module=(78,158,88,12)
        title_size,line_size=48,28
    elif v["slot"]=="live_overlay":
        zone=(80,970,920,510)
        bg=zone
        line=(109,1295,495,100); title=(109,1128,480,143); product=(618,1130,351,254); module=(121,1063,72,12)
        title_size,line_size=47,32
    else:
        zone=(84,210,912,1330)
        bg=(0,0,width,height)
        line=(106,463,850,128); title=(106,284,850,150); product=(108,666,864,748); module=(118,245,98,12)
        title_size,line_size=74,43
    image_layer=rect("product",product,None,"登记素材")
    image_layer.update(type="image",fit="contain",path=str(image),asset_sha256=asset["frame_sha256"],
                       provenance=f"{asset['source_task_id']}/{asset['frame_id']}")
    layers=[rect("background",bg,paper,"底板"),rect("module",module,accent,"位置强调"),
            text(title,v["title"],title_size,"主标题"),text(line,v["line"],line_size,"运营文案"),image_layer]
    entries={}
    for layer, short in zip(layers,("B01","M01","T01","T02","I01"),strict=True):
        ref=f"DT-P01-{short}"
        layer["id"]=ref
        entries[ref]={"internal_id":ref,"psd_layer_id":int(sha256(ref.encode()).hexdigest()[:7],16),
                      "short_id":short,"qualified_id":f"P01/{short}","category":CATEGORIES[short[0]],"active":True}
    for layer in layers[1:]:
        if not (zone[0]<=layer["x"] and zone[1]<=layer["y"]
                and layer["x"]+layer["width"]<=zone[0]+zone[2] and layer["y"]+layer["height"]<=zone[1]+zone[3]):
            raise WorkflowError("元素超出本次内部展示安全区域。")
    return {"schema":"dt-layout-v1","id":"P01","site":v["site"],"task_id":ident,"sku":v["product_id"],
            "scope":RENDER,"width":width,"height":height,"elements":layers,
            "user_id_registry":{"schema":"dt-user-element-registry-v1","pieces":{"P01":{"entries":entries}}}}, zone


def check_manifest(m, layout):
    expected=[layer["text"] for layer in layout["elements"] if layer["type"]=="text"]
    observed=[row["text"] for row in m["nativeText"]["texts"]]
    if (m["exportIntegrity"]["pass"] is not True or sorted(observed)!=sorted(expected)
            or m["psd"]["status"]!="EXPORTED_AND_READ_BACK"
            or m["psd"]["decodedLayerRecompositeComparison"]["exact"] is not True):
        raise WorkflowError("文字完整度、图层遮挡或可编辑 PSD 未通过实际导出校验。")


def assert_revision(old_zip, current, png, layout):
    with ZipFile(BytesIO(old_zip)) as old:
        prior=json.loads(old.read("manifest.json"))
        old_png=old.read("surface.png")
    old_layers={e["qualifiedReference"]:e for e in prior["layers"]}
    new_layers={e["qualifiedReference"]:e for e in current["layers"]}
    if old_layers.keys()!=new_layers.keys():raise WorkflowError("图层编号发生变化。")
    keys=("content_sha256","position_sha256","semantic_sha256","psdLayerId")
    for ref,e in new_layers.items():
        if ref==CHANGE:
            subset=keys[1],keys[3]
            if e["content_sha256"]==old_layers[ref]["content_sha256"]:
                raise WorkflowError("指定文案图层尚未发生变化。")
        elif e["type"]=="image":
            subset=keys[0],keys[1],keys[3],"origin"
            if any(e["asset"][k]!=old_layers[ref]["asset"][k] for k in ("sha256","sourceRect")):
                raise WorkflowError("未选择的登记图像发生变化。")
        else:
            subset=keys
        if any(e[k]!=old_layers[ref][k] for k in subset):
            raise WorkflowError("未选择图层或指定图层位置发生变化。")
    region=next(e for e in layout["elements"] if e["id"]=="DT-P01-T02")
    a=Image.open(BytesIO(old_png)).convert("RGBA")
    with Image.open(png) as b:
        boxes=[channel.getbbox() for channel in ImageChops.difference(a,b.convert("RGBA")).split()]
    boxes=[b for b in boxes if b]
    if not boxes or any(b[0]<region["x"] or b[1]<region["y"] or b[2]>region["x"]+region["width"]
                        or b[3]>region["y"]+region["height"] for b in boxes):
        raise WorkflowError("新版像素差异落在指定文字框外，或渲染未变化。")
    return {"selected":CHANGE,"unselected_layer_count":len(new_layers)-1,"diff_confined_to_selected_box":True,
            "previous_png_sha256":sha256(old_png).hexdigest()}


def export(folder, brief, bound, engine, node):
    work=folder/"surface-work"
    asset=brief["asset"]
    photo=work/"source-frame.jpg"
    media._write(photo,bound["frame"])
    layout,zone=layouts(brief,photo,folder.name,asset["source"])
    spec=work/"layout.json"
    media._write(spec,media._raw(layout))
    log=work/"engine"
    log.mkdir()
    target=work/"render"
    _run_engine(engine,node,["render",spec,target],log)
    manifest=json.loads((target/"manifest.json").read_text("utf-8"))
    check_manifest(manifest,layout)
    png=target/POSTER_FILES["png"]
    psd=target/POSTER_FILES["psd"]
    with Image.open(png) as image:
        if image.size!=SIZE[brief["slot"]]:raise WorkflowError("导出尺寸与确认位置不符。")
        if brief["slot"]=="live_overlay" and image.convert("RGBA").getpixel((0,0))[3]!=0:
            raise WorkflowError("贴片背景未保留透明。")
    revision=assert_revision(bound["previous_bundle"],manifest,png,layout) if brief["version"]>1 else None
    layout["elements"][-1]["path"]="source-frame.jpg"
    bundle=folder/ASSETS["bundle"]
    with ZipFile(bundle,"x",ZIP_DEFLATED) as z:
        for path,name in ((png,"surface.png"),(psd,"layered.psd"),(photo,"source-frame.jpg"),
                          (target/"manifest.json","manifest.json")):
            z.write(path,name)
        z.writestr("layout.json",media._raw(layout))
        z.writestr("operator-brief.json",media._raw(brief))
    return {"canvas":list(SIZE[brief["slot"]]),"internal_safe_zone":list(zone),"revision":revision,
            "psd_sha256":media._sha(psd),"native_text_layers":2,"layer_quality":manifest["exportIntegrity"],
            "psd_readback":manifest["psd"]["status"]}


def run_workflow(site, tool, v, folder, bound, engine, node, progress):
    (folder/"surface-work").mkdir()
    result={key:v[key] for key in COMMON.split()}
    result.update(task_id=folder.name, tool_id=tool, offline_simulation=True,
                  production_permission_established=False, platform_specs_verified=False)
    files=dict(SAVE)
    if tool in BRIEFS:
        result.update(approved_fields(v), asset=bound["asset"], receipt_kind="recorded_operator_brief",
                      previous_task_id=v["previous_task_id"], localization=localization(v))
    elif tool==RENDER:
        if progress:progress("正在从已核素材生成该展示位，并检查文字边界、遮挡和 PSD。")
        brief=bound["brief"]
        result.update(brief_task_id=v["brief_task_id"], previous_art_task_id=v["previous_art_task_id"],
                      slot=brief["slot"], receipt_kind="visual_surface_exported", asset=brief["asset"],
                      localization=brief["localization"], **export(folder,brief,bound,engine,node))
        files.update(ASSETS)
    else:
        art=bound["art"]
        files["bundle"]=ASSETS["bundle"]
        media._write(folder/files["bundle"],bound["bundle"])
        result.update(art_task_id=art["task_id"], slot=art["slot"], receipt_kind="received_by_local_operations",
                      receiver=ROUTES[site][0]+"-"+DESKS[RECEIPTS[tool]], art_sha256=art["artifacts"]["bundle"],
                      briefing=art["brief_task_id"], localization=art["localization"])
    result["artifacts"]={kind:media._sha(folder/name) for kind,name in files.items() if kind in ASSETS}
    raw=media._raw(result)
    report=(f"{site} · {SLOT_CN[result['slot']]} · {v['key']} v{v['version']}\n\n"
            "合成离线规格，仅用于本机流程验收。\n\n"+json.dumps(result,ensure_ascii=False,indent=2)+"\n").encode()
    media._write(folder/files["json"],raw)
    media._write(folder/files["report"],report)
    media._write(folder/files["receipt"],media._raw(receipt(result,raw,report)))
    return result,{key:folder/name for key,name in files.items()}


def receipt(result, raw, report):
    return {"task_id":result["task_id"], "site":result["site"], "tool_id":result["tool_id"],
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),
            "artifact_sha256":result["artifacts"]}


def verify(task, files):
    r=task["result"]
    expected={**SAVE,**{k:ASSETS[k] for k in r["artifacts"]}}
    if (task["local_files"]!=expected or json.loads(files["json"])!=r or r["site"]!=task["site"]
            or r["task_id"]!=task["id"] or r["tool_id"]!=task["source"]["tool_id"]
            or json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])
            or any(sha256(files[k]).hexdigest()!=h for k,h in r["artifacts"].items())):
        raise WorkflowError("位置素材、回执或文件哈希与任务不符。")


def public_tools():
    labels={"shop":"商城","campaign":"活动","live":"直播"}
    rows=[dict(id=RENDER,label="店铺、活动与直播视觉出图",role_suffix="visual-designer",
               description="核对本站运营简报、素材、位置与版本，导出本机可编辑源档。")]
    for template, label in ((BRIEFS,"确认视觉简报"),(RECEIPTS,"接收视觉交付")):
        for tool,desk in template.items():
            rows.append(dict(id=tool,label=labels[desk]+label,role_suffix=DESKS[desk],
                             description="仅适用于本站同 SKU 的本机合成任务。"))
    return [{**r,"sites":list(ROUTES),"max_media_files":0,
             "input_examples":{s:example(s,r["id"]) for s in ROUTES}} for r in rows]


def example(site, tool):
    base={"site":site,"scope":SYNTHETIC,"key":f"VIS-{site}-01","version":1,"product_id":f"SYN-{site}-01"}
    if tool in BRIEFS:
        desk=BRIEFS[tool]
        slot={"shop":"store_home","campaign":"campaign_banner",
              "live":"live_overlay" if site=="CN" else "live_cover"}[desk]
        return {**base,"slot":slot,"previous_task_id":None,"asset_task_id":"0"*32,
                "as_of":"2026-11-20T18:00:00+08:00","title":"紫色收纳包" if site=="CN" else "Purple Zip Pouch",
                "line":"桌面收纳" if site=="CN" else "Desk organizer",
                "copy_ref":"SYN-COPY-01","language":"zh-CN" if site=="CN" else "en-PH",
                "currency":"CNY" if site=="CN" else "PHP","session_ref":"SYN-SESSION-01" if desk=="live" else None}
    if tool==RENDER:return {**base,"brief_task_id":"0"*32,"previous_art_task_id":None}
    return {**base,"art_task_id":"0"*32}
