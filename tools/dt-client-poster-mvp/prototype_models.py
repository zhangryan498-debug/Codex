from copy import deepcopy
import json
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4


PURPOSES = ("language", "image", "video")
CAPABILITIES = {*PURPOSES, "vision"}
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}\Z")
WORKSPACE_ID = re.compile(r"llm-[a-z0-9]{4,60}\Z")
SECRET_TEXT = re.compile(r"(?:sk-(?:sp-|proj-)?[a-z0-9_-]{8,}|demo-local-)", re.I)
FIXTURE_KEYS = {
    "demo-local-ok": "ok",
    "demo-local-empty": "empty",
    "demo-local-denied": "denied",
    "demo-local-failed": "failed",
}

TOKEN_ENDPOINT = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
PROVIDERS = (
    {"id": "bailian", "label": "阿里云百炼（中国）", "plans": (
        {"id": "token-personal", "label": "Token Plan 个人版", "source": "official",
         "endpoint": TOKEN_ENDPOINT, "billing": "套餐 Credits，账户余量未核", "needs_workspace": False},
        {"id": "token-team", "label": "Token Plan 团队版", "source": "official",
         "endpoint": TOKEN_ENDPOINT, "billing": "团队套餐 Credits，账户余量未核", "needs_workspace": False},
        {"id": "payg", "label": "百炼按量（北京工作空间）", "source": "interface",
         "endpoint": "https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1/models",
         "billing": "推理可能按量计费，账单与额度未核", "needs_workspace": True},
    )},
    {"id": "openai", "label": "OpenAI", "plans": (
        {"id": "api", "label": "OpenAI API", "source": "interface",
         "endpoint": "https://api.openai.com/v1/models",
         "billing": "推理按 API 定价，账单与额度未核", "needs_workspace": False},
    )},
)
PLANS = {(provider["id"], plan["id"]): plan for provider in PROVIDERS for plan in provider["plans"]}

# Reference candidates, not an account's entitlements. Run availability below
# belongs solely to the local substitute, which is keyed by this allowlist.
TOKEN_COMMON = (
    ("qwen4-max", "Qwen4-Max", ("language", "vision")),
    ("qwen3.8-max", "Qwen3.8-Max", ("language", "vision")),
    ("qwen3.8-flash", "Qwen3.8-Flash", ("language", "vision")),
    ("qwen3.7-max", "Qwen3.7-Max", ("language",)),
    ("qwen3.7-plus", "Qwen3.7-Plus", ("language", "vision")),
)
TOKEN_REFERENCE = {
    "token-personal": (*TOKEN_COMMON, ("qwen-image-3.0-pro", "Qwen-Image 3.0 Pro", ("image",))),
    "token-team": (*TOKEN_COMMON, ("wan3.0-video", "Wan 3.0 Video", ("video",))),
}
BAILIAN_RESPONSE = {"code": None, "message": None, "success": True,
    "output": {"total": 4, "page_no": 1, "page_size": 20, "models": [
        {"model": "qwen3.8-max", "name": "Qwen3.8-Max", "capabilities": ["TG", "VU"],
         "features": ["function-calling"]},
        {"model": "qwen-image-3.0-pro", "name": "Qwen-Image 3.0 Pro", "capabilities": ["IG"],
         "features": []},
        {"model": "wan3.0-video", "name": "Wan 3.0 Video", "capabilities": ["VG"], "features": []},
        {"model": "qwen-audio-3.0-asr-flash", "name": "Qwen ASR", "capabilities": ["ASR"], "features": []},
    ]}}
OPENAI_RESPONSE = {"object": "list", "data": [
    {"object": "model", "id": "gpt-5-mini", "created": 1754000000, "owned_by": "openai", "shutdown_date": None},
    {"object": "model", "id": "gpt-image-1", "created": 1745000000, "owned_by": "openai", "shutdown_date": None},
    {"object": "model", "id": "sora-2", "created": 1759200000, "owned_by": "openai", "shutdown_date": None},
    {"object": "model", "id": "text-embedding-3-small", "created": 1706000000,
     "owned_by": "openai", "shutdown_date": None},
]}
OPENAI_REFERENCE = {
    "gpt-5-mini": (("language", "vision"), "catalog"),
    "gpt-image-1": (("image",), "unknown"),
    "sora-2": (("video",), "unknown"),
}
AUTHORIZED_MOCKS = {"bailian": {row[0] for row in TOKEN_COMMON}, "openai": {"gpt-5-mini"}}


class WorkspaceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def model(provider, ident, name, capabilities, tools, source, *, selectable=False):
    return {"id": ident, "name": name, "capabilities": list(capabilities),
            "tools": tools, "source": source,
            "runnable": (selectable and "language" in capabilities
                         and ident in AUTHORIZED_MOCKS[provider])}


def catalog(provider, plan):
    if plan in TOKEN_REFERENCE:
        return [model(provider, ident, name, flags, "catalog" if "language" in flags else "unknown",
                      "official", selectable=True)
                for ident, name, flags in TOKEN_REFERENCE[plan]]
    if provider == "bailian":
        mapping = {"TG": "language", "VU": "vision", "IG": "image", "VG": "video"}
        return [model(provider, row["model"], row["name"],
                      (flags := tuple(mapping[code] for code in row["capabilities"] if code in mapping)),
                      "catalog" if "function-calling" in row["features"] else "unknown",
                      "interface", selectable=True)
                for row in BAILIAN_RESPONSE["output"]["models"]]
    rows = []
    for row in OPENAI_RESPONSE["data"]:
        ident = row["id"]
        flags, tools = OPENAI_REFERENCE.get(ident, ((), "unknown"))
        rows.append(model(provider, ident, ident, flags, tools, "interface", selectable=True))
    return rows


def safe_model_id(value):
    return (isinstance(value, str) and MODEL_ID.fullmatch(value) and not SECRET_TEXT.search(value)
            and ".." not in value and not value.endswith("/") and "//" not in value
            and not value.startswith(("http:", "https:")))


class ModelWorkspace:
    def __init__(self, profile):
        self.path = Path(profile) / "prototype-model-workspace.json"
        self.lock = RLock()
        self.state = {"accounts": [], "assignments": dict.fromkeys(PURPOSES)}
        try:
            if self.path.exists():
                saved = json.loads(self.path.read_bytes())
                if (set(saved) != {"accounts", "assignments"} or not isinstance(saved["accounts"], list)
                        or set(saved["assignments"]) != set(PURPOSES)):
                    raise ValueError()
                self.state = saved
        except (OSError, ValueError, TypeError):
            raise WorkspaceError("workspace_read_failed", "本机模型配置无法读取。") from None

    def snapshot(self):
        with self.lock:
            return {"prototype": True,
                    "providers": [{**row, "plans": [dict(plan) for plan in row["plans"]]} for row in PROVIDERS],
                    **deepcopy(self.state), "vault": "session_only"}

    def chosen(self, purpose):
        if not isinstance(purpose, str) or purpose not in PURPOSES:
            raise WorkspaceError("invalid_purpose", "请选择语言、图片或视频用途。")
        with self.lock:
            link = self.state["assignments"][purpose]
            if link is None:
                return None
            account = self._account(self.state, link["account_id"])
            selected = self._model(account, link["model_id"])
            return deepcopy({**selected, **link, "provider": account["provider"], "plan": account["plan"],
                             "workspace": account["workspace"], "source": selected["source"]})

    def change(self, action, payload):
        operations = {"connect": self._connect, "manual": self._manual, "assign": self._assign, "remove": self._remove}
        if not isinstance(action, str) or action not in operations or not isinstance(payload, dict):
            raise WorkspaceError("invalid_action", "请选择有效的模型账户操作。")
        with self.lock:
            revised = deepcopy(self.state)
            operations[action](revised, payload)
            self._save(revised)
            self.state = revised
            return self.snapshot()

    def _save(self, revised):
        scratch = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with scratch.open("x", encoding="utf-8") as stream:
                json.dump(revised, stream, ensure_ascii=False, indent=2)
            scratch.replace(self.path)
        except (OSError, ValueError):
            raise WorkspaceError("workspace_save_failed", "本机模型配置保存失败。") from None
        finally:
            try:
                scratch.unlink(missing_ok=True)
            except OSError:
                pass

    def _connect(self, state, payload):
        if not set(payload) <= {"provider", "plan", "workspace", "label", "key", "account_id"}:
            raise WorkspaceError("invalid_connect", "模型账户连接字段不受支持。")
        provider, plan = payload.get("provider"), payload.get("plan")
        if not isinstance(provider, str) or not isinstance(plan, str) or (provider, plan) not in PLANS:
            raise WorkspaceError("invalid_plan", "请选择已支持的供应商和套餐组合。")
        config = PLANS[(provider, plan)]
        workspace = payload.get("workspace") or ""
        if (not isinstance(workspace, str) or
                (config["needs_workspace"] and not WORKSPACE_ID.fullmatch(workspace)) or
                (not config["needs_workspace"] and workspace)):
            raise WorkspaceError("invalid_workspace", "请填写此套餐格式正确的工作空间编号。")
        label = payload.get("label") or config["label"]
        if (not isinstance(label, str) or not label.strip() or len(label) > 64 or not label.isprintable()
                or SECRET_TEXT.search(label)):
            raise WorkspaceError("invalid_label", "请填写简短账户名，勿在名称中填入凭据。")
        value = payload.get("key")
        if not isinstance(value, str) and value is not None:
            raise WorkspaceError("demo_credential_only", "此原型只接受本机测试凭据，勿输入真实密钥。")
        if value not in (None, "") and value not in FIXTURE_KEYS:
            raise WorkspaceError("demo_credential_only", "此原型只接受本机测试凭据，勿输入真实密钥。")
        outcome = FIXTURE_KEYS.get(value)
        if config["source"] == "interface" and outcome is None:
            raise WorkspaceError("demo_credential_required", "读取模拟接口目录需要本机测试凭据。")
        if outcome == "denied":
            raise WorkspaceError("catalog_denied", "模拟接口拒绝读取模型目录，调用权限未验证。")
        if outcome == "failed":
            raise WorkspaceError("catalog_failed", "模拟接口未能返回模型目录。")
        if outcome == "empty" and config["source"] != "interface":
            raise WorkspaceError("catalog_unsupported", "此套餐使用官方参考表，没有账户目录接口。")
        models = [] if outcome == "empty" else catalog(provider, plan)
        existing = self._account(state, payload["account_id"]) if "account_id" in payload else None
        if existing and any(existing[k] != v for k, v in {"provider": provider, "plan": plan, "workspace": workspace}.items()):
            raise WorkspaceError("account_changed", "重新连接需要沿用原供应商、套餐和工作空间。请重新选择账户。")
        if existing:
            models += [row for row in existing["models"] if row["source"] == "manual"
                       and all(fresh["id"] != row["id"] for fresh in models)]
        status = "official_reference" if config["source"] == "official" else (
            "mock_empty" if outcome == "empty" else "mock_listed")
        updated = {"id": existing["id"] if existing else uuid4().hex, "label": label.strip(),
                   "provider": provider, "plan": plan, "workspace": workspace,
                   "source": config["source"], "models": models, "status": status}
        if not existing:
            state["accounts"].append(updated)
            return
        existing.update(updated)
        for purpose, link in state["assignments"].items():
            if link and link["account_id"] == updated["id"] and not any(
                    m["id"] == link["model_id"] and purpose in m["capabilities"]
                    and (purpose != "language" or m["runnable"]) for m in models):
                state["assignments"][purpose] = None

    def _manual(self, state, payload):
        if set(payload) != {"account_id", "model_id"}:
            raise WorkspaceError("invalid_manual", "请选择账户并填写模型编号。")
        account = self._account(state, payload["account_id"])
        ident = payload["model_id"]
        if not safe_model_id(ident):
            raise WorkspaceError("invalid_model_id", "模型编号格式不正确。")
        if any(row["id"] == ident for row in account["models"]):
            raise WorkspaceError("model_exists", "此账户已经包含该模型编号。")
        rows = catalog(account["provider"], account["plan"])
        known = next((row for row in rows if row["id"] == ident), None)
        flags, tools = (known["capabilities"], known["tools"]) if known else ((), "unknown")
        account["models"].append(model(account["provider"], ident, ident, flags, tools, "manual"))

    def _assign(self, state, payload):
        if not set(payload) <= {"purpose", "account_id", "model_id"}:
            raise WorkspaceError("invalid_assignment", "用途选择字段不受支持。")
        purpose = payload.get("purpose")
        if not isinstance(purpose, str) or purpose not in PURPOSES:
            raise WorkspaceError("invalid_purpose", "请选择语言、图片或视频用途。")
        account_id, model_id = payload.get("account_id"), payload.get("model_id")
        if account_id is None and model_id is None:
            if not {"account_id", "model_id"} & set(payload):
                raise WorkspaceError("invalid_assignment", "请明确选择模型或清除用途。")
            state["assignments"][purpose] = None
            return
        account = self._account(state, account_id)
        selected = self._model(account, model_id)
        if purpose not in selected["capabilities"]:
            raise WorkspaceError("model_capability_unknown", "该模型没有此用途的已知能力，暂不能分配。")
        if purpose == "language" and not selected["runnable"]:
            raise WorkspaceError("model_tools_unverified", "此模型的工具调用尚未验证，不能用于此预览。")
        state["assignments"][purpose] = {"account_id": account_id, "model_id": model_id}

    def _remove(self, state, payload):
        if set(payload) != {"account_id"}:
            raise WorkspaceError("invalid_remove", "请选择要移除的模型账户。")
        account = self._account(state, payload["account_id"])
        state["accounts"].remove(account)
        for purpose, value in state["assignments"].items():
            if value and value["account_id"] == account["id"]:
                state["assignments"][purpose] = None

    @staticmethod
    def _account(state, ident):
        account = next((row for row in state["accounts"] if row["id"] == ident), None)
        if account is None:
            raise WorkspaceError("account_not_found", "找不到所选模型账户。")
        return account

    @staticmethod
    def _model(account, ident):
        selected = next((row for row in account["models"] if row["id"] == ident), None)
        if selected is None:
            raise WorkspaceError("model_not_found", "找不到所选账户的模型。")
        return selected

