"""Local media library custody and editable short-video cover export."""
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
import re
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image
from poster import CATEGORIES, OUTPUTS as POSTER_FILES, _run_engine
from role_tools import ROUTES, SYNTHETIC

DIRECTOR = "director-media-library"
VISUAL = "visual-media-library"
RECEIVE = "director-media-visual-intake"
COVER = "visual-video-cover"
LIBRARIES = {DIRECTOR:"director", VISUAL:"visual"}
TOOLS = (*LIBRARIES, RECEIVE, COVER)
PURPOSE = {"director":"director_reference", "visual":"video_cover"}
RIGHTS_PURPOSES = (*PURPOSE.values(), "store_campaign_static", "live_graphic")
PROFILE = "DT-VIDEO-COVER-PORTRAIT-1"
SIZE = (1080, 1920)
COMMON = "site scope as_of"
REGISTRY = "asset_id version previous_task_id product_id source_task_id frame_id tags rights facts script template"
USE_FIELDS = "asset_id version asset_task_id product_id purpose"
OUTPUTS = {"json":"media-work/result.json","receipt":"media-work/receipt.json","report":"media-work/report.md"}
EXTRA = {"bundle":"media-work/media-bundle.zip","preview":"media-work/cover/original.png"}
NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,71}"


class WorkflowError(ValueError):
    pass


def _shape(obj, fields):
    if not isinstance(obj,dict) or obj.keys()!=set(fields.split()):
        raise WorkflowError("结构与所选素材库或封面工具不符；请参照本站示例。")


def _id(value, expression=NAME):
    if not isinstance(value,str) or not re.fullmatch(expression,value,re.ASCII):
        raise WorkflowError("来源、素材或版本引用格式无效。")


def _time(value):
    try:
        if not isinstance(value,str) or len(value)>40 or "T" not in value:
            raise ValueError
        v=datetime.fromisoformat(value)
        if v.utcoffset() is None:raise ValueError
        return v
    except (ValueError,OverflowError):
        raise WorkflowError("评估及权利时点须有 ISO 8601 时区。") from None


def _text(value,cap):
    if not isinstance(value,str) or not value.strip() or not value.isprintable() or len(value)>cap:
        raise WorkflowError("文案须为可见、有界的单行文本。")


def _tags(items,empty=False):
    if not isinstance(items,list) or not (int(not empty)<=len(items)<=12):
        raise WorkflowError("请提供不超过 12 个标签。")
    for item in items:_id(item)
    if len(set(items))!=len(items):
        raise WorkflowError("标签不可重复。")


def _rights(v):
    _shape(v,"state reference expires_at purposes")
    if v["state"] not in ("recorded_offline_only","unknown"):
        raise WorkflowError("权限只登记离线使用记录或待核。")
    if v["state"]=="recorded_offline_only" or v["reference"] is not None:
        _id(v["reference"])
    if v["expires_at"] is not None:_time(v["expires_at"])
    if (not isinstance(v["purposes"],list) or len(v["purposes"])>len(RIGHTS_PURPOSES)
            or any(p not in RIGHTS_PURPOSES for p in v["purposes"])
            or len(set(v["purposes"]))!=len(v["purposes"])):
        raise WorkflowError("仅可登记列出的离线素材用途。")


def validate(site,tool,v):
    if tool not in TOOLS or not isinstance(v,dict) or v.get("site")!=site or v.get("scope")!=SYNTHETIC:
        raise WorkflowError("仅接受所选站点的合成离线材料。")
    _time(v.get("as_of"))
    if tool in LIBRARIES:
        mode=v.get("mode")
        if mode=="search":
            _shape(v,COMMON+" mode purpose tags history")
            if v["purpose"]!=PURPOSE[LIBRARIES[tool]] or type(v["history"]) is not bool:
                raise WorkflowError("检索用途与所选素材库不符。")
            _tags(v["tags"],empty=True)
            return
        _shape(v,COMMON+" mode "+REGISTRY)
        if mode!="register":raise WorkflowError("素材库仅支持 register 或 search。")
        for k in ("asset_id","product_id"):_id(v[k])
        _id(v["source_task_id"],r"[0-9a-f]{32}")
        _id(v["frame_id"],r"F[0-9]{3}")
        if (v["version"]==1)!=(v["previous_task_id"] is None):
            raise WorkflowError("新链从 v1 开始，后续须引用上一版。")
        if v["previous_task_id"] is not None:_id(v["previous_task_id"],r"[0-9a-f]{32}")
        _tags(v["tags"])
        _rights(v["rights"])
        _shape(v["facts"],"product_id title source_ref")
        if v["facts"]["product_id"]!=v["product_id"]:
            raise WorkflowError("事实卡与素材对象不一致。")
        _text(v["facts"]["title"],26)
        _id(v["facts"]["source_ref"])
        _shape(v["script"],"source_ref text")
        _id(v["script"]["source_ref"])
        _text(v["script"]["text"],1400)
        _shape(v["template"],"template_id version profile source_ref")
        for k in ("template_id","source_ref"):_id(v["template"][k])
        if (v["template"]["profile"]!=PROFILE or type(v["template"]["version"]) is not int
                or not 1<=v["template"]["version"]<=1_000_000_000):
            raise WorkflowError("所选模板不是本机已验证的封面规格。")
    else:
        _shape(v,COMMON+" "+USE_FIELDS+(" receiver" if tool==RECEIVE else " title"))
        for k in ("asset_id","product_id"):_id(v[k])
        _id(v["asset_task_id"],r"[0-9a-f]{32}")
        if tool==RECEIVE and (v["purpose"]!=PURPOSE["director"] or v["receiver"]!=ROUTES[site][0]+"-visual-designer"):
            raise WorkflowError("编导素材只能由本站视觉岗位按登记用途签收。")
        if tool==COVER:
            if v["purpose"]!=PURPOSE["visual"]:raise WorkflowError("覆盖用途须为视频封面。")
            _text(v["title"],26)
    if type(v["version"]) is not int or not 1<=v["version"]<=1_000_000_000:
        raise WorkflowError("版本须为正整数。")


def available(record, as_of, purpose):
    r=record["rights"]
    if r["state"]!="recorded_offline_only" or r["expires_at"] is None:
        return "rights_pending"
    if _time(record["as_of"])>_time(as_of):
        return "record_not_yet_available"
    if _time(r["expires_at"])<=_time(as_of):
        return "rights_expired"
    return "allowed_offline_reference" if purpose in r["purposes"] else "purpose_not_recorded"


def same(record,v):
    return all(record[k]==v[k] for k in ("site","asset_id","product_id","version"))


def visual_layout(site, v, media, frame, ident):
    title=v["title"]
    bg="#F3EEE9" if site=="CN" else "#EDF4F8"
    ink="#253245" if site=="CN" else "#223C55"
    font, psd=("Microsoft YaHei","MicrosoftYaHei") if site=="CN" else ("Arial","ArialMT")
    elements=[
        dict(type="rect",role="background",x=0,y=0,width=1080,height=1920,fill=bg),
        dict(type="text",label="视频封面标题",x=86,y=228,width=908,height=180,text=title,
             fontSize=80,fontFamily=font,psdFontName=psd,textInsetY=12,padding=10,lineHeight=1.14,fill=ink),
        dict(type="image",role="product",x=80,y=594,width=920,height=920,fit="contain",path=str(frame),
             asset_sha256=media["frame_sha256"],provenance=media["frame_id"]+" of "+media["source_task_id"]),
    ]
    registry={}
    for element,k in zip(elements,("B01","T01","I01"),strict=True):
        ref=f"DT-P01-{k}"
        element["id"]=ref
        registry[ref]={"internal_id":ref,"psd_layer_id":int(sha256(ref.encode()).hexdigest()[:7],16),
                       "short_id":k,"qualified_id":f"P01/{k}","category":CATEGORIES[k[0]],"active":True}
    return {"schema":"dt-layout-v1","site":site,"id":"P01","sku":v["product_id"],"task_id":ident,
            "width":SIZE[0],"height":SIZE[1],"scope":COVER,"elements":elements,
            "user_id_registry":{"schema":"dt-user-element-registry-v1","pieces":{"P01":{"entries":registry}}}}


def _sha(path):
    return sha256(path.read_bytes()).hexdigest()


def _raw(x):
    return (json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode("utf-8")


def _write(path,buf):
    with path.open("xb") as f:f.write(buf)


def _cover(dir,site,v,registration,frame_bytes,engine,node,ident):
    frame=dir/"source-frame.jpg"
    _write(frame,frame_bytes)
    layout=visual_layout(site,v,registration["source"],frame,ident)
    spec=dir/"cover-layout.json"
    _write(spec,_raw(layout))
    attempt=dir/"engine"; attempt.mkdir()
    cover=dir/"cover"
    _run_engine(engine,node,["render",spec,cover],attempt)
    source_manifest=cover/"manifest.json"
    m=json.loads(source_manifest.read_text("utf-8"))
    if (m["psd"]["status"]!="EXPORTED_AND_READ_BACK" or m["nativeText"]["count"]!=1
            or m["exportIntegrity"]["pass"] is not True):
        raise WorkflowError("封面导出、文字层或 PSD 读回未验证。")
    png=cover/POSTER_FILES["png"]
    with Image.open(png) as img:
        if img.size!=SIZE:raise WorkflowError("覆盖尺寸与内部目标不符。")
        img.verify()
    psd=cover/POSTER_FILES["psd"]
    if psd.read_bytes()[:4]!=b"8BPS":
        raise WorkflowError("封面源档缺失。")
    paths={"preview":png,"bundle":dir/"media-bundle.zip"}
    layout["elements"][2]["path"]="source-frame.jpg"
    with ZipFile(paths["bundle"],"x",ZIP_DEFLATED) as archive:
        archive.write(png,"cover.png")
        archive.write(psd,"layered.psd")
        archive.write(source_manifest,"manifest.json")
        archive.write(frame,"source-frame.jpg")
        archive.writestr("layout.json",_raw(layout))
        archive.writestr("registry-record.json",_raw(registration))
    return paths,{"title":v["title"],"source_frame":registration["source"],"size":list(SIZE),
                  "profile":PROFILE,"platform_safe_zone_verified":False,"native_text_layers":1,
                  "psd_readback":m["psd"]["status"],"export_integrity":m["exportIntegrity"],
                  "psd_sha256":_sha(psd),"title_product_binding":"exact_match_to_synthetic_facts"}


def run_workflow(site,tool,v,task_dir,bound,engine,node,progress):
    linked,pins,blobs=bound
    dir=task_dir/"media-work"
    dir.mkdir()
    paths={k:task_dir/name for k,name in OUTPUTS.items()}
    result={"site":site,"scope":SYNTHETIC,"as_of":v["as_of"],"tool_id":tool,"task_id":task_dir.name,
            "upstream":pins,"external_calls":0,"rights_newly_granted":False,"public_release_authorized":False}
    if tool in LIBRARIES:
        result.update(library=LIBRARIES[tool],mode=v["mode"],purpose=v.get("purpose") or PURPOSE[LIBRARIES[tool]])
        if v["mode"]=="register":
            result.update({key:v[key] for key in REGISTRY.split() if key!="source_task_id"})
            result["source"]=linked["source"]
            result["disposition"]=available(result,v["as_of"],result["purpose"])
        else:
            result.update(disposition="local_search_completed",tags=v["tags"],history=v["history"],records=linked["records"])
    else:
        registration=linked["registration"]
        result.update({k:v[k] for k in ("asset_id","version","product_id","purpose")})
        result.update(registration_task_id=registration["task_id"],source=registration["source"],
                      rights=registration["rights"],interface_scope="registered_synthetic_offline_inputs")
        if tool==RECEIVE:
            result.update(receiver=v["receiver"],disposition="received_and_exported_local_pack",
                          template=registration["template"],script_source_ref=registration["script"]["source_ref"])
            paths["bundle"]=dir/"media-bundle.zip"
            with ZipFile(paths["bundle"],"x",ZIP_DEFLATED) as archive:
                archive.writestr("source.mp4",blobs["video"])
                archive.writestr("selected-frame.jpg",blobs["frame"])
                archive.writestr("script.txt",registration["script"]["text"].encode("utf-8"))
                archive.writestr("template.json",_raw(registration["template"]))
                archive.writestr("registry-record.json",_raw(registration))
        else:
            try:
                additional,meta=_cover(dir,site,v,registration,blobs["frame"],engine,node,task_dir.name)
            except (OSError,ValueError,KeyError) as error:
                if isinstance(error,WorkflowError):raise
                raise WorkflowError("封面源档、布局或导出校验未完成。") from error
            paths.update(additional)
            result.update(meta,disposition="cover_and_editable_source_exported")
    if progress:progress("正在保存版本、来源和本机复核凭据。")
    result["artifact_sha256"]={k:_sha(paths[k]) for k in EXTRA if k in paths}
    report=(f"{site} · {tool} · {result.get('asset_id','本机检索')} · {result['disposition']}\n"
            "仅登记合成来源和授权范围。内部目标版式，平台展示或上架未验证。\n"+
            json.dumps(result,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
    raw=_raw(result)
    _write(paths["json"],raw)
    _write(paths["report"],report)
    _write(paths["receipt"],_raw(receipt(result,raw,report)))
    return result,paths


def receipt(result,raw,report):
    return {"task_id":result["task_id"],"site":result["site"],"tool":result["tool_id"],
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),
            "artifacts":result["artifact_sha256"],"rights_newly_granted":False}


def verify(task, files):
    r=task["result"]
    expected={**OUTPUTS,**{k:EXTRA[k] for k in r["artifact_sha256"]}}
    if (task["local_files"]!=expected or json.loads(files["json"])!=r or r["site"]!=task["site"]
            or r["task_id"]!=task["id"] or r["tool_id"]!=task["source"]["tool_id"]
            or receipt(r,files["json"],files["report"])!=json.loads(files["receipt"])
            or any(sha256(files[k]).hexdigest()!=h for k,h in r["artifact_sha256"].items())):
        raise WorkflowError("素材版本、文件与来源回执不一致。")


def public_tools():
    return [{"id":tool,"label":label,"role_suffix":"video-director" if tool==DIRECTOR else "visual-designer",
             "sites":list(ROUTES),"description":description,
             "input_examples":{s:example(s,tool) for s in ROUTES},"max_media_files":0}
            for tool,label,description in (
                (DIRECTOR,"编导素材、脚本与模板库","支持 register 登记与 search 按标签检索，记录明确用途、版本和本机拆解来源。"),
                (VISUAL,"视觉素材与封面模板库","支持 register 登记与 search 按站点、用途和标签检索。"),
                (RECEIVE,"视觉岗位接收编导素材","选择同站最新登记，核对用途后实际取件。"),
                (COVER,"短视频封面与 PSD","从本站视觉库的最新授权版取固定帧并核对标题，输出内部 9:16 封面和源档。"))]


def example(site,tool):
    base={"site":site,"scope":SYNTHETIC,"as_of":"2026-11-20T12:00:00+08:00"}
    if tool in LIBRARIES:
        return {**base,"mode":"register","asset_id":f"{site}-ASSET-01","version":1,"previous_task_id":None,
         "source_task_id":"0"*32,"frame_id":"F001","product_id":f"SYN-{site}-01","tags":["pouch","sample"],
         "rights":{"state":"unknown","reference":None,"expires_at":None,"purposes":[]},
         "facts":{"product_id":f"SYN-{site}-01","title":"紫色拉链包" if site=="CN" else "Purple Zip Pouch","source_ref":"SYN-FACT-01"},
         "script":{"source_ref":"SYN-SCRIPT-01","text":"Same synthetic product, no performance claims."},
         "template":{"template_id":"SYN-9X16","version":1,"profile":PROFILE,"source_ref":"SYN-TEMPLATE-01"}}
    return {**base,"asset_task_id":"0"*32,"asset_id":f"{site}-ASSET-01","version":1,"product_id":f"SYN-{site}-01",
            **({"purpose":"director_reference","receiver":ROUTES[site][0]+"-visual-designer"} if tool==RECEIVE
               else {"purpose":"video_cover","title":"紫色拉链包" if site=="CN" else "Purple Zip Pouch"})}

