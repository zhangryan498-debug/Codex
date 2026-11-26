"""Local adapters for D&T role contracts, without running the jobs they describe."""

from importlib import import_module
import json
from pathlib import Path
import re
import sys

from role_tools import (
    IMPORT_LOCK, ROUTES, SYNTHETIC, RoleToolError, _object, _validate_json,
)


def _shop_example(site):
    return {
        "site": site, "scope": SYNTHETIC,
        "expect": {"sku_id": "DEMO-A", "version": 1, "image_ids": ["IMG-A"]},
        "evaluation_time": "2026-11-18T12:00:00Z",
        "facts_pack": {
            "schema_version": "shop_product_facts/1.0", "pack_id": "DEMO-PACK",
            "version": 1, "mode": "simulated", "product_id": "DEMO-PRODUCT",
            "sku_id": "DEMO-A", "as_of": "2026-11-18T00:00:00Z",
            "expires_at": "2026-11-19T00:00:00Z", "image_ids": ["IMG-A"],
            "sources": [],
            "facts": [
                {"fact_id": "stock", "subject": "可售库存", "kind": "quantity",
                 "status": "unknown", "value": None, "source_ids": [], "alternatives": []},
            ],
        },
    }


def _lead_example(site):
    return {
        "site": site, "scope": SYNTHETIC,
        "job": {
            "job_id": "DEMO-01", "idempotency_key": "mock-demo-01",
            "country": site, "platform": ROUTES[site][0], "role_suffix": "shop-ops",
            "task": "检查模拟 SKU DEMO-A 的商品事实包。", "requires": [],
        },
    }


def _live_example(site):
    evidence = {
        "platform": ROUTES[site][0], "country": site, "recording_sha256": "a" * 64,
        "start_seconds": 0, "end_seconds": 5,
    }
    return {
        "site": site, "scope": SYNTHETIC, "segment": evidence,
        "observation": {
            **evidence, "origin": "human_local", "visual_observed": True,
            "speech_observed": True, "visual_status": "sufficient", "relation": "consistent",
        },
        "alerts": [
            {"id": "DEMO-BRAND", "scope": "brand_identity_authorization", "independent_risk": False},
            {"id": "DEMO-PRICE", "scope": "price", "independent_risk": True},
        ],
    }


def _workflow_example(site):
    return {
        "site": site, "scope": SYNTHETIC,
        "expected": {"new_commit_count": 0, "new_charge_count": 0},
        "observed": {"new_commit_count": 0, "new_charge_count": 0},
    }


def _subtitle_example(site):
    text = "本机模拟字幕" if site == "CN" else "Lokal na halimbawang subtitle"
    return {"site": site, "scope": SYNTHETIC,
            "srt": f"1\n00:00:00,000 --> 00:00:02,100\n{text}\n"}


def public_tools():
    return [
        {
            "id": "campaign-state-replay", "role_suffix": "campaign-ops", "sites": ["CN", "PH"],
            "label": "活动状态回放对照",
            "description": "比对模拟活动的预期与实际状态，也可提供原模拟台账。只核对输入之间的一致性，不计算底价或发起活动。",
            "input_example": _workflow_example("CN"),
            "input_examples": {site: _workflow_example(site) for site in ROUTES},
        },
        {
            "id": "director-subtitle-preflight", "role_suffix": "video-director", "sites": ["CN", "PH"],
            "label": "短视频字幕时间轴检查",
            "description": "复用编导审核的 SRT 时间轴校验，检查时间格式、时长和重叠。不检查口播正确性或生成整条视频。",
            "input_example": _subtitle_example("CN"),
            "input_examples": {site: _subtitle_example(site) for site in ROUTES},
        },
        {
            "id": "shop-product-facts", "role_suffix": "shop-ops", "sites": ["CN", "PH"],
            "label": "商城商品事实包检查",
            "description": "对照另填的 SKU、版本和图片编号，检查模拟事实包的来源、时效和冲突。保留未知值。",
            "input_example": _shop_example("CN"),
            "input_examples": {site: _shop_example(site) for site in ROUTES},
        },
        {
            "id": "lead-route-preflight", "role_suffix": "lead", "sites": ["CN", "PH"],
            "label": "负责人派单预检查",
            "description": "检查模拟派单的进站、接收岗位、任务编号及依赖声明。结果供提交前核对。",
            "input_example": _lead_example("CN"),
            "input_examples": {site: _lead_example(site) for site in ROUTES},
        },
        {
            "id": "live-brand-evidence", "role_suffix": "live-ops", "sites": ["CN", "PH"],
            "label": "直播品牌线索证据检查",
            "description": "按 D&T 内部测试口径检查模拟的画面与口播线索。证据不足或存在独立风险时保留提醒，提交人工复核。",
            "input_example": _live_example("CN"),
            "input_examples": {site: _live_example(site) for site in ROUTES},
        },
    ]


def _load(project, name):
    # Module names below are fixed by the adapters; input never selects a filename.
    scripts = (project / "scripts").resolve()
    expected = scripts / (name + ".py")
    if not expected.is_file() or expected.resolve() != expected:
        raise RoleToolError("未找到项目原有的离线校验器。")
    with IMPORT_LOCK:
        sys.path.insert(0, str(scripts))
        try:
            module = import_module(name)
        except ImportError:
            raise RoleToolError("项目离线校验器的本机依赖缺失。") from None
        finally:
            sys.path.remove(str(scripts))
    if Path(getattr(module, "__file__", "")).resolve() != expected:
        raise RoleToolError("项目离线校验器的加载路径不符。")
    return module


def _shop(project, site, payload):
    _object(payload, "商城检查输入", {"site", "scope", "expect", "evaluation_time", "facts_pack"})
    expect = _object(payload.get("expect"), "expect", {"sku_id", "version", "image_ids", "source_versions"})
    if (not isinstance(expect.get("sku_id"), str) or type(expect.get("version")) is not int
            or not isinstance(expect.get("image_ids"), list)
            or any(not isinstance(item, str) for item in expect["image_ids"])):
        raise RoleToolError("请在 expect 中另填预期 SKU、整数版本和图片编号列表。")
    expected_sources = expect.get("source_versions")
    if expected_sources is not None:
        _object(expected_sources, "source_versions")
    facts = _object(payload.get("facts_pack"), "facts_pack")
    if facts.get("mode") != "simulated":
        raise RoleToolError("本次商城检查仅接受明确标记的模拟事实包。")
    validator = _load(project, "shop_product_contract")
    try:
        result = validator.validate_facts(
            facts, expected_sku_id=expect["sku_id"], expected_version=expect["version"],
            expected_image_ids=expect["image_ids"], now=payload.get("evaluation_time"),
            expected_source_versions=expected_sources,
        )
    except ValueError as error:
        raise RoleToolError("事实包未通过校验。请检查 SKU、版本、图片、时效和来源。详情 " + str(error)) from None

    counts = dict.fromkeys(("known", "unknown", "conflicting"), 0)
    for fact in facts["facts"]:
        counts[fact["status"]] += 1
    return {
        **result, "sku_id": facts["sku_id"], "facts_version": facts["version"],
        "fact_status_counts": counts, "facts": facts["facts"], "release_status": "not_authorized",
    }


def _lead(project, site, payload):
    _object(payload, "派单输入", {"site", "scope", "job"})
    job = _object(payload.get("job"), "job", {
        "job_id", "idempotency_key", "country", "platform", "role_suffix",
        "task", "requires", "priority",
    })
    platform, _currency = ROUTES[site]
    if (job.get("platform"), job.get("country")) != (platform, site):
        raise RoleToolError("派单目标的平台或国家与所选站点不符。")
    queue = _load(project, "offline_role_queue")
    try:
        checked = queue.normalize({**job, "kind": "offline_review"})
    except queue.ValidationError as error:
        raise RoleToolError("派单字段未通过校验。详情 " + str(error)) from None
    except queue.DependencyError as error:
        raise RoleToolError("派单依赖未通过校验。详情 " + str(error)) from None
    if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", key) is None for key in checked["requires"]):
        raise RoleToolError("依赖任务需填写有效的任务编号。")
    return {
        "job_id": checked["job_id"], "country": site, "platform": platform,
        "recipient_role": platform + "-" + checked["role_suffix"], "requires": checked["requires"],
        "priority": checked["priority"], "normalized_job_sha256": queue.digest(checked),
        "valid": True, "dependency_existence_checked": False, "job_enqueued": False,
        "scope": "Queue declaration validation only; no claim, handoff receipt or dispatch.",
    }


def _live(project, site, payload):
    _object(payload, "直播检查输入", {"site", "scope", "segment", "observation", "alerts"})
    identity = {"platform", "country", "recording_sha256", "start_seconds", "end_seconds"}
    segment = _object(payload.get("segment"), "segment", identity)
    if (segment.get("platform"), segment.get("country")) != (ROUTES[site][0], site):
        raise RoleToolError("直播片段的平台或国家与所选站点不符。")
    observation = payload.get("observation")
    if observation is not None:
        _object(observation, "observation", identity | {
            "origin", "visual_observed", "speech_observed", "visual_status", "relation",
        })
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or len(alerts) > 100:
        raise RoleToolError("请提供最多 100 条模拟直播提醒。")
    for alert in alerts:
        _object(alert, "alert", {"id", "scope", "independent_risk"})
    policy = _load(project, "live_brand_visual_silence_v2")
    result = policy.evaluate(segment, observation, alerts)
    return {
        **result, "observation_is_simulated": True, "media_examined": False,
        "official_enforcement_evaluated": False, "human_review_recommended": bool(result["kept_ids"]),
    }


def _campaign_state(project, site, payload):
    _object(payload, "活动回放输入", {"site", "scope", "expected", "observed", "workflow"})
    expected = _object(payload.get("expected"), "expected")
    observed = _object(payload.get("observed"), "observed")
    if not expected:
        raise RoleToolError("请提供用于比对的模拟预期状态。")
    workflow = payload.get("workflow")
    if workflow is not None:
        _object(workflow, "workflow")
    checker = _load(project, "campaign_workflow_state")
    return {**checker.compare_workflow_state(expected, observed, workflow),
            "expected_facts_independently_verified": False, "price_review_performed": False,
            "campaign_started": False}


def _director_subtitles(project, site, payload, task_dir):
    _object(payload, "字幕检查输入", {"site", "scope", "srt"})
    srt = payload.get("srt")
    if not isinstance(srt, str) or not srt.strip() or len(srt.encode("utf-8")) > 96 * 1024:
        raise RoleToolError("请提供不超过 96 KiB 的非空 UTF-8 SRT 字幕。")
    if not isinstance(task_dir, Path) or not task_dir.is_dir():
        raise RoleToolError("无法使用当前任务的本机资料目录。")
    source = task_dir / "submitted.srt"
    with source.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(srt)
    checker = _load(project, "render_real_footage")
    try:
        duration = checker.validate_review_srt(source)
    except ValueError:
        raise RoleToolError("SRT 时间轴未通过检查。请核对字幕编号、毫秒格式、正时长及时间段重叠。") from None
    return {"timeline_valid": True, "last_cue_end_seconds": duration,
            "speech_accuracy_checked": False, "video_rendered": False}


def run_role_tool(project, site, tool_id, payload, task_id, *, task_dir=None):
    handlers = {"shop-product-facts": _shop, "lead-route-preflight": _lead, "live-brand-evidence": _live,
                "campaign-state-replay": _campaign_state}
    if type(site) is not str or site not in ROUTES or type(tool_id) is not str or tool_id not in {*handlers, "director-subtitle-preflight"}:
        raise RoleToolError("所选岗位检查在本站点不可用。")
    _object(payload, "离线输入")
    _validate_json(payload)
    if payload.get("site") != site or payload.get("scope") != SYNTHETIC:
        raise RoleToolError("请选择与输入一致的站点，并明确标记为离线模拟。")
    try:
        project = Path(project).resolve()
        if tool_id == "director-subtitle-preflight":
            result = _director_subtitles(project, site, payload, task_dir)
        else:
            result = handlers[tool_id](project, site, payload)
        json.dumps(result, allow_nan=False)
    except RoleToolError:
        raise
    except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError):
        raise RoleToolError("输入格式不符合离线岗位契约，请参照示例核对。") from None
    return {
        "task_id": task_id, "site": site, "tool_id": tool_id, "scope": SYNTHETIC,
        "execution_mode": "deterministic_local_check", "business_execution_authorized": False,
        "model_called": False, "provider_called": False, "result": result,
    }

