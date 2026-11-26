"""Offline quarter resource ranking from explicitly synthetic assumptions."""
from datetime import date
from fractions import Fraction
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_environment import identifier

TOOL="lead-quarter-resource-review"
BASES={"store":"mature_units","mcn":"qualified_leads"}
BUDGET={"cap_minor","committed_minor","available_cash_minor","reserved_cash_minor","expected_receivables_minor"}
ROLES={"shop-ops","ad-buyer","selection","live-ops","campaign-ops","competitor-analyst",
       "video-director","video-teardown","visual-designer"}


class WorkflowError(ValueError):
    pass


def _number(x):
    return x is None or type(x) is int and 0<=x<=10**12


def validate(site,p):
    fields={"site","scope","case_id","quarter_start","basis","object_ref","goal","currency","budget","options"}
    if (not isinstance(p,dict) or p.keys()!=fields or site not in ROUTES or p["site"]!=site
            or p["scope"]!=SYNTHETIC or not identifier(p["case_id"]) or not identifier(p["object_ref"]) or p["currency"]!=ROUTES[site][1]):
        raise WorkflowError("请使用有场景编号、本站币种的合成季度策略输入。")
    try:
        d=date.fromisoformat(p["quarter_start"])
        if d.isoformat()!=p["quarter_start"] or d.day!=1 or d.month not in (1,4,7,10):
            raise ValueError
    except (TypeError,ValueError):
        raise WorkflowError("季度开始须为 1、4、7、10 月 1 日。") from None
    basis=p["basis"]
    if not isinstance(basis,str) or basis not in BASES:
        raise WorkflowError("先明确使用店铺或 MCN 口径。")
    goal=p["goal"]
    if (not isinstance(goal,dict) or goal.keys()!={"metric","baseline","target"} or goal["metric"]!=BASES[basis]
            or not _number(goal["baseline"]) or type(goal["target"]) is not int or not 1<=goal["target"]<=10**9):
        raise WorkflowError("目标须匹配所选运营口径，并指定正数目标。")
    b=p["budget"]
    if not isinstance(b,dict) or b.keys()!=BUDGET or not all(_number(x) for x in b.values()):
        raise WorkflowError("预算及现金须分列，未知金额用 null。")
    if not isinstance(p["options"],list) or not 1<=len(p["options"])<=32:
        raise WorkflowError("需有 1–32 条候选投入。")
    ids=set()
    opt={"id","owner","basis","metric","cost_minor","estimated_units","dependency_key","risk","source_ref"}
    for row in p["options"]:
        if (not isinstance(row,dict) or row.keys()!=opt or not identifier(row["id"]) or row["id"] in ids
                or not isinstance(row["owner"],str) or row["owner"] not in ROLES
                or not identifier(row["dependency_key"]) or row["risk"] not in ("clear","hold","unknown")
                or not _number(row["cost_minor"]) or not _number(row["estimated_units"])
                or (row["source_ref"] is not None and not identifier(row["source_ref"]))):
            raise WorkflowError("候选须有唯一编号、所属岗位、约束、费用及证据。")
        ids.add(row["id"])


def _balance(amount,reserved):
    return max(0,amount-reserved) if amount is not None and reserved is not None else None


def review(site,p):
    validate(site,p)
    b=p["budget"]
    budget=_balance(b["cap_minor"],b["committed_minor"])
    cash=_balance(b["available_cash_minor"],b["reserved_cash_minor"])
    ceiling=min(budget,cash) if budget is not None and cash is not None else None
    remaining=ceiling
    platform=ROUTES[site][0]
    decisions={}
    eligible=[]
    for option in p["options"]:
        key=option["id"]
        row={**option,"owner":platform+"-"+option["owner"]}
        decisions[key]=row
        if option["basis"]!=p["basis"] or option["metric"]!=p["goal"]["metric"]:
            row.update(status="excluded_wrong_basis",reason="不同业务口径，不参与排序或预计累计。")
        elif option["risk"]=="hold":
            row.update(status="excluded_known_risk",reason="合成风险条件明确要求暂缓。")
        elif (remaining is None or option["cost_minor"] is None or option["estimated_units"] is None
              or not option["source_ref"] or option["risk"]=="unknown"):
            row.update(status="pending_evidence",reason="预算、现金、预期产出、风险或来源不足。")
        else:
            eligible.append(row)
    ordered=sorted(eligible,key=lambda r:(r["cost_minor"]!=0,
                     -Fraction(r["estimated_units"],max(r["cost_minor"],1)),r["cost_minor"],r["id"]))
    picked=set()
    projected=0
    for rank,row in enumerate(ordered,1):
        row["rank"]=rank
        if row["dependency_key"] in picked:
            row.update(status="excluded_overlap",reason="与已安排项目共享成效口径，避免重复计算。")
        elif row["estimated_units"]==0:
            row.update(status="excluded_no_projected_gain",reason="模拟材料未给正向产出。")
        elif row["cost_minor"]>remaining:
            row.update(status="deferred_funds",reason="超出剩余预算或可动用现金。",
                       additional_funding_minor=row["cost_minor"]-remaining)
        else:
            remaining-=row["cost_minor"]
            projected+=row["estimated_units"]
            picked.add(row["dependency_key"])
            row.update(status="candidate_offline",reason="在本地假设预算和现金范围内，交负责人复核。")
    return {"scope":SYNTHETIC,"case_id":p["case_id"],"site":site,"currency":p["currency"],
            "quarter_start":p["quarter_start"],"basis":p["basis"],"object_ref":p["object_ref"],"goal":p["goal"],
            "budget_room_minor":budget,"available_cash_after_reserve_minor":cash,
            "expected_receivables_minor":b["expected_receivables_minor"],"receivables_counted_as_cash":False,
            "simulated_ceiling_minor":ceiling,"remaining_after_candidates_minor":remaining,
            "simulated_selected_estimate_units":projected if ceiling is not None else None,
            "goal_gap_under_selected_assumptions":max(0,p["goal"]["target"]-p["goal"]["baseline"]-projected)
                  if ceiling is not None and p["goal"]["baseline"] is not None else None,
            "options":[decisions[x["id"]] for x in p["options"]],
            "ranking_method":"positive output per unit of cost, then deterministic ties; overlapping increments counted once; no real forecast",
            "review_owner":platform+"-lead","execution_authorized":False,"model_called":False,"provider_called":False}


def run_workflow(project,site,tool_id,payload,task_dir,assets,progress):
    if tool_id!=TOOL or assets:
        raise WorkflowError("季度策略只处理本机结构化离线资料。")
    out=review(site,payload)
    out.update(task_id=task_dir.name,workflow=TOOL)
    if progress:
        progress("正在核对预算、现金和所选业务口径的投入优先级。")
    folder=task_dir/"quarter-review"
    folder.mkdir()
    paths={k:folder/name for k,name in (("json","result.json"),("receipt","receipt.json"),("report","review.md"))}
    lines=[f"{out['case_id']} · {site} · {out['quarter_start']} · {out['basis']} · {out['currency']}",
           "金额使用最小货币单位。合成情景，待负责人确认。",
           f"目标 {out['goal']}；模拟剩余 {out['remaining_after_candidates_minor']}；现金与应收分列。",
           f"复核人 {out['review_owner']}"]
    for row in out["options"]:
        lines.append(f"{row['id']} {row['owner']} {row['status']} {row['reason']}")
    with paths["report"].open("x",encoding="utf-8",newline="\n") as f:f.write("\n".join(lines)+"\n")
    with paths["json"].open("x",encoding="utf-8",newline="\n") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    receipt={"scope":SYNTHETIC,"task_id":task_dir.name,"site":site,"case_id":out["case_id"],
             "files_sha256":{k:sha256(paths[k].read_bytes()).hexdigest() for k in ("json","report")},
             "model_called":False,"provider_called":False,"execution_authorized":False}
    with paths["receipt"].open("x",encoding="utf-8",newline="\n") as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
    return out,paths


def public_tools():
    examples={site:{"scope":SYNTHETIC,"site":site,"case_id":f"{site}-QUARTER-01","currency":currency,
        "quarter_start":"2026-10-01","basis":"store","object_ref":"SYNTHETIC-STORE","goal":{"metric":"mature_units","baseline":140,"target":200},
        "budget":{"cap_minor":5000,"committed_minor":1000,"available_cash_minor":4500,
                  "reserved_cash_minor":500,"expected_receivables_minor":None},
        "options":[{"id":"SYN-OPTION-01","owner":"shop-ops","basis":"store","metric":"mature_units",
                    "cost_minor":1000,"estimated_units":70,"dependency_key":"scope1",
                    "risk":"clear","source_ref":"SYN-EVIDENCE-1"}]} for site,(_,currency) in ROUTES.items()}
    return [{"id":TOOL,"role_suffix":"lead","sites":list(ROUTES),"label":"季度策略与资源选择（离线）",
             "description":"分站点、店铺或 MCN 目标，按冻结预算与可动用现金筛选资源候选并保留风险与缺证。",
             "input_examples":examples,"max_media_files":0}]

