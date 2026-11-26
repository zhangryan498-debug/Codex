"""Offline live readiness review with exact bound stock, campaign and site evidence."""

from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, safe_md, term
from workflow_live_rehearsal import BENEFIT, STOCK, amount, check, identify, snapshot_at, stamp, task_id

TOOL="live-preflight-evidence-review"
FILES={"json":"live-preflight.json","report":"live-preflight.md","receipt":"live-preflight-receipt.json"}
IDENTITY=("account_id","shop_id","room_id","session_id","sku_id","revision")
CATEGORIES={"CN":("account","product","compliance","prepaid"),
            "PH":("account","product","compliance","prepaid","cod")}


class WorkflowError(ValueError):
    pass


def validate(site,p):
    check(p,"site currency scope review_id stock_task_id benefit_task_id price_version evaluated_at "
            "account_id shop_id room_id session_id sku_id revision evidence owner next_review_at","开播前证据核查")
    if (p["site"],p["currency"],p["scope"])!=(site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("仅接受本站、本币和合成开播前演练。")
    for k in ("review_id",*IDENTITY[:-1]):
        identify(p[k],k)
    for k in ("revision","price_version"):
        amount(p[k],k,1,1000000,False)
    for k in ("stock_task_id","benefit_task_id"):
        task_id(p[k],k)
    at=stamp(p["evaluated_at"])
    term(p["owner"],"复核责任人")
    if stamp(p["next_review_at"])<at:
        raise WorkflowError("复核期限须在检查之后。")
    check(p["evidence"]," ".join(CATEGORIES[site]),"开播前证据分项")
    for k,r in p["evidence"].items():
        if r is None:
            continue
        check(r,"site currency account_id shop_id room_id session_id sku_id revision source_ref state "
                "captured_at valid_from valid_until scope_note","同对象材料")
        actual=tuple(r[x] for x in ("site","currency",*IDENTITY))
        if actual!=tuple(p[x] for x in ("site","currency",*IDENTITY)):
            raise WorkflowError("账号、店铺、直播间、场次或 SKU 证据错配。")
        states=("confirmed","blocked","unknown","not_applicable") if k in ("prepaid","cod") else ("confirmed","blocked","unknown")
        if r["state"] not in states:
            raise WorkflowError("材料需明确确认、阻断、未知；支付还可明确不适用。")
        term(r["source_ref"],"证据引用",True)
        term(r["scope_note"],"适用边界")
        if stamp(r["valid_from"])>=stamp(r["valid_until"]) or stamp(r["captured_at"])>at:
            raise WorkflowError("证据签到或有效区间不合法。")
    return at


def bind(site,p,load):
    at=validate(site,p)
    st,sp=load(p["stock_task_id"],STOCK,site,True)
    ben,bp=load(p["benefit_task_id"],BENEFIT,site,True)
    for r in (st,ben):
        if ((r["session_id"],r["sku_id"],r["revision"],r["site"],r["currency"],r["scope"])!=
            (p["session_id"],p["sku_id"],p["revision"],site,p["currency"],SYNTHETIC)
            or stamp(r["evaluated_at"])>at):
            raise WorkflowError("库存或权益收件不是当前同站、场次、SKU、版本及核查时点。")
    if ben["upstream"].get("stock")!=sp or ben["price_version"]!=p["price_version"] or ben["mechanism"]!="coupon":
        raise WorkflowError("必须引用该库存和对应券价版本的既有收件回执。")
    return at,{"inventory":sp,"benefit":bp},st,ben


def compute(site,p,linked):
    at,pins,st,ben=linked
    blocked=[]
    pending=[]
    fields={}
    proven_payment=[]
    for label in CATEGORIES[site]:
        r=p["evidence"][label]
        if not r or not r["source_ref"]:
            disposition="PENDING_MISSING"
        elif not stamp(r["valid_from"])<=at<stamp(r["valid_until"]):
            disposition="PENDING_OUTSIDE_VALIDITY"
        else:
            disposition={"confirmed":"SUPPORTED_SYNTHETIC","blocked":"BLOCKED","unknown":"PENDING_UNKNOWN",
                         "not_applicable":"OUT_OF_SCOPE_WITH_SOURCE"}[r["state"]]
        fields[label]={"disposition":disposition,"evidence":r}
        if disposition=="BLOCKED":
            blocked.append(label+"_not_supported")
        if disposition.startswith("PENDING"):
            pending.append(label+"_unconfirmed")
        if label in ("cod","prepaid") and disposition=="SUPPORTED_SYNTHETIC":
            proven_payment.append(label)
    if not proven_payment:
        pending.append("no_confirmed_payment_route")
    inventory=snapshot_at(site,p,st["inventory"]["snapshot"],p["evaluated_at"])
    blocked.extend(inventory["blocked"]+st["fulfillment"]["blocked"]+ben["blocking_reasons"])
    pending.extend(inventory["missing"]+st["fulfillment"]["missing"]+ben["pending_reasons"])
    quote=ben["quote"];terms=ben["terms"]
    if not quote or quote.get("passed") is not True or quote.get("price_minor") is None:
        pending.append("trusted_approved_quote_unknown")
    elif not quote.get("valid_until_exclusive"):
        pending.append("price_approval_expiry_unknown")
    elif at>=stamp(quote["valid_until_exclusive"]):
        blocked.append("price_approval_expired")
    if terms.get("eligible") is False:
        blocked.append("benefit_audience_ineligible")
    elif not terms.get("source_ref") or terms.get("eligible") is not True or not terms.get("valid_from_inclusive") or not terms.get("valid_until_exclusive"):
        pending.append("benefit_scope_unknown")
    elif not stamp(terms["valid_from_inclusive"])<=at<stamp(terms["valid_until_exclusive"]):
        blocked.append("benefit_outside_validity")
    disposition="BLOCKED" if blocked else "PENDING" if pending else "READY_OFFLINE"
    return {"state":disposition,"identity":{k:p[k] for k in IDENTITY},"review_id":p["review_id"],
            "evaluated_at":p["evaluated_at"],"price_version":p["price_version"],"upstream":pins,
            "price_quote":quote,"benefit_terms":terms,"stock_at_review":inventory,"screened_evidence":fields,
            "supported_synthetic_payment_routes":proven_payment,"blocking_reasons":sorted(set(blocked)),
            "pending_reasons":sorted(set(pending)),"platform_eligibility_confirmed":False,"go_live_authorized":False,
            "next_review":{"owner":p["owner"],"due_at":p["next_review_at"],"action":"复核同一账号、直播间、SKU、参与条件和当前价审来源。"}}


def receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","site","tool_id","input_sha256","upstream","review_id")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets or tool!=TOOL:
        raise WorkflowError("仅接受已归档、演练范围内的同站资格核查。")
    r={**compute(site,p,linked),"task_id":folder.name,"site":site,"currency":p["currency"],"scope":SYNTHETIC,
       "tool_id":tool,"input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,
       "provider_called":False,"model_called":False}
    raw=encode(r)
    head=f"# {site} 开播前资料核查 · {r['review_id']}\n\n结论：{r['state']}。仅本机合成资料。\n"
    body=(head+"\n"+raw.decode()).encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,b in {"json":raw,"report":body,"receipt":encode(receipt(r,raw,body))}.items():
        with paths[k].open("xb") as f:f.write(b)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["preflight_pins"]
            or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])):
        raise WorkflowError("开播前资料结果或源证据无法核验。")


def public_tools():
    return [{"id":TOOL,"sites":list(ROUTES),"role_suffix":"live-ops","label":"开播资格、支付与价审资料核对",
             "description":"只处理本机合成对象和价审回执，输出就绪、待核与拦截。","input_example":{}}]

