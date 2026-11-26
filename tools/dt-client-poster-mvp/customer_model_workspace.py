"""Keep the local web service out of the credential and provider data path."""
from threading import RLock

from foreground_token_plan import PROVIDERS, protocol_fixture_endpoint
from interactive_agent_bridge import SessionError
from interactive_agent_runtime import Denied
from native_model_connection import NativeModelConnection
from prototype_models import WorkspaceError


PROVIDER_LABELS = {"bailian": "阿里云百炼（北京）", "qwen-china": "千问 AI 平台（中国）"}
PLAN = "token-personal"
FIXTURE_MODEL = "dt-preview-text"


class CustomerModelWorkspace:
    def __init__(self, fixture=None):
        self.fixture = fixture
        self.lock = RLock()
        self.connection = None

    @staticmethod
    def check_fixture(url):
        try:
            protocol_fixture_endpoint(url)
        except Denied:
            raise ValueError("A protocol substitute requires an exact local Chat Completions endpoint.") from None

    def snapshot(self):
        with self.lock:
            raw = self.connection.status() if self.connection else {}
            provider = raw.get("provider")
            model = raw.get("model_id")
            state = raw.get("state", "not_connected")
            state = {"connecting": "pending", "claimed": "consumed"}.get(state, state)
            if raw.get("reason") == "connection_expired":
                state = "expired"
            public = [{"id": key, "label": label,
                       "endpoint": f"https://{PROVIDERS[key][0]}/compatible-mode/v1",
                       "model_ids": list(PROVIDERS[key][1])}
                      for key, label in PROVIDER_LABELS.items()]
            candidates = [{"id": ident, "name": ident, "provider": row["id"], "tools": "official_reference"}
                          for row in public for ident in row["model_ids"]]
            return {"customer": True, "protocol_fixture": self.fixture is not None,
                    "provider": provider, "plan": PLAN,
                    "providers": public, "candidates": candidates, "account_access": "unverified",
                    "connection": {"model_id": model, "state": state, "reason": raw.get("reason")}}

    def connect(self, payload):
        provider = payload.get("provider")
        if (set(payload) != {"provider", "plan"} or not isinstance(provider, str)
                or provider not in PROVIDER_LABELS or payload["plan"] != PLAN):
            raise WorkspaceError("unsupported_customer_plan", "请选择受支持的供应商与个人套餐。Key 只在 Windows 本机窗输入。")
        with self.lock:
            if self.connection:
                previous = self.connection.status()
                if previous["state"] in {"connecting", "ready"}:
                    if previous["provider"] != provider:
                        raise WorkspaceError("native_connection_active", "请先关闭当前本机连接，再换供应商。")
                    return self.snapshot()
                self.connection.cancel()
            try:
                self.connection = NativeModelConnection(FIXTURE_MODEL if self.fixture else None,
                                                        provider=provider, protocol_fixture=self.fixture)
            except (OSError, SessionError):
                self.connection = None
                raise WorkspaceError("native_window_unavailable", "本机输入窗未能打开。请在此电脑的 Windows 桌面重试。") from None
            return self.snapshot()

    def claim(self):
        with self.lock:
            state = self.snapshot()["connection"]
            if not self.connection or state["state"] != "ready":
                raise WorkspaceError("native_connection_required", "请先在 Windows 本机窗连接自己的模型，再回到当前草稿。")
            try:
                return self.connection.claim(state["model_id"])
            except SessionError:
                raise WorkspaceError("native_connection_required", "本机连接已退出。原草稿已保存，请重新连接。") from None

    def disconnect(self, payload):
        if payload != {}:
            raise WorkspaceError("invalid_customer_disconnect", "关闭本机连接不需要任何凭据。")
        with self.lock:
            self.close()
            self.connection = None
            return self.snapshot()

    def close(self):
        with self.lock:
            if self.connection:
                self.connection.cancel()
