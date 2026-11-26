"""Single-owner local link to the Windows credential dialog. The key never crosses this IPC."""
import json
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import subprocess
import sys
from threading import RLock, Thread
from uuid import uuid4

from foreground_token_plan import PROVIDERS, protocol_fixture_endpoint
from interactive_agent_bridge import APP, ENV_ALLOW, SessionError
from interactive_agent_runtime import MAX_RECORD, WORD


class NativeModelConnection:
    def __init__(self, model_id=None, *, provider="bailian", protocol_fixture=None):
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise SessionError("unsupported_native_provider")
        self.provider = provider
        self._models = PROVIDERS[provider][1]
        self.fixture = protocol_fixture is not None
        if protocol_fixture is not None:
            protocol_fixture_endpoint(protocol_fixture)
        if model_id is not None and (
            not isinstance(model_id, str)
            or (not WORD.fullmatch(model_id) if self.fixture else model_id not in self._models)
        ):
            raise SessionError("unsupported_native_model")
        self._gate = RLock()
        self._state = "connecting"
        self._reason = None
        self._preferred_model = model_id
        self._model = None
        self.selected_model = None
        self.connection_id = uuid4().hex
        self.frames = SimpleQueue()
        self.proc = None
        try:
            python = Path(sys._base_executable).resolve(strict=True)
            self.proc = subprocess.Popen(
                [str(python), "-I", "-B", str(APP / "native_customer_runtime.py")],
                cwd=APP, env={key: value for key, value in os.environ.items() if key.upper() in ENV_ALLOW},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            Thread(target=self._receive, daemon=True).start()
            self.send({"event": "configure", "connection_id": self.connection_id, "model_id": model_id,
                       "provider": provider, "protocol_fixture": protocol_fixture})
        except (OSError, ValueError, subprocess.SubprocessError):
            self.finish()
            self._state = "failed"
            self._reason = "desktop_required"

    def _receive(self):
        try:
            while True:
                line = self.proc.stdout.readline(MAX_RECORD + 1)
                self.frames.put(line)
                if not line or len(line) > MAX_RECORD:
                    return
        except (OSError, ValueError):
            self.frames.put(b"")

    def send(self, document):
        raw = json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode()
        if len(raw) >= MAX_RECORD:
            raise SessionError("ipc_message_too_large")
        self.proc.stdin.write(raw + b"\n")
        self.proc.stdin.flush()

    def _refresh(self):
        while self._state in {"connecting", "ready"}:
            try:
                raw = self.frames.get_nowait()
            except Empty:
                return
            try:
                if not raw.endswith(b"\n") or len(raw) > MAX_RECORD:
                    raise ValueError
                event = json.loads(raw)
                if event.get("connection_id") != self.connection_id:
                    raise ValueError
                if self._state == "connecting" and set(event) == {"connection_id", "event", "model_id"}:
                    selected = event["model_id"]
                    if (event["event"] != "configuration_ready" or not isinstance(selected, str)
                            or (not WORD.fullmatch(selected) if self.fixture else selected not in self._models)
                            or (self._preferred_model is not None and selected != self._preferred_model)):
                        raise ValueError
                    self._model = self.selected_model = selected
                    self._state = "ready"
                    continue
                if event.get("event") == "connection_closed" and set(event) == {"connection_id", "event", "code"}:
                    reason = event["code"]
                    if reason in {"foreground_session_closed", "connection_expired"}:
                        self.finish()
                        self._state = "cancelled"
                        self._reason = reason
                        return
                    if reason == "desktop_required":
                        self.finish()
                        self._state = "failed"
                        self._reason = reason
                        return
                raise ValueError
            except (ValueError, TypeError, AttributeError, KeyError):
                self.finish()
                self._state = "failed"
                self._reason = "native_connection_closed"

    def status(self):
        with self._gate:
            self._refresh()
            return {"state": self._state, "model_id": self._model, "reason": self._reason,
                    "connection_id": self.connection_id, "provider": self.provider}

    def claim(self, expected_model):
        with self._gate:
            self._refresh()
            if self._state != "ready":
                raise SessionError("native_connection_not_ready")
            if expected_model != self._model:
                raise SessionError("native_model_mismatch")
            self._state = "claimed"
            return self

    def finish(self):
        with self._gate:
            if self.proc is not None:
                if self.proc.poll() is None:
                    self.proc.kill()
                    self.proc.wait(timeout=4)
                self.proc.stdin.close()
                self.proc.stdout.close()
            self._state = "consumed"

    def cancel(self):
        with self._gate:
            self._refresh()
            if self._state not in {"consumed", "cancelled", "failed"}:
                self.finish()
                self._state = "cancelled"
                self._reason = "foreground_session_closed"
