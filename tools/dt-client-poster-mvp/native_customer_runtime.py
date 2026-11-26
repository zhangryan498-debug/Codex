"""A customer-operated desktop process, with one task per in-memory model credential."""
from pathlib import Path
from queue import Empty, SimpleQueue
import sys
from threading import Event, Thread

sys.path.insert(0, str(Path(__file__).resolve().parent))

from foreground_token_plan import KEY_FORMAT, PROVIDERS, TokenPlan, protocol_fixture_endpoint
from fresh_poster_agent_runtime import exchange as exchange_new, validate_boot as validate_new
from foreground_agent_runtime import exchange as exchange_reviewed, validate_boot as validate_reviewed
from interactive_agent_runtime import Denied, ID, WORD, emit, read_line

SETUP_MS = 100_000
IDLE_SECONDS = 600
PROVIDER_NAMES = {"bailian": "阿里云百炼", "qwen-china": "千问AI平台（中国）"}


def desktop(title):
    try:
        import tkinter as tk
        from tkinter import ttk
        window = tk.Tk()
    except (ImportError, RuntimeError):
        raise Denied("desktop_required") from None
    except tk.TclError:
        raise Denied("desktop_required") from None
    window.title(title)
    window.resizable(False, False)
    window.attributes("-topmost", True)
    frame = ttk.Frame(window, padding=18)
    frame.grid(sticky="nsew")
    return window, frame, ttk


def configure(connection):
    window, frame, ttk = desktop("D&T 模型连接")
    model = connection["model_id"]
    simulated = connection["protocol_fixture"] is not None
    provider = connection["provider"]
    name = PROVIDER_NAMES[provider]
    catalogue = (model or "dt-preview-text",) if simulated else PROVIDERS[provider][1]
    description = ("本机协议测试 · 无需密钥，不连接云端" if simulated else
                   f"{name} · 个人 Token Plan\n"
                   "所列模型来自官方套餐目录，账号权限未验证。此步骤不发请求。")
    ttk.Label(frame, text=description, justify="left").grid(row=0, columnspan=2, sticky="w", padx=(0, 12))
    ttk.Label(frame, text="语言模型").grid(row=1, column=0, sticky="w", pady=8)
    selector = ttk.Combobox(frame, values=catalogue, state="readonly", width=28)
    selector.grid(row=1, column=1, sticky="ew", pady=8)
    if model is not None:
        selector.set(model)
        selector.configure(state="disabled")
    elif simulated:
        selector.current(0)
    if not simulated:
        ttk.Label(frame, text="我的个人 API Key").grid(row=2, column=0, sticky="w", pady=8)
        secret = ttk.Entry(frame, show="•", width=32)
        secret.grid(row=2, column=1, sticky="ew", pady=8)
    state = ttk.Label(frame, text="保存后，开始任务仍需本机确认。")
    state.grid(row=3, columnspan=2, sticky="w", pady=6)
    result = None

    def save():
        nonlocal result
        selected = selector.get()
        key = None if simulated else secret.get()
        if selected not in catalogue or (not simulated and not KEY_FORMAT.fullmatch(key)):
            state.configure(text="请选择语言模型，并核对个人套餐凭证的格式。")
            if not simulated:
                secret.delete(0, "end")
            return
        result = (selected, key)
        window.quit()

    ttk.Button(frame, text="本次连接", command=save).grid(row=4, column=0, sticky="w", pady=8)
    ttk.Button(frame, text="取消", command=window.quit).grid(row=4, column=1, sticky="e", pady=8)
    window.protocol("WM_DELETE_WINDOW", window.quit)
    window.after(SETUP_MS, window.quit)
    selector.focus_set()
    try:
        window.mainloop()
    finally:
        if not simulated:
            secret.delete(0, "end")
        window.destroy()
    if result is None:
        raise Denied("foreground_session_closed")
    return result


def one_task():
    queue = SimpleQueue()

    def read_task():
        try:
            queue.put(read_line())
        except (ValueError, OSError, TypeError) as error:
            queue.put(error)

    Thread(target=read_task, daemon=True).start()
    try:
        request = queue.get(timeout=IDLE_SECONDS)
    except Empty:
        raise Denied("connection_expired") from None
    if isinstance(request, Exception):
        raise Denied("foreground_session_closed")
    return request


def confirm(boot, model, key, fixture, fresh, provider):
    window, frame, ttk = desktop("D&T 确认模型请求")
    combined = "detail_facts" in boot
    maximum = 5 if combined else 4 if fresh else 3
    host = PROVIDERS[provider][0]
    destination = "本机协议替身（没有云请求）" if fixture else f"{PROVIDER_NAMES[provider]} · {host}"
    if fresh:
        facts = [boot["product"], *boot["facts"], *boot.get("detail_facts", {}).values()]
    else:
        facts = list(boot["approved_copy"].values())
    payload = ("本次会发送下方商品文字、任务／会话／版本标识、工具定义及同一会话的回执摘要。"
               "原图、PNG、PSD 和本机文件路径留在此设备。"
               f"最多 {maximum} 次请求。")
    if not fixture:
        payload += "个人套餐调用可能消耗 Credits，供应商可能使用本次的输入和输出改进服务。"
    text = (f"{destination}\n语言模型 {model}\n任务 {boot['task_id']} · v{boot['version']}\n\n"
            + payload + "\n\n" + "\n".join(facts))
    ttk.Label(frame, text=text, wraplength=580, justify="left").grid(row=0, columnspan=2, sticky="w")
    status = ttk.Label(frame, text="确认前不会发请求。")
    status.grid(row=1, columnspan=2, sticky="w", pady=8)
    stopped, finished = Event(), Event()
    outcome = []
    started = False

    def approve():
        nonlocal started
        if started:
            return
        started = True
        launch.configure(state="disabled")
        window.attributes("-topmost", False)
        status.configure(text="运行中。回到工作台审校，关闭本窗可结束此会话。")

        def run():
            transport = None
            try:
                transport = TokenPlan(model, key, stopped, audit=emit, fresh=fresh, combined=combined,
                                      provider=provider, protocol_fixture=fixture)
                exchange = exchange_new if fresh else exchange_reviewed
                exchange(boot, transport, simulated=bool(fixture))
            except Denied as error:
                outcome.append(error)
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                outcome.append(Denied("foreground_runtime_error"))
            finally:
                if transport is not None:
                    transport.close()
                finished.set()

        Thread(target=run, daemon=True).start()

    def tick():
        if finished.is_set():
            window.quit()
        else:
            window.after(100, tick)

    launch = ttk.Button(frame, text="确认并运行", command=approve)
    launch.grid(row=2, column=0, sticky="w", pady=8)
    ttk.Button(frame, text="取消", command=window.quit).grid(row=2, column=1, sticky="e", pady=8)
    window.protocol("WM_DELETE_WINDOW", window.quit)
    window.after(100, tick)
    window.after(SETUP_MS, lambda: None if started else window.quit())
    try:
        window.mainloop()
    finally:
        stopped.set()
        window.destroy()
    if not finished.is_set():
        raise Denied("foreground_session_closed")
    if outcome:
        raise outcome[0]


def main():
    connection = read_line()
    ident = connection.get("connection_id")
    if (set(connection) != {"event", "connection_id", "provider", "model_id", "protocol_fixture"}
            or connection["event"] != "configure" or not isinstance(connection["provider"], str)
            or connection["provider"] not in PROVIDERS
            or not isinstance(ident, str) or not ID.fullmatch(ident)):
        raise Denied("invalid_session_binding")
    provider = connection["provider"]
    fixture = connection["protocol_fixture"]
    if fixture is not None:
        protocol_fixture_endpoint(fixture)
    model = connection["model_id"]
    if model is not None and (not isinstance(model, str)
                              or (not WORD.fullmatch(model) if fixture is not None else model not in PROVIDERS[provider][1])):
        raise Denied("invalid_session_binding")
    try:
        selected, key = configure(connection)
        emit("configuration_ready", connection_id=ident, model_id=selected)
        task = one_task()
    except Denied as error:
        emit("connection_closed", connection_id=ident, code=str(error))
        return
    try:
        if (set(task) != {"event", "connection_id", "runtime", "boot"} or task["event"] != "task"
                or task["connection_id"] != ident or not isinstance(task["boot"], dict)
                or task["runtime"] not in {"fresh_poster_agent_runtime.py", "foreground_agent_runtime.py"}
                or (fixture is not None and task["runtime"] != "fresh_poster_agent_runtime.py")):
            raise Denied("invalid_session_binding")
        boot = task["boot"]
        fresh = task["runtime"] == "fresh_poster_agent_runtime.py"
        validate = validate_new if fresh else validate_reviewed
        validate(boot)
        confirm(boot, selected, key, fixture, fresh, provider)
    finally:
        key = None


if __name__ == "__main__":
    try:
        main()
    except Denied as error:
        emit("failed", code=str(error))
        sys.exit(1)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        emit("failed", code="foreground_runtime_error")
        sys.exit(1)
