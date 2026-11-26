"""Wire adapters for explicit Beijing Bailian account routes.

The Windows credential worker supplies its in-memory key. An explicitly
configured loopback fixture substitutes all network destinations and uses no key.
"""
from http.client import HTTPException
import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


PROVIDER_METHOD = ("bailian", "workspace-payg")
TOKEN_PLAN_METHOD = ("bailian", "personal-token-plan")
TOKEN_PLAN_ORIGIN = "https://token-plan.cn-beijing.maas.aliyuncs.com"
TOKEN_PLAN_TEXT_MODELS = frozenset({"qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus"})
WORKSPACE_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
HOST_SUFFIX = ".cn-beijing.maas.aliyuncs.com"
RESERVED_PLAN_WORKSPACES = {"token-plan", "coding-plan", "coding"}
CHAT_PATH = "/compatible-mode/v1/chat/completions"
IMAGE_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
VIDEO_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"
TASK_PATH = "/api/v1/tasks/"
ROUTES = {
    "language": ("qwen-plus",),
    "image": ("wan2.6-t2i",),
    "video": ("wan2.7-t2v", "wan2.7-t2v-2026-06-12"),
}
VIDEO_STATUSES = {"pending", "running", "succeeded", "failed", "canceled", "unknown"}
DOWNLOAD_HOSTS = {
    "dashscope-result-bj.oss-cn-beijing.aliyuncs.com",
    "dashscope-result-bj.oss-accelerate.aliyuncs.com",
    "dashscope-result-sh.oss-accelerate.aliyuncs.com",
    "dashscope-a717.oss-accelerate.aliyuncs.com",
}
MAX_DOCUMENT = 1024 * 1024
MAX_ASSET = 24 * 1024 * 1024


class InferenceWireError(Exception):
    """Only fixed, non-provider error codes may leave the worker."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def supports_route(provider, method, purpose, model_id):
    if (provider, method) == TOKEN_PLAN_METHOD:
        return purpose == "language" and isinstance(model_id, str) and model_id in TOKEN_PLAN_TEXT_MODELS
    return ((provider, method) == PROVIDER_METHOD and isinstance(purpose, str)
            and model_id in ROUTES.get(purpose, ()))


def _origin(provider, method, workspace_id, key, fixture):
    token_plan = (provider, method) == TOKEN_PLAN_METHOD
    if token_plan:
        if workspace_id is not None:
            raise InferenceWireError("invalid_workspace")
    else:
        if not isinstance(workspace_id, str) or not WORKSPACE_ID.fullmatch(workspace_id):
            raise InferenceWireError("invalid_workspace")
        if workspace_id in RESERVED_PLAN_WORKSPACES:
            raise InferenceWireError("token_plan_direct_disabled")
    if fixture is not None:
        try:
            parts = urlsplit(fixture)
            port = parts.port
        except (TypeError, ValueError, AttributeError):
            raise InferenceWireError("invalid_fixture") from None
        if not port or fixture != f"http://127.0.0.1:{port}" or key is not None:
            raise InferenceWireError("invalid_fixture")
        return fixture
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.~+/=-]{8,512}", key):
        raise InferenceWireError("credential_required")
    return TOKEN_PLAN_ORIGIN if token_plan else f"https://{workspace_id}{HOST_SUFFIX}"


def _transport(opener, url, *, key=None, payload=None, async_job=False, media=False, timeout=30):
    headers = {"Accept": "*/*" if media else "application/json"}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if async_job:
        headers["X-DashScope-Async"] = "enable"
    try:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8") if payload is not None else None
    except (TypeError, ValueError, UnicodeError):
        raise InferenceWireError("invalid_payload") from None
    if data is not None and len(data) > MAX_DOCUMENT:
        raise InferenceWireError("invalid_payload")
    req = Request(url, data=data, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as response:
            limit = MAX_ASSET if media else MAX_DOCUMENT
            data = response.read(limit + 1)
            mime_type = response.headers.get_content_type()
            content_length = response.headers.get("Content-Length")
            if content_length is not None and (not content_length.isdecimal() or int(content_length) != len(data)):
                raise ValueError
    except HTTPError as exc:
        status = exc.code
        exc.close()
        if media:
            raise InferenceWireError("asset_unavailable") from None
        if status in {401, 403}:
            raise InferenceWireError("provider_permission_denied") from None
        if async_job and (status >= 500 or status in {408, 409, 425} or status < 400):
            raise InferenceWireError("submission_uncertain") from None
        if status == 429:
            raise InferenceWireError("provider_rate_limited") from None
        raise InferenceWireError("provider_request_failed") from None
    except (OSError, URLError, HTTPException, ValueError):
        code = "submission_uncertain" if async_job else "asset_unavailable" if media else "provider_unavailable"
        raise InferenceWireError(code) from None

    if media:
        if not data or len(data) > limit:
            raise InferenceWireError("asset_unavailable")
        return data, mime_type
    try:
        if not data or len(data) > limit:
            raise ValueError
        document = json.loads(data)
        if not isinstance(document, dict):
            raise ValueError
    except (ValueError, UnicodeError, RecursionError):
        code = "submission_uncertain" if async_job else "invalid_provider_response"
        raise InferenceWireError(code) from None
    if document.get("code") or document.get("error"):
        code = document.get("code")
        if code in ("InvalidApiKey", "AccessDenied", "Model.AccessDenied", "PermissionDenied"):
            raise InferenceWireError("provider_permission_denied")
        raise InferenceWireError("submission_uncertain" if async_job else "provider_request_failed")
    return document


def _safe_identifier(value, key):
    return isinstance(value, str) and SAFE_ID.fullmatch(value) and (key is None or key not in value)


def _assistant_message(message):
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise InferenceWireError("invalid_provider_response")
    content = message.get("content")
    calls = message.get("tool_calls")
    if calls is None:
        calls = []
    if ((content is not None and not isinstance(content, str)) or not isinstance(calls, list)
            or len(calls) > 8 or (not calls and not (content and content.strip()))):
        raise InferenceWireError("invalid_provider_response")
    valid_calls = []
    used_ids = set()
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise InferenceWireError("invalid_provider_response")
        function = call.get("function")
        ident = call.get("id")
        if (not isinstance(function, dict) or not _safe_identifier(ident, None) or ident in used_ids
                or not _safe_identifier(function.get("name"), None)
                or not isinstance(function.get("arguments"), str)):
            raise InferenceWireError("invalid_provider_response")
        used_ids.add(ident)
        valid_calls.append({"id": ident, "type": "function",
                            "function": {"name": function["name"], "arguments": function["arguments"]}})
    result = {"role": "assistant", "content": content}
    if valid_calls:
        result["tool_calls"] = valid_calls
    return result


def _chat_payload(model, payload):
    if set(payload) - {"messages", "tools"}:
        raise InferenceWireError("invalid_payload")
    messages, tools = payload.get("messages"), payload.get("tools", [])
    if (not isinstance(messages, list) or not 1 <= len(messages) <= 32
            or not isinstance(tools, list) or len(tools) > 12):
        raise InferenceWireError("invalid_payload")
    for message in messages:
        if not isinstance(message, dict):
            raise InferenceWireError("invalid_payload")
        role = message.get("role")
        fields = {"role", "content"}
        if role == "assistant" and "tool_calls" in message:
            try:
                if _assistant_message(message) != message:
                    raise InferenceWireError("invalid_payload")
            except InferenceWireError:
                raise InferenceWireError("invalid_payload") from None
            continue
        if role == "tool":
            fields.add("tool_call_id")
            if not _safe_identifier(message.get("tool_call_id"), None):
                raise InferenceWireError("invalid_payload")
        elif role not in ("system", "user", "assistant"):
            raise InferenceWireError("invalid_payload")
        if set(message) != fields or not isinstance(message.get("content"), str):
            raise InferenceWireError("invalid_payload")
    request = {"model": model, "messages": messages, "stream": False, "enable_thinking": False}
    if tools:
        request["tools"] = tools
        request["tool_choice"] = "auto"
    return request


def _prompt(payload, limit):
    prompt = payload.get("prompt")
    if set(payload) != {"prompt"} or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > limit:
        raise InferenceWireError("invalid_payload")
    return prompt


def _download(opener, url, expected_type, fixture, key):
    if (not isinstance(url, str) or not url.isascii() or len(url) > 8192
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
        raise InferenceWireError("asset_unavailable")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise InferenceWireError("asset_unavailable") from None
    if not parts.path or "#" in url or (key and key in url):
        raise InferenceWireError("asset_unavailable")
    if fixture:
        if not url.startswith(f"{fixture}/") or parts.netloc != urlsplit(fixture).netloc:
            raise InferenceWireError("asset_unavailable")
    elif parts.scheme != "https" or parts.netloc not in DOWNLOAD_HOSTS:
        raise InferenceWireError("asset_unavailable")
    data, mime_type = _transport(opener, url, media=True, timeout=45)
    if mime_type not in {expected_type, "application/octet-stream"}:
        raise InferenceWireError("asset_unavailable")
    if expected_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise InferenceWireError("asset_unavailable")
    if expected_type == "video/mp4" and (len(data) < 12 or data[4:8] != b"ftyp"):
        raise InferenceWireError("asset_unavailable")
    return {"media": data, "mime_type": expected_type}


def dispatch_inference(provider, method, workspace_id, key, operation, model_id, payload, *, fixture=None):
    """Execute one explicitly selected route, with no retries or implicit polling."""
    if operation not in ("language", "image", "video_submit", "video_status"):
        raise InferenceWireError("unsupported_route")
    purpose = "video" if operation.startswith("video_") else operation
    if not supports_route(provider, method, purpose, model_id):
        raise InferenceWireError("unsupported_route")
    if not isinstance(payload, dict):
        raise InferenceWireError("invalid_payload")
    origin = _origin(provider, method, workspace_id, key, fixture)
    if key and key in json.dumps(payload, ensure_ascii=False):
        raise InferenceWireError("protected_input")
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    if operation == "language":
        body = _chat_payload(model_id, payload)
        document = _transport(opener, origin + CHAT_PATH, key=key, payload=body, timeout=60)
        choices = document.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise InferenceWireError("invalid_provider_response")
        if choices[0].get("finish_reason") not in ("stop", "tool_calls"):
            raise InferenceWireError("incomplete_model_response")
        message = _assistant_message(choices[0].get("message"))
        if (choices[0]["finish_reason"] == "tool_calls") != ("tool_calls" in message):
            raise InferenceWireError("invalid_provider_response")
        if key and key in json.dumps(message, ensure_ascii=False):
            raise InferenceWireError("invalid_provider_response")
        result = {"status": "succeeded", "message": message}
    elif operation == "image":
        body = {"model": model_id, "input": {"messages": [{"role": "user", "content": [
            {"text": _prompt(payload, 2100)}]}]}, "parameters": {"n": 1, "size": "1280*1280"}}
        document = _transport(opener, origin + IMAGE_PATH, key=key, payload=body, timeout=90)
        try:
            output = document["output"]
            choice, = output["choices"]
            content, = choice["message"]["content"]
            if output["finished"] is not True or choice["finish_reason"] != "stop" or content["type"] != "image":
                raise ValueError
            url = content["image"]
        except (KeyError, TypeError, ValueError):
            raise InferenceWireError("invalid_provider_response") from None
        result = {"status": "succeeded", **_download(opener, url, "image/png", fixture, key)}
    else:
        if operation == "video_submit":
            body = {"model": model_id, "input": {"prompt": _prompt(payload, 5000)},
                    "parameters": {"resolution": "720P", "ratio": "16:9", "duration": 5}}
            document = _transport(opener, origin + VIDEO_PATH, key=key, payload=body,
                                  async_job=True, timeout=35)
        else:
            if set(payload) != {"task_id"} or not _safe_identifier(payload.get("task_id"), key):
                raise InferenceWireError("invalid_payload")
            document = _transport(opener, origin + TASK_PATH + payload["task_id"], key=key)
        output = document.get("output")
        if not isinstance(output, dict):
            raise InferenceWireError("submission_uncertain" if operation == "video_submit" else "invalid_provider_response")
        task_id = output.get("task_id")
        status = output.get("task_status")
        if not _safe_identifier(task_id, key) or not isinstance(status, str) or status.lower() not in VIDEO_STATUSES:
            raise InferenceWireError("submission_uncertain" if operation == "video_submit" else "invalid_provider_response")
        result = {"task_id": task_id, "status": status.lower()}
        if operation == "video_status":
            if task_id != payload["task_id"]:
                raise InferenceWireError("video_task_mismatch")
            if result["status"] == "succeeded":
                result.update(_download(opener, output.get("video_url"), "video/mp4", fixture, key))
        elif result["status"] == "succeeded":
            # Submission returns a durable ID. Read results only on the status path.
            result["status"] = "pending"

    request_id = document.get("request_id", document.get("id"))
    if _safe_identifier(request_id, key):
        result["request_id"] = request_id
    return result
