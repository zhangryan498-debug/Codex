"""Offline assortment, same-site receiving and complete rehearsal timeline."""

from collections import defaultdict
from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import CONTROL, RECOVERY, amount, check, identify, posture, snapshot_at, stamp, task_id
from workflow_live_preflight import TOOL as PREFLIGHT
from workflow_live_team import TOOL as TEAM


SAVE = "live-assortment-plan"
INTAKE = "live-assortment-intake"
REHEARSE = "live-full-session-rehearsal"
TOOLS = (SAVE, INTAKE, REHEARSE)
FILES = {"json": "live-program.json", "report": "live-program.md", "receipt": "live-program-receipt.json"}
CATEGORIES = ("traffic", "margin", "hero", "image", "clearance")
IDENTITY = "account_id shop_id room_id session_id"
PRODUCT = "sku_id revision category rationale specification pieces_per_sale_unit unit facts readiness_task_id"
EVIDENCE = "site currency account_id shop_id room_id session_id sku_id revision unit stock_unit price_minor floor_minor seller_revenue_minor contribution_minor approved qualified source_ref observed_at valid_until stock warehouse"
STAGES = ("opening", "present", "switch", "pause", "resume", "encore", "closing")


class WorkflowError(ValueError):
    pass


def request_key(tool, p):
    return f"{p['identity']['session_id']}:{p['version']}" if tool == SAVE else p["job_id"]


def _timing(row, a, b):
    if not stamp(row[a]) < stamp(row[b]):
        raise WorkflowError("排期或资料区间的结束时间须晚于开始。")
    return stamp(row[a]), stamp(row[b])


def _same(site, evidence, p, product):
    expect = {"site": site, "currency": p["currency"], **p["identity"],
              **{key: product[key] for key in ("sku_id", "revision", "unit")}}
    if any(evidence.get(key) != value for key, value in expect.items()):
        raise WorkflowError("证据错站、错帐号、错场次、SKU、单位或商品修订。")


def _facts(site, p, row, at):
    f = row["facts"]
    if f is None:
        return
    check(f, EVIDENCE + (" cod" if site == "PH" else ""), "本站商品证据快照")
    _same(site, f, p, row)
    _timing(f, "observed_at", "valid_until")
    if stamp(f["observed_at"]) > at:
        raise WorkflowError("商品证据的采集时间晚于计划。")
    term(f["source_ref"], "商品证据来源", True)
    for k in ("price_minor", "floor_minor", "seller_revenue_minor"):
        amount(f[k], k)
    amount(f["contribution_minor"],"合成销售单位贡献",lo=-10**12)
    if f["stock_unit"] is not None and f["stock_unit"] != row["unit"]:
        raise WorkflowError("库存单位与销售单位不同，不能按同一单位计算。")
    for k in ("approved", "qualified"):
        if f[k] is not None and type(f[k]) is not bool:
            raise WorkflowError("批准或资格须为明确状态或未知。")
    w = f["warehouse"]
    if w is not None:
        check(w,"id source_ref state","仓库资料")
        identify(w["id"],"仓库号")
        term(w["source_ref"],"仓库证据",True)
        if w["state"] not in ("local_supported","not_local",None):
            raise WorkflowError("仓库范围须明确。")
    if f["stock"] is not None:
        if f["stock"].get("captured_at") is not None and stamp(f["stock"]["captured_at"])>at:
            raise WorkflowError("库存采集不得晚于本机计划。")
        snapshot_at(site, {**p["identity"], **row}, f["stock"], p["evaluated_at"])
    if site == "PH" and f["cod"] is not None:
        c = f["cod"]
        check(c, "source_ref cohort_id placed signed refused open", "同批 COD")
        term(c["source_ref"],"COD 合成来源",True)
        identify(c["cohort_id"],"COD 批号")
        for k in ("placed","signed","refused","open"):
            amount(c[k],k)
        known_parts = sum(c[k] for k in ("signed","refused","open") if c[k] is not None)
        if c["placed"] is not None and (known_parts > c["placed"] or (
                all(c[k] is not None for k in ("signed", "refused", "open")) and known_parts != c["placed"])):
            raise WorkflowError("COD 签收、拒收、在途须与该批下单总数对账。")


def _plan(site, p, at):
    start, end = _timing(p, "starts_at", "ends_at")
    if not at <= start or (end - start).total_seconds() > 21600:
        raise WorkflowError("排品应在该次演练前登记，最长六小时。")
    amount(p["version"],"计划版本",1,10**6,False)
    if (p["version"] == 1) != (p["previous_task_id"] is None):
        raise WorkflowError("仅首版无前序，后续须引用相同场次上一版。")
    if p["previous_task_id"]:
        task_id(p["previous_task_id"],"上一版排品")
    if not isinstance(p["products"],list) or not 1 <= len(p["products"]) <= 40:
        raise WorkflowError("须提交一至 40 个候选 SKU。")
    seen = set()
    for r in p["products"]:
        check(r,PRODUCT,"货盘 SKU")
        identify(r["sku_id"],"商品 SKU")
        if r["sku_id"] in seen:
            raise WorkflowError("底表 SKU 不得重复；各时间段的独立分配在时间轴登记。")
        seen.add(r["sku_id"])
        amount(r["revision"],"商品修订",1,10**6,False)
        amount(r["pieces_per_sale_unit"],"每销售单位件数",1,10000)
        if r["category"] not in CATEGORIES or r["unit"] not in ("sales_package","single_piece"):
            raise WorkflowError("须标明五类角色之一和销售单位。")
        if r["unit"] == "single_piece" and r["pieces_per_sale_unit"] not in (None,1):
            raise WorkflowError("单件销售单位只能登记为一件或未知。")
        term(r["rationale"],"排品理由")
        term(r["specification"],"规格",True)
        if r["readiness_task_id"] is not None:
            task_id(r["readiness_task_id"],"开场准备回执")
        _facts(site,p,r,at)
    if not isinstance(p["allocations"],list) or not 1 <= len(p["allocations"]) <= 100:
        raise WorkflowError("须提交一至 100 个时间段分配或重复引用。")
    distinct = {}
    for row in p["allocations"]:
        check(row,"id sku_id revision start end sale_units stage owner","时间轴分配")
        identify(row["id"],"分配编号")
        if row["sku_id"] not in seen or row["stage"] not in ("first_pass","encore"):
            raise WorkflowError("时间段须引用底表 SKU 及明确首轮或返场。")
        product = next(r for r in p["products"] if r["sku_id"] == row["sku_id"])
        if type(row["revision"]) is not int or row["revision"] != product["revision"]:
            raise WorkflowError("分配使用了错误的商品修订。")
        amount(row["sale_units"],"销售单位个数",1,10**6,False)
        term(row["owner"],"时间段责任人")
        left,right = _timing(row,"start","end")
        if left < start or right > end:
            raise WorkflowError("排品时间必须位于本站同一场次。")
        if row["id"] in distinct and distinct[row["id"]] != row:
            raise WorkflowError("同一分配编号出现不同内容，须分别核对。")
        distinct[row["id"]] = row
    rows = sorted(distinct.values(), key=lambda r:(stamp(r["start"]),r["id"]))
    if any(stamp(a["end"]) > stamp(b["start"]) for a,b in zip(rows, rows[1:])):
        raise WorkflowError("不允许同场独立商品时段重叠。")
    firsts = {}
    for row in rows:
        if row["stage"] == "encore" and row["sku_id"] not in firsts:
            raise WorkflowError("返场必须接在同 SKU 首轮之后。")
        if row["stage"] == "first_pass":
            firsts[row["sku_id"]] = row["id"]
    return rows


def validate(site, tool, p):
    tail = {SAVE:"version previous_task_id starts_at ends_at products allocations",
            INTAKE:"plan_task_id version",
            REHEARSE:"intake_task_id team_task_id switch_task_id control_task_id restore_task_id focus_sku_id focus_revision checkpoints"}[tool]
    if tool==INTAKE and "lead_review_task_id" in p:
        tail+=" lead_review_task_id"
        task_id(p["lead_review_task_id"],"九岗批准修订")
    check(p,"site currency scope job_id identity evaluated_at owner "+tail,"同场本机直播")
    if (p["site"],p["currency"],p["scope"]) != (site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("只允许所选本站、原币及显式合成演练。")
    identify(p["job_id"],"任务编号")
    check(p["identity"],IDENTITY,"场次身份")
    for key in p["identity"]:
        identify(p["identity"][key],key)
    term(p["owner"],"负责人")
    at = stamp(p["evaluated_at"])
    if tool == SAVE:
        return _plan(site,p,at)
    if tool == INTAKE:
        amount(p["version"],"计划版本",1,10**6,False)
        task_id(p["plan_task_id"],"货盘计划")
    else:
        for k in ("intake_task_id","team_task_id"):
            task_id(p[k],k)
        for k in ("switch_task_id","control_task_id","restore_task_id"):
            if p[k] is not None:
                task_id(p[k],k)
        if len({bool(p[k]) for k in ("switch_task_id","control_task_id","restore_task_id")})!=1:
            raise WorkflowError("控制回执和恢复回执须同给或同为未知。")
        identify(p["focus_sku_id"],"首次检查 SKU")
        amount(p["focus_revision"],"首次检查修订",1,10**6,False)
        if not isinstance(p["checkpoints"],list) or not 7 <= len(p["checkpoints"]) <= 80:
            raise WorkflowError("须记录从开场到收尾的时间点。")
        for mark in p["checkpoints"]:
            check(mark,"stage time allocation_id owner observation","本机彩排记录")
            if mark["stage"] not in STAGES:
                raise WorkflowError("未知的彩排阶段。")
            stamp(mark["time"])
            if mark["allocation_id"] is not None:
                identify(mark["allocation_id"],"计划分配编号")
            term(mark["owner"],"首责人")
            term(mark["observation"],"核对或交接记录",True)
    return None


def bind(site, tool, p, load, history, review_approval=None):
    rows = validate(site,tool,p)
    pins, data = {}, {"allocations":rows}
    def get(name, ident, kind):
        data[name],pins[name]=load(ident,site,kind)
        return data[name]
    if tool == SAVE:
        versions = history(site,tool,p["identity"]["session_id"])
        if p["version"] < max((v["version"] for v in versions),default=0):
            raise WorkflowError("已有同场新版，旧版只保留历史。")
        if p["previous_task_id"]:
            previous = get("previous",p["previous_task_id"],SAVE)
            if ((previous["identity"],previous["version"]+1)!=(p["identity"],p["version"])
                or stamp(previous["evaluated_at"]) >= stamp(p["evaluated_at"])):
                raise WorkflowError("只接受同场同对象且连续的上一版。")
        for r in p["products"]:
            if r["readiness_task_id"]:
                pre = get("preflight:"+r["sku_id"],r["readiness_task_id"],PREFLIGHT)
                if pre["identity"] != {**p["identity"],"sku_id":r["sku_id"],"revision":r["revision"]}:
                    raise WorkflowError("开场资料与货盘对象或修订不符。")
                if stamp(pre["evaluated_at"]) > stamp(p["evaluated_at"]):
                    raise WorkflowError("开场资料晚于计划记录。")
    elif tool == INTAKE:
        plan = get("plan",p["plan_task_id"],SAVE)
        _require_current(p,plan,history,site)
        if p["version"] != plan["version"] or stamp(p["evaluated_at"]) < stamp(plan["evaluated_at"]):
            raise WorkflowError("收件版本或时点与原货盘不符。")
        _load_plan_preflights(plan,get,pins)
        if "lead_review_task_id" in p:
            data["lead_review"],pins["lead_review"]=review_approval(site,p,plan,pins["plan"])
    else:
        intake = get("intake",p["intake_task_id"],INTAKE)
        plan = get("plan",intake["plan_task_id"],SAVE)
        _require_current(p,plan,history,site)
        if (intake["identity"] != p["identity"] or stamp(intake["evaluated_at"])>stamp(p["evaluated_at"])
                or intake["upstream"]["plan"] != pins["plan"]):
            raise WorkflowError("收件与演练身份、时点或货盘内容不符。")
        if "lead_review_intake" in intake:
            inherited=intake["lead_review_intake"]
            reference={**p,"lead_review_task_id":inherited["approval_task_id"],"evaluated_at":plan["window"]["start"]}
            _,pins["lead_review"]=review_approval(site,reference,plan,pins["plan"])
            if pins["lead_review"]!=inherited["approval_source"]:
                raise WorkflowError("收件的批准修订不能再推进。")
        team = get("team",p["team_task_id"],TEAM)
        if team["session_id"] != p["identity"]["session_id"]:
            raise WorkflowError("人员排班不是当前场次。")
        focus = next((r for r in plan["products"] if r["sku_id"]==p["focus_sku_id"]),None)
        if focus is None or focus["revision"] != p["focus_revision"]:
            raise WorkflowError("演练首次检查未对齐货盘 SKU 修订。")
        _load_plan_preflights(plan,get,pins)
        data["focus_preflight"]=data.get("preflight:"+focus["sku_id"])
        if p["control_task_id"]:
            control=get("control",p["control_task_id"],CONTROL)
            restore=get("restore",p["restore_task_id"],RECOVERY)
            if any(r["session_id"]!=p["identity"]["session_id"] for r in (control,restore)):
                raise WorkflowError("切品与恢复资料不是本站同场次。")
            paused=get("paused_control",restore["paused_task_id"],CONTROL)
            switched=get("switch",p["switch_task_id"],CONTROL)
            cursor=paused
            while cursor["version"]>switched["version"]:
                prior=get("control-v"+str(cursor["version"]-1),cursor["previous_task_id"],CONTROL)
                if ((prior["version"]+1,prior["session_id"])!=(cursor["version"],p["identity"]["session_id"])
                        or pins["control-v"+str(prior["version"])]!=cursor["upstream"].get("previous")
                        or stamp(prior["evaluated_at"])>=stamp(cursor["evaluated_at"])):
                    raise WorkflowError("切品到暂停的历史回执前序断开。")
                cursor=prior
            if cursor["task_id"]!=switched["task_id"] or switched["state"]!="propose_switch":
                raise WorkflowError("切品需来自暂停所绑定的原分支。")
            linked = control["upstream"].get("restoration")
            if (linked != pins["restore"] or restore["upstream"].get("paused")!=pins["paused_control"]
                    or paused["session_id"]!=p["identity"]["session_id"]):
                raise WorkflowError("控制与恢复资料未绑定同场暂停凭据。")
    return pins,data


def _load_plan_preflights(plan,get,pins):
    for r in plan["products"]:
        if r["readiness_task_id"]:
            label="preflight:"+r["sku_id"]
            get(label,r["readiness_task_id"],PREFLIGHT)
            if plan["upstream"][label]!=pins[label]:
                raise WorkflowError("开场资料与货盘的保存版本不符。")


def _preflight_reasons(site,pre,at):
    if pre["state"]!="READY_OFFLINE":
        return ["linked_preflight_not_ready"]
    reasons=[]
    quote=pre.get("price_quote")
    expires=quote and quote.get("valid_until_exclusive")
    if not expires or not stamp(pre["evaluated_at"])<=at<stamp(expires):
        reasons.append("linked_price_due_or_expired")
    names=("account","product","compliance","cod","prepaid") if site=="PH" else ("account","product","compliance","prepaid")
    for n in names:
        status=pre["screened_evidence"][n]
        if status["disposition"]=="OUT_OF_SCOPE_WITH_SOURCE":
            continue
        row=status["evidence"]
        if not row or status["disposition"]!="SUPPORTED_SYNTHETIC" or not stamp(row["valid_from"])<=at<stamp(row["valid_until"]):
            reasons.append("linked_"+n+"_unverified_or_expired")
    terms=pre["benefit_terms"]
    if not terms.get("eligible") or not terms.get("valid_from_inclusive") or not terms.get("valid_until_exclusive") or not (
            stamp(terms["valid_from_inclusive"])<=at<stamp(terms["valid_until_exclusive"])):
        reasons.append("linked_eligibility_unverified_or_expired")
    return reasons


def _require_current(p, plan, history, site):
    if (plan["identity"] != p["identity"] or plan["version"] != max(
            (v["version"] for v in history(site,SAVE,p["identity"]["session_id"])),default=0)):
        raise WorkflowError("货盘不属于本站当前场次或已被新版替代。")


def _assess_product(site,p,r,allocs,pre,at):
    pending, blocked = [], []
    good_times = [stamp(x["end"])-timedelta(microseconds=1) for x in allocs]
    availability_at = max([at,*good_times])
    q = sum(x["sale_units"] for x in allocs)
    if not r["specification"] or r["pieces_per_sale_unit"] is None:
        pending.append("specification_or_piece_factor_unknown")
    f = r["facts"]
    price, available = None, None
    cod = None
    if not f or not f["source_ref"]:
        pending.append("price_stock_or_qualification_source_missing")
    else:
        if availability_at >= stamp(f["valid_until"]):
            blocked.append("product_evidence_expired")
        if None in (f["price_minor"],f["floor_minor"],f["approved"],f["qualified"]):
            pending.append("price_floor_approval_or_qualification_unknown")
        if f["approved"] is False or f["qualified"] is False:
            blocked.append("price_or_qualification_rejected")
        if None not in (f["price_minor"],f["floor_minor"]) and f["price_minor"] < f["floor_minor"]:
            blocked.append("below_synthetic_floor")
        if f["approved"] is True and f["qualified"] is True and None not in (f["price_minor"],f["floor_minor"]) and not blocked:
            price = f["price_minor"]
        if f["stock"] is None:
            pending.append("stock_snapshot_missing")
        else:
            current = snapshot_at(site,{**p["identity"],**r},f["stock"],availability_at.isoformat())
            available = current["available_units"] if f["stock_unit"] else None
            if not f["stock_unit"]:
                pending.append("stock_sales_unit_unverified")
            if current["fresh"] is False:
                blocked.append("inventory_snapshot_expired")
            pending.extend(current["missing"])
            blocked.extend(current["blocked"])
            if available is not None and q > available:
                blocked.append("independent_allocations_exceed_stock")
        w=f["warehouse"]
        if not w or not w["source_ref"] or w["state"] is None:
            pending.append("warehouse_unknown")
        elif w["state"]!="local_supported":
            blocked.append("warehouse_not_in_fixture_scope")
        if site=="PH":
            c=f["cod"]
            if not c or not c["source_ref"] or any(c[k] is None for k in ("placed","signed","refused","open")):
                pending.append("cod_cohort_unknown")
            cod = {"cohort":c,"inventory_basis":"placed_orders_not_signed","cash_collected":None}
    if f and f["source_ref"] and None in (f["seller_revenue_minor"],f["contribution_minor"]):
        pending.append("seller_revenue_or_contribution_unknown")
    if pre:
        until=pre["price_quote"].get("valid_until_exclusive") if pre.get("price_quote") else None
        if until and availability_at>=stamp(until):
            blocked.append("linked_price_approval_expired")
        if pre["state"]!="READY_OFFLINE" or not until or price is None:
            pending.append("linked_preflight_not_ready")
        elif (price!=pre["price_quote"]["price_minor"] or (f and f["stock"]!=pre["stock_at_review"]["snapshot"])):
            blocked.append("conflicting_linked_preflight_quote_or_stock")
        for reason in _preflight_reasons(site,pre,availability_at):
            (blocked if pre["state"]=="READY_OFFLINE" else pending).append(reason)
    return {**r,"allocations":allocs,"planned_sale_units":q,
            "planned_pieces":None if r["pieces_per_sale_unit"] is None else q*r["pieces_per_sale_unit"],
            "reference_price_minor":None if blocked else price,"available_sale_units":available,"cod_view":cod,
            "reference_seller_revenue_minor":f["seller_revenue_minor"] if price is not None and not blocked else None,
            "reference_contribution_minor":f["contribution_minor"] if price is not None and not blocked else None,
            "blocked":sorted(set(blocked)),"pending":sorted(set(pending)),"state":posture(blocked,pending)}


def _save(site,p,data):
    buckets=defaultdict(list)
    for row in data["allocations"]:
        buckets[row["sku_id"]].append(row)
    at=stamp(p["evaluated_at"])
    products=[_assess_product(site,p,r,buckets[r["sku_id"]],data.get("preflight:"+r["sku_id"]),at)
              for r in p["products"]]
    all_roles={r["category"] for r in products if r["allocations"]}
    pending=[r["sku_id"] for r in products if r["pending"] or not r["allocations"]]
    blocked=[r["sku_id"] for r in products if r["blocked"]]
    unknown=sorted(set(CATEGORIES)-all_roles)
    units={unit:sum(r["planned_sale_units"] for r in products if r["unit"]==unit)
           for unit in sorted({r["unit"] for r in products})}
    known_pieces=sum(r["planned_pieces"] for r in products if r["planned_pieces"] is not None)
    def projected(field):
        if any(r[field] is None for r in products):
            return None
        return sum(r["planned_sale_units"]*r[field] for r in products)
    return {"version":p["version"],"window":{"start":p["starts_at"],"end":p["ends_at"]},"products":products,
            "allocation_timeline":data["allocations"],"unique_allocations":len(data["allocations"]),
            "repeated_references_ignored":len(p["allocations"])-len(data["allocations"]),
            "planned_by_unit":units,"known_piece_subtotal":known_pieces,
            "planned_physical_pieces":known_pieces if all(r["planned_pieces"] is not None for r in products) else None,
            "projected_buyer_goods_minor":projected("reference_price_minor"),
            "projected_seller_revenue_minor":projected("reference_seller_revenue_minor"),
            "projected_contribution_minor":projected("reference_contribution_minor"),
            "actual_revenue":None,"missing_categories":unknown,
            "pending_skus":pending,"blocked_skus":blocked,"state":posture(blocked,pending+unknown)}


def _intake(site,p,data):
    plan=data["plan"]
    at=stamp(p["evaluated_at"])
    window_end=stamp(plan["window"]["end"])
    if at>=window_end:
        return {"state":"BLOCK","plan_task_id":plan["task_id"],"version":plan["version"],
                "received_for_rehearsal":False,"pending_skus":plan["pending_skus"],"blocked_skus":plan["blocked_skus"],
                "reason":"session_window_expired"}
    expired=[row["sku_id"] for row in plan["products"]
             if _checkpoint_product_gaps(site,p,row,data.get("preflight:"+row["sku_id"]),at)]
    late=at>stamp(plan["window"]["start"])
    state="BLOCK" if expired or plan["state"]=="BLOCK" else "PENDING" if late else plan["state"]
    reason="expired_evidence" if expired else "received_after_planned_opening" if late else None
    return {"state":state,"plan_task_id":plan["task_id"],"version":plan["version"],
            "pending_skus":plan["pending_skus"],"blocked_skus":sorted(set(plan["blocked_skus"]+expired)),
            "received_for_rehearsal":state=="READY_OFFLINE","reason":reason}


def _verify_team(p, plan, team):
    window = team["session_window"]
    zone = "Asia/Shanghai" if p["site"]=="CN" else "Asia/Manila"
    expected = {"start":window["date"]+"T"+window["start"]+":00+08:00",
                "end":window["date"]+"T"+window["end"]+":00+08:00"}
    if window["timezone"]!=zone or not stamp(expected["start"])<=stamp(plan["window"]["start"])<stamp(plan["window"]["end"])<=stamp(expected["end"]):
        raise WorkflowError("直播团队岗位、时区或范围与当前货盘不一致。")
    if team["product"].get("sku_id") != p["focus_sku_id"]:
        raise WorkflowError("团队试讲的商品与当前首次检查 SKU 不一致。")
    return {role:name if name==team["rehearsal_owners"].get(role) else None
            for role,name in team["shift_owners"].items()}


def _checkpoint_product_gaps(site,p,row,pre,at):
    gaps=[]
    f=row["facts"]
    if f and at>=stamp(f["valid_until"]):
        gaps.append("product_evidence_expired")
    if f and f["stock"] and snapshot_at(site,{**p["identity"],**row},f["stock"],at.isoformat())["fresh"] is False:
        gaps.append("inventory_snapshot_expired")
    if pre and pre["state"]=="READY_OFFLINE":
        gaps.extend(_preflight_reasons(site,pre,at))
    return gaps


def _opening_hold(plan, intake, team, pre, at):
    holds=[]
    if intake["state"]=="BLOCK" or plan["state"]=="BLOCK":
        holds.append("assortment_blocked")
    elif (not intake["received_for_rehearsal"] or stamp(intake["evaluated_at"])>at):
        holds.append("assortment_pending")
    if team["readiness"] in ("hold_opening","hold_product"):
        holds.append(team["readiness"])
    if not pre or pre["state"]!="READY_OFFLINE":
        holds.append("starting_eligibility_source_unconfirmed")
    else:
        holds.extend(_preflight_reasons(pre["site"],pre,at))
        inv=pre["stock_at_review"]["snapshot"]
        if (at-stamp(inv["captured_at"])).total_seconds()>inv["max_age_seconds"]:
            holds.append("starting_stock_expired")
    return holds


def _rehearsal(p,data,pins):
    intake,plan,team=data["intake"],data["plan"],data["team"]
    owners=_verify_team(p,plan,team)
    by_id={r["id"]:r for r in plan["allocation_timeline"]}
    by_sku={r["sku_id"]:r for r in plan["products"]}
    marks=p["checkpoints"]
    times=[stamp(m["time"]) for m in marks]
    stages=[m["stage"] for m in marks]
    if sorted(set(times))!=times or set(STAGES)-set(stages) or (stages[0],stages[-1])!=("opening","closing") or any(stages.count(k)!=1 for k in ("opening","closing","pause","resume")):
        raise WorkflowError("彩排须依时序记录所有阶段，开场首位、收尾末位。")
    if (times[0],times[-1])!=tuple(stamp(plan["window"][k]) for k in ("start","end")) or times[-1]>stamp(p["evaluated_at"]):
        raise WorkflowError("彩排记录须覆盖整场并在最后检查后保存。")
    holds=_opening_hold(plan,intake,team,data.get("focus_preflight"),times[0])
    checkpoints=[]
    current=None
    visited=set()
    paused=False
    closed=False
    for m,at in zip(marks,times):
        stage=m["stage"]
        role="host" if stage in ("present","encore") else "lead" if stage in ("opening","closing") else "control"
        if owners[role] is None:
            holds.append(role+"_unconfirmed")
        elif m["owner"]!=owners[role]:
            raise WorkflowError("实填责任人不属于同场已登记排班。")
        row=by_id.get(m["allocation_id"])
        if stage in ("opening","closing"):
            if m["allocation_id"] is not None:
                raise WorkflowError("开场和归档不得冒充商品分配。")
        elif not row or not (stamp(row["start"])<=at<stamp(row["end"])):
            raise WorkflowError("彩排所用的分配或时点不属于当前货盘。")
        if stage=="encore" and row["stage"]!="encore":
            raise WorkflowError("返场需要货盘的独立返场分配。")
        reasons=[]
        upstream={}
        if not m["observation"]:
            reasons.append("observation_missing")
        if holds and stage=="opening":
            reasons.extend(holds)
        if stage in ("present","switch","encore"):
            if not visited and (stage!="present" or row["stage"]!="first_pass" or
                    (row["sku_id"],row["revision"])!=(p["focus_sku_id"],p["focus_revision"])):
                if not holds:
                    raise WorkflowError("首次讲品须为开场 SKU、修订和首轮分配。")
            if (stage=="encore")!=(row["stage"]=="encore"):
                raise WorkflowError("首轮与返场需要对应各自的登记分配。")
            if paused:
                reasons.append("cannot_present_while_paused")
            if stage=="switch" and (not visited or by_id[current]["sku_id"]==row["sku_id"]):
                reasons.append("switch_without_a_different_active_product")
            if stage=="switch" and current and len(visited)==1:
                sw=data.get("switch")
                if not sw:
                    reasons.append("switch_proof_missing")
                elif (sw["active_sku"],sw["active_revision"],stamp(sw["evaluated_at"]))!=(row["sku_id"],row["revision"],at):
                    raise WorkflowError("首次切品与原恢复分支的同场、时点、SKU 或版本不符。")
                else:
                    upstream["switch"]=pins["switch"]
            product=by_sku[row["sku_id"]]
            if product["state"]!="READY_OFFLINE":
                reasons.append("sku_evidence_not_ready")
            reasons.extend(_checkpoint_product_gaps(p["site"],p,product,data.get("preflight:"+row["sku_id"]),at))
            if not reasons and not holds:
                current=row["id"]
                visited.add(current)
        if stage=="pause":
            if current!=row["id"] or paused:
                reasons.append("pause_without_active_matching_allocation")
            paused=True
            if "paused_control" not in data:
                reasons.append("pause_proof_missing")
            else:
                if (data["paused_control"]["active_sku"],data["paused_control"]["active_revision"],stamp(data["paused_control"]["evaluated_at"]))!=(
                        row["sku_id"],row["revision"],at) or data["paused_control"]["state"]!="pause":
                    raise WorkflowError("暂停证据不对应彩排时间、SKU 和版本。")
                upstream["paused_control"]=pins["paused_control"]
        if stage=="resume":
            if current!=row["id"] or not paused:
                reasons.append("no_matching_paused_checkpoint")
            ctl,restore=data.get("control"),data.get("restore")
            if ctl is None:
                reasons.append("recovery_proof_missing")
            else:
                if ((ctl["active_sku"],ctl["active_revision"],stamp(ctl["evaluated_at"]))!=(row["sku_id"],row["revision"],at)
                        or (restore["sku_id"],restore["revision"])!=(row["sku_id"],row["revision"])):
                    raise WorkflowError("恢复证据不对应彩排时间、SKU 或版本。")
                if not (ctl["state"]=="propose_resume" and restore["state"]=="READY_OFFLINE"
                        and stamp(restore["evaluated_at"])<=at<stamp(restore["valid_until"])
                        and ctl["upstream"].get("previous")==pins["paused_control"]):
                    reasons.append("restoration_incomplete_or_expired")
                upstream.update(control=pins["control"],restore=pins["restore"])
            product=by_sku[row["sku_id"]]
            reasons.extend(_checkpoint_product_gaps(p["site"],p,product,data.get("preflight:"+row["sku_id"]),at))
            if not reasons and not holds:
                paused=False
        if stage=="closing":
            closed=True
            if visited!=set(by_id):
                reasons.append("not_all_allocations_rehearsed")
            if paused:
                reasons.append("pause_still_active_at_closing")
        checkpoints.append({**m,"sku_id":row["sku_id"] if row else None,"revision":row["revision"] if row else None,
                            "state":"HELD_LOCAL" if reasons or holds else "SIMULATED_CHECKED","reasons":sorted(set(reasons)),
                            "upstream":upstream,"paused_after":paused})
    checks=[r for r in checkpoints if r["state"]=="HELD_LOCAL"]
    return {"state":"REHEARSAL_HELD" if checks else "REHEARSED_OFFLINE","plan_task_id":plan["task_id"],
            "plan_version":plan["version"],"intake_task_id":intake["task_id"],"replayable_checkpoints":checkpoints,
            "closed_log":closed,"visited_allocations":sorted(visited),"pending_actions":sorted(set(holds+[h for r in checks for h in r["reasons"]])),
            "platform_pause_or_resume_performed":False,"real_operators_confirmed":False}


def _receipt(r,raw,body):
    fields=("task_id","job_id","site","tool_id","identity","input_sha256","upstream","state")
    return {**{k:r[k] for k in fields},"result_sha256":sha256(raw).hexdigest(),
            "report_sha256":sha256(body).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("仅接收本机结构化合成证据。")
    pins,data=linked
    if tool==SAVE:
        result=_save(site,p,data)
    elif tool==INTAKE:
        result=_intake(site,p,data)
        if "lead_review" in data:
            review=data["lead_review"]
            result["lead_review_intake"]={"approval_task_id":review["task_id"],"approval_version":review["version"],
                "approval_source":pins["lead_review"],"approval_batch_id":review["batch_id"],"recipient":"live-ops",
                "used_plan_sale_units":data["plan"]["planned_by_unit"] if result["received_for_rehearsal"] else None,
                "used_plan_physical_pieces":data["plan"]["planned_physical_pieces"] if result["received_for_rehearsal"] else None,
                "only_locally_consumed":True}
    else:
        result=_rehearsal(p,data,pins)
    r={**result,"task_id":folder.name,"tool_id":tool,"site":site,"currency":p["currency"],"scope":SYNTHETIC,
       "job_id":p["job_id"],"identity":p["identity"],"evaluated_at":p["evaluated_at"],"owner":p["owner"],
       "upstream":pins,"input_sha256":sha256(encode(p)).hexdigest(),
       "execution_authorized":False,"provider_called":False,"model_called":False}
    raw=encode(r); body=(f"# {site} · {p['job_id']} · {r['state']}\n\n"
                        "全场仅按合成本机资料记录。费用、库存和支付均不代表实店；未操作直播平台。\n\n"+raw.decode()).encode()
    paths={k:folder/v for k,v in FILES.items()}
    for k,v in {"json":raw,"report":body,"receipt":encode(_receipt(r,raw,body))}.items():
        with paths[k].open("xb") as f:
            f.write(v)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["role_packet_pins"]
        or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
        or json.loads(files["receipt"])!=_receipt(r,files["json"],files["report"])):
        raise WorkflowError("排品、收件或彩排回执与实际留档不一致。")


def public_tools():
    return [{"id":tool,"sites":list(ROUTES),"role_suffix":"live-ops","label":label,"input_example":{},
             "description":"只接受本站合成场次。证据缺失或过期时留待核实，不连接直播平台。","max_media_files":0}
            for tool,label in ((SAVE,"保存五类排品和多 SKU 货盘"),(INTAKE,"收件并核对当前货盘"),(REHEARSE,"复盘本机全场彩排记录"))]

