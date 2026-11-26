"""Interactive, memory-only Windows credential worker for model catalogue metadata.

The parent sends only fixed IPC commands. The native dialog supplies the key.
Model jobs use the same explicitly connected account through a bounded IPC command.
"""
import argparse
from http.client import HTTPException, HTTPSConnection
import ipaddress
import json
import re
import socket
import ssl
from queue import Empty, Queue
import sys
from threading import Thread
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from pathlib import Path
from provider_inference_ipc import worker_operation
from provider_inference_wire import RESERVED_PLAN_WORKSPACES, TOKEN_PLAN_METHOD, TOKEN_PLAN_ORIGIN, TOKEN_PLAN_TEXT_MODELS
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


OPENAI_MODELS = "https://api.openai.com/v1/models"
BAILIAN_SUFFIX = ".cn-beijing.maas.aliyuncs.com"
BAILIAN_PATH = "/api/v1/models"
WORKSPACE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]|[a-z0-9]")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}")
CREDENTIAL = re.compile(r"[A-Za-z0-9_.~+/=-]{8,512}")
ANTHROPIC_VERSION = "2023-06-01"
CUSTOM_PATH = re.compile(r"(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*")
SUBSCRIPTION_ENDPOINT = re.compile(r"(?:token[-_.]?plan|coding[-_.]?plan|claude[-_.]?code|(?:^|[./-])coding(?:[./-]|$))")
NAT64_PREFIXES = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
BAILIAN_CAPABILITIES = {"TG": "language", "VU": "vision", "IG": "image", "VG": "video"}
MAX_IPC_INPUT = 32768
MAX_HTTP_RESPONSE = 2 * 1024 * 1024
MAX_ROWS = 600
PAGE_SIZE = 20
MAX_COLLECTION_SECONDS = 35
IDLE_SECONDS = 600


class CatalogError(Exception):
    """Only fixed error codes are eligible for IPC."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fixture_url(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme == "http" and parts.hostname == "127.0.0.1"
                and parts.port and value == f"http://127.0.0.1:{parts.port}"):
            return value
    except ValueError:
        pass
    raise CatalogError("invalid_setup")


def subscription_base(value):
    text = value if "://" in value else "https://" + value
    parts = urlsplit(text)
    return bool(SUBSCRIPTION_ENDPOINT.search(f"{parts.hostname or ''}{parts.path}".lower()))


def protocol_mismatch(method, host, path):
    if method == "openai-compatible":
        return (host in {"api.anthropic.com", "api.claude.com"}
                or path.lower().endswith(("/apps/anthropic", "/apps/anthropic/v1")))
    return host == "api.openai.com" or path.lower().endswith("/compatible-mode/v1")


def catalog_endpoint(provider, method, workspace_id=None, base_url=None):
    if (provider, method) == ("openai", "api") and workspace_id is None and base_url is None:
        return OPENAI_MODELS
    if (provider, method) == TOKEN_PLAN_METHOD and workspace_id is None and base_url is None:
        return TOKEN_PLAN_ORIGIN + "/compatible-mode/v1/models"
    if (provider, method) == ("bailian", "workspace-payg") and base_url is None:
        if not isinstance(workspace_id, str) or not WORKSPACE_ID.fullmatch(workspace_id):
            raise CatalogError("invalid_setup")
        if workspace_id in RESERVED_PLAN_WORKSPACES:
            raise CatalogError("token_plan_direct_disabled")
        return f"https://{workspace_id}{BAILIAN_SUFFIX}{BAILIAN_PATH}"
    if provider != "custom" or method not in {"openai-compatible", "anthropic-compatible"}:
        raise CatalogError("unsupported")
    if (workspace_id is not None or not isinstance(base_url, str) or not base_url.isascii()
            or len(base_url) > 300 or any(ord(char) <= 32 or ord(char) == 127 for char in base_url)):
        raise CatalogError("invalid_setup")
    try:
        if subscription_base(base_url):
            raise CatalogError("token_plan_direct_disabled")
        parts = urlsplit(base_url)
        host = parts.hostname or ""
        path = parts.path.rstrip("/") or "/v1"
        if (parts.scheme != "https" or parts.netloc != host or "?" in base_url or "#" in base_url
                or len(host) > 253 or "." not in host or path.endswith("/models")
                or not all(WORKSPACE_ID.fullmatch(label) for label in host.split("."))
                or host.endswith((".local", ".localhost", ".internal", ".home.arpa", ".onion"))
                or not CUSTOM_PATH.fullmatch(path)):
            raise CatalogError("invalid_setup")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise CatalogError("non_public_endpoint")
        if protocol_mismatch(method, host, path):
            raise CatalogError("catalog_protocol_mismatch")
    except ValueError:
        raise CatalogError("invalid_setup") from None
    if method == "anthropic-compatible" and not path.endswith("/v1"):
        path += "/v1"
    return f"https://{host}{path}/models"


def read_setup(message):
    if not isinstance(message, dict) or message.get("op") != "configure":
        raise CatalogError("invalid_setup")
    provider = message.get("provider")
    method = message.get("method")
    fields = {
        ("openai", "api"): {"op", "provider", "method"},
        ("bailian", "workspace-payg"): {"op", "provider", "method", "workspace_id"},
        TOKEN_PLAN_METHOD: {"op", "provider", "method"},
        ("custom", "openai-compatible"): {"op", "provider", "method", "base_url"},
        ("custom", "anthropic-compatible"): {"op", "provider", "method", "base_url"},
    }
    if not isinstance(provider, str) or not isinstance(method, str) or (provider, method) not in fields:
        raise CatalogError("unsupported")
    if set(message) != fields[(provider, method)]:
        raise CatalogError("invalid_setup")
    return provider, method, catalog_endpoint(provider, method, message.get("workspace_id"), message.get("base_url"))


def input_desktop():
    if sys.platform != "win32":
        raise CatalogError("desktop_required")
    from ctypes import WinDLL, byref, create_unicode_buffer, sizeof
    from ctypes import wintypes as w
    user = WinDLL("user32", use_last_error=True)
    kernel = WinDLL("kernel32", use_last_error=True)
    user.GetProcessWindowStation.restype = w.HANDLE
    user.GetThreadDesktop.argtypes = [w.DWORD]
    user.GetThreadDesktop.restype = w.HANDLE
    user.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    user.OpenInputDesktop.restype = w.HANDLE
    user.CloseDesktop.argtypes = [w.HANDLE]
    user.GetUserObjectInformationW.argtypes = [w.HANDLE, w.INT, w.LPVOID, w.DWORD, w.LPDWORD]
    user.IsWindowVisible.argtypes = [w.HWND]

    def name(handle):
        result = create_unicode_buffer(256)
        needed = w.DWORD()
        if not handle or not user.GetUserObjectInformationW(handle, 2, result, sizeof(result), byref(needed)):
            raise CatalogError("desktop_required")
        return result.value.casefold()

    active = user.OpenInputDesktop(0, False, 0x0001)
    if not active:
        raise CatalogError("desktop_required")
    try:
        if (name(user.GetProcessWindowStation()) != "winsta0"
                or name(user.GetThreadDesktop(kernel.GetCurrentThreadId())) != name(active)):
            raise CatalogError("desktop_required")
    finally:
        user.CloseDesktop(active)
    return user


def validate_credential(provider, method, entered):
    entered = entered.strip()
    if not CREDENTIAL.fullmatch(entered):
        raise CatalogError("invalid_credential_format")
    if entered.lower().startswith("sk-sp-") and (provider, method) != TOKEN_PLAN_METHOD:
        raise CatalogError("credential_plan_mismatch")
    return entered


def prompt_credential(provider, method, endpoint):
    user = input_desktop()
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        raise CatalogError("desktop_required") from None

    try:
        window = tk.Tk()
    except (tk.TclError, RuntimeError):
        raise CatalogError("desktop_required") from None

    name = {"openai": "OpenAI API", "bailian": "阿里云百炼 · 北京业务空间按量 API", "custom": f"自定义供应商 · {method}"}[provider]
    if (provider, method) == TOKEN_PLAN_METHOD:
        name = "阿里云百炼 · 北京 Token Plan 个人版"
    window.title("D&T 模型账户")
    window.attributes("-topmost", True)
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=18)
    frame.grid(sticky="nsew")
    tty_label = f"{name}\n请输入本人 API Key，仅保存在此次本机会话内存中。\n回到工作台点击「获取可用模型」后，才会向以下域名查询模型目录：\n{endpoint}"
    ttk.Label(frame, text=tty_label, justify="left").grid(row=0, columnspan=2, sticky="w")
    credential = ttk.Entry(frame, show="*", width=42)
    credential.grid(row=1, columnspan=2, sticky="ew", pady=12)
    feedback = ttk.Label(frame, text="")
    feedback.grid(row=2, columnspan=2, sticky="w")
    result = None
    expired = False

    def expire():
        nonlocal expired
        expired = True
        window.quit()

    def submit():
        nonlocal result
        try:
            result = validate_credential(provider, method, credential.get())
        except CatalogError as exc:
            messages = {"invalid_credential_format": "API Key 格式不正确，请确认已完整复制。",
                        "credential_plan_mismatch": "此 Key 属于套餐，请选择匹配的连接方式。"}
            feedback.configure(text=messages[str(exc)])
            return
        window.quit()

    ttk.Button(frame, text="在本次会话保留", command=submit).grid(row=3, column=0, sticky="w", pady=8)
    ttk.Button(frame, text="取消", command=window.quit).grid(row=3, column=1, sticky="e", pady=8)
    window.protocol("WM_DELETE_WINDOW", window.quit)
    window.bind("<Escape>", lambda event: window.quit())
    window.after(100_000, expire)
    credential.focus_set()
    try:
        window.update_idletasks()
        window.update()
        window.lift()
        credential.focus_force()
        if not window.winfo_viewable() or not user.IsWindowVisible(window.winfo_id()):
            raise CatalogError("desktop_required")
        emit({"state": "prompt_visible"})
        window.mainloop()
    except tk.TclError:
        raise CatalogError("desktop_required") from None
    finally:
        credential.delete(0, "end")
        window.destroy()
    if expired:
        raise CatalogError("credential_prompt_expired")
    return result


def tls_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    return context


def global_address(address):
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip.sixtofour or ip.teredo or any(ip in block for block in NAT64_PREFIXES):
            return False
    return ip.is_global and not ip.is_multicast


class PublicHTTPS(HTTPSConnection):
    """Connect to exactly a validated DNS address, keeping the URL host for TLS."""

    def connect(self):
        addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not addresses or any(not global_address(row[4][0]) for row in addresses):
            raise CatalogError("non_public_endpoint")
        # No fallback request is issued. Connections to alternate addresses
        # from one validated DNS result are attempted only before a request.
        deadline = monotonic() + self.timeout
        for family, kind, proto, _, address in addresses:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            candidate = socket.socket(family, kind, proto)
            candidate.settimeout(remaining)
            try:
                candidate.connect(address)
            except OSError:
                candidate.close()
                continue
            try:
                self.sock = self._context.wrap_socket(candidate, server_hostname=self.host)
            except BaseException:
                candidate.close()
                raise
            return
        raise OSError("catalog connection unavailable")


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PublicHTTPS, request, context=self._context)


def request_metadata(url, key, deadline, *, anthropic=False, custom=False, optional_catalog=False):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise CatalogError("query_limit")
    headers = {"Accept": "application/json"}
    if anthropic:
        headers["anthropic-version"] = ANTHROPIC_VERSION
        if key is not None:
            headers["x-api-key"] = key
    elif key is not None:
        headers["Authorization"] = f"Bearer {key}"
    request = Request(url, headers=headers, method="GET")
    handler = PublicHTTPSHandler if custom and key is not None else HTTPSHandler
    opener = build_opener(ProxyHandler({}), NoRedirect(), handler(context=tls_context()))
    try:
        with opener.open(request, timeout=min(8, remaining)) as response:
            if response.status != 200:
                raise CatalogError("bad_response")
            payload = bytearray()
            while monotonic() < deadline:
                chunk = response.read1(65_536)
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > MAX_HTTP_RESPONSE:
                    raise CatalogError("query_limit")
            data = bytes(payload)
    except HTTPError as exc:
        with exc:
            if exc.code in {401, 403}:
                raise CatalogError("denied") from None
            if (custom or optional_catalog) and exc.code in {404, 405}:
                raise CatalogError("unsupported") from None
            raise CatalogError("bad_response") from None
    except (OSError, URLError, HTTPException):
        raise CatalogError("network") from None
    if len(data) > MAX_HTTP_RESPONSE or monotonic() > deadline:
        raise CatalogError("query_limit")
    try:
        doc = json.loads(data)
    except (ValueError, RecursionError):
        raise CatalogError("bad_response") from None
    if not isinstance(doc, dict):
        raise CatalogError("bad_response")
    return doc


def bailian_rows(endpoint, key, deadline):
    expected_total = None
    for page in range(1, MAX_ROWS // PAGE_SIZE + 1):
        query = urlencode({"page_no": page, "page_size": PAGE_SIZE})
        doc = request_metadata(f"{endpoint}?{query}", key, deadline)
        if doc.get("success") is not True:
            raise CatalogError("bad_response")
        output = doc.get("output")
        if not isinstance(output, dict):
            raise CatalogError("bad_response")
        rows, total = output.get("models"), output.get("total")
        if (
            not isinstance(rows, list)
            or type(total) is not int or total < 0
            or output.get("page_no") != page or output.get("page_size") != PAGE_SIZE
            or (expected_total is not None and total != expected_total)
            or len(rows) != max(0, min(PAGE_SIZE, total - (page - 1) * PAGE_SIZE))
        ):
            raise CatalogError("bad_response")
        if total > MAX_ROWS:
            raise CatalogError("query_limit")
        expected_total = total
        yield from rows
        if PAGE_SIZE * page >= total:
            return


def anthropic_rows(endpoint, key, deadline, *, custom):
    cursor = None
    seen = set()
    count = 0
    for _ in range(MAX_ROWS // PAGE_SIZE):
        query = {"limit": PAGE_SIZE}
        if cursor is not None:
            query["after_id"] = cursor
        doc = request_metadata(f"{endpoint}?{urlencode(query)}", key, deadline, anthropic=True, custom=custom)
        rows, more = doc.get("data"), doc.get("has_more")
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE or type(more) is not bool:
            raise CatalogError("bad_response")
        count += len(rows)
        if count > MAX_ROWS:
            raise CatalogError("query_limit")
        yield from rows
        if not more:
            return
        cursor = doc.get("last_id")
        if (not isinstance(cursor, str) or not MODEL_ID.fullmatch(cursor) or (key and key in cursor)
                or cursor in seen or not rows or not isinstance(rows[-1], dict)
                or rows[-1].get("id") != cursor):
            raise CatalogError("bad_response")
        seen.add(cursor)
    raise CatalogError("query_limit")


def normalize_model(provider, method, row, key):
    if not isinstance(row, dict):
        raise CatalogError("bad_response")
    token_plan = (provider, method) == TOKEN_PLAN_METHOD
    name = row.get("model" if provider == "bailian" and not token_plan else "id")
    if not isinstance(name, str) or not MODEL_ID.fullmatch(name) or (key and key in name):
        raise CatalogError("bad_response")
    capabilities = set()
    if token_plan and name in TOKEN_PLAN_TEXT_MODELS:
        capabilities.add("language")
    if provider == "bailian" and not token_plan:
        raw = row.get("capabilities", [])
        features = row.get("features", [])
        if not isinstance(raw, list) or not isinstance(features, list):
            raise CatalogError("bad_response")
        capabilities.update(BAILIAN_CAPABILITIES[item] for item in raw
                            if isinstance(item, str) and item in BAILIAN_CAPABILITIES)
        if "function-calling" in features:
            capabilities.add("tools")
    if method == "anthropic-compatible":
        raw = row.get("capabilities")
        image_input = raw.get("image_input") if isinstance(raw, dict) else None
        if isinstance(image_input, dict) and image_input.get("supported") is True:
            capabilities.add("vision")
    return {
        "id": name,
        "capabilities": sorted(capabilities),
        "capability_source": ("official_reference" if token_plan else "provider_metadata") if capabilities else "unknown",
        "permission": "unverified",
    }


def list_models(provider, method, endpoint, key, fixture=None):
    token_plan = (provider, method) == TOKEN_PLAN_METHOD
    source = ("fixture" if fixture else "account_response" if token_plan else {
        "openai": "account_response", "bailian": "workspace_catalog", "custom": "custom_response",
    }[provider])
    custom = provider == "custom"
    if fixture:
        endpoint = fixture_url(fixture) + urlsplit(endpoint).path
        key = None
    deadline = monotonic() + MAX_COLLECTION_SECONDS
    if method == "anthropic-compatible":
        rows = anthropic_rows(endpoint, key, deadline, custom=custom)
    elif provider != "bailian" or token_plan:
        doc = request_metadata(endpoint, key, deadline, custom=custom, optional_catalog=token_plan)
        rows = doc.get("data")
        if doc.get("object") != "list" or not isinstance(rows, list):
            raise CatalogError("bad_response")
        if len(rows) > MAX_ROWS:
            raise CatalogError("query_limit")
    else:
        rows = bailian_rows(endpoint, key, deadline)
    models = {}
    for row in rows:
        model = normalize_model(provider, method, row, key)
        if model["id"] in models and models[model["id"]] != model:
            raise CatalogError("bad_response")
        models[model["id"]] = model
    return {"state": "listed" if models else "empty", "source": source, "models": list(models.values())}


def emit(document):
    sys.stdout.write(json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def next_command(queue):
    try:
        raw = queue.get(timeout=IDLE_SECONDS)
    except Empty:
        raise CatalogError("connection_expired") from None
    if not raw:
        return None
    if not raw.endswith(b"\n") or len(raw) > MAX_IPC_INPUT:
        raise CatalogError("invalid_command")
    try:
        message = json.loads(raw)
    except (ValueError, RecursionError):
        raise CatalogError("invalid_command") from None
    if not isinstance(message, dict):
        raise CatalogError("invalid_command")
    return message


def worker(fixture=None, spool=None):
    commands = Queue(maxsize=1)

    def receive():
        # A daemon may be waiting for its next IPC frame when the process exits.
        # Avoid the buffered reader lock during interpreter shutdown.
        stream = sys.stdin.buffer.raw
        while True:
            line = stream.readline(MAX_IPC_INPUT + 1)
            commands.put(line)
            if not line or len(line) > MAX_IPC_INPUT:
                return

    Thread(target=receive, daemon=True).start()
    first = next_command(commands)
    if first is None:
        return
    provider, method, endpoint = read_setup(first)
    key = None if fixture else prompt_credential(provider, method, endpoint)
    if fixture is None and key is None:
        emit({"state": "cancelled"})
        return
    emit({"state": "ready"})
    while True:
        command = next_command(commands)
        if command is None or command == {"op": "close"}:
            return
        if command.get("op") == "operate":
            try:
                emit(worker_operation(command, provider, method, first.get("workspace_id"), key, fixture, spool))
            except ValueError:
                raise CatalogError("invalid_command") from None
            continue
        if command != {"op": "catalog"}:
            raise CatalogError("invalid_command")
        try:
            emit(list_models(provider, method, endpoint, key, fixture))
        except CatalogError as exc:
            emit({"state": "error", "code": str(exc), "models": []})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture")
    parser.add_argument("--operation-spool", type=Path)
    args = parser.parse_args()
    try:
        worker(fixture_url(args.fixture) if args.fixture is not None else None,
               args.operation_spool.resolve(strict=True) if args.operation_spool else None)
    except CatalogError as exc:
        emit({"state": "error", "code": str(exc)})
    except (BrokenPipeError, OSError):
        return


if __name__ == "__main__":
    main()
