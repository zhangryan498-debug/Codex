"""Exercise the official wire shapes using an in-process loopback test double."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest
from unittest.mock import MagicMock, patch

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools' / 'dt-client-poster-mvp'))

from model_catalog_windows import CatalogError, prompt_credential, read_setup, validate_credential
from provider_inference_wire import (
    CHAT_PATH, IMAGE_PATH, TASK_PATH, VIDEO_PATH, InferenceWireError, dispatch_inference,
    supports_route,
)


PNG = bytes.fromhex("89504e470d0a1a0a") + b"synthetic-png-bytes"
MP4 = bytes.fromhex("000000186674797069736f6d") + b"synthetic-mp4-bytes"
TOOL = {"type": "function", "function": {"name": "selling_points", "parameters": {"type": "object"}}}
CALL = {"id": "call_fixture", "type": "function",
        "function": {"name": "selling_points", "arguments": "{}"}}


class CredentialInputTests(unittest.TestCase):
    def test_personal_plan_setup_accepts_prefixed_and_unprefixed_synthetic_keys(self):
        provider, method, endpoint = read_setup({
            "op": "configure", "provider": "bailian", "method": "personal-token-plan",
        })
        self.assertEqual(endpoint, "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/models")
        for value in ("sk-sp-local-test-only", "unprefixed-local-test-only"):
            with self.subTest(value=value):
                self.assertEqual(validate_credential(provider, method, f" {value}\n"), value)

    def test_native_personal_plan_submit_accepts_a_synthetic_plan_key(self):
        provider, method, endpoint = read_setup({
            "op": "configure", "provider": "bailian", "method": "personal-token-plan",
        })
        toolkit = MagicMock()
        toolkit.ttk.Entry.return_value.get.return_value = "sk-sp-local-test-only"

        def submit():
            for call in toolkit.ttk.Button.call_args_list:
                if call.kwargs["text"] == "在本次会话保留":
                    call.kwargs["command"]()
                    return
            self.fail("Missing native submit action")

        toolkit.Tk.return_value.mainloop.side_effect = submit
        with patch.dict(sys.modules, {"tkinter": toolkit}), patch("model_catalog_windows.input_desktop"), \
                patch("model_catalog_windows.emit") as notify:
            self.assertEqual(prompt_credential(provider, method, endpoint), "sk-sp-local-test-only")
            notify.assert_called_once_with({"state": "prompt_visible"})

    def test_personal_plan_key_is_rejected_on_other_routes(self):
        for provider, method in (("bailian", "workspace-payg"), ("openai", "api"),
                                 ("custom", "openai-compatible"), ("qwen-cn", "personal-token-plan")):
            with self.subTest(provider=provider, method=method):
                with self.assertRaisesRegex(CatalogError, "^credential_plan_mismatch$"):
                    validate_credential(provider, method, "sk-sp-local-test-only")

    def test_invalid_format_is_rejected_on_personal_plan(self):
        with self.assertRaisesRegex(CatalogError, "^invalid_credential_format$"):
            validate_credential("bailian", "personal-token-plan", "invalid key format")


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _reply(self, http_status, body, mime="application/json", headers=None, *, extra_bytes=0):
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        self.send_response(http_status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body) + extra_bytes))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body=None):
        self.server.requests.append((self.command, self.path, dict(self.headers.items()), body))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._record(body)
        if self.path == CHAT_PATH:
            complete = body["messages"][-1]["role"] == "tool"
            message = {"role": "assistant", "content": "Done" if complete else None,
                       "tool_calls": None if complete else [CALL]}
            self._reply(200, {"id": "chatcmpl-synthetic", "choices": [{
                "finish_reason": "stop" if complete else "tool_calls", "message": message}]})
            return
        if self.path == IMAGE_PATH:
            self._reply(200, {"request_id": "img-synthetic", "output": {"finished": True, "choices": [{
                "finish_reason": "stop", "message": {"role": "assistant", "content": [
                    {"type": "image", "image": self.server.origin + "/synthetic.png"}]}}]}})
            return
        if self.path == VIDEO_PATH:
            prompt = body["input"]["prompt"]
            if prompt == "dropped":
                self.close_connection = True
            elif prompt == "truncated":
                self._reply(200, {"output": {"task_id": "fixture-job", "task_status": "PENDING"}}, extra_bytes=9)
            elif prompt == "bad-json":
                self._reply(200, b"{")
            elif prompt == "gateway-error":
                self._reply(503, {"message": "sensitive data ignored"})
            elif prompt == "denied":
                self._reply(403, {"message": "sensitive data ignored"})
            elif prompt == "limit":
                self._reply(429, {"message": "sensitive data ignored"})
            else:
                self._reply(200, {"request_id": "submit-synthetic",
                                  "output": {"task_id": "fixture-job", "task_status": "PENDING"}})
            return
        self._reply(404, {})

    def do_GET(self):
        self._record()
        if self.path.startswith(TASK_PATH):
            requested = self.path.removeprefix(TASK_PATH)
            output = dict(task_id=requested, task_status="SUCCEEDED", video_url=self.server.origin + "/synthetic.mp4")
            if requested == "fixture-running":
                output["task_status"] = "RUNNING"
            elif requested == "fixture-failed":
                output["task_status"] = "FAILED"
                output["message"] = "sensitive data ignored"
            elif requested == "fixture-unknown":
                output["task_status"] = "UNKNOWN"
            elif requested == "fixture-mismatch":
                output["task_id"] = "different-job"
            elif requested == "fixture-redirect":
                output["video_url"] = self.server.origin + "/redirect.mp4"
            elif requested == "fixture-cross-origin":
                output["video_url"] = "https://example.invalid/never-request"
            elif requested == "fixture-bad-asset":
                output["video_url"] = self.server.origin + "/invalid.mp4"
            elif requested == "fixture-truncated-asset":
                output["video_url"] = self.server.origin + "/truncated.mp4"
            self._reply(200, {"request_id": "status-synthetic", "output": output})
        elif self.path == "/synthetic.mp4":
            self._reply(200, MP4, "video/mp4")
        elif self.path == "/synthetic.png":
            self._reply(200, PNG, "application/octet-stream")
        elif self.path == "/redirect.mp4":
            self._reply(302, b"", headers={"Location": self.server.origin + "/synthetic.mp4"})
        elif self.path == "/truncated.mp4":
            self._reply(200, MP4, "video/mp4", extra_bytes=9)
        elif self.path == "/invalid.mp4":
            self._reply(200, b"invalid", "video/mp4")
        else:
            self._reply(404, {})


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.server.origin = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def setUp(self):
        self.server.requests = []

    def invoke(self, operation, ident, payload, *, method="workspace-payg"):
        return dispatch_inference("bailian", method, "fixture-workspace", None, operation, ident, payload,
                                  fixture=self.server.origin)

    def test_route_identity_and_fixture_never_accepts_key(self):
        self.assertTrue(supports_route("bailian", "workspace-payg", "language", "qwen-plus"))
        self.assertTrue(supports_route("bailian", "workspace-payg", "video", "wan2.7-t2v-2026-06-12"))
        self.assertFalse(supports_route("bailian", "workspace-payg", "video", "wan2.6-t2v"))
        with self.assertRaisesRegex(InferenceWireError, "^unsupported_route$"):
            self.invoke("image", "wan2.6-t2i", {"prompt": "test"}, method="token-plan")
        with self.assertRaisesRegex(InferenceWireError, "^invalid_fixture$"):
            dispatch_inference("bailian", "workspace-payg", "fixture-workspace", "x" * 8, "image",
                               "wan2.6-t2i", {"prompt": "test"}, fixture=self.server.origin)
        with self.assertRaisesRegex(InferenceWireError, "^invalid_fixture$"):
            dispatch_inference("bailian", "workspace-payg", "fixture-workspace", None, "image",
                               "wan2.6-t2i", {"prompt": "test"}, fixture="http://localhost:80")
        with self.assertRaisesRegex(InferenceWireError, "^credential_required$"):
            dispatch_inference("bailian", "workspace-payg", "fixture-workspace", None, "image",
                               "wan2.6-t2i", {"prompt": "test"})
        self.assertEqual(self.server.requests, [])

    def test_language_tool_roundtrip_and_nullable_tools(self):
        messages = [{"role": "user", "content": "Approved synthetic item facts"}]
        selection = self.invoke("language", "qwen-plus", {"messages": messages, "tools": [TOOL]})
        self.assertEqual(selection["message"]["tool_calls"], [CALL])
        self.assertEqual(selection["request_id"], "chatcmpl-synthetic")
        tool_result = {"role": "tool", "tool_call_id": CALL["id"], "content": '{"result":"synthetic"}'}
        messages.extend([selection["message"], tool_result])
        result = self.invoke("language", "qwen-plus", {"messages": messages, "tools": [TOOL]})
        self.assertEqual(result["message"], {"role": "assistant", "content": "Done"})
        self.assertEqual([request[1] for request in self.server.requests], [CHAT_PATH, CHAT_PATH])
        first, next_round = (request[3] for request in self.server.requests)
        self.assertEqual((first["model"], first["tool_choice"], first["tools"]), ("qwen-plus", "auto", [TOOL]))
        self.assertEqual(next_round["messages"][-2:], [selection["message"], tool_result])
        self.assertTrue(all("Authorization" not in request[2] for request in self.server.requests))

    def test_language_does_not_send_media_message(self):
        with self.assertRaisesRegex(InferenceWireError, "^invalid_payload$"):
            self.invoke("language", "qwen-plus", {"messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,synthetic"}}]}]})
        self.assertEqual(self.server.requests, [])

    def test_image_result_uses_separate_credential_free_download(self):
        result = self.invoke("image", "wan2.6-t2i", {"prompt": "Synthetic image"})
        self.assertEqual(result, {"status": "succeeded", "request_id": "img-synthetic",
                                  "mime_type": "image/png", "media": PNG})
        inference, download = self.server.requests
        self.assertEqual(inference[:2], ("POST", IMAGE_PATH))
        self.assertEqual(inference[3], {"model": "wan2.6-t2i", "input": {"messages": [
            {"role": "user", "content": [{"text": "Synthetic image"}]}]},
            "parameters": {"n": 1, "size": "1280*1280"}})
        self.assertEqual(download[:2], ("GET", "/synthetic.png"))
        self.assertNotIn("Authorization", download[2])

    def test_video_submission_then_explicit_status_with_bytes(self):
        result = self.invoke("video_submit", "wan2.7-t2v", {"prompt": "Synthetic video"})
        self.assertEqual(result, {"status": "pending", "task_id": "fixture-job", "request_id": "submit-synthetic"})
        self.assertEqual(len(self.server.requests), 1)
        upload = self.server.requests[0]
        self.assertEqual(upload[:2], ("POST", VIDEO_PATH))
        self.assertEqual({k.lower(): v for k, v in upload[2].items()}["x-dashscope-async"], "enable")
        self.assertEqual(upload[3], {"model": "wan2.7-t2v", "input": {"prompt": "Synthetic video"},
                                     "parameters": {"resolution": "720P", "ratio": "16:9", "duration": 5}})
        result = self.invoke("video_status", "wan2.7-t2v", {"task_id": result["task_id"]})
        self.assertEqual(result, {"status": "succeeded", "task_id": "fixture-job", "request_id": "status-synthetic",
                                  "mime_type": "video/mp4", "media": MP4})
        self.assertEqual([request[:2] for request in self.server.requests],
                         [("POST", VIDEO_PATH), ("GET", TASK_PATH + "fixture-job"), ("GET", "/synthetic.mp4")])

    def test_uncertain_submit_uses_one_request_and_fixed_failure_codes(self):
        for prompt, code in (("dropped", "submission_uncertain"), ("bad-json", "submission_uncertain"),
                             ("gateway-error", "submission_uncertain"), ("truncated", "submission_uncertain"),
                             ("denied", "provider_permission_denied"),
                             ("limit", "provider_rate_limited")):
            with self.subTest(prompt=prompt):
                self.server.requests.clear()
                with self.assertRaisesRegex(InferenceWireError, f"^{code}$"):
                    self.invoke("video_submit", "wan2.7-t2v", {"prompt": prompt})
                self.assertEqual([request[:2] for request in self.server.requests], [("POST", VIDEO_PATH)])

    def test_retryable_download_failures_cannot_submit_or_follow_redirect(self):
        for suffix, paths, code in (
            ("mismatch", [], "video_task_mismatch"),
            ("redirect", ["/redirect.mp4"], "asset_unavailable"),
            ("cross-origin", [], "asset_unavailable"),
            ("bad-asset", ["/invalid.mp4"], "asset_unavailable"),
            ("truncated-asset", ["/truncated.mp4"], "asset_unavailable"),
        ):
            with self.subTest(suffix=suffix):
                self.server.requests.clear()
                ident = f"fixture-{suffix}"
                with self.assertRaisesRegex(InferenceWireError, f"^{code}$"):
                    self.invoke("video_status", "wan2.7-t2v", {"task_id": ident})
                self.assertEqual([request[:2] for request in self.server.requests],
                                 [("GET", path) for path in [TASK_PATH + ident, *paths]])

    def test_video_pending_failed_unknown_results_have_no_bytes(self):
        for status in ("running", "failed", "unknown"):
            with self.subTest(status=status):
                self.server.requests.clear()
                ident = f"fixture-{status}"
                result = self.invoke("video_status", "wan2.7-t2v", {"task_id": ident})
                self.assertEqual(result, {"status": status, "task_id": ident, "request_id": "status-synthetic"})
                self.assertEqual([request[:2] for request in self.server.requests], [("GET", TASK_PATH + ident)])


if __name__ == "__main__":
    unittest.main()
