"""Versioned local Philippine listing templates with exact review and case pins."""

from hashlib import sha256
import json
import re

from role_tools import SYNTHETIC
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id


SAVE = "ph-local-listing-template-save"
FIND = "ph-local-listing-template-find"
DOSSIER = "ph-local-listing-term-dossier"
DRAFT = "ph-local-listing-draft-intake"
TOOLS = (SAVE, FIND, DOSSIER, DRAFT)
LISTING_SCOPE = "site currency scope job_id template_id locale category as_of product_id sku_id sku_revision"
LISTING_IDENTITY = ("site", "currency", "scope", "template_id", "locale", "category", "product_id", "sku_id", "sku_revision")
POLICY_FACTS = ("certification", "cod", "delivery", "returns")
UNVERIFIED_CLAIMS = re.compile(
    r"cod|cashondelivery|certif|fdaapproved|dtiregistered|psmark|"
    r"freereturn|returnpolicy|returnsaccepted|moneyback|guarantee|"
    r"samedaydelivery|nextdaydelivery|freeshipping|librengpadala|bayadsapagdating"
)
FILES = {"json":"listing-template.json","report":"listing-template.md","receipt":"listing-template-receipt.json"}
KEYS = ("template_id","locale","category")
COPY = "title description search_terms hashtags visual_context currency_unit_note"


class WorkflowError(ValueError):
    pass


def content_sha(copy):
    return sha256(encode(copy)).hexdigest()


def request_key(tool,p):
    if tool == SAVE:
        return ":".join((*[p[k] for k in KEYS], str(p["version"])))
    return p["job_id"]


def _revision(body):
    amount(body["version"],"修订",1,10**6,False)
    if (body["version"]==1)!=(body["previous_task_id"] is None):
        raise WorkflowError("模板后续版本须绑定同范围上一版。")
    if body["previous_task_id"]:
        task_id(body["previous_task_id"],"上一版结果")
        term(body["revision_note"],"修订理由")
    elif body["revision_note"] is not None:
        raise WorkflowError("首版不使用后续修订理由。")


def _term_key(word):
    return re.sub(r"[^a-z0-9]", "", word.casefold())


def _listing_fields(p, tool):
    for key in ("product_id", "sku_id"):
        identify(p[key], key)
    amount(p["sku_revision"], "SKU revision", 1, 10**6, False)
    if tool == DRAFT:
        task_id(p["dossier_task_id"], "term dossier task")
        amount(p["dossier_version"], "term dossier version", 1, 10**6, False)
        identify(p["dossier_result_sha256"], "term dossier file checksum", r"[a-f0-9]{64}")
        return
    amount(p["dossier_version"], "term dossier version", 1, 10**6, False)
    evidence = p["evidence"]
    if evidence is None:
        return
    check(evidence, "source_ref kind observed_at valid_until catalog search_terms title_terms hashtags", "term dossier evidence")
    term(evidence["source_ref"], "synthetic evidence identifier")
    if evidence["kind"] != "synthetic_offline_fixture":
        raise WorkflowError("This offline lane accepts explicitly synthetic fixtures only.")
    if not stamp(evidence["observed_at"]) <= stamp(p["as_of"]) < stamp(evidence["valid_until"]):
        raise WorkflowError("Term dossier evidence is outside its valid interval.")
    catalog = evidence["catalog"]
    check(catalog, "product_id sku_id sku_revision source_ref dimensions_cm", "same SKU catalog evidence")
    if tuple(catalog[k] for k in ("product_id", "sku_id", "sku_revision")) != tuple(
            p[k] for k in ("product_id", "sku_id", "sku_revision")) or type(catalog["sku_revision"]) is not int:
        raise WorkflowError("Catalog evidence belongs to a different product, SKU, or version.")
    term(catalog["source_ref"], "synthetic catalog evidence identifier")
    dimensions = catalog["dimensions_cm"]
    if dimensions is not None:
        if not isinstance(dimensions, list) or len(dimensions) != 3:
            raise WorkflowError("Use exactly three dimension values in cm.")
        for value in dimensions:
            amount(value, "dimension in cm", 1, 10000, False)
    for field in ("search_terms", "title_terms", "hashtags"):
        words = evidence[field]
        if not isinstance(words, list) or len(words) > 12:
            raise WorkflowError("Keep search terms, title terms and hashtags in separate lists.")
        for word in words:
            term(word, field)
            if not _term_key(word) or (field == "hashtags") != word.strip().startswith("#"):
                raise WorkflowError("Search/title terms cannot start with # and hashtags must start with #.")
            if field == "hashtags" and not re.fullmatch(r"#[a-zA-Z0-9_]+", word.strip()):
                raise WorkflowError("Use a single hashtag with no spaces.")


def _dossier(p):
    evidence = p["evidence"]
    groups = {key: [] for key in ("search_terms", "title_terms", "hashtags")}
    excluded = []
    if evidence:
        used = set()
        for field, output in groups.items():
            for candidate in evidence[field]:
                word = " ".join(candidate.split())
                key = word.casefold()
                if key in used:
                    excluded.append({"field": field, "candidate": word, "reason": "duplicate_in_field"})
                elif UNVERIFIED_CLAIMS.search(_term_key(word)):
                    excluded.append({"field": field, "candidate": word, "reason": "unverified_policy_claim"})
                else:
                    output.append(word)
                used.add(key)
            used.clear()
    complete = all(groups.values())
    return {"dossier_version": p["dossier_version"], "groups": groups,
            "source": {key: evidence[key] for key in ("source_ref", "kind", "observed_at", "valid_until", "catalog")}
                      if evidence else None,
            "excluded": excluded, "search_volume": None, "search_volume_status": "UNKNOWN",
            "state": "SYNTHETIC_TERMS_READY" if complete else "INSUFFICIENT_TERM_EVIDENCE"}


def _draft(p, dossier):
    matches = dossier["state"] == "SYNTHETIC_TERMS_READY"
    facts = dossier["source"]["catalog"] if matches else None
    copy = None
    if facts:
        dimensions = facts["dimensions_cm"]
        copy = {"title": " | ".join(dossier["groups"]["title_terms"]),
                "description": "Fixture dimensions: " + " × ".join(map(str, dimensions)) + " cm." if dimensions else None,
                "search_terms": dossier["groups"]["search_terms"], "hashtags": dossier["groups"]["hashtags"]}
    return {"dossier_task_id": p["dossier_task_id"], "dossier_version": p["dossier_version"],
            "dossier_result_sha256": p["dossier_result_sha256"], "draft_version": 1 if copy else None,
            "sourced_product_facts": facts, "policy_facts": dict.fromkeys(POLICY_FACTS),
            "search_volume": None, "search_volume_status": "UNKNOWN", "copy": copy,
            "state": "LOCAL_SYNTHETIC_DRAFT" if copy else "DRAFT_BLOCKED_MISSING_TERM_EVIDENCE"}


def validate(site,tool,p):
    listing = tool in (DOSSIER, DRAFT)
    if listing:
        tail = ("dossier_version evidence" if tool == DOSSIER else
                "dossier_task_id dossier_version dossier_result_sha256")
        check(p, LISTING_SCOPE + " " + tail, "offline PH listing")
    tail=("version previous_task_id effective_at expires_at revision_note author copy review example source_ref"
          if tool==SAVE else "include_history historical_version")
    if not listing:
        check(p,"site currency scope job_id template_id locale category as_of "+tail,"菲律宾本地化模板")
    if (site,p["site"],p["currency"],p["scope"])!=("PH","PH","PHP",SYNTHETIC):
        raise WorkflowError("模板库仅接受菲律宾本机合成资料及原币。")
    for k in ("job_id","template_id","category"):
        identify(p[k],k)
    if p["locale"] not in ("en-PH","fil-PH","taglish-PH"):
        raise WorkflowError("请明确登记 en-PH、fil-PH 或 Taglish 项目语种。")
    at=stamp(p["as_of"])
    if listing:
        _listing_fields(p, tool)
        return at
    if tool==FIND:
        if type(p["include_history"]) is not bool:
            raise WorkflowError("是否查历史须明确填写。")
        amount(p["historical_version"],"显式历史版",1,10**6)
        return at
    _revision(p)
    term(p["author"],"作者")
    term(p["source_ref"],"来源",True)
    if not stamp(p["effective_at"])<=at<stamp(p["expires_at"]):
        raise WorkflowError("保存的模板必须在明确有效区间内。")
    c=p["copy"]
    check(c,COPY,"已提交原文")
    for k in ("title","description","visual_context","currency_unit_note"):
        term(c[k],k)
    for k in ("search_terms","hashtags"):
        words=c[k]
        if not isinstance(words,list) or not 1<=len(words)<=12:
            raise WorkflowError("搜索词和标签须分别登记。")
        for word in words:
            term(word,k)
            if (k=="hashtags")!=word.startswith("#") or (k=="hashtags" and any(ch.isspace() for ch in word)):
                raise WorkflowError("标签以 # 开头且不含空格，搜索词不带 #。")
        if len({v.casefold() for v in words})!=len(words):
            raise WorkflowError("同组搜索词或标签重复。")
    review=p["review"]
    if review is not None:
        check(review,"reviewer basis reviewed_at copy_sha256 version disposition source_ref limitations","独立校对")
        term(review["reviewer"],"校对人")
        term(review["source_ref"],"复核来源",True)
        term(review["limitations"],"复核边界")
        identify(review["copy_sha256"],"被审文案哈希",r"[a-f0-9]{64}")
        amount(review["version"],"复核所审修订",1,10**6,False)
        if stamp(review["reviewed_at"])>at or review["basis"]!="internal_readability" or review["disposition"] not in ("accepted_internal","revise"):
            raise WorkflowError("校对仅记录已完成的内部可读性范围。")
    example=p["example"]
    if example is not None:
        check(example,"site locale category template_id template_version sku_id sku_revision example_id copy_sha256 fact_status source_ref","模板案例")
        if any(example[k]!=p[k] for k in ("site",*KEYS)):
            raise WorkflowError("案例错站、语种、类目或模板。")
        if example["template_version"]!=p["version"] or example["copy_sha256"]!=content_sha(c):
            raise WorkflowError("案例没有绑定本版原文。")
        identify(example["sku_id"],"虚构商品")
        amount(example["sku_revision"],"虚构商品修订",1,10**6,False)
        identify(example["example_id"],"案例号")
        term(example["source_ref"],"合成案例来源",True)
        check(example["fact_status"],"measurements material payment delivery returns certification","商品事实状态")
        if any(v is not None for v in example["fact_status"].values()):
            raise WorkflowError("此合成案例仅留事实占位，未知应为 null。")
    return at


def bind(site,tool,p,load,history):
    at=validate(site,tool,p)
    if tool == DOSSIER:
        return {}, None
    if tool == DRAFT:
        ident = p["dossier_task_id"]
        result, pin = load(ident, site, DOSSIER)
        if (any(result[k] != p[k] for k in LISTING_IDENTITY) or result["dossier_version"] != p["dossier_version"]
                or pin["result_sha256"] != p["dossier_result_sha256"]):
            raise WorkflowError("The selected term dossier does not match this product, locale, category, version or checksum.")
        if at < stamp(result["as_of"]) or (result["source"] and at >= stamp(result["source"]["valid_until"])):
            raise WorkflowError("The selected term dossier is not valid at draft intake time.")
        return {ident: pin}, result
    rows=history(site,SAVE,p["template_id"],p["locale"],p["category"])
    links,versions={},[]
    def get(task):
        value,pin=load(task,site,SAVE)
        links[task]=pin
        return value
    if tool==SAVE:
        latest=max((r["version"] for r in rows), default=0)
        if p["version"]<latest:
            raise WorkflowError("新版已存在，旧版仍可按历史查询。")
        if p["previous_task_id"]:
            prev=get(p["previous_task_id"])
            if (any(prev[k]!=p[k] for k in KEYS) or prev["version"]+1!=p["version"]
                    or stamp(prev["effective_at"])>=stamp(p["effective_at"]) or stamp(prev["as_of"])>at):
                raise WorkflowError("只接受同站点、语种、类目和模板号的连续版本。")
    else:
        for row in rows:
            if stamp(row["effective_at"])<=at and stamp(row["as_of"])<=at:
                versions.append(get(row["task_id"]))
    return links,versions


def _record(p):
    reasons=[]
    digest=content_sha(p["copy"])
    review=p["review"]
    if not p["source_ref"]:
        reasons.append("template_source_missing")
    if (not review or review["reviewer"].strip().casefold()==p["author"].strip().casefold() or
            (review["copy_sha256"],review["version"],review["disposition"])!=(digest,p["version"],"accepted_internal")
            or not review["source_ref"]):
        reasons.append("independent_current_review_missing")
    if not p["example"] or not p["example"]["source_ref"]:
        reasons.append("same_version_example_missing")
    fields=("version","previous_task_id","effective_at","expires_at","revision_note","author","copy","review","example","source_ref")
    return {**{k:p[k] for k in fields},"copy_sha256":digest,"state":"PENDING_REVIEW" if reasons else "INTERNAL_TEMPLATE_ARCHIVED",
            "reasons":reasons}


def _find(p,rows):
    at=stamp(p["as_of"])
    rows=sorted(rows,key=lambda r:r["version"],reverse=True)
    newest=rows[0] if rows else None
    usable=newest if newest and newest["state"]=="INTERNAL_TEMPLATE_ARCHIVED" and at<stamp(newest["expires_at"]) else None
    selection=next((r for r in rows if r["version"]==p["historical_version"]),None)
    def summary(r):
        if r is None:
            return None
        fields=("task_id","version","state","effective_at","expires_at","copy","copy_sha256","review","example")
        return {k:r[k] for k in fields}
    if p["historical_version"] is not None:
        state="HISTORICAL_REFERENCE" if selection else "SCOPED_HISTORY_NOT_FOUND"
    else:
        state=("INTERNAL_REFERENCE_AVAILABLE" if usable else "CURRENT_EXPIRED" if newest and at>=stamp(newest["expires_at"])
               else "CURRENT_PENDING" if newest else "NO_MATCHING_SCOPE")
    return {"state":state,"current":summary(usable),"latest_record_version":newest["version"] if newest else None,
            "historical_selected":summary(selection),"history":[summary(r) for r in rows] if p["include_history"] else []}


def _receipt(r,raw,body):
    return {**{k:r[k] for k in ("task_id","tool_id","site","job_id",*KEYS,"state","input_sha256","upstream")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(body).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("模板只处理已标合成的文本与证据。")
    pins,rows=linked
    identity = LISTING_IDENTITY if tool in (DOSSIER, DRAFT) else KEYS
    if tool == DOSSIER:
        result = _dossier(p)
    elif tool == DRAFT:
        result = _draft(p, rows)
    else:
        result = _record(p) if tool == SAVE else _find(p, rows)
    r={**result,**{k:p[k] for k in identity},"scope":SYNTHETIC,
       "site":site,"currency":"PHP","task_id":folder.name,"tool_id":tool,"as_of":p["as_of"],"job_id":p["job_id"],
       "input_sha256":sha256(encode(p)).hexdigest(),"upstream":pins,
       "native_review_verified":False,"platform_policy_verified":False,"listing_publish_allowed":False,"execution_authorized":False,
       "model_called":False,"provider_called":False}
    raw=encode(r)
    report=(f"# PH · {p['job_id']} · {r['state']}\n\n"
            "仅合成模板内部校对，实际商品事实、母语语义、搜索表现、履约和发布资格待核。\n\n"+raw.decode()).encode()
    if tool in (DOSSIER, DRAFT):
        copy = r["groups"] if tool == DOSSIER else r["copy"] or {}
        lines = [f"# PH | {p['job_id']} | {r['state']}", "", "Synthetic offline fixture only.",
                 "Search volume: unknown. Native-language review: unverified.",
                 "Certification, COD, delivery and returns: unverified. No publication authorization.", ""]
        for field, label in (("search_terms", "Search terms"), ("title_terms" if tool == DOSSIER else "title", "Title"),
                             ("hashtags", "Hashtags"), ("description", "Description")):
            value = copy.get(field)
            if value is not None:
                lines += [f"## {label}", "", *(value if isinstance(value, list) else [value]), ""]
        report = ("\n".join(lines) + "\n" + raw.decode()).encode("utf-8")
    paths={k:folder/name for k,name in FILES.items()}
    for k,v in {"json":raw,"report":report,"receipt":encode(_receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:
            f.write(v)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["role_packet_pins"]
        or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
        or json.loads(files["receipt"])!=_receipt(r,files["json"],files["report"])):
        raise WorkflowError("存档、结果或模板取件回执不一致。")


def public_tools():
    tools = [{"id":tool,"sites":["PH"],"role_suffix":"shop-ops","label":title,"input_example":{},
             "description":"按菲律宾语种和类目保存或查回经过确切版本校对的合成模板。",
             "max_media_files":0}
            for tool,title in ((SAVE,"保存菲律宾本地化模板及校对"),(FIND,"查取菲律宾模板与历史案例"))]



    base = {"site":"PH", "currency":"PHP", "scope":SYNTHETIC, "job_id":"PH-LISTING-LOCAL-01",
            "template_id":"PH-DESK-POUCH", "locale":"en-PH", "category":"HOME_STORAGE",
            "as_of":"2026-11-22T10:00:00+08:00", "product_id":"PH-SYN-PRODUCT", "sku_id":"PH-SYN-SKU",
            "sku_revision":1, "dossier_version":1}
    return tools + [
        {"id": DOSSIER, "sites":["PH"], "role_suffix":"shop-ops", "label":"菲律宾商城｜登记分栏词档",
         "description":"离线记录合成词档的搜索词、标题词和标签。真实搜索量未知。",
         "input_example":{**base, "evidence":None}, "max_media_files":0},
        {"id": DRAFT, "sites":["PH"], "role_suffix":"shop-ops", "label":"菲律宾商城｜接收同版词档并查看草稿",
         "description":"用词档任务号、版本和结果校验值独立取件，按商品、SKU、语言、类目和版本验收。",
         "input_example":{**base, "dossier_task_id":"0"*32, "dossier_result_sha256":"0"*64}, "max_media_files":0},
    ]
