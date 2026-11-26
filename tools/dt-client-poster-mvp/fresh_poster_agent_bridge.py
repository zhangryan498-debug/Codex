"""Reuse reviewed poster assembly, native export and the independent CN Mall receiver."""
from hashlib import sha256
import json
from pathlib import Path
import re
from time import monotonic, sleep
from uuid import uuid4

from foreground_agent_bridge import ForegroundSession, SKU
from fresh_poster_contract import DETAIL, LAYOUT, SCOPE, SELL, readable
from interactive_agent_bridge import SessionError, require
from interactive_agent_runtime import SHOP, WORD, digest
import planned_poster
import poster_copy_executor
from poster_workflow import _RESTRICTED, _SKILLS, _role, _skill
from workflow_poster_delivery import dimensions
import foreground_detail_outputs


def facts_for_model(task):
    selected = [one for chunk in re.split(r"[\r\n。；;]+",task["facts"]) if (one:=chunk.strip())
                and not _RESTRICTED.search(one)][:2]
    return selected if selected and all(len(f)<=160 for f in selected) else []


def eligible(task):
    source = task.get("source",{})
    intake = task.get("copy_intake",{})
    return (not task.get("kind") and task["site"]=="CN" and source.get("kind")=="local_upload"
            and source.get("simulated") is False and source.get("sku")!=SKU
            and bool(planned_poster.REFERENCE.fullmatch(source.get("sku",""))) and task["status"]=="awaiting_selling_points"
            and not task.get("confirmed_copy") and not task["versions"] and task["run"]["mode"]=="awaiting_skill"
            and intake.get("task_id")==task["id"] and intake.get("asset_sha256")==source["sha256"]
            and bool(facts_for_model(task)))


class NewPosterSession(ForegroundSession):
    runtime = "fresh_poster_agent_runtime.py"
    tool_turns = 3

    def __init__(self, bench,task,ident,save,snapshot=None):
        super().__init__(bench,task,ident,save,snapshot)
        self.deadline = monotonic()+560
        self.record.update(scope=SCOPE,old_artwork_reused=False)
        self.record.pop("reviewed_release_sha256",None)
        self.product = task["title"]
        self.facts = facts_for_model(task)
        self.sell_nonce = self.layout_nonce = self.visual_nonce = None
        self.captured_detail = task.get("foreground_detail")
        self.selectors = None
        self.visual_outputs = None
        self.demo_model_id = None
        self.order = (SELL,LAYOUT,DETAIL,SHOP) if self.captured_detail is not None else (SELL,LAYOUT,SHOP)
        self.tool_turns = len(self.order)

    def bootstrap(self):
        data = {**self.binding,"product":self.product,"facts":self.facts}
        if self.captured_detail is not None:
            data["detail_facts"] = foreground_detail_outputs.model_evidence(self.captured_detail)["facts"]
        if self.demo_model_id is not None:
            data["local_demo_model"] = self.demo_model_id
        return data

    def mall_options(self):
        return {"combination":self.selectors} if self.selectors is not None else {}

    def mock_scope(self):
        return poster_copy_executor.FRESH_TEST if self.record.get("simulated") else None

    def on_blocked(self):
        with self.bench.gate:
            task = self.bench.task(self.binding["task_id"])
            receipt = self.bench.tasks_dir / task["id"] / poster_copy_executor.RECEIPT
            if task["status"] == "drafting" and not task.get("confirmed_copy") and not receipt.exists():
                self.bench.status(task,"awaiting_selling_points","本机会话已结束，可由本人重新打开确认窗。",
                                  copy_intake={**task["copy_intake"],"state":"interrupted","layout_allowed":False})

    def tool(self,event):
        from server import APIError

        turn, call = event.get("turn"),event.get("call_id")
        name, args = event.get("name"),event.get("arguments")
        require(type(turn) is int and turn==self.models==len(self.calls)+1 and 1<=turn<=self.tool_turns
                and name==self.order[turn-1] and isinstance(call,str) and WORD.fullmatch(call)
                and call not in self.calls and isinstance(args,dict), "tool_not_allowed")
        ident = self.binding["task_id"]
        common = {"task_id":ident,"version":1}
        require(digest({k:args.get(k) for k in common})==digest(common),"wrong_tool_binding")
        self.note("model_selected_tool",turn=turn,name=name,call_id=call)
        result = dict(common)
        try:
            with self.bench.gate:
                task = self.bench.task(ident)
                require(task["agent_session"]["session_id"]==self.binding["session_id"]
                        and task["title"]==self.product and facts_for_model(task)==self.facts,"wrong_task_binding")
            if name==SELL:
                require(set(args)==set(common)|{"title","subtitle"} and task["status"]=="drafting"
                        and all(isinstance(args[k],str) and args[k] and any(args[k] in f for f in self.facts)
                                for k in ("title","subtitle")), "tool_binding_or_copy_mismatch")
                result.update(self.selling_point_new(task, args, call))
            elif name==LAYOUT:
                require(set(args)==set(common)|{"selling_point_result_id","objects"} and self.sell_nonce is not None
                        and args["selling_point_result_id"]==self.sell_nonce, "tool_binding_or_copy_mismatch")
                self.sell_nonce = None
                result.update(self.layout(task,args["objects"],call))
            elif name==DETAIL:
                require(self.layout_nonce is not None and set(args)==set(common)|{"layout_result_id","cards","modules"}
                        and args["layout_result_id"]==self.layout_nonce and self.captured_detail is not None
                        and not self.captured_detail["missing"] and self.selectors is None,
                        "tool_binding_or_copy_mismatch")
                result.update(self.build_detail(task,args,call))
            else:
                extra = {"visual_result_id"} if self.captured_detail is not None else set()
                require(self.layout_nonce is not None and set(args)==set(common)|{"layout_result_id","release_sha256","decision"}|extra
                        and (self.captured_detail is None or self.visual_nonce is not None
                             and args["visual_result_id"] == self.visual_nonce and self.selectors is not None)
                        and args["layout_result_id"]==self.layout_nonce and args["release_sha256"]==self.release,
                        "tool_binding_or_copy_mismatch")
                self.layout_nonce = self.visual_nonce = None
                self.note("mall_model_decision_in_main_history",call_id=call,decision=args["decision"])
                require(args["decision"]=="accept","mall_decision_held")
                result.update(self.mall_receipt(task),release_sha256=self.release)
                if self.selectors is not None:
                    sealed = self.bench.combined_visual_bytes(self.child_id,"mall_combined_receipt")
                    result.update(visual_receipt_sha256=sha256(sealed).hexdigest(),same_sku_v1=True)
        except (APIError,SessionError,OSError,TypeError,ValueError,KeyError,StopIteration) as error:
            self.child_failed = True
            code = str(error)
            safe = {"wrong_task_binding","tool_binding_or_copy_mismatch","layout_text_obstruction","layout_low_contrast",
                    "local_skill_rejected","mall_decision_held","poster_quality_rejected","customer_review_timeout",
                    "reviewed_source_changed","mall_receipt_unavailable","runtime_stopped","visual_material_missing",
                    "visual_source_mismatch","visual_product_not_ready"}
            reason = code if code in safe else "local_tool_rejected"
            result.update(status="failed",code=reason)
            self.note("fresh_tool_rejected",call_id=call,code=reason)
        self.calls[call] = digest(result)
        self.send({"event":"tool_result","session_id":self.binding["session_id"],"call_id":call,"result":result})
        self.note("local_tool_result_sent",call_id=call,result_sha256=self.calls[call],status=result["status"])
        if name==SHOP and result["status"]=="ok":
            self.result_nonce_digest = digest(result["nonce"])

    def build_detail(self,task,args,call):
        expected = {**self.binding,"product_id":task["source"]["sku"],"sku_id":task["source"]["sku"]}
        choice = {k:args[k] for k in ("cards","modules")}
        self.selectors = foreground_detail_outputs.build_outputs(
            self.bench,task,expected,self.captured_detail,choice,call_id=call,simulated=self.record["simulated"])
        for ident in self.selectors.values():
            ready = self.poll(ident,{"ready","failed","interrupted"},set())
            require(ready["status"]=="ready","visual_product_not_ready")
        context = {"model_mode":"local_fixture" if self.record["simulated"] else "customer_foreground",
                   "sku_id":task["source"]["sku"],"asset_sha256":task["source"]["sha256"]}
        self.visual_outputs = foreground_detail_outputs.verify_outputs(
            self.bench,expected,self.selectors,expected_context=context)
        self.visual_nonce = uuid4().hex
        receipt = digest(self.visual_outputs)
        self.note("local_long_detail_and_main_verified",call_id=call,source_tasks=self.selectors,
                  visual_receipt_sha256=receipt)
        return {"status":"ok","nonce":self.visual_nonce,"visual_receipt_sha256":receipt,"same_sku_v1":True}

    def selling_point_new(self,task,args,call):
        kind = self.mock_scope() or poster_copy_executor.FRESH_LIVE
        def selected(binding,prompt):
            require(binding["task_id"]==task["id"] and binding["target_version"]==1,"wrong_task_binding")
            return {"binding":binding,"copy":{k:args[k] for k in ("title","subtitle")},"invocation_id":call}
        self.bench.run_reviewed_poster_copy(task,kind,selected,None)
        with self.bench.gate:
            actual = self.bench.task(task["id"])
            receipt = actual["run"]["workflow"]
            require(actual["status"]==planned_poster.AWAITING_PLAN and receipt.get("invocation_id")==call
                    and receipt.get("execution_kind")==kind,"local_skill_rejected")
        self.sell_nonce = uuid4().hex
        self.note("fresh_selling_point_skill",call_id=call,receipt_sha256=receipt["copy_receipt_sha256"],
                  skill_sha256=receipt["skill_sha256"],execution_kind=kind)
        return {"status":"ok","nonce":self.sell_nonce,"skill":receipt["skill"],
                "skill_sha256":receipt["skill_sha256"],"copy_receipt_sha256":receipt["copy_receipt_sha256"],
                "copy":actual["confirmed_copy"]}

    def layout(self,task,objects,call):
        folder = self.bench.tasks_dir/task["id"]
        plan = {"binding":task["plan_binding"],"selected_selling_point":task["confirmed_copy"]["title"],"objects":objects}
        planned_poster.build(task,folder,plan)
        readable(objects)
        skill = _SKILLS["B"]
        path = self.bench.project/".agents"/"skills"/skill/"SKILL.md"
        require(path.resolve(strict=True)==path and path.stat().st_size<128000,"local_skill_rejected")
        skill_sha = sha256(path.read_bytes()).hexdigest()
        brief, _ = _role(self.bench.project,task,folder)
        inputs, method, _, references = _skill(self.bench.project,{"route":"B","layout_allowed":False,
            "skill_path":str(path),"skill_sha256":skill_sha},brief,task)
        require(bool(inputs["product_fact"]),"local_skill_rejected")
        plan_sha = planned_poster.signature(plan)
        local_skill_receipt = {"binding":task["plan_binding"],"session_id":self.binding["session_id"],
                               "tool_call_id":call,"plan_sha256":plan_sha,"skill":skill,"skill_sha256":skill_sha,
                               "method":method["method"],"references_sha256":digest(references)}
        self.save(folder/"fresh-layout-skill-receipt.json",local_skill_receipt)
        layout_skill_sha = digest(local_skill_receipt)
        self.bench.planned_artwork(task["id"],"layout-plan",plan,agent_plan_session=self.binding["session_id"],
                                   agent_layout_call=call)
        self.update("waiting_for_review")
        self.note("fresh_layout_skill_plan",call_id=call,plan_sha256=plan_sha,skill_sha256=skill_sha,
                  layout_skill_receipt_sha256=layout_skill_sha)
        while monotonic()<self.deadline-36:
            require(self.proc.poll() is None,"runtime_stopped")
            with self.bench.gate:
                actual = self.bench.task(task["id"])
                require(not actual.get("fresh_render_failed"),"poster_quality_rejected")
                if actual["status"]=="ready" and actual["versions"]:
                    break
                require(actual["status"] in {planned_poster.AWAITING_REVIEW,"rendering"},"poster_quality_rejected")
            sleep(.12)
        else:
            raise SessionError("customer_review_timeout")
        with self.bench.gate:
            entry, _, raw = self.bench.verified_poster_delivery_source("CN",task["id"],1,reviewed=True,
                                                                      agent_session=self.binding["session_id"])
        self.release = sha256(raw["poster-chain-release.json"]).hexdigest()
        seal = json.loads(raw["poster-chain-release.json"])
        require(entry["version"]==1 and seal["release"]["initial_plan_sha256"]==plan_sha
                and seal["initial_review"]["plan_sha256"]==planned_poster.signature(plan),"reviewed_source_changed")
        manifest=json.loads(raw["manifest.json"])
        require(dimensions(raw["original.png"])==dimensions(raw["layered.psd"],psd=True)==(720,880)
                and manifest["exportIntegrity"]["pass"] is True and manifest["elementCount"]==5
                and manifest["nativeText"]["count"]==2 and manifest["psd"]["status"]=="EXPORTED_AND_READ_BACK",
                "poster_quality_rejected")
        self.layout_nonce = uuid4().hex
        self.note("fresh_export_quality_verified",call_id=call,release_sha256=self.release,dimensions=[720,880],
                  layers=5,native_text_layers=2,source_sha256=task["source"]["sha256"])
        self.update("drafting")
        return {"status":"ok","nonce":self.layout_nonce,"release_sha256":self.release,"dimensions_px":[720,880],
                "editable_native_text_layers":2,"reviewed_original_retained":True,"local_approval":True,
                "layout_skill_receipt_sha256":layout_skill_sha}
