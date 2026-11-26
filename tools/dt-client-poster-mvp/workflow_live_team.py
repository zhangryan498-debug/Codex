"""Offline livestream team plans from a supplied roster and rehearsal facts."""
from datetime import date, time
from hashlib import sha256
import json
from pathlib import Path
import re

from role_tools import ROUTES, SYNTHETIC

TOOL = "live-team-plan"
ZONES = {"CN": "Asia/Shanghai", "PH": "Asia/Manila"}
DUTIES = {"lead": "统筹与异常交接", "host": "主持与讲品", "assistant_host": "助播与反馈",
          "control": "场控与素材核对", "backup_host": "替补主播候选"}
CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,71}")
TOP = {"scope", "site", "session_id", "objective", "session_window", "people", "training_topics",
       "rehearsal_window", "product", "reported_exception"}


class WorkflowError(ValueError):
    """The given planning inputs cannot be interpreted safely."""


def _object(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise WorkflowError(f"{label}缺少必填项或包含不支持的字段，请参照本站示例。")
    return value


def _string(value, limit, label):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise WorkflowError(f"{label}须为非空单行文字，最多 {limit} 字。")
    return value.strip()


def _ident(value, label):
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise WorkflowError(f"{label}须为 1–72 位字母、数字、点、下划线或连字符。")
    return value


def _clock(value):
    if not isinstance(value, str) or not CLOCK.fullmatch(value):
        raise WorkflowError("时段须使用 24 小时制 HH:MM。")
    stamp = time.fromisoformat(value)
    return stamp.hour * 60 + stamp.minute


def _period(start, end):
    first, last = _clock(start), _clock(end)
    if first >= last:
        raise WorkflowError("当日时段的结束时间须晚于开始时间。")
    return first, last


def _window(value, site):
    fields = {"date", "start", "end", "timezone"}
    obj = _object(value, fields, fields, "时段")
    day = obj["date"]
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except (TypeError, ValueError):
        raise WorkflowError("时段日期须使用 YYYY-MM-DD。") from None
    if obj["timezone"] != ZONES[site]:
        raise WorkflowError(f"所选站点的时区须为 {ZONES[site]}。")
    return _period(obj["start"], obj["end"])


def _list(items, maximum, label):
    if not isinstance(items, list) or len(items) > maximum:
        raise WorkflowError(f"{label}须为列表，最多 {maximum} 项。")
    return items


def _person(value):
    fields = {"name", "role", "available_local", "bag_add_allowed"}
    obj = _object(value, fields, fields - {"bag_add_allowed"}, "人员")
    name = _string(obj["name"], 60, "姓名或测试编号")
    role = obj["role"]
    permission = obj.get("bag_add_allowed")
    if not isinstance(role, str) or role not in DUTIES or (permission is not None and type(permission) is not bool):
        raise WorkflowError("人员角色或购物袋添加权限格式不正确。")
    availability = []
    for entry in _list(obj["available_local"], 12, "可用时段"):
        if not isinstance(entry, str):
            raise WorkflowError("可用时段须为 HH:MM–HH:MM。")
        parts = re.split(r"[-–]", entry)
        if len(parts) != 2:
            raise WorkflowError("可用时段须为 HH:MM–HH:MM。")
        availability.append(_period(*parts))
    return {"name": name, "role": role, "bag_add_allowed": permission, "availability": availability}


def validate(site, value):
    if site not in ZONES:
        raise WorkflowError("当前站点无直播团队方案。")
    obj = _object(value, TOP, TOP - {"reported_exception"}, "团队方案")
    if obj["scope"] != SYNTHETIC or obj["site"] != site:
        raise WorkflowError("方案材料的站点须与所选站点一致，且标记为 synthetic_offline。")
    session_id = _ident(obj["session_id"], "场次编号")
    _string(obj["objective"], 400, "目标")
    session = _window(obj["session_window"], site)
    rehearsal = _window(obj["rehearsal_window"], site)
    if obj["session_window"]["date"] != obj["rehearsal_window"]["date"] or rehearsal[1] > session[0]:
        raise WorkflowError("此离线方案支持同日排班，彩排须在场次开始前结束。")
    people = [_person(row) for row in _list(obj["people"], 24, "人员")]
    if len({p["name"].casefold() for p in people}) != len(people):
        raise WorkflowError("人员须使用唯一标识，同一人不能重复填为其他岗位。")
    topics = [_string(t, 160, "培训项") for t in _list(obj["training_topics"], 16, "培训项")]
    fields = {"sku_id", "rehearsal_only", "bag_observation", "bag_status"}
    product = _object(obj["product"], fields, {"sku_id", "rehearsal_only"}, "计划商品")
    _ident(product["sku_id"], "SKU")
    if product["rehearsal_only"] is not True or product.get("bag_status", "unknown") not in ("present", "missing", "unknown"):
        raise WorkflowError("商品仅作彩排，购物袋状态须为 present、missing 或 unknown。")
    if "bag_observation" in product:
        _string(product["bag_observation"], 400, "购物袋来源说明")
    if obj.get("reported_exception") is not None:
        _string(obj["reported_exception"], 400, "异常记录")
    return session_id, session, rehearsal, people, topics


def _at(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _render_window(window):
    return dict(zip(("start", "end"), map(_at, window)))


def _covered(people, period):
    start, end = period
    windows = sorted((max(start, a), min(end, b)) for p in people for a, b in p["availability"]
                     if a < end and b > start)
    merged = []
    for left, right in windows:
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def _gaps(people, period):
    remaining = period[0]
    gaps = []
    for left, right in _covered(people, period):
        if remaining < left:
            gaps.append(_render_window((remaining, left)))
        remaining = right
    if remaining < period[1]:
        gaps.append(_render_window((remaining, period[1])))
    return gaps


def _owner(people, role, period):
    full = [p["name"] for p in people if p["role"] == role and not _gaps([p], period)]
    return full[0] if len(full) == 1 else None


def _product_check(site, product, people, session, rehearsal, lead):
    check = {"sku_id": product["sku_id"], "rehearsal_only": True,
             "observation": product.get("bag_observation"), "handoff_owner": lead}
    if site != "PH":
        return {**check, "status": "rehearsal_reference", "instruction": "以输入 SKU 核对样品与讲品清单。"}
    observed = product.get("bag_status", "unknown")
    eligible = [(start, p["name"]) for p in people if p["bag_add_allowed"] is True
                for start, _ in _covered([p], (rehearsal[0], session[1]))]
    earliest = min((start for start, _ in eligible), default=None)
    first = sorted({name for start, name in eligible if start == earliest})
    return {
        **check, "input_bag_status": observed,
        "status": "present_in_test_input" if observed == "present" else "hold_until_verified",
        "permission_by_person": {p["name"]: p["bag_add_allowed"] for p in people},
        "authorized_candidate": first[0] if len(first) == 1 else None,
        "first_candidate_available_local": _at(earliest) if earliest is not None else None,
        "instruction": ("彩排逐项核对购物袋所见 SKU，记录人工确认；只登记输入证据。"
                        if observed == "present" else
                        "暂停该商品讲解及下单指引。交给已声明有添加权限且当时可用的人员处理；"
                        "先确认权限，再核对购物袋内同一 SKU，交负责人确认后才考虑恢复该商品。"),
    }


def _build(site, value):
    session_id, session, rehearsal, people, topics = validate(site, value)
    lead = _owner(people, "lead", session)
    rehearsing = {role: _owner(people, role, rehearsal) for role in DUTIES}
    scheduled = {role: _owner(people, role, session) for role in DUTIES}
    host = [p for p in people if p["role"] == "host" and p["name"] == scheduled["host"]]
    backup = [p for p in people if p["role"] == "backup_host"]
    later = [(left, p["name"]) for p in backup for left, _ in _covered([p], session)]
    earliest = min((left for left, _ in later), default=None)
    backup_names = sorted({name for left, name in later if left == earliest})
    product = _product_check(site, value["product"], people, session, rehearsal, lead)
    pending = [{"owner": lead, "check": "确认人员接受草案、异常处理结果和离线彩排验收。"}]
    for role in ("lead", "host", "assistant_host", "control"):
        if not scheduled[role] or not rehearsing[role]:
            pending.append({"owner": lead, "check": f"确认 {DUTIES[role]}在场次和彩排的人员及交接。"})
    if not scheduled["backup_host"]:
        pending.append({"owner": lead, "check": "确认替补人选与可覆盖时段；待命可用性不代表接班已确认。"})
    if not topics:
        pending.append({"owner": rehearsing["lead"], "check": "补充商品讲解、岗位交接和异常演练的培训项。"})
    if product["status"] == "hold_until_verified":
        pending.append({"owner": lead, "check": "需确认有权限人员接手，保留同 SKU 购物袋复核及交接结果。"})
    exception = value.get("reported_exception")
    if exception:
        pending.append({"owner": lead, "check": "复核已报告异常并记录处理和交接结果。"})
    if scheduled["host"] is None:
        status = "hold_opening"
        action = "暂缓原定开场。仅在负责人确认可覆盖的主持人、重新排班及完成必要彩排后，才提出改期方案。"
    elif product["status"] == "hold_until_verified":
        status = "hold_product"
        action = product["instruction"]
    else:
        status = "human_review_required"
        action = "彩排后核对职责交接与未决项，由负责人复核草案。"
    return {
        "workflow": TOOL, "site": site, "scope": SYNTHETIC, "session_id": session_id,
        "role": ROUTES[site][0] + "-live-ops", "objective": value["objective"],
        "session_window": value["session_window"], "rehearsal_window": value["rehearsal_window"],
        "people": [{"name": p["name"], "role": p["role"], "duty": DUTIES[p["role"]],
                    "session_availability": list(map(_render_window, _covered([p], session))),
                    "rehearsal_availability": list(map(_render_window, _covered([p], rehearsal)))} for p in people],
        "shift_owners": scheduled, "rehearsal_owners": rehearsing,
        "training": {"owner": rehearsing["lead"], "checklist": topics, "completed": False},
        "rehearsal_steps": [
            {"owner": rehearsing["lead"], "check": "对照本地时间、签到并核对替补和沟通方式。"},
            {"owner": rehearsing["host"], "check": "试讲已知商品事实，向助播和尚待核实事项交接。"},
            {"owner": rehearsing["control"], "check": ("确认同 SKU 购物袋可见及角色权限，仅演练。"
                                                     if site == "PH" else "核对演示素材、设备和轮换信号。")},
            {"owner": rehearsing["lead"], "check": "演练异常暂停条件、记录交接及下一次复核。"},
        ],
        "host_coverage": {"no_confirmed_host_shift": _gaps(host, session),
                          "no_primary_or_backup_available": _gaps([p for p in people if p["role"] in
                                                                    ("host", "backup_host")], session),
                          "first_backup_candidate": backup_names[0] if len(backup_names) == 1 else None,
                          "first_backup_available_local": _at(earliest) if earliest is not None else None,
                          "backup_accepted": None},
        "product": product, "reported_exception": exception, "handoff": {"owner": lead, "action": action},
        "pending_confirmations": pending, "readiness": status,
        "execution_authorized": False, "provider_called": False, "model_called": False,
    }


def _report(result):
    def clean(value):
        return re.sub(r"([\\\x60*{}\[\]()#+.!_>|<])", r"\\\1", str(value) if value is not None else "待确认")
    s, r = result["session_window"], result["rehearsal_window"]
    lines = [f"# 直播团队彩排草案 {clean(result['session_id'])}", "", "本机合成离线方案，待人工审核。",
             f"- 站点 {result['site']} · {s['date']} · {s['timezone']}",
             f"- 场次 {s['start']}–{s['end']}；彩排 {r['start']}–{r['end']}",
             f"- 商品 {clean(result['product']['sku_id'])}", "", "## 分工和班表", ""]
    for role, owner in result["shift_owners"].items():
        lines.append(f"- {DUTIES[role]}：场次 {clean(owner)}；彩排 {clean(result['rehearsal_owners'][role])}")
    for person in result["people"]:
        slots = [f"{w['start']}–{w['end']}" for w in person["session_availability"]]
        lines.append(f"- {clean(person['name'])}（{person['duty']}）场次可用：{'、'.join(slots) or '无所报可用时段'}")
    lines += ["", "## 培训与彩排", "",
              f"- 培训负责人：{clean(result['training']['owner'])}"]
    lines += [f"- 培训核对：{clean(t)}" for t in result["training"]["checklist"]]
    lines += [f"- 彩排：{clean(x['owner'])} — {x['check']}" for x in result["rehearsal_steps"]]
    lines += ["", "## 异常与交接", "", f"- 结论：{result['readiness']}",
              f"- 输入异常：{clean(result['reported_exception'])}",
              f"- 交接：{clean(result['handoff']['owner'])} — {result['handoff']['action']}",
              f"- 商品核对：{result['product']['instruction']}"]
    if result["product"]["status"] == "hold_until_verified":
        p = result["product"]
        lines.append(f"- 添加候选：{clean(p['authorized_candidate'])}；最早可用 {clean(p['first_candidate_available_local'])}。")
    host = result["host_coverage"]
    if host["first_backup_available_local"] is not None:
        lines.append(f"- 替补候选：{clean(host['first_backup_candidate'])}；"
                     f"最早可用 {host['first_backup_available_local']}，接班待确认。")
    for gap in host["no_primary_or_backup_available"]:
        lines.append(f"- 主播与替补皆不可用：{gap['start']}–{gap['end']}。")
    lines += ["", "## 待确认", ""]
    return "\n".join(lines + [f"- {clean(x['owner'])}：{x['check']}" for x in result["pending_confirmations"]]) + "\n"


def _save_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return sha256(path.read_bytes()).hexdigest()


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress):
    if tool_id != TOOL or assets:
        raise WorkflowError("直播团队方案只接收本站的结构化资料。")
    result = _build(site, payload)
    platform = ROUTES[site][0]
    sources = [f"runtime-agents/{platform}-live-ops/agent.md",
               f".agents/skills/{platform}-live-ops-playbook/SKILL.md"]
    evidence = [{"path": item, "sha256": sha256((project / item).read_bytes()).hexdigest()} for item in sources]
    if progress:
        progress("正在保存直播团队彩排草案及待确认事项。")
    folder = Path(task_dir) / TOOL
    try:
        folder.mkdir()
    except FileExistsError:
        raise WorkflowError("该任务已有团队草案，请从任务记录取回。") from None
    paths = {key: folder / name for key, name in (
        ("json", "plan.json"), ("report", "plan.md"), ("receipt", "receipt.json"))}
    json_hash = _save_json(paths["json"], result)
    with paths["report"].open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_report(result))
    _save_json(paths["receipt"], {"workflow": TOOL, "site": site, "role": result["role"],
        "session_id": result["session_id"], "task_id": Path(task_dir).name, "scope": SYNTHETIC,
        "execution": "deterministic_local_planning", "source_methods": evidence,
        "input_sha256": sha256((Path(task_dir) / "role-input.json").read_bytes()).hexdigest(),
        "result_sha256": json_hash, "report_sha256": sha256(paths["report"].read_bytes()).hexdigest(),
        "model_called": False, "provider_called": False, "execution_authorized": False})
    return result, paths


def public_tools():
    def example(site):
        def window(start, end):
            return {"date": "2026-11-23", "start": start, "end": end, "timezone": ZONES[site]}
        return {"scope": SYNTHETIC, "site": site, "session_id": f"DEMO-{site}-TEAM-01",
                "objective": "线下核对一次虚构商品直播团队的排班、培训和彩排。",
                "session_window": window("19:30", "20:30"), "rehearsal_window": window("19:00", "19:20"),
                "people": [{"name": f"{site}-{role}", "role": role, "available_local": ["18:50–20:45"],
                            **({"bag_add_allowed": role == "control"} if site == "PH" else {})} for role in DUTIES],
                "training_topics": ["已知商品事实核对", "分工与异常交接演练"],
                "product": {"sku_id": f"DEMO-{site}-SKU", "rehearsal_only": True, "bag_status": "unknown"},
                "reported_exception": None}
    return [{"id": TOOL, "label": "直播团队分工与彩排方案", "role_suffix": "live-ops", "sites": list(ZONES),
             "description": "用当天人员可用时段、培训项和商品观察生成可审核草案；未确认的人员或权限留待确认。",
             "execution_scope": "仅合成离线方案，使用本站时区和当日时段。",
             "input_example": example("CN"), "input_examples": {s: example(s) for s in ZONES},
             "max_media_files": 0}]

