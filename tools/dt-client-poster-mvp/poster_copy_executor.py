"""Bind a process-configured local Skill adapter to a pending reviewed poster."""
from hashlib import sha256
import json
import re
from uuid import uuid4

from copy_constraints import normalize_copy
from poster_workflow import WorkflowError, _RESTRICTED, _SKILLS, _image_evidence, _role, _run_gate, _skill

TEST = "isolated_copy_test"
LOCAL = "local_skill_executor"
CLOUD = "customer_cloud_skill_projection"
CLOUD_TEST = "customer_cloud_http_mock"
CLOUD_KINDS = {CLOUD, CLOUD_TEST}
INTERACTIVE_TEST = "customer_interactive_agent_mock"
FRESH_LIVE = "customer_foreground_poster_tool_calls"
FRESH_TEST = "customer_foreground_poster_protocol_mock"
ORDINARY_VISUAL = "customer_ordinary_api_visual_skill"
INTERACTIVE_KINDS = {INTERACTIVE_TEST, FRESH_LIVE, FRESH_TEST, ORDINARY_VISUAL}
WORKBENCH_MOCK_KINDS = {CLOUD_TEST, INTERACTIVE_TEST, FRESH_TEST}
RUNNERS = {TEST, LOCAL, *INTERACTIVE_KINDS, *CLOUD_KINDS}
RECEIPT = "poster-copy-executor-receipt.json"
RESPONSE_FIELDS = {"binding", "copy", "invocation_id"}
INVOCATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode()).hexdigest()


def request_binding(project, task_dir, task, route):
    if route not in _SKILLS or task["source"].get("kind") != "local_upload":
        raise WorkflowError("已有原图和分配的 Skill 才能提炼。")
    source = task["source"]
    intake = task["copy_intake"]
    expected = {"task_id": task["id"], "site": task["site"], "product_ref": source["sku"],
                "asset_sha256": source["sha256"], "target_version": 1}
    if {key: intake[key] for key in expected} != expected or type(intake["target_version"]) is not int:
        raise WorkflowError("任务、站点、商品、图源或目标版已变化。")
    if task_dir.resolve(strict=True) != task_dir or task_dir.name != task["id"]:
        raise WorkflowError("本机文案任务目录无效。")
    _image_evidence(task_dir, task)
    skill = f".agents/skills/{_SKILLS[route]}/SKILL.md"
    path = project / skill
    if path.resolve(strict=True) != path:
        raise WorkflowError("Skill 路径无法核验。")
    return {**expected, "input_sha256": fingerprint([task["title"], task["facts"]]),
            "source_request_sha256": task["request_hash"], "route": route, "skill": _SKILLS[route],
            "skill_file": skill, "skill_sha256": sha256(path.read_bytes()).hexdigest()}


def validated_response(task, expected, result):
    if (not isinstance(result, dict) or set(result) != RESPONSE_FIELDS
            or fingerprint(result["binding"]) != fingerprint(expected)):
        raise WorkflowError("执行回执与任务、站点、商品、图源、版本或所分配 Skill 不匹配。")
    invocation = result["invocation_id"]
    if not isinstance(invocation, str) or not INVOCATION_ID.fullmatch(invocation):
        raise WorkflowError("缺少有效的执行编号。")
    copy = result["copy"]
    if not isinstance(copy, dict) or set(copy) != {"title", "subtitle"}:
        raise WorkflowError("执行结果缺少标题或副标题。")
    for field, value in copy.items():
        if normalize_copy(value, field) != value or _RESTRICTED.search(value):
            raise WorkflowError("返回文案未通过字形或声明限制。")
        if value not in task["facts"]:
            raise WorkflowError("返回文案须直接引用本任务已填商品事实。")
    return copy


def access_receipt(task_dir):
    path = task_dir / RECEIPT
    if path.resolve() != path:
        raise WorkflowError("文案回执必须位于当前本机任务。")
    return path


def provenance(kind, binding):
    return {"execution_method": kind,
            "read_sources": [{"file": binding["skill_file"], "sha256": binding["skill_sha256"]}]}


def read_receipt(project, task_dir, task, kind, route, receipt):
    data = receipt.read_bytes()
    entry = json.loads(data)
    expected = request_binding(project, task_dir, task, route)
    if (set(entry) != RESPONSE_FIELDS | {"variant", "provenance"}
            or entry["variant"] != route or entry["provenance"] != provenance(kind, expected)):
        raise WorkflowError("留存执行来源与 Skill 版本无法核对。")
    copy = validated_response(task, expected, {key: entry[key] for key in RESPONSE_FIELDS})
    return entry, copy, sha256(data).hexdigest()


def draft_with_cloud_skill(project, task_dir, task, prepared, binding, executor):
    brief, _ = _role(project, task, task_dir)
    inputs, _, strategy, _ = _skill(project, prepared, brief, task)
    if not inputs["product_fact"]:
        raise WorkflowError("没有可发送的已填商品事实。")
    facts = [line.strip() for line in re.split(r"[\r\n。；;]+", task["facts"])
             if line.strip() and not _RESTRICTED.search(line)][:2]
    guidance = (strategy + " Use the first eligible fact for product identity and at most one feature. "
                "Return both fields as short exact excerpts of the supplied fact lines. "
                "Use the product title as context. Do not infer from images or add claims.")
    reply = executor(dict(binding), {"site": brief["site"].lower(), "title": task["title"], "facts": facts,
                                     "guidance": guidance})
    copy = validated_response(task, binding, reply)
    if any(not any(text in line for line in facts) for text in copy.values()):
        raise WorkflowError("云端文案须来自本次已选的商品事实。")
    return reply


def run(project, task_dir, task, state, kind, executor, persist):
    if kind not in RUNNERS or not callable(executor):
        raise WorkflowError("尚未配置获准的本机卖点 Skill 执行器。")
    if state.resolve() != state:
        raise WorkflowError("A/B 轮转路径无效。")
    work = task_dir / "copy-workflow"
    if work.resolve() != work:
        raise WorkflowError("文案过程目录无效。")
    work.mkdir(exist_ok=True)
    attempt = work / ("skill-" + uuid4().hex)
    attempt.mkdir()
    prepared = _run_gate(project, attempt, state, task["id"], "prepare")
    route = prepared["route"]
    binding = request_binding(project, task_dir, task, route)
    if (prepared["layout_allowed"] or prepared["skill_sha256"] != binding["skill_sha256"]
            or prepared["task_id"] != task["id"]):
        raise WorkflowError("A/B 轮次与本地 Skill 无法核对。")
    persist(attempt / "binding.json", {"kind": kind, "binding": binding})
    receipt = access_receipt(task_dir)
    if not receipt.exists():
        try:
            if kind in CLOUD_KINDS or kind in INTERACTIVE_KINDS:
                reply = draft_with_cloud_skill(project, task_dir, task, prepared, binding, executor)
            else:
                reply = executor(dict(binding), {"title": task["title"], "facts": task["facts"],
                                                 "local_product_image": task_dir / "product.png",
                                                 "local_skill_file": project / binding["skill_file"]})
            copy = validated_response(task, binding, reply)
            if binding != request_binding(project, task_dir, task, route):
                raise WorkflowError("运行过程中的任务事实、图源或 Skill 已变化。")
        except Exception:
            persist(attempt / "rejected.json", {"kind": kind, "binding": binding,
                                               "state": "rejected", "layout_allowed": False})
            raise
        persist(receipt, {"variant": route, "provenance": provenance(kind, binding),
                          "binding": binding, "copy": copy, "invocation_id": reply["invocation_id"]})
    entry, copy, digest = read_receipt(project, task_dir, task, kind, route, receipt)
    completed = _run_gate(project, attempt, state, task["id"], "complete", receipt)
    approved = _run_gate(project, attempt, state, task["id"], "copy")
    if (completed.get("sha256") != approved.get("sha256") or approved.get("copy") != copy
            or approved.get("route") != route or approved.get("invocation_id") != "sha256:" + digest):
        raise WorkflowError("轮转回读与本机执行回执不一致。")
    return copy, {"execution_kind": kind, "copy_route": route, "skill": binding["skill"],
                  "skill_sha256": binding["skill_sha256"], "copy_receipt_sha256": digest,
                  "invocation_id": entry["invocation_id"], "copy_bound_version": 1,
                  "model_called": kind in {LOCAL, CLOUD, FRESH_LIVE, ORDINARY_VISUAL}, "native_skill_execution_verified": kind == LOCAL,
                  "cloud_provider_called": kind in {CLOUD, FRESH_LIVE, ORDINARY_VISUAL}, "cloud_http_simulated": kind in {CLOUD_TEST, FRESH_TEST}}


def verify_accepted(project, task_dir, task):
    workflow = task["run"]["workflow"]
    kind = workflow.get("execution_kind")
    if kind not in RUNNERS:
        return
    entry, copy, digest = read_receipt(project, task_dir, task, kind, workflow["copy_route"], access_receipt(task_dir))
    if (digest != workflow["copy_receipt_sha256"] or copy != task.get("confirmed_copy")
            or entry["invocation_id"] != workflow["invocation_id"]
            or entry["binding"]["skill_sha256"] != workflow["skill_sha256"]
            or task["copy_intake"]["state"] != "accepted"
            or workflow["copy_bound_version"] != entry["binding"]["target_version"]):
        raise WorkflowError("已通过文案与原执行回执、任务或版本不符。")
