"""The D&T foreground Agent transport. The HTTP service never receives the customer's key."""
from contextlib import closing
from http.client import HTTPConnection, HTTPSConnection
import json
import re
from threading import Event, Thread
from time import monotonic
from urllib.parse import urlsplit

from interactive_agent_runtime import Denied, MAX_RESPONSE, emit

HOST = "token-plan.cn-beijing.maas.aliyuncs.com"
TARGET = "/compatible-mode/v1/chat/completions"
MODELS = ("qwen4-max", "qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus")
# Each provider issues its own Token Plan credentials; the common key prefix cannot select a host.
PROVIDERS = {
    "bailian": (HOST, MODELS),
    "qwen-china": ("token-plan.maas.qianwenaiapi.com",
                   ("qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus", "qwen3.6-flash")),
}
KEY_FORMAT = re.compile(r"sk-sp-[A-Za-z0-9_-]{12,180}")


def protocol_fixture_endpoint(value):
    if not isinstance(value, str):
        raise Denied("fixture_loopback_only")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise Denied("fixture_loopback_only") from None
    if (url.scheme != "http" or not port or url.netloc != f"127.0.0.1:{port}"
            or url.path not in {TARGET, "/v1/chat/completions"} or url.query or url.fragment):
        raise Denied("fixture_loopback_only")
    return port, url.path


class TokenPlan:
    def __init__(self, model, key, stopped, *, audit=None, fresh=False, combined=False,
                 provider="bailian", protocol_fixture=None):
        self.fixture = protocol_fixture_endpoint(protocol_fixture) if protocol_fixture is not None else None
        if provider not in PROVIDERS:
            raise Denied("unsupported_native_provider")
        self.host, models = PROVIDERS[provider]
        self.provider = provider
        if not self.fixture and (model not in models or not isinstance(key, str) or not KEY_FORMAT.fullmatch(key)):
            raise Denied("customer_config_invalid")
        if self.fixture and key is not None:
            raise Denied("customer_config_invalid")
        self.model = model
        self._key = key
        self.stopped = stopped
        self.audit = audit
        self.turns = 0
        self.max_turns = 5 if combined else 4 if fresh else 3
        self.until = monotonic() + (445 if fresh else 125)

    def __call__(self, messages, tools):
        if self.stopped.is_set() or monotonic() >= self.until or self.turns >= self.max_turns or (not self.fixture and not self._key):
            raise Denied("foreground_session_closed")
        self.turns += 1
        payload = {"model": self.model, "messages": messages, "tools": tools,
                   "tool_choice": "auto", "stream": False, "max_tokens": 1200}
        if self.provider == "qwen-china":
            payload.update(enable_thinking=False, parallel_tool_calls=False)
        body = json.dumps(payload).encode()
        if len(body) > 49152:
            raise Denied("model_history_too_large")
        try:
            target = TARGET
            headers = {"Content-Type": "application/json", "Connection": "close"}
            if self.fixture:
                port, target = self.fixture
                connector = HTTPConnection("127.0.0.1", port, timeout=22)
            else:
                connector = HTTPSConnection(self.host, 443, timeout=22)
                headers["Authorization"] = "Bearer " + self._key
            with closing(connector) as connection:
                if self.stopped.is_set():
                    raise Denied("foreground_session_closed")
                if self.audit is not None and not self.fixture:
                    self.audit("provider_transport_attempt", turn=self.turns)
                connection.request("POST", target, body, headers)
                response = connection.getresponse()
                if response.status != 200:
                    raise Denied({401:"provider_auth_rejected", 403:"provider_auth_rejected", 429:"provider_limit"}
                                 .get(response.status, "provider_response_rejected"))
                raw = response.read(MAX_RESPONSE + 1)
            if self.stopped.is_set():
                raise Denied("foreground_session_closed")
            if len(raw) > MAX_RESPONSE:
                raise ValueError()
            data = json.loads(raw)
            choices = data["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError()
            result = choices[0]
            message = result["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError()
            finish = result["finish_reason"]
            if self.audit is not None and not self.fixture:
                self.audit("provider_transport_response", turn=self.turns)
            return finish, message
        except Denied:
            raise
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            raise Denied("provider_response_rejected") from None

    def close(self):
        self.stopped.set()
        self._key = None


def from_customer(approved_copy, run, *, fresh=False):
    """A native, visible confirmation owned by the interactive process for this one task."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        raise Denied("desktop_required") from None
    try:
        window = tk.Tk()
    except tk.TclError:
        raise Denied("desktop_required") from None
    window.title("连接模型")
    window.resizable(False, False)
    window.attributes("-topmost", True)
    frame = ttk.Frame(window, padding=20)
    frame.grid(sticky="nsew")
    details = "" if fresh else ("抖音中国 · BAVA P01 v1 已审稿\n\n"
               "发送给所选服务的内容为下列已审正文、会话和任务版本标识、受限工具定义及其回传摘要。"
               "同一模型会话最多三次请求。原图、PNG 和 PSD 留在本机。"
               "所选个人版服务会将输入与输出用于服务改进与模型优化。\n\n"
               + approved_copy["title"] + "\n" + approved_copy["subtitle"])
    if fresh:
        combined = "detail_facts" in approved_copy
        title = "中国抖音 · 新商品海报、主图和商品详情页" if combined else "中国抖音 · 新商品海报 720×880"
        questions = {"fit":"谁能用？能用在哪儿？", "quality":"用什么做的？哪里有特点？",
                     "spec":"多大？一盒有几个？", "use":"怎么用？"}
        evidence = "\n".join(questions[key] + " " + fact
                             for key, fact in approved_copy.get("detail_facts",{}).items())
        max_requests = 5 if combined else 4
        details = (title + "\n\n"
                   "发送商品名和以下事实、会话任务版号、受限工具定义及回执摘要。原图、PNG 和 PSD 留在本机。"
                   f"当前只用本机图片排版；等待本人在原任务核对版式。最多 {max_requests} 次同一会话请求。"
                   "所选个人版服务会将输入与输出用于服务改进与模型优化。\n\n"
                   + approved_copy["product"] + "\n" + "\n".join(approved_copy["facts"])
                   + ("\n\n商品详情页信息\n" + evidence if combined else ""))
    ttk.Label(frame, text=details, wraplength=505, justify="left").grid(row=0, columnspan=2, sticky="w", pady=(0, 14))
    ttk.Label(frame, text="模型服务").grid(row=1, column=0, sticky="w", pady=5)
    provider = ttk.Combobox(frame, values=("阿里云百炼 · Token Plan",), state="readonly", width=30)
    provider.current(0)
    provider.grid(row=1, column=1, sticky="w", pady=5)
    ttk.Label(frame, text="本人的模型").grid(row=2, column=0, sticky="w", pady=5)
    choice = ttk.Combobox(frame, values=MODELS, state="readonly", width=30)
    choice.grid(row=2, column=1, sticky="w", pady=5)
    ttk.Label(frame, text="本人的 API Key").grid(row=3, column=0, sticky="w", pady=5)
    field = ttk.Entry(frame, width=32, show="*")
    field.grid(row=3, column=1, sticky="w", pady=5)
    status = ttk.Label(frame, text="选择模型并输入个人凭据后，可开始本次前台会话。")
    status.grid(row=4, columnspan=2, sticky="w", pady=8)
    stopped, finished = Event(), Event()
    outcome = {}
    running = False

    def start():
        nonlocal running
        if running:
            return
        key = field.get()
        field.delete(0, "end")
        try:
            transport = TokenPlan(choice.get(), key, stopped, audit=emit, fresh=fresh,
                                  combined=fresh and "detail_facts" in approved_copy)
        except Denied:
            status.configure(text="请选择本人的模型，并输入所选服务的有效个人凭据。")
            return
        running = True
        launch.configure(state="disabled")
        provider.configure(state="disabled")
        choice.configure(state="disabled")
        field.configure(state="disabled")
        if fresh:
            window.attributes("-topmost", False)
            status.configure(text="请回到工作台审校。关闭本机确认窗会结束会话。")
        else:
            status.configure(text="交互会话进行中。关闭此窗口即结束会话。")

        def interact():
            try:
                run(transport)
            except Denied as error:
                outcome["error"] = error
            except (OSError, TypeError, KeyError, ValueError, RecursionError):
                outcome["error"] = Denied("foreground_runtime_error")
            finally:
                transport.close()
                finished.set()

        Thread(target=interact, daemon=True).start()

    def tick():
        if finished.is_set():
            window.quit()
        else:
            window.after(100, tick)

    def stop():
        stopped.set()
        field.configure(state="normal")
        field.delete(0, "end")
        window.quit()

    launch = ttk.Button(frame, text="用此模型开始", command=start)
    launch.grid(row=5, column=0, sticky="w")
    ttk.Button(frame, text="关闭", command=stop).grid(row=5, column=1, sticky="e")
    window.protocol("WM_DELETE_WINDOW", stop)
    window.after(100, tick)
    window.after(100000, lambda: None if running else stop())
    choice.focus_set()
    try:
        window.mainloop()
    finally:
        stopped.set()
        window.destroy()
    if not finished.is_set():
        raise Denied("foreground_session_closed")
    if "error" in outcome:
        raise outcome["error"]
