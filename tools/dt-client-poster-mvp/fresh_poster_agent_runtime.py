"""Customer foreground model history for a newly imported CN poster."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from foreground_token_plan import from_customer
from fresh_poster_contract import DETAIL, HEIGHT, LAYOUT, LAYERS, MAIN_CARDS, SELL, VISUAL_SECTIONS, WIDTH
from interactive_agent_runtime import Denied, HASH, ID, SHOP, WORD, digest, emit, read_line


def object_shape(fields):
    return {"type":"object","properties":fields,"required":list(fields),"additionalProperties":False}


def make_tools(task, combined=False):
    common = {"task_id":{"type":"string","enum":[task]},"version":{"type":"integer","enum":[1]}}
    word = {"type":"string","minLength":1,"maxLength":84}
    token = {"type":"string","minLength":32,"maxLength":32}
    hash_ = {"type":"string","minLength":64,"maxLength":64}
    number = {"type":"integer"}
    rectangle = dict.fromkeys(("x","y","width","height"),number)
    color = {"type":"string","pattern":"^#[A-Fa-f0-9]{6}$"}
    fields = {}
    for name in LAYERS:
        props = dict(rectangle)
        if name != "I01":
            props["fill"] = color
        if name.startswith("T"):
            props["fontSize"] = number
        fields[name] = object_shape(props)
    steps = (
        (SELL,"Invoke the installed local selling-point Skill with two brief exact excerpts from the provided facts.",
         {**common,"title":word,"subtitle":word}),
        (LAYOUT,"Propose five editable layers for the original image and validated copy. The workbench reviews and renders locally.",
         {**common,"selling_point_result_id":token,"objects":object_shape(fields)}),
        (SHOP,"Act as CN mall receiving Agent. On accept, request the exact reviewed and rendered version; otherwise hold.",
         {**common,"layout_result_id":token,"release_sha256":hash_,"decision":{"type":"string","enum":["accept","hold"]}}),
    )
    if combined:
        card = object_shape({"title":word,"subtitle":word})
        section = object_shape({"title":word,"body":word})
        combined_tool = (DETAIL, "Run the installed CN merchandise visual Skill: use the provided evidence to compose "
                         "main image cards and the four sourced long-detail sections, keeping all other facts unknown.",
                         {**common,"layout_result_id":token,"cards":object_shape(dict.fromkeys(MAIN_CARDS,card)),
                          "modules":object_shape(dict.fromkeys(VISUAL_SECTIONS,section))})
        mall = (steps[-1][0], steps[-1][1], {**steps[-1][2],"visual_result_id":token})
        steps = (*steps[:-1], combined_tool, mall)
    return [{"type":"function","function":{"name":name,"description":desc,"parameters":object_shape(args)}}
            for name,desc,args in steps]


def exchange(boot, request, *, simulated=False, send_event=emit, receive_result=read_line,
             send_failure_feedback=True):
    key = {k:boot[k] for k in ("session_id","task_id","version")}
    task = {"task_id":boot["task_id"],"version":1}
    combined = "detail_facts" in boot
    order = (SELL,LAYOUT,DETAIL,SHOP) if combined else (SELL,LAYOUT,SHOP)
    tools = make_tools(boot["task_id"], combined)
    data = {**key,"product":boot["product"],"facts":boot["facts"],"original_image_stays_local":True}
    detail_rule = ""
    if combined:
        data["detail_facts"] = boot["detail_facts"]
        detail_rule = ("After verified poster layout, call the main-image and long-detail tool. Use each exact whole "
                       "fact as the corresponding detail body. Use section titles 商品与适配, 结构与品质, 尺寸与规格, 使用说明. "
                       "Use main-card labels 商品主图, 商品特点, 适配说明, 结构说明, 使用说明. Use the exact fit, quality, "
                       "and use facts as the benefit, proof, and scene subtitles respectively. The first two cards can "
                       "use confirmed product name, approved selling point or fit fact. Preserve all its original words. "
                       "Do not claim extra product views, prices, service or sales. Other four optional sections stay "
                       "unknown. Then pass both poster and visual bundle proofs to the mall tool. ")
    messages = [
        {"role":"system","content":(
            "Work as the customer's foreground CN single poster agent. Use only facts supplied. Pick the selling-point "
            "Skill first. Copy must be short exact excerpts from supplied facts. After its success propose independent "
            f"editable rectangles for a {WIDTH} by {HEIGHT} canvas. B01 covers the canvas; M01 is a decorative card; "
            "I01 is the original image, contained; T01 is headline in the top 200 pixels, T02 subtitle. "
            "Use clear contrast, margins and no overlapping text or image. Font sizes are 18 through 56, "
            "headline larger. Do not invent products, assets, prices, locations or claims. "
            "After the reviewed local render returns its result, act as CN mall receiving Agent. "
            "Choose accept only for the proven task/version and layout proof; otherwise hold. "
            + detail_rule +
            'Return {"status":"completed","result_nonce":"<mall result nonce>"} only after verified receipt.'
        )},
        {"role":"user","content":json.dumps(data,ensure_ascii=False)},
    ]
    send_event("foreground_confirmed", simulated=simulated, model=request.model, tools_sha256=digest(tools))
    results = []
    last = None
    poster = None
    for turn, tool in enumerate(order,1):
        send_event("model_request",turn=turn,tool_results=results)
        finish, message = request(messages,tools)
        calls = message.get("tool_calls")
        if (finish!="tool_calls" or message.get("content") not in ("",None) or not isinstance(calls,list)
                or len(calls)!=1 or not isinstance(calls[0],dict)
                or not set(message)<={"role","tool_calls","content","reasoning_content","reasoning_opaque","refusal"}):
            raise Denied("model_did_not_call_tool")
        call = calls[0]
        call_id, fn = call.get("id"), call.get("function")
        if (not set(call)<={"id","type","index","function"} or call.get("type")!="function"
                or not isinstance(call_id,str) or not WORD.fullmatch(call_id)
                or call_id in {row["call_id"] for row in results} or not isinstance(fn,dict)
                or set(fn)!={"name","arguments"} or fn["name"]!=tool
                or not isinstance(fn["arguments"],str) or len(fn["arguments"])>6000):
            raise Denied("invalid_tool_call")
        args = json.loads(fn["arguments"])
        arg_fields = {SELL:{"title","subtitle"}, LAYOUT:{"selling_point_result_id","objects"},
                      DETAIL:{"layout_result_id","cards","modules"},
                      SHOP:{"layout_result_id","release_sha256","decision"} | ({"visual_result_id"} if combined else set())}
        fields = arg_fields[tool]
        if (not isinstance(args,dict) or set(args)!=set(task)|fields
                or digest({k:args[k] for k in task}) != digest(task)
                or tool==LAYOUT and args["selling_point_result_id"]!=last["nonce"]
                or tool==DETAIL and args["layout_result_id"]!=poster["nonce"]
                or tool==SHOP and (args["layout_result_id"]!=poster["nonce"] or args["release_sha256"]!=poster["release_sha256"]
                                    or args["decision"] not in ("accept","hold")
                                    or combined and args["visual_result_id"]!=last["nonce"])):
            raise Denied("tool_binding_or_copy_mismatch")
        send_event("model_tool_call",turn=turn,name=tool,call_id=call_id,arguments=args)
        frame = receive_result()
        last = frame.get("result")
        if (set(frame)!={"event","session_id","call_id","result"} or frame["event"]!="tool_result"
                or frame["session_id"]!=boot["session_id"] or frame["call_id"]!=call_id or not isinstance(last,dict)
                or last.get("status") not in ("ok","failed")
                or digest({k:last[k] for k in task})!=digest(task)):
            raise Denied("tool_result_binding")
        if last["status"]=="ok" and (not ID.fullmatch(last.get("nonce",""))
                or tool==LAYOUT and not HASH.fullmatch(last.get("release_sha256",""))
                or tool==SHOP and not ID.fullmatch(last.get("receipt_task_id",""))):
            raise Denied("tool_result_binding")
        if tool == LAYOUT:
            poster = last
        results.append({"call_id":call_id,"sha256":digest(last)})
        messages += [message,{"role":"tool","tool_call_id":call_id,"content":json.dumps(last,separators=(",",":"))}]
        if last["status"]=="failed":
            if send_failure_feedback:
                send_event("model_request",turn=turn+1,tool_results=results)
                request(messages,tools)
            raise Denied("local_tool_rejected")
    final_turn = len(order) + 1
    send_event("model_request",turn=final_turn,tool_results=results)
    finish, message = request(messages,tools)
    if finish!="stop" or message.get("tool_calls") or json.loads(message.get("content") or "null")!={
            "status":"completed","result_nonce":last["nonce"]}:
        raise Denied("model_did_not_acknowledge_tools")
    send_event("completed",turns=final_turn,result_refs=results,acknowledgement_sha256=digest(last["nonce"]))


def validate_boot(boot):
    combined = "detail_facts" in boot
    if (set(boot)!={"session_id","task_id","version","product","facts"} | ({"detail_facts"} if combined else set())
            or any(not isinstance(boot[k],str) or not ID.fullmatch(boot[k]) for k in ("session_id","task_id"))
            or type(boot["version"]) is not int or boot["version"]!=1 or not isinstance(boot["product"],str)
            or not 0<len(boot["product"])<=46 or not isinstance(boot["facts"],list) or not 0<len(boot["facts"])<=2
            or any(not isinstance(f,str) or not 0<len(f)<=160 for f in boot["facts"])):
        raise Denied("invalid_session_binding")
    if combined and (not isinstance(boot["detail_facts"],dict) or set(boot["detail_facts"]) != set(VISUAL_SECTIONS)
                     or any(not isinstance(f,str) or not 0<len(f)<=160 for f in boot["detail_facts"].values())):
        raise Denied("invalid_session_binding")


def main():
    boot = read_line()
    preview = boot.pop("local_demo_model", None)
    local_only = sys.argv[1:] == ["--local-preview-only"]
    if local_only:
        from prototype_models import AUTHORIZED_MOCKS
        if not isinstance(preview, str) or not any(preview in choices for choices in AUTHORIZED_MOCKS.values()):
            raise Denied("invalid_session_binding")
    elif preview is not None or len(sys.argv) > 1:
        raise Denied("invalid_session_binding")
    validate_boot(boot)
    if not local_only:
        from_customer(boot, lambda request: exchange(boot,request), fresh=True)
    else:
        from demo_customer_model import DemoCustomerModel
        exchange(boot, DemoCustomerModel(boot, preview), simulated=True)


if __name__=="__main__":
    try:
        main()
    except Denied as error:
        emit("failed",code=str(error))
        sys.exit(1)
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        emit("failed",code="foreground_runtime_error")
        sys.exit(1)
