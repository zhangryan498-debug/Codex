"""Device-local account selection with a native catalogue worker and scoped test routes."""
from contextlib import suppress
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from queue import Empty, SimpleQueue
import re
import subprocess
import sys
from threading import Lock, RLock, Thread
from urllib.parse import urlsplit
from uuid import uuid4

from model_catalog_windows import CatalogError, catalog_endpoint
from model_route_fixture import FixtureRouteError, dispatch_fixture
from provider_inference_ipc import read_media
from provider_inference_wire import InferenceWireError, TOKEN_PLAN_METHOD, TOKEN_PLAN_ORIGIN, supports_route
from prototype_models import WorkspaceError


APP = Path(__file__).resolve().parent
PURPOSES = ("language", "image", "video")
PUBLIC_ENV = {"APPDATA", "LOCALAPPDATA", "PATH", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "WINDIR"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:/._-]{0,127}")
KEY_LIKE = re.compile(r"(?:bearer\s|\bsk-[\w-]{8,}|\bakia[0-9a-z]{16}\b|\beyJ[A-Za-z0-9_-]+\.)", re.I)
FAILURES = {"denied", "network", "bad_response", "query_limit", "invalid_setup", "desktop_required",
            "unsupported", "connection_expired", "invalid_command", "worker_closed", "unsafe_endpoint",
            "credential_prompt_expired", "credential_launch_timeout", "token_plan_direct_disabled",
            "catalog_protocol_mismatch"}


def method(ident, label, protocol, catalog, detail, *, workspace=False, base=False, endpoint=None, reference=None):
    return {"id": ident, "label": label, "protocol": protocol, "catalog": catalog, "detail": detail,
            "workspace_required": workspace, "base_required": base, "endpoint": endpoint, "reference": [{"label": "官方目录与接入说明", "url": reference}] if reference else []}


PROVIDERS = [
    {"id": "openai", "label": "OpenAI", "methods": [
        method("api", "OpenAI API", "OpenAI", "supported", "查询账号可见目录。列表未提供能力分类，也未验证推理权限。",
               endpoint="https://api.openai.com/v1/models",
               reference="https://developers.openai.com/api/reference/resources/models/methods/list")]},
    {"id": "bailian", "label": "阿里云百炼", "methods": [
        method("workspace-payg", "北京业务空间 API", "百炼原生", "supported",
               "查询工作空间目录和供应商能力标注。推理授权仍待核。", workspace=True,
               endpoint="https://{业务空间ID}.cn-beijing.maas.aliyuncs.com/api/v1/models",
               reference="https://help.aliyun.com/zh/model-studio/list-models"),
        method("legacy-payg", "普通兼容地址 · 手动记录", "OpenAI 兼容", "unsupported",
               "这条连接方式未核实可查询的账号目录。可手动记录准确型号，实际调用仍待接入。",
               reference="https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope"),
        method("personal-token-plan", "Token Plan 个人版 · 北京", "OpenAI 兼容", "supported",
               "使用你的北京个人套餐。先在本机输入 Key，再获取模型；若目录不可读，可手动填入准确型号。此入口用于商品任务的语言和工具调用。",
               endpoint=TOKEN_PLAN_ORIGIN + "/compatible-mode/v1/models",
               reference="https://help.aliyun.com/zh/model-studio/more-tools")]},
    {"id": "qwen-cn", "label": "千问 AI 平台（中国）", "methods": [
        method("personal-token-plan", "Token Plan 个人版", "套餐", "blocked",
               "当前 D&T 自有进程直连未获适用确认，此方式已停用。",
               reference="https://platform.qianwenai.com/docs/developer-guides/clients-and-developer-tools/other-tools")]},
    {"id": "custom", "label": "自定义供应商", "methods": [
        method("openai-compatible", "OpenAI 兼容", "OpenAI 兼容", "supported",
               "用所填 HTTPS API 基础地址请求模型目录。部分对话兼容服务未实现目录。", base=True,
               reference="https://developers.openai.com/api/reference/resources/models/methods/list"),
        method("anthropic-compatible", "Anthropic 兼容", "Anthropic 兼容", "supported",
               "用所填 HTTPS API 基础地址请求模型目录。部分对话兼容服务未实现目录。", base=True,
               reference="https://platform.claude.com/docs/en/api/models/list")]},
]
METHODS = {(row["id"], mode["id"]): mode for row in PROVIDERS for mode in row["methods"]}



def checked_endpoint(provider, mode, workspace, base):
    try:
        return catalog_endpoint(provider, mode, workspace, base)
    except (CatalogError, ValueError) as error:
        messages = {
            "token_plan_direct_disabled": "这是 Token Plan 套餐地址，当前工作台不支持这种直连。",
            "catalog_protocol_mismatch": "所选协议与这个服务地址不匹配，请按服务商提供的协议和基础地址填写。",
        }
        code = str(error)
        if code in messages:
            raise WorkspaceError(code, messages[code]) from None
        raise WorkspaceError("invalid_catalog_endpoint", "请核对 HTTPS 基础地址或业务空间编号。") from None


def usable_id(value):
    return isinstance(value, str) and bool(ID.fullmatch(value)) and not KEY_LIKE.search(value) and "://" not in value


def fixture_url(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (ValueError, TypeError):
        raise ValueError("Catalog test requires an exact IPv4 loopback origin.") from None
    if (parsed.scheme != "http" or not port or parsed.netloc != f"127.0.0.1:{port}"
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Catalog test requires an exact IPv4 loopback origin.")
    return f"http://127.0.0.1:{port}"


class CatalogueChild:
    def __init__(self, account, fixture):
        self.scratch = TemporaryDirectory(prefix="dt-provider-")
        self.spool = Path(self.scratch.name).resolve()
        self.operation_gate = Lock()
        self.results = SimpleQueue()
        boot = "import os, runpy, sys; sys.path.insert(0, os.getcwd()); runpy.run_module('model_catalog_windows', run_name='__main__')"
        argv = [str(Path(sys._base_executable).resolve(strict=True)), "-I", "-B", "-c", boot,
                "--operation-spool", str(self.spool)]
        if fixture:
            argv.extend(["--fixture", fixture])
        self.proc = subprocess.Popen(argv, cwd=APP, env={k: v for k, v in os.environ.items() if k.upper() in PUBLIC_ENV},
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.events = SimpleQueue()
        self.credential = "pending"
        self.window_state = "opening" if fixture is None else "closed"
        self.opening_since = monotonic()
        self.catalog_state = "not_requested"
        self.catalog_source = None
        self.models = []
        self.code = None
        Thread(target=self._receive, daemon=True).start()
        setup = {k: account[k] for k in ("provider", "method", "workspace_id", "base_url") if account.get(k) is not None}
        try:
            self.send({"op": "configure", **setup})
        except OSError:
            self.close()
            raise

    def _receive(self):
        try:
            while True:
                line = self.proc.stdout.readline(1_048_577)
                if not line:
                    return
                if len(line) > 1_048_576:
                    raise ValueError
                frame = json.loads(line)
                queue = self.results if isinstance(frame, dict) and "operation_id" in frame else self.events
                queue.put(frame)
        except (OSError, ValueError, TypeError):
            self.events.put({"state": "error", "code": "bad_response"})

    def send(self, command):
        self.proc.stdin.write(json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def refresh(self):
        while True:
            try:
                frame = self.events.get_nowait()
            except Empty:
                break
            if not isinstance(frame, dict):
                frame = {"state": "error", "code": "bad_response"}
            kind = frame.get("state")
            if self.credential == "pending" and kind == "prompt_visible":
                self.window_state = "visible"
            elif self.credential == "pending" and kind == "ready":
                self.credential = "ready"
                self.window_state = "closed"
            elif self.credential == "pending" and kind == "cancelled":
                self.credential = "cancelled"
                self.window_state = "closed"
            elif self.credential == "ready" and self.catalog_state == "loading" and kind in {"listed", "empty"}:
                source = frame.get("source")
                self.catalog_source = {"workspace_catalog": "workspace", "fixture": "fixture"}.get(source, "account")
                self.models = clean_models(frame.get("models"), self.catalog_source)
                self.catalog_state = "listed" if self.models else "empty"
            elif kind == "error":
                code = frame.get("code")
                self.code = code if code in FAILURES else "bad_response"
                if self.credential == "pending" or self.code == "connection_expired":
                    self.credential = "failed"
                    self.window_state = "closed"
                    self.models = []
                self.catalog_state = self.code if self.code in {"denied", "unsupported"} else "failed"
        if self.window_state == "opening" and monotonic() - self.opening_since >= 12 and self.proc.poll() is None:
            self.code = "credential_launch_timeout"
            self.close()
        if self.proc.poll() is not None and self.credential in {"pending", "ready"}:
            self.credential = "failed"
            self.window_state = "closed"
            self.catalog_state = "failed"
            self.code = self.code or "worker_closed"
            self.models = []

    def lookup(self):
        if self.operation_gate.locked():
            raise WorkspaceError("account_busy", "此账户正在处理一个请求。")
        self.refresh()
        if self.credential != "ready":
            raise WorkspaceError("native_key_required", "请先在 Windows 本机窗完成输入。")
        if self.catalog_state == "loading":
            return
        self.code = None
        self.catalog_source = None
        self.models = []
        self.catalog_state = "loading"
        try:
            self.send({"op": "catalog"})
        except OSError:
            self.catalog_state = "failed"
            self.credential = "failed"
            self.code = "worker_closed"


    def operate(self, operation, model_id, payload):
        ident = uuid4().hex
        relative = self.spool / (ident + ".bin")
        try:
            self.send({"op": "operate", "id": ident, "operation": operation,
                       "model_id": model_id, "payload": payload})
            deadline = monotonic() + 150
            while monotonic() < deadline:
                try:
                    frame = self.results.get(timeout=0.25)
                except Empty:
                    if self.proc.poll() is not None:
                        break
                    continue
                if frame.get("operation_id") != ident:
                    continue
                if "error" in frame:
                    raise InferenceWireError(frame["error"])
                result = frame["result"]
                if "media_size" in result:
                    result["media"] = read_media(self.spool, ident, result)
                return result
            raise InferenceWireError("worker_unavailable")
        except (OSError, KeyError, TypeError, ValueError):
            raise InferenceWireError("worker_unavailable") from None
        finally:
            with suppress(OSError):
                relative.unlink(missing_ok=True)
            self.operation_gate.release()

    def close(self):
        with suppress(OSError, ValueError):
            self.proc.stdin.close()
        if self.proc.poll() is None:
            with suppress(OSError):
                self.proc.kill()
            with suppress(OSError, subprocess.TimeoutExpired):
                self.proc.wait(timeout=2)
        with suppress(OSError):
            self.scratch.cleanup()


def clean_models(raw, source):
    result = []
    seen = set()
    if not isinstance(raw, list):
        return result
    for row in raw:
        if not isinstance(row, dict) or not usable_id(row.get("id")) or row["id"] in seen:
            continue
        caps = row.get("capabilities")
        caps = sorted({c for c in caps if isinstance(c, str) and c in (*PURPOSES, "vision", "tools")}) if isinstance(caps, list) else []
        seen.add(row["id"])
        result.append({"id": row["id"], "source": "workspace" if source == "workspace" else "account",
                       "capabilities": caps, "permission": "unverified",
                       "capability_source": "official_reference" if caps and row.get("capability_source") == "official_reference" else
                       "provider_metadata" if caps else "unknown"})
    return result


class ProviderModelWorkspace:
    def __init__(self, profile, fixture=None):
        self.path = profile / "provider-model-assignment.json"
        self.fixture = fixture_url(fixture) if fixture is not None else None
        self.lock = RLock()
        self.children = {}
        self.state = {"accounts": [], "assignments": dict.fromkeys(PURPOSES)}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self):
        temp = self.path.with_name(f".{self.path.name}-{uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)

    def account(self, ident):
        found = next((a for a in self.state["accounts"] if a["id"] == ident), None)
        if found is None:
            raise WorkspaceError("account_not_found", "请先选择现有本机账户。")
        return found

    def public_account(self, account):
        provider, mode = account["provider"], account["method"]
        configured = METHODS[provider, mode]
        child = self.children.get(account["id"])
        if child:
            child.refresh()
        models = {m["id"]: m.copy() for m in child.models} if child else {}
        for ident, caps in account["manual"].items():
            if ident not in models:
                models[ident] = {"id": ident, "capabilities": [], "source": "manual", "permission": "unverified",
                                 "capability_source": "manual"}
            models[ident]["manual_purposes"] = caps.copy()
        endpoint = configured.get("endpoint")
        problem = None
        if configured["catalog"] == "supported":
            try:
                endpoint = checked_endpoint(provider, mode, account["workspace_id"], account["base_url"])
            except WorkspaceError as error:
                endpoint, problem = None, error.code
        return {**{k: v for k, v in account.items() if k != "manual"}, "models": list(models.values()),
                "endpoint": endpoint,
                "credential": "failed" if problem else child.credential if child else "not_connected",
                "window_state": child.window_state if child and not problem else "closed",
                "catalog_state": "failed" if problem else child.catalog_state if child else
                "unsupported" if configured["catalog"] == "unsupported" else "not_requested",
                "catalog_source": child.catalog_source if child else None, "code": problem or (child.code if child else None)}

    def assignment(self, purpose, accounts):
        pick = self.state["assignments"].get(purpose)
        if not pick:
            return None
        account = next((a for a in accounts if a["id"] == pick["account_id"]), None)
        if not account:
            return None
        model = next((m for m in account["models"] if m["id"] == pick["model_id"]), None)
        caps = model["capabilities"] if model else []
        token_plan = (account["provider"], account["method"]) == TOKEN_PLAN_METHOD
        supported = supports_route(account["provider"], account["method"], purpose, pick["model_id"])
        if token_plan:
            ready = model is not None and supported and account["credential"] == "ready"
            return {**pick, "provider": account["provider"], "method": account["method"], "label": account["label"],
                    "state": "ready" if ready else "credential_required" if account["credential"] != "ready" else
                    "capability_unverified", "source": model["source"] if model else pick.get("source", "manual"),
                    "execution": ("token_plan_visual_fixture" if self.fixture else "token_plan_visual_ready") if ready else "not_wired"}
        proven = purpose in caps and (purpose != "language" or "tools" in caps)
        state = "ready" if proven else "capability_unverified" if model else "catalog_required"
        if account["credential"] != "ready" and account["catalog_state"] != "unsupported":
            state = "credential_required"
        if account["catalog_state"] not in {"listed", "empty", "unsupported"} and state == "capability_unverified":
            state = "catalog_required"
        execution = "not_wired"
        if state == "ready":
            if supported:
                execution = "provider_fixture_ready" if self.fixture else "provider_ready"
            elif self.fixture:
                execution = "fixture_ready"
        return {**pick, "provider": account["provider"], "method": account["method"], "label": account["label"],
                "state": state, "source": model["source"] if model else pick.get("source", "manual"),
                "execution": execution}

    def snapshot(self):
        with self.lock:
            accounts = [self.public_account(a) for a in self.state["accounts"]]
            return {"workspace": "provider-catalog-v2", "fixture": bool(self.fixture), "providers": PROVIDERS,
                    "accounts": accounts, "assignments": {p: self.assignment(p, accounts) for p in PURPOSES}}

    def open(self, payload):
        if set(payload) == {"account_id"}:
            account = self.account(payload["account_id"])
        else:
            if set(payload) - {"provider", "method", "label", "workspace_id", "base_url"}:
                raise WorkspaceError("invalid_account", "网页只能提交供应商和账户设置。")
            provider, mode = payload.get("provider"), payload.get("method")
            if not isinstance(provider, str) or not isinstance(mode, str) or (provider, mode) not in METHODS:
                raise WorkspaceError("invalid_account", "请先选择供应商与连接方式。")
            config = METHODS[provider, mode]
            if config["catalog"] == "blocked":
                raise WorkspaceError("token_plan_direct_disabled", "此 Token Plan 直连方式已停用。")
            label = payload.get("label") or config["label"]
            if not isinstance(label, str) or not 1 <= len(label) <= 64 or not label.isprintable() or KEY_LIKE.search(label):
                raise WorkspaceError("invalid_account_label", "请输入简短账户名称，勿在名称中填写 Key。")
            workspace = payload.get("workspace_id") or None
            base = payload.get("base_url") or None
            if (bool(workspace) != config["workspace_required"] or bool(base) != config["base_required"]
                    or (workspace and not isinstance(workspace, str))
                    or (base and (not isinstance(base, str) or KEY_LIKE.search(base)))):
                raise WorkspaceError("invalid_catalog_endpoint", "请填写这一方式对应的业务空间或 HTTPS API 基础地址。")
            row = {"provider": provider, "method": mode, "label": label.strip(), "workspace_id": workspace, "base_url": base}
            if config["catalog"] == "supported":
                checked_endpoint(provider, mode, workspace, base)
            account = next((a for a in self.state["accounts"] if all(a[k] == v for k, v in row.items())), None)
            if account is None:
                account = {"id": uuid4().hex, **row, "manual": {}}
                self.state["accounts"].append(account)
                self.save()
        conf = METHODS[account["provider"], account["method"]]
        if conf["catalog"] == "unsupported":
            return
        if conf["catalog"] != "supported":
            raise WorkspaceError("token_plan_direct_disabled", "此 Token Plan 直连方式已停用。")
        checked_endpoint(account["provider"], account["method"], account["workspace_id"], account["base_url"])
        for ident, child in self.children.items():
            child.refresh()
            if ident == account["id"] and child.credential == "ready":
                return
            if child.credential == "pending":
                if ident == account["id"]:
                    return
                raise WorkspaceError("native_window_active", "请先完成或关闭已有的 Windows 本机输入窗。")
        self.close_account(account["id"])
        try:
            self.children[account["id"]] = CatalogueChild(account, self.fixture)
        except OSError:
            raise WorkspaceError("native_window_unavailable", "无法在此 Windows 桌面打开本机输入窗。") from None

    def close_account(self, ident, *, shutting_down=False):
        child = self.children.get(ident)
        if child and child.operation_gate.locked() and not shutting_down:
            raise WorkspaceError("account_busy", "此账户仍有任务请求，请等待任务回执。")
        child = self.children.pop(ident, None)
        if child:
            child.close()

    def mutate(self, action, payload):
        with self.lock:
            if action == "open":
                self.open(payload)
            elif action in {"catalog", "close", "remove"} and set(payload) == {"account_id"}:
                account = self.account(payload["account_id"])
                if action == "catalog":
                    child = self.children.get(account["id"])
                    if child is None:
                        raise WorkspaceError("native_key_required", "此方式需先完成本机输入，或按页面提示手工记录。")
                    child.lookup()
                else:
                    self.close_account(account["id"])
                    if action == "remove":
                        self.state["accounts"].remove(account)
                        for p, pick in self.state["assignments"].items():
                            if pick and pick["account_id"] == account["id"]:
                                self.state["assignments"][p] = None
                        self.save()
            elif action in {"manual", "assign"} and set(payload) == {"account_id", "model_id", "purpose"}:
                self.select_model(action, payload)
            else:
                raise WorkspaceError("invalid_model_action", "请按当前页面的模型操作重试。")
            return self.snapshot()

    def select_model(self, action, payload):
        purpose = payload["purpose"]
        ident = payload["model_id"]
        if purpose not in PURPOSES:
            raise WorkspaceError("invalid_purpose", "请选择语言、生图或视频用途。")
        if action == "assign" and payload["account_id"] is None and ident is None:
            self.state["assignments"][purpose] = None
            self.save()
            return
        account = self.account(payload["account_id"])
        if not usable_id(ident):
            raise WorkspaceError("invalid_model_id", "请填写准确模型 ID，勿填写地址或密钥。")
        rows = self.public_account(account)["models"]
        row = next((m for m in rows if m["id"] == ident), None)
        if ((account["provider"], account["method"]) == TOKEN_PLAN_METHOD
                and not supports_route(account["provider"], account["method"], purpose, ident)):
            raise WorkspaceError("unsupported_route", "北京 Token Plan 在此页仅支持商品任务使用的已适配语言模型。")
        if action == "manual":
            options = account["manual"].setdefault(ident, [])
            if purpose not in options:
                options.append(purpose)
        else:
            if not row or purpose not in row["capabilities"] + row.get("manual_purposes", []):
                raise WorkspaceError("model_purpose_unknown", "当前目录未确认此用途。可按准确 ID 手动记录并保留待验状态。")
            self.state["assignments"][purpose] = {"account_id": account["id"], "model_id": ident, "source": row["source"]}
        self.save()


    def operation_child(self, purpose, account_id, model_id, *, continuation=False, visual_session=False):
        with self.lock:
            account = self.account(account_id)
            token_plan = (account["provider"], account["method"]) == TOKEN_PLAN_METHOD
            if token_plan and (not visual_session or continuation):
                raise WorkspaceError("token_plan_interactive_only", "请在商品任务中本人确认开始，套餐仅用于本次交互。")
            if not supports_route(account["provider"], account["method"], purpose, model_id):
                raise WorkspaceError("unsupported_route", "该连接方式与模型没有可用的任务适配。")
            if not continuation:
                selected = self.snapshot()["assignments"].get(purpose)
                enabled = {"token_plan_visual_ready", "token_plan_visual_fixture"} if token_plan else {"provider_ready", "provider_fixture_ready"}
                if (not selected or selected["account_id"] != account_id or selected["model_id"] != model_id
                        or selected["execution"] not in enabled):
                    raise WorkspaceError("selection_changed", "当前用途的账户或模型已变更，或连接尚未就绪。")
            child = self.children.get(account_id)
            if child is None:
                raise WorkspaceError("native_key_required", "请在模型连接中重新连接此账户。")
            child.refresh()
            if child.credential != "ready":
                raise WorkspaceError("native_key_required", "请在模型连接中重新连接此账户。")
            if child.catalog_state == "loading" or not child.operation_gate.acquire(blocking=False):
                raise WorkspaceError("account_busy", "此账户正在处理一个请求，请稍后返回本任务。")
            return child

    def dispatch(self, payload):
        if not self.fixture or set(payload) != {"purpose"} or payload["purpose"] not in PURPOSES:
            raise WorkspaceError("fixture_only", "此入口仅用于本机替身预检。请从已保存的任务发起普通 API 调用。")
        with self.lock:
            purpose = payload["purpose"]
            snapshot = self.snapshot()
            selected = snapshot["assignments"][purpose]
            if not selected or selected["execution"] != "fixture_ready":
                raise WorkspaceError("purpose_unavailable", "所选用途没有已验证的本机替身路线。")
            index = {a["id"]: {"fixture_origin": self.fixture, "models": {m["id"]: m["capabilities"] for m in a["models"]}}
                     for a in snapshot["accounts"] if a["credential"] == "ready"}
        fake = {"prompt": "Synthetic preview only"}
        if purpose == "language":
            fake = {"messages": [{"role": "user", "content": "Synthetic preview only"}],
                    "tools": [{"type": "function", "function": {"name": "synthetic_check", "parameters": {"type": "object"}}}]}
        try:
            return dispatch_fixture(fixture_mode=True, accounts=index, selection=selected, purpose=purpose, payload=fake)
        except FixtureRouteError:
            raise WorkspaceError("fixture_route_failed", "所选本机替身路线失败。已停止，未切换账户或模型。") from None

    def close(self):
        with self.lock:
            for ident in list(self.children):
                self.close_account(ident, shutting_down=True)
