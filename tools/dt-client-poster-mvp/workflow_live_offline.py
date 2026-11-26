"""Versioned offline live SOP updates and short-video referral custody."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import re

from role_tools import ROUTES, SYNTHETIC
import workflow_live
import workflow_live_team
import workflow_teardown

SOP = "live-sop-version"
PLAN = "live-referral-plan"
DIRECTOR = "live-referral-director-intake"
LEAD = "live-referral-lead-intake"
INTAKES = {DIRECTOR: "video-director", LEAD: "lead"}
TOOLS = (SOP, PLAN, DIRECTOR, LEAD)
ZONES = {"CN": "Asia/Shanghai", "PH": "Asia/Manila"}
LANGUAGES = {"CN": ("zh-CN",), "PH": ("en-PH", "fil-PH", "Taglish")}
LOCAL_TIME = timezone(timedelta(hours=8))
OUTPUTS = {kind: f"live-offline/{name}" for kind, name in
           (("json", "result.json"), ("report", "report.md"), ("receipt", "receipt.json"))}


class WorkflowError(ValueError):
    pass


def _fail(message):
    raise WorkflowError(message)


def _shape(data, required, note):
    if not isinstance(data, dict) or data.keys() != set(required.split()):
        _fail(note)


def _id(value, pattern=r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}"):
    if not isinstance(value, str) or not re.fullmatch(pattern, value, re.ASCII):
        _fail("任务、来源及对象编号须使用有效的英文、数字或连接符。")


def _task(value):
    _id(value, r"[0-9a-f]{32}")


def _text(value, limit=320):
    if not isinstance(value, str) or not value.strip() or not value.isprintable() or len(value) > limit:
        _fail("文字须为有界的非空单行内容。")


def _when(value):
    try:
        if not isinstance(value, str) or len(value) > 40 or "T" not in value:
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except (ValueError, OverflowError):
        _fail("时点须为带时区偏移的 ISO 8601。")


def _interval(start, end):
    if _when(start) >= _when(end):
        _fail("区间的结束时间须晚于开始时间。")


def _age(value, limit):
    return type(value) is int and 0 <= value <= limit


def validate(site, tool, value):
    common = "site scope key version sku_id session_id"
    extra = {
        SOP: "language previous_task_id replay_task_id step_changes",
        PLAN: "language previous_task_id team_task_id clip_task_id clip_sha256 clip_start_ms clip_end_ms media_binding "
              "stage_times timezone as_of sample rights next_check affiliate",
        DIRECTOR: "plan_task_id receiver",
        LEAD: "plan_task_id receiver",
    }
    if tool not in extra or site not in ROUTES:
        _fail("此路径只用于已支持站点的离线工作台。")
    _shape(value, f"{common} {extra[tool]}", "输入字段与所选的直播工作不匹配。")
    if value["site"] != site or value["scope"] != SYNTHETIC:
        _fail("只接受所选站点的合成离线资料。")
    for k in ("key", "sku_id", "session_id"):
        _id(value[k])
    if not _age(value["version"], 1_000_000_000) or value["version"] == 0:
        _fail("版本须为正整数。")
    if tool in INTAKES:
        _task(value["plan_task_id"])
        if value["receiver"] != ROUTES[site][0] + "-" + INTAKES[tool]:
            _fail("收件工具与目标岗位或站点不符。")
        return
    if value["language"] not in LANGUAGES[site]:
        _fail("语言须属于该站点的离线范本。")
    previous = value["previous_task_id"]
    if previous is not None:
        _task(previous)
    if (previous is None) != (value["version"] == 1):
        _fail("从第二版开始须引用上一版任务。")
    if tool == SOP:
        _task(value["replay_task_id"])
        items = value["step_changes"]
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            _fail("每版须有 1–20 项步骤或话术更新。")
        seen = set()
        for item in items:
            _shape(item, "step_id sop phrase kind start end review rationale", "须为每个步骤写明 SOP、话术、时段、复核与理由。")
            _id(item["step_id"])
            if (item["step_id"] in seen or item["review"] not in ("consistent", "conflicting", "unverified")
                    or item["kind"] not in (("ordinary", "COD") if site == "PH" else ("ordinary",))):
                _fail("步骤编号重复、复核状态无效或复用了另一站点的 COD 话术。")
            seen.add(item["step_id"])
            for k in ("sop", "phrase", "rationale"):
                _text(item[k])
            _interval(item["start"], item["end"])
        return
    _validate_plan(site, value)


def _validate_plan(site, value):
    for key in ("team_task_id", "clip_task_id"):
        _task(value[key])
    _id(value["clip_sha256"], r"[0-9a-f]{64}")
    a, b = value["clip_start_ms"], value["clip_end_ms"]
    if not _age(a, 60_000) or not _age(b, 60_000) or a >= b:
        _fail("短视频切片入出点须为 0–60 秒内的有效毫秒区间。")
    _shape(value["media_binding"], "sku_id source_ref", "媒体对象须注明 SKU 及合成对象依据。")
    if value["media_binding"]["sku_id"] != value["sku_id"]:
        _fail("短视频对象与直播 SKU 不符。")
    _id(value["media_binding"]["source_ref"])
    as_of = _when(value["as_of"])
    times = value["stage_times"]
    _shape(times, "video_to_live live_start live_to_video", "请提供引流、场次和反向剪辑日期；不适用项填 null。")
    if (value["timezone"] != ZONES[site] or not as_of <= _when(times["video_to_live"]) < _when(times["live_start"])
            or (site == "PH" and _when(times["live_to_video"]) <= _when(times["live_start"]))
            or (site == "CN" and times["live_to_video"] is not None)):
        _fail("排期须匹配本站时区、正向先于场次，菲律宾反向排期须晚于开场。")
    _shape(value["sample"], "source_ref scope observed_at observation", "观察样本须声明来源、观察日期与限制。")
    sample = value["sample"]
    _id(sample["source_ref"])
    _text(sample["observation"])
    if sample["scope"] != SYNTHETIC or _when(sample["observed_at"]) > as_of:
        _fail("仅使用评估时点前已记录的合成对照。")
    rights = value["rights"]
    _shape(rights, "status evidence_ref expires_at spark_ads_status", "须将剪辑授权及 Spark Ads 凭证分别登记。")
    if rights["status"] not in ("offline_test_only", "unknown") or rights["spark_ads_status"] not in ("unknown", "not_in_scope"):
        _fail("只接受离线验证范围；Spark Ads 必须单列未知或不在范围。")
    if rights["status"] == "offline_test_only" or rights["evidence_ref"] is not None:
        _id(rights["evidence_ref"])
    if rights["expires_at"] is not None:
        _when(rights["expires_at"])
    _text(value["next_check"])
    affiliate = value["affiliate"]
    if site == "PH":
        _shape(affiliate, "creator_ref host_ref viewing_check clip_destination", "菲律宾需合成达人、主持人、观看核对与切片去向。")
        for key in ("creator_ref", "host_ref"):
            _id(affiliate[key])
        for key in ("viewing_check", "clip_destination"):
            _text(affiliate[key])
    elif affiliate is not None:
        _fail("抖音合成计划不接收菲律宾达人协作字段。")


def _same(old, value, *, version=True):
    return all(old[key] == value[key] for key in ("site", "key", "sku_id")
               + (("version",) if version else ()))


def bind(site, tool, value, load):
    validate(site, tool, value)
    data, pins = {}, {}
    def add(name, ident, upstream):
        data[name], pins[name] = load(ident, upstream, site)
        return data[name]
    if tool in INTAKES:
        p = add("plan", value["plan_task_id"], PLAN)
        if not _same(p, value) or p["session_id"] != value["session_id"] or value["receiver"] not in p["receivers"]:
            _fail("收件对象与该计划的站点、版本、商品、场次或岗位不符。")
        if p["disposition"] != "ready_for_local_handoff":
            _fail("上游仍有权利或场次待核，未接收剪辑计划。")
        for name, expected_tool in (("team", workflow_live_team.TOOL), ("clip", workflow_teardown.TOOL_ID)):
            original = p["upstream"][name]
            add(name, original["task_id"], expected_tool)
            if pins[name] != original:
                _fail("导流上游资料已改变，暂不签收。")
        return data, pins
    if previous_id := value["previous_task_id"]:
        old = add("previous", previous_id, tool)
        if not _same(old, value, version=False) or old["version"] + 1 != value["version"] \
                or old["language"] != value["language"]:
            _fail("上一版须为同站、同编号、同语言与同 SKU 的连续版本。")
    if tool == SOP:
        r = add("replay", value["replay_task_id"], workflow_live.TOOL)
        if (r["scope"], r["session_id"], r["sku_id"]) != (SYNTHETIC, value["session_id"], value["sku_id"]):
            _fail("复盘来源不是同一站点、SKU 和场次。")
        keys = {(_when(row["start"]), _when(row["end"])) for row in r["windows"] if row["sku_id"] == value["sku_id"]}
        if any((_when(x["start"]), _when(x["end"])) not in keys for x in value["step_changes"]):
            _fail("更新引用的时段不属于该 SKU 的已留存复盘。")
    else:
        t = add("team", value["team_task_id"], workflow_live_team.TOOL)
        clip = add("clip", value["clip_task_id"], workflow_teardown.TOOL_ID)
        if (t["scope"], t["session_id"], t["product"]["sku_id"]) != (SYNTHETIC, value["session_id"], value["sku_id"]):
            _fail("该团队计划不属于所填的场次或 SKU。")
        w = t["session_window"]
        from_team = datetime.fromisoformat(w["date"]+"T"+w["start"]).replace(tzinfo=LOCAL_TIME)
        if (w["timezone"] != value["timezone"] or from_team != _when(value["stage_times"]["live_start"])
                or not clip["synthetic_material"] or clip["source_sha256"] != value["clip_sha256"]
                or value["clip_end_ms"] > math.floor(clip["duration_seconds"]*1000)):
            _fail("场次日期、媒体指纹、素材类型或切片时长与原回执不符。")
        if site == "PH":
            finish = datetime.fromisoformat(w["date"]+"T"+w["end"]).replace(tzinfo=LOCAL_TIME)
            if (value["affiliate"]["host_ref"] != t["shift_owners"]["host"]
                    or _when(value["stage_times"]["live_to_video"]) < finish):
                _fail("菲律宾主持人与团队草案不符，或反向切片早于场次结束。")
    return data, pins


def _sop_result(value, upstream):
    replay = upstream["replay"]
    previous = upstream.get("previous")
    steps = {row["step_id"]: row for row in (previous or {}).get("accepted_steps", [])}
    windows = {(_when(row["start"]), _when(row["end"])): row for row in replay["windows"]
               if row["sku_id"] == value["sku_id"]}
    evidence = []
    for change in value["step_changes"]:
        row = windows[(_when(change["start"]), _when(change["end"]))]
        mature = row.get("mature")
        if change["review"] == "conflicting" or replay.get("conflicting_event_evidence"):
            state = "conflicting"
        elif change["review"] == "unverified" or row["state"] != "complete" or not mature:
            state = "pending_evidence"
        else:
            state = "reviewed_for_offline_library"
        evidence.append({**change, "state": state, "window_state": row["state"],
                         "fixture_mature_retained_revenue_minor": mature.get("retained_revenue_minor") if mature else None})
    holds = [row["step_id"] for row in evidence if row["state"] != "reviewed_for_offline_library"]
    if not holds:
        steps.update((row["step_id"], row) for row in evidence)
    currency = ROUTES[value["site"]][1]
    return {"language": value["language"], "timezone": ZONES[value["site"]], "currency":currency,
            "disposition": "pending_evidence" if holds else "saved_to_offline_library",
            "accepted_version": (previous or {}).get("accepted_version") if holds else value["version"],
            "accepted_steps": list(steps.values()),"candidate_changes": evidence,"unresolved_steps": holds,
            "source_event_conflicts": replay.get("conflicting_event_evidence"),
            "cod_eligibility": "not_verified" if value["site"] == "PH" else "not_applicable",
            "basis_limit":"逐款、逐窗合成观察，不能由此确认爆款、推广效果或对外话术。", "lift_verified": False}


def _plan_result(site, value, upstream):
    times = value["stage_times"]
    rights = value["rights"]
    validity = rights["expires_at"]
    required_until = max(_when(v) for v in times.values() if v is not None)
    holds = []
    if rights["status"] != "offline_test_only" or validity is None or _when(validity) <= required_until:
        holds.append("clip_rights_missing_or_expired")
    t = upstream["team"]["readiness"]
    if t in ("hold_opening", "hold_product"):
        holds.append("referenced_team_plan_has_hold")
    platform = ROUTES[site][0]
    return {"language":value["language"],"timezone":value["timezone"],
            "disposition":"hold_for_local_review" if holds else "ready_for_local_handoff",
            "holds":holds,"stage_times":times,"as_of":value["as_of"],
            "clip":{"sha256":value["clip_sha256"],"start_ms":value["clip_start_ms"],
                    "end_ms":value["clip_end_ms"],"product_binding":value["media_binding"],
                    "physical_product_verified":False,"publication_verified":False},
            "sample":value["sample"],"rights":rights,"affiliate":value["affiliate"],
            "spark_ads_authorized":False,"attribution":None,
            "measurement":"计划分别核对内容观看与直播入口时间、来源和口径。未观察到引流效果。",
            "next_check":value["next_check"],"receivers":[platform+"-"+r for r in INTAKES.values()]}


def chain_source(tool, value):
    if tool in INTAKES:
        return {"chain_key":value["key"],"version":value["version"]}
    return {"chain_key":value["key"],"version":value["version"],
            "language":value["language"] if tool == SOP else None}


def receipt(result, raw, report):
    return {"task_id":result["task_id"],"site":result["site"],"tool_id":result["tool_id"],
            "key":result["key"],"version":result["version"],"inputs":result["upstream"],
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),
            "model_called":False,"provider_called":False,"execution_authorized":False}


def _object_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode("utf-8")


def run_workflow(project, site, tool, value, task_dir, assets, progress, *, linked=None):
    if linked is None or assets:
        _fail("缺失已核对的本机证据绑定。")
    data, pins = linked
    result = {k:value[k] for k in ("site","key","version","sku_id","session_id","scope")}
    result.update(task_id=task_dir.name,tool_id=tool,upstream=pins,
                  model_called=False,provider_called=False,execution_authorized=False,published=False)
    if tool == SOP:
        result.update(_sop_result(value,data))
    elif tool == PLAN:
        result.update(_plan_result(site,value,data))
    else:
        plan = data["plan"]
        result.update(disposition="received_for_offline_review",receiver=value["receiver"],
                      plan_task_id=value["plan_task_id"],language=plan["language"],timezone=plan["timezone"],
                      clip=plan["clip"],stage_times=plan["stage_times"],rights=plan["rights"],
                      affiliate=plan["affiliate"],sample=plan["sample"],measurement=plan["measurement"],
                      next_check=plan["next_check"],attribution=None,spark_ads_authorized=False)
    if progress:
        progress("正在保存离线版本、关联证据和回执。")
    (task_dir/"live-offline").mkdir()
    paths={k:task_dir/name for k,name in OUTPUTS.items()}
    raw=_object_bytes(result)
    report=(f"{result['key']} · {site} · v{result['version']} · {result['disposition']}\n"
            "合成资料和本机记录，待人工核对。\n"+json.dumps(result,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
    for k, buf in {"json":raw,"report":report,"receipt":_object_bytes(receipt(result,raw,report))}.items():
        with paths[k].open("xb") as stream:
            stream.write(buf)
    return result,paths


def verify_saved(task, bundle):
    value=task["result"]
    if (task["local_files"]!=OUTPUTS or json.loads(bundle["json"])!=value
            or value["task_id"]!=task["id"] or value["site"]!=task["site"]
            or value["tool_id"]!=task["source"]["tool_id"]
            or json.loads(bundle["receipt"])!=receipt(value,bundle["json"],bundle["report"])):
        _fail("产物与本机版本回执不一致。")


def public_tools():
    def example(site, tool):
        base={"site":site,"scope":SYNTHETIC,"key":f"{site}-DEMO","version":1,"sku_id":"SYN-SKU","session_id":"SYN-LIVE"}
        if tool in INTAKES:
            return {**base,"plan_task_id":"0"*32,"receiver":ROUTES[site][0]+"-"+INTAKES[tool]}
        base.update(language=LANGUAGES[site][0],previous_task_id=None)
        if tool == SOP:
            return {**base,"replay_task_id":"0"*32, "step_changes":[{
                "step_id":"demo","sop":"复核演练","phrase":"商品条件以已核对的信息为准","kind":"ordinary",
                "start":"2026-11-18T12:00:00+08:00","end":"2026-11-18T12:05:00+08:00","review":"unverified","rationale":"示例待核"}]}
        return {**base,"team_task_id":"0"*32,"clip_task_id":"0"*32,"clip_sha256":"0"*64,"clip_start_ms":0,"clip_end_ms":1000,
                "media_binding":{"sku_id":"SYN-SKU","source_ref":"SYN-OBJECT"},"timezone":ZONES[site],
                "stage_times":{"video_to_live":"2026-11-20T18:00:00+08:00","live_start":"2026-11-20T19:30:00+08:00",
                               "live_to_video":"2026-11-21T12:00:00+08:00" if site=="PH" else None},
                "as_of":"2026-11-20T12:00:00+08:00",
                "sample":{"source_ref":"SYN-SAMPLE","scope":SYNTHETIC,
                          "observed_at":"2026-11-19T12:00:00+08:00","observation":"样例，并非外部调取。"},
                "rights":{"status":"unknown","evidence_ref":None,"expires_at":None,"spark_ads_status":"unknown"},
                "next_check":"核对入场来源、场次与计划是否一致。",
                "affiliate":{"creator_ref":"SYN-CREATOR","host_ref":"SYN-HOST","clip_destination":"仅本机草稿",
                             "viewing_check":"将内容观看与直播入场分开核对"} if site=="PH" else None}
    return [{"id":tool,"role_suffix":INTAKES.get(tool,"live-ops"),"sites":list(ROUTES),"label":label,
             "description":"使用同站已完成的本机任务号；下方编号是示例，需替换成真实测试回执。",
             "input_examples":{site:example(site,tool) for site in ROUTES},"max_media_files":0}
            for tool,label in ((SOP,"直播 SOP 与话术版本"),(PLAN,"短视频与直播导流离线计划"),
                               (DIRECTOR,"编导接收直播导流草案"),(LEAD,"负责人接收直播导流草案"))]

