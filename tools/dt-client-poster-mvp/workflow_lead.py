"""Run the supported local lead workflows on the project queue."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from role_tools import ROUTES, SYNTHETIC, RoleToolError
from role_tools_extra import _load
import workflow_campaign
import workflow_live


class WorkflowError(ValueError):
    pass


NAMED_TOOL = "lead-local-named-dispatch"
NAMED_ROUTES = {
    "campaign-ops":"campaign-ledger-replay", "live-ops":"live-assortment-intake",
    "selection":"selection-category-shortlist", "shop-ops":"shop-funnel-alignment",
    "ad-buyer":"ad-period-review", "competitor-analyst":"competitor-surface-alignment",
    "visual-designer":"visual-media-library", "video-director":"director-offline-topic-schedule",
    "video-teardown":"teardown-offline-element-library",
}
DEPENDENCY_STATES = {
    "selection-category-shortlist":"CATEGORY_RECORDED", "shop-funnel-alignment":"ALIGNED_FIXTURE",
    "competitor-surface-alignment":"ALIGNED_FIXTURE", "director-offline-topic-schedule":"TOPICS_RECORDED",
    "live-assortment-intake":"READY_OFFLINE",
}
JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")

STEPS = (
    ("campaign", "campaign-ops", "campaign-ledger-replay", workflow_campaign),
    ("live", "live-ops", "live-window-replay", workflow_live),
)


def public_tools():
    examples = {
        site: {key: module.public_tools()[0]["input_examples"][site] for key, _, _, module in STEPS}
        for site in ROUTES
    }
    return [{
        "id": "lead-local-queue-run", "role_suffix": "lead", "sites": list(ROUTES),
        "label": "负责人本机派单与回收",
        "description": "在独立本机任务队列中执行活动账本重算和直播逐窗复盘，回收两岗结果并记录依赖与中断状态。",
        "execution_scope": "仅执行这两项模拟复盘。依赖表示先后顺序，活动结果不会成为直播的价格或经营授权。",
        "max_media_files": 0,
        "input_example": examples["CN"], "input_examples": examples,
    }, _named_tool(examples)]


def _allowed_named_roles(site):
    platform, _ = ROUTES[site]
    return {f"{platform}-{step[1]}": step for step in STEPS}


def _named_tool(examples):
    inputs = {
        site: {
            "job_id": f"{site}-LOCAL-001",
            "role": next(iter(_allowed_named_roles(site))),
            "input": examples[site]["campaign"],
        }
        for site in ROUTES
    }
    return {
        "id": NAMED_TOOL, "role_suffix": "lead", "sites": list(ROUTES),
        "label": "负责人按岗单条本机派单",
        "description": (
            "填写 job_id、岗位全名 role 和该岗结构化模拟 input。"
            "双站九岗可通过 target_tool_id 指定受支持的本机处理器；旧活动和直播入口仍可直接提交。"
            "可用 upstream_job_id 指向本站已完成的同类派单。任务记录按 job_id 显示。"
        ),
        "execution_scope": "通过本机队列运行单个岗位工作流，结果和回执照录任务编号。只处理合成输入。",
        "max_media_files": 0,
        "input_example": inputs["CN"], "input_examples": inputs,
    }


def validate_named_request(site, payload):
    required = {"job_id", "role", "input"}
    if (not isinstance(payload, dict) or not required <= payload.keys()
            or payload.keys() - required - {"upstream_job_id","target_tool_id"}):
        raise WorkflowError("请提供 job_id、岗位全名 role、input，可选 upstream_job_id。")
    job_id, role = payload["job_id"], payload["role"]
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise WorkflowError("任务编号须由 1–96 个英文字母、数字、点、下划线或连字符组成，并以字母或数字开头。")
    if "target_tool_id" in payload:
        platform,_=ROUTES[site]
        suffix=role.removeprefix(platform+"-") if isinstance(role,str) and role.startswith(platform+"-") else ""
        if suffix not in NAMED_ROUTES or not isinstance(payload["target_tool_id"],str) or NAMED_ROUTES[suffix]!=payload["target_tool_id"]:
            raise WorkflowError("该站点的命名岗位与可用本机处理器不匹配。")
    elif not isinstance(role,str) or role not in _allowed_named_roles(site):
        raise WorkflowError("该岗位不在旧版单条本机岗位名单内；九岗派单须指定准确处理器。")
    if not isinstance(payload["input"], dict):
        raise WorkflowError("岗位 input 须为结构化 JSON 对象。")
    upstream = payload.get("upstream_job_id")
    if "upstream_job_id" in payload and (
        not isinstance(upstream, str) or not JOB_ID.fullmatch(upstream) or upstream == job_id
    ):
        raise WorkflowError("上游须为本站不同任务的有效编号。")
    return job_id, role, upstream


def named_request_key(site, job_id):
    # Colons separate server-owned identities from user request IDs.
    return f"named-local:{site}:{job_id}"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _save(path, obj):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _open_queue(project, folder):
    try:
        native = _load(project, "offline_role_queue")
    except RoleToolError as error:
        raise WorkflowError(str(error)) from None
    folder.mkdir()
    return native.Queue(folder / "queue.sqlite")


def _run_child(project, site, step, job_input, step_dir, queue, lease, progress, *, routed=None):
    _, _, selected_tool, module = step
    job_id, token = lease["job_id"], lease["token"]
    attempt = queue.start(job_id, token, _now())
    try:
        step_dir.mkdir()
        _save(step_dir / "batch-input.json", job_input)
        if routed is None:
            result, outputs = module.run_workflow(project, site, selected_tool, job_input, step_dir, {}, progress)
        else:
            result, outputs = routed(step_dir,progress)
            if set(outputs)!={"json","report","receipt"}:
                raise WorkflowError("指定岗位须返回结果、报告与原运行回执。")
        digests = {}
        for kind, path in outputs.items():
            path = path.resolve(strict=True)
            if not path.is_relative_to(step_dir.resolve(strict=True)) or not path.is_file() or not path.stat().st_size:
                raise WorkflowError("岗位任务没有生成可核对的本机输出。")
            digests[kind] = sha256(path.read_bytes()).hexdigest()
        if "json" not in digests or "receipt" not in digests:
            raise WorkflowError("岗位任务缺少结果或执行回执。")
        saved_result=json.loads(outputs["json"].read_bytes())
        if saved_result!=json.loads(json.dumps(result,ensure_ascii=False,allow_nan=False)):
            raise WorkflowError("原岗位结果与保存的业务 JSON 不一致。")
        result=saved_result
    except (WorkflowError, module.WorkflowError, OSError) as error:
        reason = "本机岗位文件无法读取或保存。" if isinstance(error, OSError) else str(error)
        queue.settle(job_id, token, "failed", {"scope": SYNTHETIC, "reason": reason, "model_called": False}, _now())
        return {"attempt": attempt, "state": "failed", "reason": reason}, {}
    queue.settle(job_id, token, "completed",
                 {"scope": SYNTHETIC, "files_sha256": digests, "model_called": False}, _now())
    return {"attempt": attempt, "state": "completed", "files_sha256": digests, "result": result}, outputs


def _run_named(project, site, payload, task_dir, progress, upstream, route):
    job_id, role, upstream_id = validate_named_request(site, payload)
    if (upstream_id is None and upstream is not None) or (
        upstream_id is not None and (upstream is None or upstream["job_id"] != upstream_id)
    ):
        raise WorkflowError("未核实上游完成回执，暂不派发。")
    platform, _ = ROUTES[site]
    target=payload.get("target_tool_id")
    if target:
        if route is None:
            raise WorkflowError("指定处理器尚未通过宿主证据核验。")
        route_pins,runner=route
        step=("named",role.removeprefix(platform+"-"),target,_RoutedErrors)
    else:
        route_pins,runner=None,None
        step=_allowed_named_roles(site)[role]
    folder = task_dir / "named-dispatch"
    queue = _open_queue(project, folder)
    submitted = queue.submit([{
        "job_id": job_id, "idempotency_key": named_request_key(site, job_id),
        "platform": platform, "country": site, "role_suffix": step[1], "kind": "offline_review",
        "task": "运行工作台提交的本站岗位合成输入。",
    }])
    lease = queue.claim("dt-local-named", _now(), 1800)
    if submitted != [job_id] or lease is None or (lease["job_id"], lease["role"]) != (job_id, role):
        raise WorkflowError("岗位队列返回了不匹配的任务编号或接收岗位。")
    progress(f"已派发 {job_id} 给 {role}，正在执行本机岗位任务。")
    work=folder/"worker"
    if target:
        work.mkdir()
        work=work/task_dir.name
    handled, originals = _run_child(project, site, step, payload["input"], work, queue, lease, progress, routed=runner)
    finished = queue.get(job_id)
    if handled["state"] == "failed":
        raise WorkflowError(f"任务 {job_id} 的岗位执行失败：{handled['reason']}")
    if (finished["job_id"], finished["role"], finished["state"], finished["attempt_id"]) != (
        job_id, role, "completed", handled["attempt"],
    ):
        raise WorkflowError("岗位完成记录与派发的任务编号、岗位或执行标识不一致。")
    receipt = {
        "execution": "actual_deterministic_queue", "native_agent_delegation": False,
        "site": site, "scope": SYNTHETIC, "job_id": finished["job_id"], "role": finished["role"],
        "workbench_task_id": task_dir.name, "state": finished["state"],
        "attempt_id": finished["attempt_id"], "attempts": finished["attempts"],
        "upstream": upstream, "artifact_sha256": finished["evidence"]["files_sha256"],
        "queue_sha256": sha256((folder / "queue.sqlite").read_bytes()).hexdigest(),
        "model_called": False, "business_execution_authorized": False, "provider_called": False,
    }
    if target:
        result=handled["result"]
        ready=DEPENDENCY_STATES.get(target)
        receipt.update(worker_tool_id=target,route_pins=route_pins,
            worker_files={kind:path.relative_to(folder).as_posix() for kind,path in originals.items()},
            dependency_ready=ready is not None and result.get("state")==ready)
    result = {**receipt, "worker_result": handled["result"]}
    outputs = {"json": folder / "result.json", "receipt": folder / "receipt.json"}
    _save(outputs["json"], result)
    _save(outputs["receipt"], receipt)
    if "report" in originals:
        outputs["report"] = originals["report"]
    return result, outputs


class _RoutedErrors:
    WorkflowError=WorkflowError


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress, *, upstream=None, route=None):
    if not isinstance(site, str) or site not in ROUTES or assets:
        raise WorkflowError("当前站点或工具不支持本机派单。")
    if tool_id == NAMED_TOOL:
        return _run_named(project, site, payload, task_dir, progress, upstream, route)
    if tool_id != "lead-local-queue-run":
        raise WorkflowError("当前工具不支持本机派单。")
    if not isinstance(payload, dict) or set(payload) != {"campaign", "live"} or any(
        not isinstance(body, dict) for body in payload.values()
    ):
        raise WorkflowError("请分别提供活动账本复盘和直播逐窗复盘的模拟输入。")
    folder = task_dir / "lead-batch"
    queue = _open_queue(project, folder)
    platform, _ = ROUTES[site]
    campaign, live = STEPS
    queue.submit([
        {"job_id": key.upper(), "idempotency_key": "offline-"+task_dir.name+"-"+key,
         "platform": platform, "country": site, "role_suffix": role, "kind": "offline_review",
         "task": "执行模拟活动账本复盘" if key == "campaign" else "执行模拟直播逐窗复盘",
         "requires": [] if key == "campaign" else ["CAMPAIGN"]}
        for key, role, _, _ in STEPS
    ])
    # The first actual result must settle before the second claim; a failed
    # stage retains the existing pending child without auto-retry.
    handled = []
    for key, role, selected_tool, module in (campaign, live):
        leased = queue.claim("dt-local-demo", _now(), 1800)
        if leased is None:
            break
        if leased["job_id"] != key.upper() or leased["role"] != platform+"-"+role:
            raise RuntimeError("Unexpected queue routing")
        progress("正在执行本机岗位任务。" if key == "campaign" else "正在执行依赖活动复盘完成的直播任务。")
        outcome, _ = _run_child(project, site, (key, role, selected_tool, module), payload[key],
                                folder / key, queue, leased, progress)
        handled.append({"step": key, **outcome})
        if outcome["state"] == "failed":
            break
    states = [{"job_id": row["job_id"], "role": row["role"], "state": row["state"],
               "attempts": row["attempts"]} for row in queue.list_jobs()]
    complete = len(handled) == len(STEPS) and all(row["state"] == "completed" for row in states)
    result = {"site": site, "scope": SYNTHETIC, "batch_state": "completed" if complete else "blocked",
              "jobs": states, "collected": handled, "model_called": False,
              "business_execution_authorized": False, "provider_called": False}
    outputs = {"json": folder/"lead-result.json", "receipt": folder/"lead-receipt.json"}
    _save(outputs["json"], result)
    _save(outputs["receipt"], {"execution": "actual_deterministic_queue", "site": site,
                              "scope": SYNTHETIC, "jobs": states,
                              "queue_sha256": sha256((folder/"queue.sqlite").read_bytes()).hexdigest(),
                              "provider_called": False})
    return result, outputs

