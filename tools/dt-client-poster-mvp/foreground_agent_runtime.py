"""One customer-owned model history, receiving the finite local D&T tool set."""
import json
from pathlib import Path
import sys

# The standalone process runs with -I and only imports this installed application copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from foreground_token_plan import from_customer
from interactive_agent_runtime import Denied, HASH, ID, MALL_ROLE, SELL, SHOP, WORD, definitions, digest, emit, read_line


def exchange(boot, request, *, simulated=False):
    binding = {k: boot[k] for k in ("session_id", "task_id", "version")}
    reviewed = boot["approved_copy"]
    release = boot["release_sha256"]
    tools = definitions(binding)
    shop = tools[1]["function"]["parameters"]
    shop["properties"].update(release_sha256={"type":"string","minLength":64,"maxLength":64},
                              decision={"type":"string","enum":["accept","hold"]})
    shop["required"] = list(shop["properties"])
    tools[0]["function"]["description"] = "Run D&T's local selling-point Skill against the approved text of the signed poster."
    tools[1]["function"]["description"] = "Decide as the CN mall agent whether to receive this signed poster. Execute local receipt only on accept."
    messages = [
        {"role": "system", "content": (
            "You are the customer's interactive poster assistant. Use the allowed tools as needed, in order. "
            "Invoke the local selling-point Skill with exact approved title and subtitle for this already reviewed poster. "
            "Only after it returns a proof, take the mall operator role and decide whether verified evidence supports local receipt. "
            "Use the mall tool with the returned proof and release hash. If evidence is missing, hold. Do not invent any result. "
            'After receipt succeeds, respond only with JSON {"status":"completed","result_nonce":<receipt result nonce>}.'
        )},
        {"role": "user", "content": json.dumps({**binding, "approved_copy": reviewed, "piece": "P01"}, ensure_ascii=False)},
    ]
    emit("foreground_confirmed", simulated=simulated, model=request.model, tools_sha256=digest(tools))
    results = []
    nonce = None
    for turn, name in enumerate((SELL, SHOP), 1):
        emit("model_request", turn=turn, tool_results=results)
        finish, message = request(messages, tools)
        calls = message.get("tool_calls")
        if (finish != "tool_calls" or message.get("content") not in ("", None)
                or not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict)
                or not set(message) <= {"role","tool_calls","content","reasoning_content","reasoning_opaque","refusal"}):
            raise Denied("model_did_not_call_tool")
        call = calls[0]
        fn = call.get("function")
        call_id = call.get("id")
        if (not set(call) <= {"id","type","function","index"} or call.get("type") != "function"
                or not isinstance(fn, dict) or set(fn) != {"name","arguments"} or fn["name"] != name
                or not isinstance(call_id, str) or not WORD.fullmatch(call_id)
                or any(row["call_id"] == call_id for row in results)
                or not isinstance(fn["arguments"], str) or len(fn["arguments"]) > 1600):
            raise Denied("invalid_tool_call")
        args = json.loads(fn["arguments"])
        common = {"task_id": binding["task_id"], "version": binding["version"]}
        if name == SELL:
            valid = digest(args) == digest({**common, **reviewed})
        else:
            valid = isinstance(args, dict) and args.get("decision") in ("accept", "hold") and digest(args) == digest({
                **common, "selling_point_result_id": nonce, "decision": args["decision"], "release_sha256": release})
        if not valid:
            raise Denied("tool_binding_or_copy_mismatch")
        emit("model_tool_call", turn=turn, name=name, call_id=call_id, arguments=args)
        frame = read_line()
        result = frame.get("result")
        if (set(frame) != {"event","session_id","call_id","result"} or frame["event"] != "tool_result"
                or frame["session_id"] != binding["session_id"] or frame["call_id"] != call_id
                or not isinstance(result, dict) or result.get("status") not in ("ok","failed")):
            raise Denied("tool_result_binding")
        failed = result["status"] == "failed"
        nonce = result.get("nonce")
        if not failed and (not isinstance(nonce, str) or not ID.fullmatch(nonce)
                           or result.get("release_sha256") != release):
            raise Denied("tool_result_binding")
        if not failed and name == SHOP and (not result.get("receipt_task_id") or not result.get("agent_subsession_id")):
            raise Denied("mall_receipt_unavailable")
        results.append({"call_id":call_id,"sha256":digest(result)})
        messages += [message, {"role":"tool","tool_call_id":call_id,"content":json.dumps(result,separators=(",",":"))}]
        if failed:
            emit("model_request", turn=turn+1, tool_results=results)
            request(messages, tools)
            raise Denied("local_tool_rejected")
    emit("model_request", turn=3, tool_results=results)
    finish, message = request(messages, tools)
    if (finish != "stop" or message.get("tool_calls")
            or json.loads(message.get("content") or "null") != {"status":"completed","result_nonce":nonce}):
        raise Denied("model_did_not_acknowledge_tools")
    emit("completed", turns=3, result_refs=results, acknowledgement_sha256=digest(nonce))


def validate_boot(boot):
    if (set(boot) != {"session_id","task_id","version","approved_copy","release_sha256"}
            or not all(isinstance(boot[k], str) and ID.fullmatch(boot[k]) for k in ("session_id","task_id"))
            or type(boot["version"]) is not int or boot["version"] != 1
            or not HASH.fullmatch(boot["release_sha256"]) or set(boot["approved_copy"]) != {"title","subtitle"}
            or not all(isinstance(v, str) and 0 < len(v) <= 70 for v in boot["approved_copy"].values())):
        raise Denied("invalid_session_binding")


def main():
    boot = read_line()
    validate_boot(boot)
    from_customer(boot["approved_copy"], lambda request: exchange(boot, request))


if __name__ == "__main__":
    try:
        main()
    except Denied as error:
        emit("failed", code=str(error))
        sys.exit(1)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        emit("failed", code="foreground_runtime_error")
        sys.exit(1)
