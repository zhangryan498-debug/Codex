"""Broker limited to a signed BAVA P01 and the customer's foreground native Agent."""
from hashlib import sha256
import json
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import subprocess
import sys
from threading import Thread
from time import monotonic
from uuid import uuid4

from foreground_token_plan import MODELS
from interactive_agent_bridge import APP, ENV_ALLOW, CustomerSession, SessionError, require
from interactive_agent_runtime import MALL_ROLE, SELL, SHOP, WORD, digest
import poster_copy_executor as copy_executor
import poster_workflow
from workflow_poster_delivery import SHOP_RECEIVE

SCOPE = "customer_token_plan_foreground"
SKU = "BAVA-OWNER-SINGLE-HEADREST"


def eligible(task):
    return (not task.get("kind") and task["site"] == "CN" and task["source"].get("sku") == SKU
            and task["source"].get("kind") == "local_upload" and not task["source"].get("simulated")
            and task["status"] == "ready" and task["run"]["workflow"].get("copy_route") == "USER"
            and not task.get("test_scope") and set(task.get("confirmed_copy", {})) == {"title","subtitle"}
            and len(task["versions"]) == 1 and task["versions"][0]["version"] == 1
            and bool(task["versions"][0].get("plan_release")))


class ForegroundSession(CustomerSession):
    runtime = "foreground_agent_runtime.py"
    tool_turns = 2

    def __init__(self, bench, task, ident, save, snapshot):
        super().__init__(bench, task, ident, save)
        self.deadline = monotonic() + 250
        self.record.update(scope=SCOPE, real_provider_calls=0, provider_responses=0, old_artwork_reused=True,
                           independent_role_model_calls=0)
        self.record.pop("spend")
        self.record.pop("provider_authorization")
        self.approved = task.get("confirmed_copy", {}).copy()
        self.release = sha256(snapshot["poster-chain-release.json"]).hexdigest() if snapshot is not None else None
        self.record["reviewed_release_sha256"] = self.release
        self.confirmed = False

    def bootstrap(self):
        return {**self.binding,"approved_copy":self.approved,"release_sha256":self.release}

    def mock_scope(self):
        return None

    def on_blocked(self):
        pass

    def mall_options(self):
        return {}

    def tool(self, event):
        from server import APIError

        turn = event.get("turn")
        call_id = event.get("call_id")
        args = event.get("arguments")
        require(type(turn) is int and turn == self.models == len(self.calls) + 1 and turn in (1,2)
                and event.get("name") == (SELL, SHOP)[turn-1] and isinstance(call_id, str)
                and WORD.fullmatch(call_id) and call_id not in self.calls, "tool_not_allowed")
        self.note("model_selected_tool", turn=turn, name=event["name"], call_id=call_id)
        task_id = self.binding["task_id"]
        common = {"task_id": task_id, "version": 1}
        result = {**common, "release_sha256": self.release}
        try:
            with self.bench.gate:
                task = self.bench.task(task_id)
                require(eligible(task) and task["agent_session"]["session_id"] == self.binding["session_id"],
                        "wrong_reviewed_version")
                _, _, snapshot = self.bench.verified_poster_delivery_source("CN",task_id,1,reviewed=True)
                require(sha256(snapshot["poster-chain-release.json"]).hexdigest() == self.release
                        and task["confirmed_copy"] == self.approved, "reviewed_source_changed")
            if turn == 1:
                require(digest(args) == digest({**common, **self.approved}), "tool_binding_or_copy_mismatch")
                proof = self.selling_point(task, call_id)
                self.sell_nonce = uuid4().hex
                result.update(status="ok", nonce=self.sell_nonce, skill_receipt_sha256=proof,
                              reviewed_copy_matched=True)
            else:
                require(isinstance(args, dict) and args.get("decision") in ("accept","hold")
                        and self.sell_nonce is not None and digest(args) == digest({
                            **common,"selling_point_result_id":self.sell_nonce,"release_sha256":self.release,
                            "decision":args["decision"]}), "tool_binding_or_copy_mismatch")
                self.sell_nonce = None
                self.note("mall_model_decision_in_main_history", call_id=call_id, decision=args["decision"])
                require(args["decision"] == "accept", "mall_decision_held")
                result.update(self.mall_receipt(task))
        except (APIError, SessionError, OSError, TypeError, ValueError, KeyError, StopIteration) as error:
            self.child_failed = True
            result.update(status="failed", code=str(error) if isinstance(error, SessionError) else "local_tool_rejected")
        self.calls[call_id] = digest(result)
        self.send({"event":"tool_result","session_id":self.binding["session_id"],"call_id":call_id,"result":result})
        self.note("local_tool_result_sent", call_id=call_id, result_sha256=self.calls[call_id], status=result["status"])
        if turn == 2 and not self.child_failed:
            self.result_nonce_digest = digest(result["nonce"])

    def selling_point(self, task, call_id):
        # Reuse the installed Skill and its local evidence rules without reopening an already approved file.
        project = self.bench.project
        folder = self.bench.tasks_dir / task["id"]
        skill = poster_workflow._SKILLS["A"]
        path = project / ".agents" / "skills" / skill / "SKILL.md"
        require(path.resolve(strict=True) == path and path.stat().st_size < 128000, "local_skill_not_available")
        prepared = {"route":"A","layout_allowed":False,"skill_path":str(path),
                    "skill_sha256":sha256(path.read_bytes()).hexdigest()}
        context = {**task,"facts":"\n".join(self.approved.values())}
        role, _ = poster_workflow._role(project, task, folder)
        inputs, plan, _, sources = poster_workflow._skill(project, prepared, role, context)
        require(bool(inputs["product_fact"]), "local_skill_rejected")
        pin = {**self.binding,"skill":skill,"skill_sha256":prepared["skill_sha256"],"release_sha256":self.release,
               "asset_sha256":task["source"]["sha256"]}
        copy_executor.validated_response(context,pin,{"binding":pin,"copy":self.approved,"invocation_id":call_id})
        proof = digest({"binding":pin,"copy":self.approved,"claim_plan":plan,"sources":sources,"call_id":call_id})
        self.note("local_selling_point_skill_verified", binding=pin, call_id=call_id, receipt_sha256=proof)
        return proof

    def mall_receipt(self, task):
        session = self.binding["session_id"]
        job = "CUSTOMER-FOREGROUND-" + session
        arguments = {"site":"CN","job_id":job,"poster_task_id":task["id"],"piece":"P01","version":1,
                     "product_ref":task["source"]["sku"]}
        self.update("receiving")
        started = self.bench.create_role({"site":"CN","request_id":job,"tool_id":SHOP_RECEIVE,
                                          "input":arguments}, agent_session=session, source_release=self.release, **self.mall_options())
        self.child_id = started["id"]
        received = self.poll(self.child_id, {"ready","failed","interrupted"}, set())
        require(received["status"] == "ready", "mall_receipt_unavailable")
        receipt = json.loads(self.bench.local_artifact_bytes(self.child_id,"receipt"))
        execution = received["agent_execution"]
        binding = execution["binding"]
        require((binding["parent_session_id"],binding["parent_task_id"],binding["version"],binding["child_task_id"])
                == (session,task["id"],1,self.child_id) and execution["worker_pid"] not in {self.proc.pid,os.getpid()}
                and receipt["source_release_sha256"] == self.release and receipt.get("evidence_scope") == self.mock_scope()
                and (receipt["upstream"]["poster_task_id"],receipt["upstream"]["version"]) == (task["id"],1),
                "mall_receipt_unavailable")
        self.note("mall_local_execution_verified", child_task_id=self.child_id, subsession_id=binding["subsession_id"],
                  worker_pid=execution["worker_pid"], execution_receipt_sha256=execution["receipt_sha256"])
        return {"status":"ok","nonce":uuid4().hex,"role":MALL_ROLE,"receipt_task_id":self.child_id,
                "agent_subsession_id":binding["subsession_id"],"receipt_sha256":execution["receipt_sha256"]}

    def run(self):
        native = getattr(self, "native_connection", None)
        local_preview = getattr(self, "local_demo_only", False)
        try:
            if native is not None:
                require(not local_preview and getattr(self, "demo_model_id", None) is None,
                        "invalid_preview_runtime")
                self.proc = native.proc
                frames = native.frames
                native.send({"event": "task", "connection_id": native.connection_id,
                             "runtime": self.runtime, "boot": self.bootstrap()})
            else:
                frames = self.launch_legacy(local_preview)
            self.note("local_model_runtime_started" if local_preview else "foreground_dialog_process_started",
                      pid=self.proc.pid)
            self.read_session(frames, local_preview, native)
        except (OSError, TypeError, ValueError, KeyError, Empty, subprocess.SubprocessError) as error:
            reason = str(error) if isinstance(error, SessionError) else "foreground_runtime_error"
            self.note("blocked", reason=reason)
            self.update("blocked", reason=reason, model_turns=self.models)
            self.on_blocked()
        finally:
            try:
                if native is not None:
                    native.finish()
                elif self.proc is not None:
                    if self.proc.poll() is None:
                        self.proc.kill()
                        self.proc.wait(timeout=4)
                    self.proc.stdin.close()
                    self.proc.stdout.close()
            finally:
                with self.bench.gate:
                    self.bench.agent_active = None
                    self.bench.foreground_active = None

    def launch_legacy(self, local_preview):
        exe = Path(sys._base_executable).resolve(strict=True)
        launch = [str(exe), "-I", "-B", str(APP/self.runtime)]
        if local_preview:
            require(self.runtime == "fresh_poster_agent_runtime.py", "invalid_preview_runtime")
            launch.append("--local-preview-only")
        self.proc = subprocess.Popen(launch,cwd=APP,env={k:v for k,v in os.environ.items() if k.upper() in ENV_ALLOW},
                                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),bufsize=0)
        frames = SimpleQueue()
        Thread(target=self.receive_frames,args=(frames,),daemon=True).start()
        self.send(self.bootstrap())
        return frames

    def read_session(self, frames, local_preview, native):
        while True:
            row = self.read_frame(frames)
            event = row.get("event")
            if event == "foreground_confirmed":
                demo_id = getattr(self, "demo_model_id", None)
                if native is not None:
                    valid_model = row["model"] == native.selected_model and row["simulated"] is native.fixture
                else:
                    valid_model = (row["simulated"] is True and row["model"] == demo_id
                                   if demo_id else row["model"] in MODELS)
                require(not self.confirmed and valid_model and type(row["simulated"]) is bool,
                        "invalid_foreground_confirmation")
                self.confirmed = True
                self.record["simulated"] = row["simulated"]
                self.record["selected_model"] = row["model"]
                self.note(event, model=row["model"], tools_sha256=row["tools_sha256"])
                self.update("drafting")
            elif event == "model_request":
                refs = [{"call_id":call,"sha256":h} for call,h in self.calls.items()]
                require(self.confirmed and row["turn"] == self.models + 1 == len(refs) + 1 and self.models <= self.tool_turns
                        and row["tool_results"] == refs, "wrong_model_history")
                self.models += 1
                self.note("interactive_model_turn", turn=self.models, returned_tool_results=refs)
            elif event == "provider_transport_attempt":
                require(self.confirmed and not self.record["simulated"]
                        and row["turn"] == self.models == self.record["real_provider_calls"] + 1,
                        "invalid_transport_receipt")
                self.record["real_provider_calls"] += 1
                self.record["token_plan_used"] = True
                self.note(event, turn=self.models)
            elif event == "provider_transport_response":
                require(self.confirmed and not self.record["simulated"]
                        and row["turn"] == self.models == self.record["real_provider_calls"]
                        == self.record["provider_responses"] + 1, "invalid_transport_receipt")
                self.record["provider_responses"] += 1
                self.note(event, turn=self.models)
            elif event == "model_tool_call":
                self.tool(row)
            elif event == "completed":
                refs = [{"call_id":call,"sha256":h} for call,h in self.calls.items()]
                require(not self.child_failed and self.child_id and len(refs) == self.tool_turns
                        and row["turns"] == self.models == self.tool_turns+1
                        and row["result_refs"] == refs and row["acknowledgement_sha256"] == self.result_nonce_digest,
                        "missing_tool_roundtrip")
                require(self.proc.wait(timeout=4) == 0, "foreground_runtime_error")
                expected = 0 if self.record["simulated"] else self.tool_turns+1
                require(self.record["real_provider_calls"] == expected == self.record["provider_responses"],
                        "invalid_transport_receipt")
                self.note("same_model_received_and_acknowledged_mall_tool", result_sha256=refs[-1]["sha256"])
                scope = ("local_model_simulation" if local_preview else "http_simulation"
                         if self.record["simulated"] else "foreground_provider_response")
                self.update("completed", mall_task_id=self.child_id, model_turns=self.models, proof_scope=scope)
                return
            elif event == "failed":
                reason = row.get("code")
                allowed = {"foreground_session_closed","desktop_required","model_did_not_call_tool","invalid_tool_call",
                           "tool_binding_or_copy_mismatch","local_tool_rejected","model_did_not_acknowledge_tools",
                           "provider_auth_rejected","provider_limit","provider_response_rejected"}
                raise SessionError(reason if reason in allowed else "foreground_runtime_error")
            else:
                raise SessionError("invalid_agent_frame")
