"""Offline test using the real D&T foreground history driver with synthetic tool replies."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root.parents[1] / "tools" / "dt-client-poster-mvp"))

from demo_customer_model import DemoCustomerModel
import fresh_poster_agent_runtime as runtime
from fresh_poster_contract import DETAIL, LAYOUT, SELL, readable
from interactive_agent_runtime import Denied, SHOP

base = {"session_id": "1"*32, "task_id": "2"*32, "version": 1,
        "product": "演示收纳托盘", "facts": ["演示收纳托盘", "两个独立分格"]}
sections = {"fit":"用于平整桌面","quality":"竹制托盘","spec":"26 厘米宽","use":"分开放置文具"}
checks = []


def drive(combined, failure=None, model_id="demo-selected-in-settings"):
    boot = {**base, "detail_facts": sections} if combined else base
    request = DemoCustomerModel(boot, model_id)
    order = [SELL, LAYOUT, DETAIL, SHOP] if combined else [SELL, LAYOUT, SHOP]
    history, tool_calls, restored_results = [], [], []

    def emit(event, **fields):
        history.append((event, fields))
        if event == "model_tool_call":
            assert order[len(tool_calls)] == fields["name"]
            tool_calls.append(fields)
        if event == "model_request":
            assert len(fields["tool_results"]) == fields["turn"] - 1

    def read_line():
        action = tool_calls[-1]
        stage, step, args = action["name"], action["turn"], action["arguments"]
        result = {"task_id":base["task_id"], "version":1, "status":"ok", "nonce":f"{step:032x}"}
        if stage == SELL:
            assert args["title"] == base["facts"][0] and args["subtitle"] == base["facts"][1]
            result.update(copy={"title": args["title"], "subtitle": args["subtitle"]}, skill="demo-proof-skill",
                          skill_sha256="3"*64, copy_receipt_sha256="4"*64)
            if failure == "wrong_version":
                result["version"] = 2
        if stage == LAYOUT:
            assert args["selling_point_result_id"] == restored_results[0]["nonce"]
            readable(args["objects"])
            result.update(layout_skill_receipt_sha256="5"*64, release_sha256="6"*64, local_approval=True,
                          dimensions_px=[720, 880], editable_native_text_layers=2)
            if failure == "local_failed":
                result = {"status": "failed", "code": "demo-test", "task_id": base["task_id"], "version": 1}
        if stage == DETAIL:
            assert args["layout_result_id"] == restored_results[1]["nonce"]
            assert {k: v["body"] for k,v in args["modules"].items()} == sections
            assert args["cards"]["proof"]["subtitle"] == sections["quality"]
            result.update(same_sku_v1=True, visual_receipt_sha256="7"*64)
        if stage == SHOP:
            assert args["layout_result_id"] == restored_results[1]["nonce"]
            assert args["release_sha256"] == restored_results[1]["release_sha256"]
            if combined:
                assert args["visual_result_id"] == restored_results[2]["nonce"]
                result.update(same_sku_v1=True, visual_receipt_sha256="8"*64)
            else:
                assert "visual_result_id" not in args
            result.update(receipt_task_id="a"*32, receipt_sha256="9"*64, release_sha256="6"*64)
            if failure == "missing_receipt":
                result.pop("receipt_sha256")
        restored_results.append(result)
        return {"event":"tool_result","session_id":base["session_id"],"call_id":action["call_id"],"result":result}

    with patch.object(runtime, "emit", emit), patch.object(runtime, "read_line", read_line):
        try:
            runtime.exchange(boot, request, simulated=True)
        except Denied as e:
            assert failure, str(e)
            assert not any(event == "completed" for event,_ in history)
            checks.append({"profile":"combined" if combined else "poster_only", "case":failure, "blocked_as":str(e)})
        else:
            assert failure is None and history[-1][0] == "completed"
            assert history[0][1]["simulated"] is True and history[0][1]["model"] == model_id
            assert len({item["call_id"] for item in tool_calls}) == len(order)
            assert [row["call_id"] for row in history[-1][1]["result_refs"]] == [item["call_id"] for item in tool_calls]
            checks.append({"profile":"combined" if combined else "poster_only","status":"completed",
                           "tools":order, "turns":history[-1][1]["turns"], "any_selected_model_label_preserved":True})


drive(False, model_id="qwen3.7-plus")
drive(True, model_id="settings-approved-test-model")
drive(False, "wrong_version")
drive(True, "local_failed")
drive(True, "missing_receipt")

model = DemoCustomerModel(base, "settings-approved-test-model")
tools = runtime.make_tools(base["task_id"])
messages = [{"role":"system","content":"Demo process no network"},
            {"role":"user","content":json.dumps({**base,"original_image_stays_local":True})}]
finish, call = model(messages, tools)
assert finish == "tool_calls"
messages.extend([call, {"role":"tool","tool_call_id":"wrong-call-id",
                       "content":json.dumps({"task_id":base["task_id"],"version":1,"status":"ok"})}])
finish, denied = model(messages, tools)
assert finish == "stop" and json.loads(denied["content"])["status"] == "blocked"
checks.append({"profile":"poster_only","case":"different_tool_call_id","status":"blocked"})
out = {"scope":"offline protocol substitute only", "network_calls":0,"real_customer_acceptance":False,
       "checks":checks}
print(json.dumps(out,ensure_ascii=False))

