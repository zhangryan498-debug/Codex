"""Local customer agent session. Its only transport is one fixed loopback fixture."""
from hashlib import sha256
from http.client import HTTPConnection
import json
import os
import re
import sys
from urllib.parse import urlsplit

SELL = "dt_bava_selling_point"
SHOP = "cn_shop_poster_version_receive"
MAX_RECORD = 32768
MAX_RESPONSE = 32768
WORD = re.compile(r"[A-Za-z0-9_-]{1,80}")
ID = re.compile(r"[0-9a-f]{32}")
HASH = re.compile(r"[0-9a-f]{64}")
MALL_ROLE = "douyin-shop-ops"


class Denied(ValueError):
    pass


def read_line():
    line = sys.stdin.buffer.readline(MAX_RECORD + 1)
    if not line or len(line) > MAX_RECORD or not line.endswith(b"\n"):
        raise Denied("invalid_ipc_frame")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise Denied("invalid_ipc_frame")
    return value


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=True, separators=(",", ":")), flush=True)


def endpoint(value):
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or url.port is None
            or url.netloc != f"127.0.0.1:{url.port}" or url.path != "/v1/chat/completions"
            or url.query or url.fragment):
        raise Denied("fixture_loopback_only")
    return url.port, url.path


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def definitions(binding):
    identity = {"task_id": {"type": "string", "enum": [binding["task_id"]]},
                "version": {"type": "integer", "enum": [binding["version"]]}}
    fields = {"title": {"type": "string", "maxLength": 40},
              "subtitle": {"type": "string", "maxLength": 70}}
    tools = []
    for name, detail, props in (
        (SELL, "Validate one supported poster selling point using the assigned local Skill.", {**identity, **fields}),
        (SHOP, "Request the CN mall handoff for the same poster using the one-time selling-point result ID. "
              "The workbench will wait for customer review before fulfilling it.",
         {**identity, "selling_point_result_id": {"type": "string", "minLength": 32, "maxLength": 32}}),
    ):
        tools.append({"type": "function", "function": {"name": name, "description": detail,
                      "parameters": {"type": "object", "properties": props, "required": list(props),
                                     "additionalProperties": False}}})
    return tools


def ask(target, messages, tools):
    payload = {"model": "dt-interactive-loopback-fixture", "messages": messages, "stream": False, "max_tokens": 384}
    if tools is None:
        payload["response_format"] = {"type": "json_object"}
    else:
        payload.update(tools=tools, tool_choice="auto")
    port, target_path = target
    connection = HTTPConnection("127.0.0.1", port, timeout=8)
    try:
        connection.request("POST", target_path, body=json.dumps(payload, ensure_ascii=True).encode(),
                           headers={"Content-Type": "application/json", "Connection": "close"})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
        if response.status != 200 or len(raw) > MAX_RESPONSE:
            raise Denied("fixture_response_invalid")
        parsed = json.loads(raw)
        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise Denied("fixture_response_invalid")
        message = choices[0].get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise Denied("fixture_response_invalid")
        return choices[0].get("finish_reason"), message
    finally:
        connection.close()


def requested_call(finish, message, expected, binding, seen, proof):
    calls = message.get("tool_calls")
    if calls is None or calls == []:
        raise Denied("model_did_not_call_tool")
    if (finish != "tool_calls" or set(message) != {"role", "content", "tool_calls"}
            or message["content"] is not None or not isinstance(calls, list) or len(calls) != 1
            or not isinstance(calls[0], dict)):
        raise Denied("invalid_tool_call")
    call = calls[0]
    name = call.get("function", {}).get("name") if isinstance(call.get("function"), dict) else None
    if name not in {SELL, SHOP}:
        raise Denied("tool_not_allowed")
    if name != expected:
        raise Denied("tool_out_of_sequence")
    ident = call.get("id")
    if (set(call) != {"id", "type", "function"} or set(call["function"]) != {"name", "arguments"}
            or call.get("type") != "function" or not isinstance(ident, str) or not WORD.fullmatch(ident)
            or ident in seen):
        raise Denied("invalid_tool_call_id")
    raw = call["function"].get("arguments")
    if not isinstance(raw, str) or len(raw) > 1300:
        raise Denied("invalid_tool_arguments")
    args = json.loads(raw)
    if name == SHOP:
        if not isinstance(args, dict) or "selling_point_result_id" not in args:
            raise Denied("selling_point_result_id_missing")
        if proof is None or args["selling_point_result_id"] != proof:
            raise Denied("selling_point_result_id_mismatch")
    keys = {"task_id", "version"} | ({"title", "subtitle"} if name == SELL else {"selling_point_result_id"})
    if not isinstance(args, dict) or set(args) != keys:
        raise Denied("invalid_tool_arguments")
    if args["task_id"] != binding["task_id"]:
        raise Denied("wrong_task")
    if type(args["version"]) is not int or args["version"] != binding["version"]:
        raise Denied("wrong_version")
    if name == SELL and any(not isinstance(args[k], str) or not 0 < len(args[k]) <= 70
                            for k in ("title", "subtitle")):
        raise Denied("invalid_tool_arguments")
    seen.add(ident)
    return call, args


def review_shop(target, binding, call_id, frame):
    pin = frame.get("binding")
    expected = {**binding, "call_id": call_id, "role": MALL_ROLE}
    if (set(frame) != {"event", "binding"} or not isinstance(pin, dict)
            or set(pin) != set(expected) | {"inference_id", "release_sha256"}
            or digest({key: pin[key] for key in expected}) != digest(expected)
            or not isinstance(pin["inference_id"], str) or not ID.fullmatch(pin["inference_id"])
            or not isinstance(pin["release_sha256"], str) or not HASH.fullmatch(pin["release_sha256"])):
        raise Denied("wrong_role_request_binding")
    try:
        finish, reply = ask(target, [
            {"role": "system", "content": (
                "You are the CN mall receiving role in a local integration test. The workbench verified a saved "
                "reviewed P01 source, pinned by the release hash. Decide if that evidence supports local receipt. "
                "Return JSON with exactly the supplied binding and decision (accept or hold). Do not invent a "
                "receipt, modify files, make store changes or call tools."
            )},
            {"role": "user", "content": json.dumps({"binding": pin, "verified_reviewed_source": True})},
        ], None)
        content = reply.get("content")
        if finish != "stop" or reply.get("tool_calls") or not isinstance(content, str) or len(content) > 2300:
            raise Denied("role_model_response_invalid")
        answer = json.loads(content)
        if (not isinstance(answer, dict) or set(answer) != {"binding", "decision"}
                or answer["decision"] not in ("accept", "hold")):
            raise Denied("role_model_response_invalid")
        if digest(answer["binding"]) != digest(pin):
            raise Denied("role_model_wrong_binding")
        if answer["decision"] == "hold":
            return {"binding": pin, "status": "failed", "code": "role_model_held"}
        return {**answer, "status": "accepted", "response_sha256": digest(answer)}
    except Denied as error:
        code = str(error)
        if code not in {"role_model_wrong_binding", "role_model_response_invalid"}:
            code = "role_model_response_invalid"
        return {"binding": pin, "status": "failed", "code": code}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return {"binding": pin, "status": "failed", "code": "role_model_response_invalid"}


def run():
    boot = read_line()
    role_model = boot.pop("role_model_fixture", False)
    if type(role_model) is not bool:
        raise Denied("invalid_session_binding")
    checkpoint = boot.get("checkpoint")
    expected_keys = {"session_id", "task_id", "version", "fixture", "facts", "title"} if checkpoint is None else {
        "session_id", "task_id", "version", "fixture", "checkpoint"}
    if set(boot) != expected_keys:
        raise Denied("invalid_session_binding")
    binding = {k: boot[k] for k in ("session_id", "task_id", "version")}
    if (not all(isinstance(boot[k], str) and ID.fullmatch(boot[k]) for k in ("task_id", "session_id"))
            or type(boot["version"]) is not int or boot["version"] != 1
            or checkpoint is None and not all(isinstance(boot[k], str) and len(boot[k]) <= 2000 for k in ("title", "facts"))):
        raise Denied("invalid_session_binding")
    target = endpoint(boot["fixture"])
    tools = definitions(binding)
    messages = [
        {"role": "system", "content": (
            "Use the allowed local tools in order. Call the selling-point Skill with exact excerpts from approved facts. "
            "Return the selling-point result ID in the CN mall tool call. The workbench awaits customer review before executing it. "
            "When both succeed, return exactly JSON {\"status\":\"completed\",\"result_nonce\":<latest tool nonce>}. "
            "Do not use other tools, claim additional work, or change task or version."
        )},
        {"role": "user", "content": json.dumps({**binding, "title": boot.get("title"), "facts": boot.get("facts")}, ensure_ascii=True)},
    ]
    seen = set()
    result_refs = []
    proof = None
    order = (SELL, SHOP)
    if checkpoint is not None:
        if (not isinstance(checkpoint, dict) or set(checkpoint) != {"prior_session_id","prior_receipt_sha256",
                   "copy_receipt_sha256","selling_point_result_id"} or not ID.fullmatch(checkpoint["prior_session_id"])
                or not ID.fullmatch(checkpoint["selling_point_result_id"])
                or any(not re.fullmatch(r"[a-f0-9]{64}",checkpoint[k]) for k in ("prior_receipt_sha256","copy_receipt_sha256"))):
            raise Denied("invalid_session_binding")
        order = (SHOP,)
        tools = [one for one in tools if one["function"]["name"] == SHOP]
        proof = checkpoint["selling_point_result_id"]
        messages = [{"role":"system","content":("Use the one allowed mall role on the reviewed saved checkpoint. "
                    "Include the checkpoint result ID. Acknowledge an ok result with JSON status completed and the result_nonce. "
                    "A failed result remains blocked.")},
                    {"role":"user","content":json.dumps({**binding,"reviewed_checkpoint":checkpoint},ensure_ascii=True)}]
    emit("runtime_started", pid=os.getpid(), parent_pid=os.getppid(), binding=binding, tools_sha256=digest(tools))
    for turn, expected in enumerate(order, 1):
        emit("model_request", turn=turn, tool_results=[*result_refs])
        finish, message = ask(target, messages, tools)
        call, args = requested_call(finish, message, expected, binding, seen, proof)
        emit("model_tool_call", turn=turn, call_id=call["id"], name=expected, arguments=args)
        reply = read_line()
        role_result = None
        if reply.get("event") == "review_mall_role":
            if not role_model or expected != SHOP:
                raise Denied("unexpected_role_model_request")
            role_result = review_shop(target, binding, call["id"], reply)
            emit("mall_role_result", result=role_result)
            reply = read_line()
        if (reply.get("event") != "tool_result" or reply.get("call_id") != call["id"]
                or reply.get("session_id") != binding["session_id"] or not isinstance(reply.get("result"), dict)):
            raise Denied("tool_result_binding")
        result = reply["result"]
        failed_child = expected == SHOP and result.get("status") == "failed"
        if expected == SHOP and role_model and not failed_child:
            if (not role_result or role_result["status"] != "accepted"
                    or digest(result.get("role_review")) != digest(role_result)
                    or not isinstance(result.get("receipt_task_id"), str) or not ID.fullmatch(result["receipt_task_id"])
                    or not isinstance(result.get("agent_subsession_id"), str) or not ID.fullmatch(result["agent_subsession_id"])):
                raise Denied("missing_role_model_receipt")
        if not failed_child and (result.get("status") != "ok" or not isinstance(result.get("nonce"), str) or not ID.fullmatch(result["nonce"])):
            raise Denied("tool_execution_failed")
        result_refs.append({"call_id": call["id"], "sha256": digest(result)})
        # Keep the provider's validated assistant message and original arguments string.
        messages.append(message)
        messages.append({"role": "tool", "tool_call_id": call["id"],
                         "content": json.dumps(result, ensure_ascii=True, separators=(",", ":"))})
        if expected == SELL:
            proof = result["nonce"]

    final_turn = len(order) + 1
    emit("model_request", turn=final_turn, tool_results=[*result_refs])
    finish, message = ask(target, messages, tools)
    content = message.get("content")
    if failed_child:
        emit("child_failure_ack", response_attempted_success=isinstance(content, str) and '"completed"' in content)
        raise Denied("mall_child_failed")
    if (finish != "stop" or message.get("tool_calls") or not isinstance(content, str)
            or json.loads(content) != {"status": "completed", "result_nonce": result["nonce"]}):
        raise Denied("model_did_not_acknowledge_tools")
    emit("completed", turns=final_turn, result_refs=result_refs, acknowledgement_sha256=digest(result["nonce"]))


if __name__ == "__main__":
    try:
        run()
    except Denied as error:
        emit("failed", code=str(error))
        sys.exit(1)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        emit("failed", code="invalid_session_or_fixture")
        sys.exit(1)
