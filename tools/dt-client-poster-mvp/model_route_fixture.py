"""Loopback-only model routing for the isolated workbench fixture.

This module has no real provider or credential adapter. It proves that fixture
settings determine which account, route, and model issue each request.
"""

import json
from http.client import HTTPConnection, HTTPException
from urllib.parse import urlsplit


ROUTES = {
    "language": "/compatible-mode/v1/chat/completions",
    "image": "/api/v1/services/aigc/multimodal-generation/generation",
    "video": "/api/v1/services/aigc/video-generation/video-synthesis",
}
MAX_REPLY = 1024 * 1024


class FixtureRouteError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _loopback_port(origin):
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except (TypeError, ValueError):
        raise FixtureRouteError("fixture_origin_invalid") from None
    if (
        parsed.scheme != "http"
        or not port
        or parsed.netloc != f"127.0.0.1:{port}"
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise FixtureRouteError("fixture_origin_invalid")
    return port


def _required_text(value, code):
    if not isinstance(value, str) or not value.strip():
        raise FixtureRouteError(code)
    return value


def _request_body(purpose, model_id, capabilities, payload):
    if purpose == "language":
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise FixtureRouteError("fixture_messages_required")
        body = {"model": model_id, "messages": messages}
        if "tools" in payload:
            if "tools" not in capabilities:
                raise FixtureRouteError("fixture_model_lacks_tools")
            tools = payload["tools"]
            if not isinstance(tools, list) or not tools:
                raise FixtureRouteError("fixture_tools_required")
            body.update(tools=tools, tool_choice="auto")
        return body
    prompt = _required_text(payload.get("prompt"), "fixture_prompt_required")
    if purpose == "image":
        return {
            "model": model_id,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        }
    return {"model": model_id, "input": {"prompt": prompt}}


def _response_result(purpose, reply):
    if not isinstance(reply, dict) or reply.get("code") or reply.get("error"):
        raise FixtureRouteError("fixture_invalid_reply")
    if purpose == "language":
        try:
            message = reply["choices"][0]["message"]
            if message["role"] == "assistant" and (
                message.get("tool_calls") or _required_text(message.get("content"), "fixture_empty_reply")
            ):
                return {"message": message}
        except (IndexError, KeyError, TypeError, AttributeError):
            pass
        raise FixtureRouteError("fixture_invalid_reply")
    if purpose == "image":
        try:
            content = reply["output"]["choices"][0]["message"]["content"]
            image_ids = [item["image"] for item in content if "image" in item]
        except (IndexError, KeyError, TypeError):
            raise FixtureRouteError("fixture_invalid_reply") from None
        if image_ids and all(isinstance(item, str) and item.startswith("fixture-image:") for item in image_ids):
            return {"image_ids": image_ids}
    if purpose == "video":
        output = reply.get("output")
        if isinstance(output, dict) and output.get("task_status") in ("PENDING", "RUNNING"):
            return {
                "task_id": _required_text(output.get("task_id"), "fixture_invalid_reply"),
                "status": output["task_status"],
            }
    raise FixtureRouteError("fixture_invalid_reply")


def dispatch_fixture(*, fixture_mode, accounts, selection, purpose, payload, timeout=3.0):
    """Issue one synthetic request using exactly the chosen fixture account and model."""
    if fixture_mode is not True:
        raise FixtureRouteError("fixture_mode_required")
    if purpose not in ROUTES:
        raise FixtureRouteError("fixture_unknown_purpose")
    if not isinstance(selection, dict) or not isinstance(payload, dict):
        raise FixtureRouteError("fixture_invalid_input")

    account_id = _required_text(selection.get("account_id"), "fixture_account_required")
    model_id = _required_text(selection.get("model_id"), "fixture_model_required")
    if not isinstance(accounts, dict):
        raise FixtureRouteError("fixture_invalid_input")
    account = accounts.get(account_id)
    if not isinstance(account, dict):
        raise FixtureRouteError("fixture_account_unavailable")
    models = account.get("models", {})
    capabilities = models.get(model_id) if isinstance(models, dict) else None
    if not isinstance(capabilities, (list, tuple, set)) or purpose not in capabilities:
        raise FixtureRouteError("fixture_model_lacks_purpose")

    origin = account.get("fixture_origin")
    if not isinstance(origin, str):
        raise FixtureRouteError("fixture_origin_invalid")
    port = _loopback_port(origin)
    route = ROUTES[purpose]
    body = _request_body(purpose, model_id, capabilities, payload)
    headers = {"Content-Type": "application/json"}
    if purpose == "video":
        headers["X-DashScope-Async"] = "enable"
    connection = HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("POST", route, json.dumps(body).encode(), headers)
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise FixtureRouteError(f"fixture_http_{response.status}")
        raw = response.read(MAX_REPLY + 1)
        if len(raw) > MAX_REPLY:
            raise FixtureRouteError("fixture_reply_too_large")
        reply = json.loads(raw)
    except (OSError, HTTPException, ValueError) as exc:
        code = "fixture_invalid_reply" if isinstance(exc, (UnicodeError, ValueError)) else "fixture_connection_failed"
        raise FixtureRouteError(code) from None
    finally:
        connection.close()
    return {
        "mode": "fixture",
        "purpose": purpose,
        "account_id": account_id,
        "model_id": model_id,
        "route": route,
        "result": _response_result(purpose, reply),
    }
