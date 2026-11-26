"""Bind a customer's frozen product poster plan to the existing local renderer."""
from hashlib import sha256
import json
from pathlib import Path
import re

from copy_constraints import CopyConstraintError, normalize_copy
from poster import PIECES, OUTPUTS, PosterError, _image_digest, _register_layout

WIDTH, HEIGHT = 720, 880
OBJECTS = ("B01", "M01", "I01", "T02", "T01")
RECT_KEYS = {"x", "y", "width", "height"}
COLORS = re.compile(r"#[0-9a-fA-F]{6}")
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MODE = "reviewed_local_plan"
AWAITING_PLAN = "awaiting_layout_plan"
AWAITING_REVIEW = "awaiting_layout_review"


def signature(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def binding(task):
    return {"task_id": task["id"], "site": task["site"], "product_ref": task["source"]["sku"],
            "asset_sha256": task["source"]["sha256"], "copy_sha256": signature(task["confirmed_copy"]), "version": 1}


def exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise PosterError(f"{label}字段不完整或含当前流程不支持的字段。")


def check_binding(value, expected):
    exact(value, set(expected), "任务绑定")
    if value != expected or type(value["version"]) is not int:
        raise PosterError("任务、站点、商品、版本、原图或文案指纹不一致。")


def color(value):
    if not isinstance(value, str) or not COLORS.fullmatch(value):
        raise PosterError("颜色使用 #RRGGBB。")
    return value.upper()


def position(box):
    x, y, w, h = [box[k] for k in ("x", "y", "width", "height")]
    if any(type(n) is not int for n in (x, y, w, h)) or x < 0 or y < 0 or w < 12 or h < 12 or x+w > WIDTH or y+h > HEIGHT:
        raise PosterError("计划中的图层越出 720×880 画布。")
    return {k: box[k] for k in ("x", "y", "width", "height")}


def build(task, task_dir, plan):
    exact(plan, {"binding", "selected_selling_point", "objects"}, "排版计划")
    expected = binding(task)
    check_binding(plan["binding"], expected)
    if _image_digest(task_dir) != expected["asset_sha256"]:
        raise PosterError("任务内已归档商品图发生变化。")
    if (plan["selected_selling_point"] != task["confirmed_copy"]["title"]
            or plan["selected_selling_point"] not in task["facts"]):
        raise PosterError("首屏主卖点须为已确认标题，并能在该任务的已填事实中找到。")
    objects = plan["objects"]
    exact(objects, set(OBJECTS), "排版对象")
    parts = []
    for short in OBJECTS:
        cell = objects[short]
        extras = {"fill"} if short in {"B01", "M01"} else ({"fontSize", "fill"} if short.startswith("T") else set())
        exact(cell, RECT_KEYS | extras, short)
        spec = {"short": short, **position(cell)}
        if short == "B01":
            if tuple(spec[k] for k in ("x", "y", "width", "height")) != (0, 0, WIDTH, HEIGHT):
                raise PosterError("背景编号 B01 必须覆盖画布。")
            spec.update(type="rect", role="background", fill=color(cell["fill"]))
        elif short == "M01":
            spec.update(type="rect", role="card", fill=color(cell["fill"]), radius=16)
        elif short == "I01":
            spec.update(type="image", role="product", fit="contain", path=str(task_dir/"product.png"),
                        asset_sha256=expected["asset_sha256"], provenance="Saved customer product image; no generated substitute")
        else:
            if type(cell["fontSize"]) is not int or not 18 <= cell["fontSize"] <= 56:
                raise PosterError("计划文字的字号须为 18–56。")
            main = short == "T01"
            if main and cell["y"] > 200:
                raise PosterError("首屏主卖点须位于画布顶部 200 像素内。")
            spec.update(type="text", label="首屏主卖点" if main else "副标题",
                        text=task["confirmed_copy"]["title" if main else "subtitle"],
                        fontSize=cell["fontSize"], fill=color(cell["fill"]))
        parts.append(spec)
    if objects["T01"]["fontSize"] <= objects["T02"]["fontSize"]:
        raise PosterError("首屏主卖点字号应大于副标题。")
    layout = _register_layout({"site":task["site"],"task_id":task["id"],"sku":expected["product_ref"]}, parts)
    workflow = task["run"]["workflow"]
    if "copy_receipt_sha256" in workflow:
        layout["copy_plan"] = {key: workflow[key] for key in ("copy_route", "execution_kind", "skill_sha256",
                                                              "copy_receipt_sha256", "native_skill_execution_verified")}
    return layout


def read_plan(task, folder):
    plan_file = folder / "poster-layout-plan.json"
    if not plan_file.is_file() or plan_file.resolve() != plan_file:
        raise PosterError("排版计划缺失。")
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    if signature(plan) != task.get("plan_gate", {}).get("plan_sha256"):
        raise PosterError("留档排版计划与任务指纹不匹配。")
    return plan


def approve_input(task, payload):
    exact(payload, {"binding", "plan_sha256", "copy_checked", "asset_checked", "plan_checked"}, "审校回执")
    check_binding(payload["binding"], binding(task))
    if not payload["plan_sha256"] or payload["plan_sha256"] != task.get("plan_gate", {}).get("plan_sha256"):
        raise PosterError("审校未绑定当前排版计划。")
    if any(payload[k] is not True for k in ("copy_checked", "asset_checked", "plan_checked")):
        raise PosterError("须完成文案、商品图来源和版式三项核对。")
    return {**payload, "scope":"local_operator_assent", "independent_review_verified":False,
            "physical_product_verified":False}


def read_artwork(task, folder, version):
    target = folder / "artwork" / f"v{version}"
    if not (target/"layout.json").is_file() or target.resolve()!=target:
        raise PosterError("当前底版不存在。")
    layout = json.loads((target/"layout.json").read_text(encoding="utf-8"))
    if (layout["task_id"], layout["site"], layout["sku"]) != (task["id"], task["site"], task["source"]["sku"]):
        raise PosterError("底版不是该任务商品。")
    return layout


def edit_input(task, folder, payload):
    exact(payload, {"task_id","site","product_ref","base_version","layout_sha256","element_ref","value","reviewed","request_id"},"单层修改审校")
    if (payload["task_id"], payload["site"], payload["product_ref"]) != (task["id"], task["site"], task["source"]["sku"]):
        raise PosterError("修改任务、站点或商品不匹配。")
    entry = task["versions"][-1]
    if type(payload["base_version"]) is not int or payload["base_version"] != entry["version"] or payload["layout_sha256"] != entry["plan_release"]["layout_sha256"]:
        raise PosterError("修改底版已过期或版式指纹不符。")
    if payload["reviewed"] is not True:
        raise PosterError("请先核对本次指定元素的修改。")
    layout = read_artwork(task,folder,entry["version"])
    if signature(layout) != entry["plan_release"]["layout_sha256"]:
        raise PosterError("已保存的版式与该版的审校回执不符。")
    key = payload["element_ref"]
    rules = {f"{layout['id']}/{n}": n for n in OBJECTS}
    if not isinstance(key,str) or key not in rules:
        raise PosterError("修改引用须为当前作品编号。")
    short = rules[key]
    cell = next(e for e in layout["elements"] if e["id"] == f"DT-{layout['id']}-{short}")
    value=payload["value"]
    if short.startswith("T"):
        field = "text"
        try:
            if normalize_copy(value, "title" if short == "T01" else "subtitle") != value:
                raise PosterError("改稿不能含首尾空格。")
        except CopyConstraintError as error:
            raise PosterError(str(error)) from None
        if short=="T01" and value not in task["facts"]:
            raise PosterError("改后的首屏主卖点须仍来自该任务事实。")
    elif short=="I01":
        field="x"
        position({**cell, "x":value})
    else:
        field="fill"
        value=color(value)
    if value==cell[field]:
        raise PosterError("所选图层未变化。")
    return key,{field:value}


def finalize(task, folder, result, approval, *, edit=None):
    number=result["version"]
    d=folder/"artwork"/f"v{number}"
    layout=read_artwork(task,folder,number)
    ref=approval["binding"]
    if ref!=binding(task) or _image_digest(folder)!=ref["asset_sha256"]:
        raise PosterError("出件时原图或确认文案已失去任务绑定。")
    if "copy_plan" in layout and edit is None:
        lines = {e["id"].rsplit("-", 1)[-1]: e["text"] for e in layout["elements"] if e["type"] == "text"}
        if lines != {"T01": task["confirmed_copy"]["title"], "T02": task["confirmed_copy"]["subtitle"]}:
            raise PosterError("实际排版文字与已核回执不一致。")
    if edit:
        check=json.loads((d/"edit-check.json").read_text(encoding="utf-8"))
        if check["pass"] is not True or check["unselectedChanged"] or check["requestedReference"]!=edit["element_ref"]:
            raise PosterError("单层修改的引擎校验未通过。")
        before=read_artwork(task,folder,number-1)
        edited_id = "DT-"+edit["element_ref"].replace("/","-")
        old=next(x for x in before["elements"] if x["id"]==edited_id)
        new=next(x for x in layout["elements"] if x["id"]==edited_id)
        if old==new:
            raise PosterError("渲染后的所选图层没有变化。")
    files={kind:sha256((d/name).read_bytes()).hexdigest() for kind,name in OUTPUTS.items()}
    release={"binding":ref,"effective_version":number,"initial_plan_sha256":approval["plan_sha256"],
             "layout_sha256":signature(layout),"review_sha256":signature(edit or approval),
             "operator_review_scope":"single_layer_review" if edit else approval["scope"],
             "selected_selling_point":next(e["text"] for e in layout["elements"] if e["id"]==f"DT-{layout['id']}-T01"),
             "independent_review_verified":False,"physical_product_verified":False,"files":files}
    if "copy_plan" in layout:
        release["copy_plan"] = layout["copy_plan"]
    with (d/"poster-chain-release.json").open("x",encoding="utf-8") as f:
        json.dump({"release":release,"initial_review":approval,"edit_review":edit},f,ensure_ascii=False,indent=2)
    edit_data=[]
    for element in layout["elements"]:
        short=element["id"].rsplit("-",1)[-1]
        field="text" if short.startswith("T") else ("x" if short=="I01" else "fill")
        edit_data.append({"id":f"{layout['id']}/{short}","field":field,"value":element[field]})
    result.update(plan_release=release,editable_objects=edit_data)
    return result


def verify_download(task, task_dir, entry, kind, data):
    version=entry["version"]
    f=task_dir/"artwork"/f"v{version}"/"poster-chain-release.json"
    if f.resolve()!=f:
        raise PosterError("审校回执路径无效。")
    proof=json.loads(f.read_text(encoding="utf-8"))
    release=entry["plan_release"]
    if (proof["release"]!=release or release["binding"]!=binding(task)
            or release["effective_version"]!=version
            or release["review_sha256"]!=signature(proof["edit_review"] or proof["initial_review"])
            or release["layout_sha256"]!=signature(read_artwork(task,task_dir,version))
            or release["files"][kind]!=sha256(data).hexdigest()):
        raise PosterError("该版的审校、排版或取件文件无法核对。")


