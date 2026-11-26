"""Reconcile frozen English label evidence and manual review annotations in PH."""
from base64 import b64decode
from collections import defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

import workflow_competitor as base
from workflow_competitor_detail import OCR_EXPECTED_SHA256, WorkflowError, _admissible

TOOL = "ph-competitor-reviewed-evidence"
SCOPE = "synthetic_offline"
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 16 * 1024 * 1024
OUTPUTS = {"json":"reviewed-result.json","report":"reviewed-report.md","receipt":"reviewed-receipt.json",
           "evidence_bundle":"reviewed-evidence.zip"}
TERMS = {"NEGATED_ODOR":"Odor explicitly absent", "POS_SOFTNESS":"Softness praised",
         "NEG_SEAM":"Seam problem", "NEGATED_SEAM":"Seam problem absent",
         "NEG_ODOR":"Odor reported", "NEGATED_SOFTNESS":"Softness denied"}
PENDING = ("sarcasm","code_mixed","ambiguous","unreviewed")
LABEL_ID = re.compile("[A-Z][A-Z0-9_]{1,63}", re.ASCII)
SKU_LABEL = re.compile(r"SKU[:：]?([A-Z0-9_-]{1,64})")
WEIGHT_LABEL = re.compile(r"NETWEIGHT[:：]?([0-9]{1,6})G")


def intake(site,payload,decode):
    base._fields(payload,"site synthetic scope category products detail_images targets annotation_source annotations","PH 审阅输入")
    if site!="PH" or payload["scope"]!=SCOPE:
        raise WorkflowError("仅支持本站本机合成证据。")
    original={k:payload[k] for k in ("site","synthetic","category","products")}
    base._validate(site,original)
    products={p["product_id"]:p for p in original["products"]}
    screens=payload["targets"]
    if not isinstance(screens,dict) or screens.keys()!=products.keys():
        raise WorkflowError("须明确每件商品要核对的 SKU 及卖家文字证据。")
    for product_id,target in screens.items():
        base._fields(target,"sku_id seller_text_index","目标 SKU")
        if target["sku_id"] not in {s["sku_id"] for s in products[product_id]["skus"]}:
            raise WorkflowError("目标 SKU 与商品不符。")
        index=target["seller_text_index"]
        if index is not None and (type(index) is not int or not 0<=index<len(products[product_id]["description_blocks"])):
            raise WorkflowError("须引用卖家原文行号，缺该行时保留未知。")
    base._id(payload["annotation_source"],"合成独立标注来源")
    annotations=payload["annotations"]
    if not isinstance(annotations,list) or len(annotations)>300:
        raise WorkflowError("审阅标注须在本机有界范围内。")
    originals={(p["product_id"],r["review_key"]):r for p in products.values() for r in p["reviews"]}
    used=set()
    for a in annotations:
        base._fields(a,"product_id review_key status pending_reason spans","评价独立标注")
        for k in ("product_id","review_key"):base._id(a[k],k)
        key=a["product_id"],a["review_key"]
        if key not in originals or key in used:
            raise WorkflowError("标注必须与唯一原评价匹配。")
        used.add(key)
        spans=a["spans"]
        if (a["status"] not in ("reviewed","pending") or not isinstance(spans,list) or len(spans)>12
                or a["status"]=="pending" and (spans or a["pending_reason"] not in PENDING)
                or a["status"]=="reviewed" and a["pending_reason"] is not None):
            raise WorkflowError("讽刺、混语或不确定内容须保留待审，不能提交词频。")
        for mark in spans:
            base._fields(mark,"term span","词频证据")
            if (mark["term"] not in TERMS or not isinstance(mark["span"],str) or not mark["span"].strip()
                    or mark["span"] not in originals[key]["text"]):
                raise WorkflowError("词频证据须逐字匹配评价原文。")
    rows=payload["detail_images"]
    if not isinstance(rows,list) or len(rows)>MAX_IMAGES:
        raise WorkflowError("最多复用 8 张本机合成英文标签图片。")
    images,clean,seen=[],[],set()
    total=0
    for row in rows:
        base._fields(row,"image_id product_id sku_id image replay source_ref","图片与已保存 OCR")
        for k in ("image_id","product_id","sku_id","source_ref"):
            base._id(row[k],k)
        if (row["image_id"] in seen or row["product_id"] not in products
                or screens[row["product_id"]]["sku_id"]!=row["sku_id"]):
            raise WorkflowError("图片编号重复或申报对象与目标 SKU 不符。")
        seen.add(row["image_id"])
        replay=row["replay"]
        base._fields(replay,"raw_png_sha256 engine_language engine_sha256 raw_lines evidence_ref","已保存 OCR")
        base._id(replay["evidence_ref"],"OCR 原证据编号")
        blob=row["image"]
        base._fields(blob,"data name","图片字节")
        if not isinstance(blob["data"],str) or len(blob["data"])>5_600_000:
            raise WorkflowError("图片大小超限。")
        raw=b64decode(blob["data"],validate=True)
        if (not raw.startswith(b"\x89PNG\r\n\x1a\n") or _sha(raw)!=replay["raw_png_sha256"]
                or replay["engine_language"]!="zh-Hans-CN" or replay["engine_sha256"]!=OCR_EXPECTED_SHA256
                or not isinstance(replay["raw_lines"],list) or len(replay["raw_lines"])>40
                or any(not isinstance(line,str) or not line.isprintable() or len(line)>200 for line in replay["raw_lines"])):
            raise WorkflowError("复用 OCR 的 PNG、语言或证据与提交字节不符。")
        decode(blob)  # Apply the existing image format and size checks before storing the exact archived bytes.
        total+=len(raw)
        if total>MAX_IMAGE_BYTES:
            raise WorkflowError("本机合成图像总大小超限。")
        name=f"source-{len(clean)+1:02}.png"
        images.append(({"name":row["image_id"],"file":"assets/"+name,"sha256":_sha(raw)},raw))
        clean.append({**{k:v for k,v in row.items() if k!="image"},"local_file":name})
    return {**payload,"detail_images":clean},images


def _sha(data):
    return sha256(data).hexdigest()


def _numeric(line):
    normalized=re.sub(r"\s+","",line).upper()
    if found:=WEIGHT_LABEL.fullmatch(normalized):
        return int(found[1])


def compare_images(product,target,images):
    index=target["seller_text_index"]
    seller=product["description_blocks"][index] if index is not None else None
    stated=_numeric(seller) if seller is not None else None
    result=[]
    for image in images:
        rows=image["replay"]["raw_lines"]
        codes={m[1] for row in rows if (m:=SKU_LABEL.fullmatch(re.sub(r"\s+","",row).upper()))}
        weights={n for row in rows if (n:=_numeric(row)) is not None}
        if codes and codes!={target["sku_id"].upper()}:
            state="SKU_MISMATCH"
        elif not codes or len(weights)!=1 or stated is None:
            state="PENDING"
        else:
            state="MATCHED_SELLER_CLAIM" if weights=={stated} else "CONFLICT"
        result.append({**image,"ocr_visible_skus":sorted(codes),"ocr_visible_grams":sorted(weights),
                       "comparison":state,"is_physical_fact":False})
    return {"seller_source_ref":product["source"]["reference"],"seller_line_index":index,"seller_raw_line":seller,
            "seller_claim_grams":stated,"images":result,"missing_image":not bool(images),
            "physical_specification_verified":False}


def reviewed_terms(product,target,annotations):
    sku=target["sku_id"]
    unique={row["review_key"]:row for row in product["reviews"]}
    counts=defaultdict(set)
    rows=[]
    reviewed=pending=excluded=0
    for key,row in unique.items():
        reasons=_admissible(row,sku)
        a=annotations.get((product["product_id"],key))
        if reasons:
            state="excluded"
            excluded+=1
        elif a is None or a["status"]=="pending":
            state="pending_manual_review"
            pending+=1
        else:
            state="reviewed_by_supplied_annotation"
            reviewed+=1
            for mark in a["spans"]:
                counts[mark["term"]].add(key)
        rows.append({**row,"disposition":state,"excluded_reasons":reasons,
                     "pending_reason":(a["pending_reason"] if a else "unreviewed") if state=="pending_manual_review" else None,
                     "accepted_spans":a["spans"] if state=="reviewed_by_supplied_annotation" else None})
    return {"input_rows":len(product["reviews"]),"unique_reviews":len(unique),
            "duplicates_removed":len(product["reviews"])-len(unique),"reviewed":reviewed,"pending":pending,
            "excluded":excluded,"automatic_keyword_frequencies":None,"review_evidence":rows,
            "human_reviewed_frequencies":{term:{"count":len(keys),"review_keys":sorted(keys)}
                                         for term,keys in sorted(counts.items())} if reviewed else None}


def _cloud(path,stats):
    counts=stats["human_reviewed_frequencies"]
    if not counts:
        return {"status":"pending" if counts is None else "no_confirmed_terms"}
    try:
        from PIL import Image,ImageDraw,ImageFont
        from wordcloud import WordCloud
        font=Path(os.environ["WINDIR"])/"Fonts/arial.ttf"
        cloud=WordCloud(font_path=str(font),width=980,height=380,background_color="white",random_state=19,
                        max_words=len(counts),prefer_horizontal=1,color_func=lambda *a,**k:"#295985")
        canvas=Image.new("RGB",(1000,510),"white")
        ImageDraw.Draw(canvas).text((20,20),
            f"Synthetic reviewed terms | {stats['reviewed']} reviewed, {stats['pending']} pending",
            fill="#172942",font=ImageFont.truetype(str(font),22))
        canvas.paste(cloud.generate_from_frequencies({TERMS[t]:r["count"] for t,r in counts.items()}).to_image(),(10,90))
        canvas.save(path,"PNG")
        return {"status":"rendered","file":path.name,"sha256":_sha(path.read_bytes()),
                "scope":"manual annotations, not marketplace prevalence"}
    except (ImportError,OSError,KeyError,ValueError):
        return {"status":"pending","reason":"local_wordcloud_runtime_unavailable"}


def receipt_for(result,raw,report):
    return {"site":"PH","tool_id":TOOL,"task_id":result["task_id"],"result_sha256":_sha(raw),
            "report_sha256":_sha(report),"evidence_bundle_sha256":result["evidence_bundle_sha256"],
            "model_called":False,"provider_called":False,"is_native_english_test":False}


def run_workflow(project,site,tool,payload,folder,assets,progress):
    if site!="PH" or tool!=TOOL or payload["scope"]!=SCOPE:
        raise WorkflowError("仅支持 PH 合成证据。")
    folder=Path(folder)
    referenced={row["image_id"]:row for row in payload["detail_images"]}
    if referenced.keys()!=assets.keys():
        raise WorkflowError("任务图包未完整到达。")
    entries={}
    for ident,p in assets.items():
        row=referenced[ident]
        if _sha(p.read_bytes())!=row["replay"]["raw_png_sha256"] or p.name!=row["local_file"]:
            raise WorkflowError("图片字节或来源已变化。")
        entries["images/"+p.name]=p
    marks={(r["product_id"],r["review_key"]):r for r in payload["annotations"]}
    products=[]
    progress("正在核对已存英文 OCR、图文冲突与独立标注。")
    for i,product in enumerate(payload["products"],1):
        ident=product["product_id"]
        target=payload["targets"][ident]
        comparison=compare_images(product,target,[r for r in referenced.values() if r["product_id"]==ident])
        review=reviewed_terms(product,target,marks)
        path=folder/f"reviewed-{i:02}.png"
        cloud=_cloud(path,review)
        if cloud["status"]=="rendered":
            entries["clouds/"+path.name]=path
        products.append({"product_id":ident,"sku_id":target["sku_id"],"image_comparison":comparison,
                         "reviews":review,"wordcloud":cloud})
    files={k:folder/v for k,v in OUTPUTS.items()}
    with ZipFile(files["evidence_bundle"],"x",ZIP_DEFLATED) as bundle:
        for label,path in sorted(entries.items()):bundle.write(path,label)
    result={"site":site,"task_id":folder.name,"tool_id":tool,"scope":SCOPE,
            "ocr_scope":"archived_synthetic_english_labels_with_zh-Hans-CN_recognizer",
            "ocr_performed_this_task":False,"native_english_ocr_verified":False,"taglish_accuracy_verified":False,
            "source_authenticity_verified":False,"physical_facts_verified":False,"model_called":False,"provider_called":False,
            "annotation_source":payload["annotation_source"],"products":products,
            "evidence_bundle_sha256":_sha(files["evidence_bundle"].read_bytes())}
    raw=(json.dumps(result,ensure_ascii=False,indent=2)+"\n").encode()
    report=("# PH 人工标注与图文证据\n\n仅本机合成证据。词频来自独立标注；讽刺和混语仍待核。\n\n"
            + "\n".join(f"- {p['product_id']} / {p['sku_id']} · 已审 {p['reviews']['reviewed']} · 待核 {p['reviews']['pending']}"
                        for p in products)+"\n").encode()
    for k,value in {"json":raw,"report":report,"receipt":(json.dumps(receipt_for(result,raw,report),indent=2)+"\n").encode()}.items():
        with files[k].open("xb") as f:f.write(value)
    return result,files


def verify_artifacts(task,files):
    result=task["result"]
    if (task["local_files"]!=OUTPUTS or result!=json.loads(files["json"]) or result["task_id"]!=task["id"]
            or result["tool_id"]!=TOOL or result["site"]!="PH"
            or _sha(files["evidence_bundle"])!=result["evidence_bundle_sha256"]
            or json.loads(files["receipt"])!=receipt_for(result,files["json"],files["report"])):
        raise WorkflowError("本机 OCR 或人工标注回执已变化。")


def public_tools():
    return [{"id":TOOL,"sites":["PH"],"role_suffix":"competitor-analyst","label":"菲律宾商详图与人工标注词频",
             "description":"复用合成英文 OCR，精确绑定图片和 SKU。仅使用逐字标注计词频，保留待核语义。",
             "input_example":{},"max_media_files":0}]
