"""Run the reviewed CN visual chain through the ordinary API loopback adapter."""
from hashlib import sha256
import json
from time import monotonic
from uuid import uuid4

import fresh_poster_agent_runtime as runtime
from fresh_poster_agent_bridge import NewPosterSession
from interactive_agent_bridge import SessionError, require
from interactive_agent_runtime import WORD, digest
import planned_poster
import poster_copy_executor
import foreground_detail_outputs
from provider_model_workspace import KEY_LIKE
from provider_inference_wire import TOKEN_PLAN_METHOD

TRANSPORT = "ordinary_api_visual_chain"
SELL_TOOL = "dt_run_registered_selling_skill"
ROLE_FILE = "runtime-agents/douyin-shop-ops/agent.md"
ROLE_SKILL = ".agents/skills/douyin-shop-ops-playbook/SKILL.md"


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def definition(root, relative, expected):
    path = root / relative
    require(path.resolve(strict=True) == path and path.stat().st_size < 24000, "registered_role_missing")
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    require(text.startswith(f"---\nname: {expected}\n") or text.startswith(f"---\r\nname: {expected}\r\n"),
            "registered_role_missing")
    return sha256(raw).hexdigest()


class OrdinaryVisualChain(NewPosterSession):
    """An operator-approved, bounded visual session on one ordinary API connection."""

    max_requests = 7

    def __init__(self, bench, task, ident, save, account_id, model_id, child):
        super().__init__(bench, task, ident, save)
        simulated = bool(bench.customer_models.fixture)
        with bench.customer_models.lock:
            selected = bench.customer_models.account(account_id)
            provider, method = selected["provider"], selected["method"]
        self.token_plan = (provider, method) == TOKEN_PLAN_METHOD
        self.proof_scope = ("token_plan_loopback" if simulated else "token_plan_customer") if self.token_plan else (
            "ordinary_api_loopback" if simulated else "ordinary_api_customer")
        self.customer, self.proc = child, child.proc
        self.record.update(scope=TRANSPORT, transport=TRANSPORT, simulated=simulated, token_plan_used=False,
                           request_attempts=0, fixture_requests=0, fixture_responses=0, max_requests=self.max_requests,
                           model_assignment={"account_id": account_id, "model_id": model_id,
                                             "provider": provider, "method": method})
        if simulated:
            self.record["spend"] = 0
        self.account_id = account_id
        self.model_id = model_id
        self.model = model_id
        self.result_frame = None
        self.role_proof = None

    def require_active(self):
        workspace = self.bench.customer_models
        with self.bench.gate, workspace.lock:
            task = self.bench.task(self.binding["task_id"])
            selected = workspace.state["assignments"]["language"]
            require(self.bench.agent_active == self.binding["session_id"]
                    and task["agent_session"]["session_id"] == self.binding["session_id"]
                    and monotonic() < self.deadline, "visual_session_closed")
            require(selected and selected["account_id"] == self.account_id and selected["model_id"] == self.model_id
                    and workspace.children.get(self.account_id) is self.customer and self.proc.poll() is None,
                    "visual_connection_changed")

    def request(self, messages, tools, phase):
        self.require_active()
        payload = {"messages": messages, "tools": tools}
        require(self.record["request_attempts"] < self.max_requests, "visual_request_limit")
        require(not KEY_LIKE.search(json_text(payload)), "protected_visual_input")
        child = self.bench.customer_models.operation_child("language", self.account_id, self.model_id, visual_session=True)
        if child is not self.customer:
            child.operation_gate.release()
            raise SessionError("visual_connection_changed")
        # The per-account gate remains held through the credential worker's one dispatch.
        self.record["request_attempts"] += 1
        count = "fixture_requests" if self.record["simulated"] else "real_provider_calls"
        self.record[count] += 1
        try:
            self.note("ordinary_model_request", phase=phase, request_sha256=digest(payload))
        except OSError:
            child.operation_gate.release()
            raise
        message = child.operate("language", self.model_id, payload)["message"]
        self.require_active()
        self.record["fixture_responses" if self.record["simulated"] else "provider_responses"] += 1
        if self.token_plan and not self.record["simulated"]:
            self.record["token_plan_used"] = True
        self.note("ordinary_model_response", phase=phase, worker_pid=self.proc.pid, response_sha256=digest(message))
        return ("tool_calls" if message.get("tool_calls") else "stop"), message

    def __call__(self, messages, tools):
        return self.request(messages, tools, "main")

    def send(self, value):
        require(self.result_frame is None, "unexpected_tool_result")
        self.result_frame = value

    def receive_result(self):
        frame, self.result_frame = self.result_frame, None
        require(frame is not None, "missing_tool_roundtrip")
        return frame

    def emit_event(self, event, **row):
        self.require_active()
        if event == "foreground_confirmed":
            require(not self.confirmed and row["simulated"] is self.record["simulated"] and row["model"] == self.model_id,
                    "invalid_fixture_confirmation")
            self.confirmed = True
            self.note("ordinary_model_confirmed", model=self.model_id, tools_sha256=row["tools_sha256"])
            self.update("drafting")
        elif event == "model_request":
            refs = [{"call_id": call, "sha256": h} for call, h in self.calls.items()]
            require(self.confirmed and row["turn"] == self.models + 1 == len(refs) + 1
                    and self.models <= self.tool_turns and row["tool_results"] == refs, "wrong_model_history")
            self.models += 1
            self.note("interactive_model_turn", turn=self.models, returned_tool_results=refs)
        elif event == "model_tool_call":
            self.tool(row)
        elif event == "completed":
            refs = [{"call_id": call, "sha256": h} for call, h in self.calls.items()]
            require(not self.child_failed and self.child_id and self.role_proof and len(refs) == self.tool_turns
                    and row["turns"] == self.models == self.tool_turns + 1 and row["result_refs"] == refs
                    and row["acknowledgement_sha256"] == self.result_nonce_digest, "missing_tool_roundtrip")
            self.note("same_model_received_and_acknowledged_mall_tool", result_sha256=refs[-1]["sha256"])
            self.update("completed", mall_task_id=self.child_id, model_turns=self.models,
                        proof_scope=self.proof_scope)
        else:
            raise SessionError("unexpected_visual_event")

    def run(self):
        try:
            boot = self.bootstrap()
            runtime.validate_boot(boot)
            runtime.exchange(boot, self, simulated=self.record["simulated"], send_event=self.emit_event,
                             receive_result=self.receive_result, send_failure_feedback=False)
        except Exception as error:
            reason = str(error) if isinstance(error, (SessionError, runtime.Denied)) else "ordinary_visual_chain_failed"
            self.note("blocked", reason=reason)
            self.update("blocked", reason=reason, model_turns=self.models)
            self.on_blocked()
        finally:
            with self.bench.gate:
                self.bench.agent_active = self.bench.foreground_active = None

    def selling_point_new(self, task, _args, call):
        scope = self.mock_scope() or poster_copy_executor.ORDINARY_VISUAL

        def execute(binding, prompt):
            challenge = uuid4().hex
            fields = {"task_id": {"type": "string", "enum": [binding["task_id"]]},
                      "version": {"type": "integer", "enum": [binding["target_version"]]},
                      "skill_run_id": {"type": "string", "enum": [challenge]},
                      "title": {"type": "string"}, "subtitle": {"type": "string"}}
            tools = [{"type": "function", "function": {"name": SELL_TOOL,
                "description": "Return the assigned installed Skill's fact-bound copy.",
                "parameters": runtime.object_shape(fields)}}]
            model_binding = {key: binding[key] for key in ("task_id", "target_version", "skill")}
            model_binding["skill_run_id"] = challenge
            messages = [
                {"role": "system", "content": prompt["guidance"]},
                {"role": "user", "content": json_text({"skill_binding": model_binding,
                    "product_facts": {"title": prompt["title"], "facts": prompt["facts"]}})},
            ]
            finish, reply = self.request(messages, tools, "registered_selling_skill")
            require(finish == "tool_calls" and len(reply["tool_calls"]) == 1, "registered_skill_no_tool")
            selected = reply["tool_calls"][0]
            answer = json.loads(selected["function"]["arguments"])
            require(selected["function"]["name"] == SELL_TOOL and selected["id"] != call
                    and WORD.fullmatch(selected["id"]) and isinstance(answer, dict) and set(answer) == set(fields)
                    and digest({k: answer[k] for k in ("task_id", "version", "skill_run_id")}) == digest({
                        "task_id": binding["task_id"], "version": binding["target_version"],
                        "skill_run_id": challenge}), "registered_skill_binding_mismatch")
            self.note("registered_selling_skill_model_result", parent_call_id=call,
                      invocation_id=selected["id"], binding=binding,
                      applied_guidance_sha256=digest(prompt["guidance"]))
            return {"binding": binding, "copy": {key: answer[key] for key in ("title", "subtitle")},
                    "invocation_id": selected["id"]}

        self.bench.run_reviewed_poster_copy(task, scope, execute, None)
        with self.bench.gate:
            actual = self.bench.task(task["id"])
            receipt = actual["run"]["workflow"]
            executions = [event for event in self.record["events"] if event["event"] == "registered_selling_skill_model_result"]
            require(actual["status"] == planned_poster.AWAITING_PLAN and receipt.get("execution_kind") == scope
                    and len(executions) == 1 and executions[0]["invocation_id"] == receipt["invocation_id"],
                    "local_skill_rejected")
        self.sell_nonce = uuid4().hex
        self.note("fresh_selling_point_skill", call_id=call, invocation_id=receipt["invocation_id"],
                  skill=receipt["skill"], receipt_sha256=receipt["copy_receipt_sha256"],
                  skill_sha256=receipt["skill_sha256"], execution_kind=scope)
        return {"status": "ok", "nonce": self.sell_nonce, "skill": receipt["skill"],
                "skill_sha256": receipt["skill_sha256"], "copy_receipt_sha256": receipt["copy_receipt_sha256"],
                "copy": actual["confirmed_copy"]}

    def mall_receipt(self, task):
        require(self.visual_outputs is not None and self.role_proof is None, "mall_visual_payload_missing")
        self.require_active()
        role_hash = definition(self.bench.project, ROLE_FILE, "douyin-shop-ops")
        guide_hash = definition(self.bench.project, ROLE_SKILL, "douyin-shop-ops-playbook")
        with self.bench.gate:
            _, _, source = self.bench.verified_poster_delivery_source(
                "CN", task["id"], 1, reviewed=True, agent_session=self.binding["session_id"])
            require(sha256(source["poster-chain-release.json"]).hexdigest() == self.release, "reviewed_source_changed")
            captured = self.visual_outputs["binding"]
            current = foreground_detail_outputs.verify_outputs(self.bench, captured, self.selectors)
            require(current == self.visual_outputs, "reviewed_source_changed")
        pin = {"role_subsession_id": uuid4().hex, "parent_session_id": self.binding["session_id"],
               "task_id": task["id"], "version": self.binding["version"], "site": task["site"],
               "sku": task["source"]["sku"], "poster_release_sha256": self.release,
               "visual_receipt_sha256": digest(current), "role_sha256": role_hash,
               "playbook_sha256": guide_hash}
        challenge = {key: pin[key] for key in ("role_subsession_id", "version")}
        view = {"same_source": True, "same_sku": True, "same_release": True,
                "poster": [{"size": [720, 880], "native_text_layers": 2}]}
        for kind in ("main", "detail"):
            view[kind] = [{key: piece[key] for key in ("size", "native_text_layers")}
                          for piece in current["artifacts"][kind]["pieces"]]
        messages = [
            {"role": "system", "content": (
                "D&T registered mall role. Perform only local product-card acceptance. "
                "Require this version's matching source, SKU and release, a legible poster, main images, and a detail "
                "page with editable native text. Hold if any check is missing. Do not claim store or human approval. "
                'Return JSON only with {"binding": <exact supplied binding>, "decision":"accept" or "hold"}.')},
            {"role": "user", "content": json_text({"binding": challenge, "visual": view})},
        ]
        finish, reply = self.request(messages, [], "registered_mall_role")
        result = json.loads(reply["content"] or "null")
        require(finish == "stop" and isinstance(result, dict) and set(result) == {"binding", "decision"}
                and digest(result["binding"]) == digest(challenge) and result["decision"] == "accept",
                "mall_role_binding_or_decision")
        self.require_active()
        self.role_proof = digest({"binding": pin, "reply": result})
        self.note("registered_mall_role_accepted", binding=pin, response_sha256=self.role_proof,
                  role_file=ROLE_FILE, playbook_file=ROLE_SKILL, proof_scope=self.proof_scope)
        local = super().mall_receipt(task)
        local["role_review"] = {"role_subsession_id": pin["role_subsession_id"],
                                "response_sha256": self.role_proof, "visual_receipt_sha256": pin["visual_receipt_sha256"]}
        return local
