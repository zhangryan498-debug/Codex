"""Workbench-side broker for a local, fixture-only customer interaction session."""
from hashlib import sha256
import json
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4

from interactive_agent_runtime import MAX_RECORD, MALL_ROLE, SELL, SHOP, digest, endpoint
from poster_copy_executor import INTERACTIVE_TEST
from workflow_poster_delivery import SHOP_RECEIVE

ACTIVE = {"starting", "drafting", "waiting_for_review", "receiving"}
APP = Path(__file__).resolve().parent
ENV_ALLOW = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "PATHEXT", "TEMP", "TMP", "LOCALAPPDATA", "USERPROFILE"}


class SessionError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise SessionError(code)


class CustomerSession:
    def __init__(self, bench, task, session, save, checkpoint=None):
        self.bench = bench
        self.binding = {"session_id": session, "task_id": task["id"], "version": 1}
        self.deadline = monotonic() + 165
        self.record = {**self.binding, "state": "starting", "scope": INTERACTIVE_TEST,
                       "ui_service_pid": os.getpid(), "events": [], "provider_authorization": "unverified",
                       "real_provider_calls": 0, "token_plan_used": False, "spend": 0,
                       "native_agent_delegation_verified": False}
        self.save = save
        self.path = bench.tasks_dir / task["id"] / "customer-agent-session.json"
        self.calls = {}
        self.models = 0
        self.child_id = None
        self.child_failed = False
        self.record["mall_role_loopback_requests"] = 0
        self.checkpoint = checkpoint
        self.order = (SHOP,) if checkpoint is not None else (SELL, SHOP)
        self.sell_nonce = None
        if checkpoint is not None:
            self.sell_nonce = uuid4().hex
            self.record["reviewed_checkpoint"] = checkpoint
            self.record["selling_point_result_id_sha256"] = digest(self.sell_nonce)
        self.proc = None

    def note(self, event, **info):
        self.record["events"].append({"event": event, **info})
        self.save(self.path, self.record)

    def update(self, state, **info):
        with self.bench.gate:
            task = self.bench.task(self.binding["task_id"])
            require(task["agent_session"]["session_id"] == self.binding["session_id"], "wrong_session")
            task["agent_session"] = {**task["agent_session"], "state": state, **info}
            self.bench.save_task(task)
        self.record.update(state=state, **info)
        self.save(self.path, self.record)

    def poll(self, ident, states, failures):
        while monotonic() < self.deadline:
            require(self.proc.poll() is None, "runtime_stopped")
            with self.bench.gate:
                task = self.bench.task(ident)
                if task["status"] in states:
                    return task
                require(task["status"] not in failures, "local_tool_failed")
            sleep(.1)
        raise SessionError("session_deadline")

    def send(self, value):
        raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
        require(len(raw) < MAX_RECORD, "ipc_message_too_large")
        self.proc.stdin.write(raw + b"\n")
        self.proc.stdin.flush()

    def read_frame(self, frames):
        remaining = self.deadline - monotonic()
        require(remaining > 0, "session_deadline")
        raw = frames.get(timeout=remaining)
        require(raw and len(raw) <= MAX_RECORD and raw.endswith(b"\n"), "invalid_agent_frame")
        item = json.loads(raw)
        require(isinstance(item, dict), "invalid_agent_frame")
        return item

    def review_shop(self, call_id, frames):
        require(self.record["mall_role_loopback_requests"] == 0, "mall_role_already_requested")
        with self.bench.gate:
            _, _, original = self.bench.verified_poster_delivery_source(
                "CN", self.binding["task_id"], self.binding["version"], reviewed=True,
                agent_session=self.binding["session_id"])
            release_hash = sha256(original["poster-chain-release.json"]).hexdigest()
        pin = {**self.binding, "call_id": call_id, "role": MALL_ROLE, "inference_id": uuid4().hex,
               "release_sha256": release_hash}
        self.send({"event": "review_mall_role", "binding": pin})
        self.record["mall_role_loopback_requests"] = 1
        self.note("mall_role_request_dispatched", binding=pin)
        frame = self.read_frame(frames)
        result = frame.get("result")
        require(set(frame) == {"event", "result"} and frame["event"] == "mall_role_result"
                and isinstance(result, dict) and digest(result.get("binding")) == digest(pin), "wrong_mall_role_frame")
        if result.get("status") == "failed":
            code = result.get("code")
            require(set(result) == {"binding", "status", "code"} and code in
                    {"role_model_response_invalid", "role_model_wrong_binding", "role_model_held"},
                    "wrong_mall_role_frame")
            self.note("mall_role_model_rejected", binding=pin, code=code)
            return result
        require(set(result) == {"binding", "status", "decision", "response_sha256"}
                and result["status"] == "accepted" and result["decision"] == "accept"
                and result["response_sha256"] == digest({"binding": pin, "decision": "accept"}),
                "wrong_mall_role_frame")
        self.note("mall_role_model_verified", binding=pin, response_sha256=result["response_sha256"])
        return result

    def receive_shop(self, task, call_id, frames):
        from server import APIError

        review = None
        try:
            if self.bench.agent_role_fixture:
                review = self.review_shop(call_id, frames)
                if review["status"] != "accepted":
                    raise SessionError(review["code"])
            result = self.run_shop_worker(task, review)
        except (APIError, OSError, TypeError, KeyError, ValueError) as error:
            self.child_failed = True
            result = {"status": "failed", "task_id": task["id"], "version": 1, "role": MALL_ROLE,
                      "code": str(error) if isinstance(error, SessionError) else "mall_receipt_unavailable"}
            if self.child_id:
                result["receipt_task_id"] = self.child_id
            self.note("mall_tool_rejected", code=result["code"])
        if review is not None:
            result["role_review"] = review
        return result

    def execute(self, event, frames):
        turn = event.get("turn")
        name = event.get("name")
        call_id = event.get("call_id")
        args = event.get("arguments")
        require(type(turn) is int and turn == len(self.calls) + 1 and turn == self.models
                and 1 <= turn <= len(self.order) and name == self.order[turn - 1]
                and isinstance(call_id, str) and len(call_id) <= 80 and call_id not in self.calls
                and isinstance(args, dict), "tool_not_allowed")
        keys = {"task_id","version"} | ({"title","subtitle"} if name == SELL else {"selling_point_result_id"})
        require(set(args) == keys and args["task_id"] == self.binding["task_id"]
                and type(args["version"]) is int and args["version"] == 1, "wrong_tool_binding")
        with self.bench.gate:
            task = self.bench.task(self.binding["task_id"])
            require(task["site"] == "CN" and task["agent_session"]["session_id"] == self.binding["session_id"],
                    "wrong_session")
        self.note("model_selected_tool", turn=turn, tool=name, call_id=call_id)
        if name == SELL:
            require(task["status"] == "drafting" and task["copy_intake"]["target_version"] == 1,
                    "wrong_selling_point_state")
            def authorized_copy(binding, _prompt):
                require(binding["task_id"] == self.binding["task_id"]
                        and binding["target_version"] == self.binding["version"], "wrong_skill_binding")
                return {"binding": binding, "copy": {field: args[field] for field in ("title","subtitle")},
                        "invocation_id": call_id}
            self.bench.run_reviewed_poster_copy(task, INTERACTIVE_TEST, authorized_copy, None)
            with self.bench.gate:
                task = self.bench.task(self.binding["task_id"])
                workflow = task["run"]["workflow"]
                require(task["status"] == "awaiting_layout_plan" and task["copy_intake"]["layout_allowed"]
                        and workflow.get("execution_kind") == INTERACTIVE_TEST
                        and workflow.get("invocation_id") == call_id, "selling_point_rejected")
                result = {"status": "ok", "task_id": task["id"], "version": 1, "nonce": uuid4().hex,
                          "skill": workflow["skill"], "skill_sha256": workflow["skill_sha256"],
                          "copy_receipt_sha256": workflow["copy_receipt_sha256"], "copy": task["confirmed_copy"]}
        else:
            require(self.sell_nonce is not None and args["selling_point_result_id"] == self.sell_nonce,
                    "selling_point_result_id_mismatch")
            self.note("handoff_bound_to_selling_point_result", result_id_sha256=digest(self.sell_nonce),
                      version=1, call_id=call_id)
            self.sell_nonce = None
            if self.checkpoint is None:
                require(task["status"] == "awaiting_layout_plan", "human_review_required")
                self.update("waiting_for_review")
                task = self.poll(self.binding["task_id"], {"ready"}, {"failed","interrupted"})
            require(task["status"] == "ready" and len(task["versions"]) == 1
                    and task["versions"][0]["version"] == 1, "wrong_reviewed_version")
            self.note("same_task_human_review_confirmed" if self.checkpoint is None else "reviewed_checkpoint_reused", version=1)
            result = self.receive_shop(task, call_id, frames)
        result_ref = digest(result)
        self.calls[call_id] = result_ref
        self.send({"event":"tool_result","session_id":self.binding["session_id"],"call_id":call_id,"result":result})
        self.note("local_tool_result_sent", turn=turn, tool=name, call_id=call_id, result_sha256=result_ref)
        if name == SELL:
            self.sell_nonce = result["nonce"]
            self.record["selling_point_result_id_sha256"] = digest(self.sell_nonce)
            self.save(self.path, self.record)
        elif not self.child_failed:
            self.result_nonce_digest = digest(result["nonce"])

    def run_shop_worker(self, task, review):
        role = self.bench.project / "runtime-agents/douyin-shop-ops/agent.md"
        require(role.resolve(strict=True) == role and role.stat().st_size <= 128000, "missing_handoff_role")
        role_hash = sha256(role.read_bytes()).hexdigest()
        ident = self.binding["session_id"]
        job = "MOCK-CUSTOMER-" + ident
        payload = {"site": "CN", "request_id": job, "tool_id": SHOP_RECEIVE,
                   "input": {"site": "CN", "job_id": job, "poster_task_id": task["id"],
                             "piece": "P01", "version": 1, "product_ref": task["source"]["sku"]}}
        self.update("receiving")
        started = self.bench.create_role(payload, agent_session=ident)
        received = self.poll(started["id"], {"ready","failed","interrupted"}, set())
        self.child_id = received["id"]
        if received["status"] != "ready":
            self.child_failed = True
            safe = {"role_worker_failed","role_worker_timeout","role_worker_wrong_parent",
                    "role_worker_wrong_version","role_worker_wrong_binding","role_worker_output_hash",
                    "role_worker_outputs", "role_worker_rejected"}
            code = received.get("message")
            result = {"status":"failed","task_id":task["id"],"version":1,"receipt_task_id":self.child_id,
                      "role":"douyin-shop-ops","code":code if code in safe else "mall_child_rejected"}
            self.note("mall_child_rejected", child_task_id=self.child_id, code=result["code"])
        else:
            receipt = json.loads(self.bench.local_artifact_bytes(received["id"],"receipt"))
            evidence = received["agent_execution"]
            binding = evidence["binding"]
            require(receipt["upstream"]["poster_task_id"] == task["id"]
                    and receipt["upstream"]["version"] == 1 and receipt["evidence_scope"] == INTERACTIVE_TEST
                    and (binding["parent_session_id"],binding["parent_task_id"],binding["version"],binding["child_task_id"])
                    == (self.binding["session_id"],task["id"],1,self.child_id)
                    and evidence["worker_pid"] not in {self.proc.pid,os.getpid()}, "wrong_mall_receipt")
            if review is not None:
                require(receipt["upstream"]["source_hashes"]["poster-chain-release.json"]
                        == review["binding"]["release_sha256"], "mall_receipt_release_mismatch")
            result = {"status": "ok", "task_id": task["id"], "version": 1, "nonce": uuid4().hex,
                      "receipt_task_id": self.child_id, "site": "CN", "role": "douyin-shop-ops",
                      "role_definition_sha256": role_hash, "bundle_sha256": receipt["bundle_sha256"],
                      "agent_subsession_id":binding["subsession_id"],"agent_execution_sha256":evidence["receipt_sha256"]}
            self.note("mall_subsession_verified", child_task_id=self.child_id, worker_pid=evidence["worker_pid"],
                      agent_subsession_id=binding["subsession_id"], execution_sha256=evidence["receipt_sha256"])
        return result

    def receive_frames(self, queue):
        while True:
            raw = self.proc.stdout.readline(MAX_RECORD + 1)
            queue.put(raw)
            if not raw or len(raw) > MAX_RECORD:
                break

    def run(self):
        try:
            endpoint(self.bench.agent_fixture)
            python = Path(sys._base_executable).resolve(strict=True)
            args = [str(python), "-I", "-B", str(APP / "interactive_agent_runtime.py")]
            self.proc = subprocess.Popen(args, cwd=APP, env={k: v for k, v in os.environ.items() if k.upper() in ENV_ALLOW},
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0), bufsize=0)
            frames = SimpleQueue()
            Thread(target=self.receive_frames,args=(frames,),daemon=True).start()
            with self.bench.gate:
                task = self.bench.task(self.binding["task_id"])
                selection = {"title":task["title"],"facts":task["facts"]} if self.checkpoint is None else {
                    "checkpoint":{**self.checkpoint,"selling_point_result_id":self.sell_nonce}}
                self.send({**self.binding,"fixture":self.bench.agent_fixture,**selection,
                           "role_model_fixture":self.bench.agent_role_fixture})
            self.update("drafting")
            started = False
            while True:
                item = self.read_frame(frames)
                event = item.get("event")
                if event == "runtime_started":
                    require(not started and item["binding"] == self.binding and item["pid"] == self.proc.pid
                            and item["parent_pid"] == os.getpid() and item["pid"] != os.getpid(),
                            "invalid_runtime_identity")
                    started = True
                    self.record["runtime_pid"] = item["pid"]
                    self.note(event, tools_sha256=item["tools_sha256"])
                elif event == "model_request":
                    require(started and item["turn"] == self.models + 1 and self.models <= len(self.order)
                            and item["tool_results"] == [{"call_id":k,"sha256":v} for k,v in self.calls.items()]
                            and len(self.calls) == self.models, "wrong_model_history")
                    self.models += 1
                    self.note(event, turn=self.models, tool_results=item["tool_results"])
                elif event == "model_tool_call":
                    self.execute(item, frames)
                elif event == "completed":
                    require(not self.child_failed and self.models == item["turns"] == len(self.order) + 1
                            and len(self.calls) == len(self.order) and self.child_id
                            and item["result_refs"] == [{"call_id":k,"sha256":v} for k,v in self.calls.items()]
                            and item["acknowledgement_sha256"] == self.result_nonce_digest, "missing_tool_roundtrip")
                    require(self.proc.wait(timeout=4) == 0, "runtime_did_not_exit_cleanly")
                    self.note("model_acknowledged_returned_result", acknowledgement_sha256=item["acknowledgement_sha256"])
                    self.update("completed", mall_task_id=self.child_id, accepted_tool_calls=len(self.calls), model_turns=self.models)
                    return
                elif event == "child_failure_ack":
                    require(self.child_failed and self.models == len(self.order)+1, "unexpected_failure_ack")
                    self.note(event,response_attempted_success=item.get("response_attempted_success") is True)
                elif event == "failed":
                    code=item.get("code")
                    require(code in {"tool_not_allowed","wrong_task","wrong_version","tool_out_of_sequence",
                                     "invalid_tool_call_id","invalid_tool_arguments","model_did_not_call_tool",
                                     "model_did_not_acknowledge_tools","invalid_tool_call","fixture_response_invalid",
                                     "tool_execution_failed","selling_point_result_id_missing",
                                     "selling_point_result_id_mismatch","mall_child_failed",
                                     "missing_role_model_receipt","unexpected_role_model_request",
                                     "wrong_role_request_binding"}, "agent_runtime_failed")
                    raise SessionError(code)
                else:
                    raise SessionError("invalid_agent_frame")
        except (OSError, TypeError, ValueError, KeyError, Empty, subprocess.SubprocessError) as err:
            code = str(err) if isinstance(err, SessionError) else "local_agent_session_failed"
            self.note("blocked", reason=code, model_turns=self.models)
            self.update("blocked", reason=code, accepted_tool_calls=len(self.calls)-int(self.child_failed), model_turns=self.models)
            with self.bench.gate:
                task = self.bench.task(self.binding["task_id"])
                if task["status"] == "awaiting_layout_plan":
                    self.bench.status(task,"awaiting_layout_plan","卖点工具有回执，但交接证据未通过，不能排版出件。",
                                      copy_intake={**task["copy_intake"],"layout_allowed":False})
                if task["status"] == "drafting" and not task.get("confirmed_copy"):
                    self.bench.status(task,"awaiting_selling_points","交互会话未完成，未授权排版。",
                                      copy_intake={**task["copy_intake"],"state":"rejected","layout_allowed":False})
        finally:
            if self.proc is not None:
                if self.proc.poll() is None:
                    self.proc.kill()
                    self.proc.wait(timeout=4)
                self.proc.stdin.close()
                self.proc.stdout.close()
            with self.bench.gate:
                self.bench.agent_active = None
