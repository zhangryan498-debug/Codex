"""Build and exercise price handoffs from independent review of fresh raw answers.

The independent reference is caller evidence, never model-authored authority.
Its author source/hash and raw-answer binding must be checked by the caller.
This adds an offline experiment builder, not a platform pricing engine.
"""
import argparse
import contextlib
import copy
import hashlib
import json
import sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from replay_campaign_ledger_v3 import dump,read,sha
from run_compound_heldout_v3 import context


def pack(h,case,reference,review,binding,provenance):
    market=reference['market'];platform='douyin' if market=='douyin_cn' else 'tiktok'
    currency='CNY' if platform=='douyin' else 'PHP';f=case['facts'];floor=f['floor']
    if market not in ('douyin_cn','tiktok_ph') or reference.get('currency')!=currency or reference['case_id']!=case['case_id']:
        raise ValueError('Independent reference subject/market/currency mismatch')
    if case.get('market',market)!=market or case.get('currency',currency)!=currency:
        raise ValueError('Source identity differs from the independently reviewed identity')
    if reference['evaluation_time']!=case.get('evaluation_time',f.get('evaluation_time')):
        raise ValueError('Source clock differs from independent review')
    items=f.get('items',[f.get('sku',{})]);qs={item['sku_id']:item['quantity'] for item in items}
    if len(qs)!=len(items) or any(type(q) is not int or q<=0 for q in qs.values()):raise ValueError('Invalid source quantity coverage')
    whole=floor['scope']=='whole_order'
    if not whole and (floor['scope']!='each_sku_total' or set(floor['amounts_minor'])!=set(qs)):raise ValueError('Invalid source floor coverage')
    subjects=[None] if whole else list(qs)
    instant=reference['evaluation_time'];now=datetime.fromisoformat(instant)
    rows=[]
    for sku in subjects:
        sid=case['case_id']+'-ORDER' if whole else sku
        ident={'platform':platform,'country':'CN' if platform=='douyin' else 'PH','currency':currency,'sku':sid,'revision':1,'floor_scope':'order','floor_basis':'merchant_net_order'}
        mapping={'original_floor_scope':floor['scope'],'original_floor_basis':floor['basis'],'comparison_unit':'whole_basket' if whole else 'sku_total_suborder','quantity':sum(qs.values()) if whole else qs[sku],'all_original_sku_quantities':qs,'synthetic_revision_mapping':'1 identifies exact frozen offline source, not an inferred platform revision'}
        ledger=reference['numbers'] if whole else reference.get('sku_numbers',{}).get(sku,{})
        lower=ledger.get('net_minor')
        if lower is None:lower=ledger.get('net_lower_minor')
        if reference['floor_status']=='NOT_APPLICABLE':lower=None
        valid_lower=type(lower) is int and lower>=0
        decision=reference['decision']=='ALLOW' and reference['floor_status']=='PASS'
        independent_review_ok=review['all_checks_passed'] is True
        conflicts=[]
        if not decision:conflicts.append('independent_current_request_not_allow_pass')
        if not independent_review_ok:conflicts.append('independent_raw_activity_review_failed')
        if not valid_lower:conflicts.append('current_lower_bound_unknown_negative_or_noninteger')
        amount=floor['amount_minor'] if whole else floor['amounts_minor'][sku]
        if type(amount) is not int or amount<0:raise ValueError('Invalid source floor')
        # A complete bounded proof need not claim a known exact total.
        proof_complete=decision and valid_lower and independent_review_ok
        versions={**provenance['rule_versions'],'review_sha256':provenance['review_sha256'],'reference_sha256':provenance['reference_sha256'],'builder_sha256':sha(__file__)}
        source={**ident,**mapping,'original_proposal':copy.deepcopy(case),'campaign_binding':binding,'independent_reference_provenance':provenance,'scope':'synthetic_offline','release_status':'not_authorized','clock_provenance':h.POLICY['clock']}
        calc={**ident,'method':'independently_reviewed','producer':'Independent author Decimal/integer oracle frozen separately from model inference; raw answer independently scored','input_sha256':h.gate.digest(source),'rule_versions':versions,'lower_bound_cents':lower if valid_lower else 0,'lower_bound_basis':'merchant_net_order','seller_cost_complete':proof_complete,'fees_complete':proof_complete,'unknowns':[] if proof_complete else copy.deepcopy(reference.get('pending_fields',[])),'conflicts':conflicts,'independent_result_summary':copy.deepcopy(reference),'raw_activity_independent_review':copy.deepcopy(review),'original_floor_mapping':mapping,'bounded_pending_fields':copy.deepcopy(reference.get('pending_fields',[])) if proof_complete else [],'signed_current_lower_minor':lower}
        authority={**ident,'schema_version':'1.1','timezone':'Etc/GMT-8','valid_from_inclusive':now.isoformat(),'valid_until_exclusive':(now+timedelta(microseconds=1)).isoformat(),'clock_provenance':h.POLICY['clock'],'floor_cents':amount,'floor_inclusive':True,'lower_bound_cents':calc['lower_bound_cents'],'lower_bound_basis':'merchant_net_order','lower_bound_verified':proof_complete and not conflicts,'seller_cost_complete':proof_complete,'fees_complete':proof_complete,'unknowns':copy.deepcopy(calc['unknowns']),'conflicts':copy.deepcopy(conflicts),'rule_versions':copy.deepcopy(versions),'input_sha256':h.gate.digest(source),'calculation_sha256':h.gate.digest(calc),'evidence_scope':'synthetic_offline','campaign_review':{'binding':binding,'independent_review_sha256':provenance['review_sha256'],'all_checks_passed':independent_review_ok},'acceptance_ready':False,'release_status':'not_authorized'}
        packet={'packet_id':sid+'-independent-heldout-first','receiver_role':platform+'-shop-ops','authority_sha256':h.gate.digest(authority),'input_sha256':authority['input_sha256'],'payload':copy.deepcopy(authority)}
        rows.append({'case_id':sid,'authority':authority,'packet':packet,'source_input':source,'calculation':calc})
    return rows


def prepare(h,out,definitions):
    out.mkdir(parents=True,exist_ok=False);plans=[];index=0;allsubjects=[]
    for definition in definitions:
        raw_path,ref_path,review_path,activity=map(Path,[definition[k] for k in ('raw','reference','review','activity_run')])
        raw=read(raw_path);ref=read(ref_path);graded=read(review_path);result=read(activity/'result.json')
        if result['status']!='completed' or result['files_sha256']['answer.md']!=sha(activity/'answer.md'):raise ValueError('Raw activity result incomplete/unbound')
        if graded['answer_sha256']!=sha(activity/'answer.md'):raise ValueError('Independent score is not bound to raw answer')
        refs={r['case_id']:r for r in ref['cases']};reviews={r['case_id']:r for r in graded['cases']}
        if set(refs)!=set(reviews) or set(refs)!={r['case_id'] for r in raw['cases']}:raise ValueError('Exact independent reference/score coverage required')
        if any(r['market']!=raw['market'] for r in refs.values()):raise ValueError('Reference market differs from raw suite')
        provenance={'reference_sha256':sha(ref_path),'reference_file':str(ref_path),'review_sha256':sha(review_path),'review_file':str(review_path),'raw_sha256':sha(raw_path),'raw_file':str(raw_path),'rule_versions':definition['rule_versions'],'reference_oracle_sources':ref.get('source_sha256',ref.get('source_files',{}))}
        binding={'run_dir':str(activity),'answer_sha256':sha(activity/'answer.md'),'task_sha256':sha(activity/'task.txt'),'result_sha256':sha(activity/'result.json')}
        groups={}
        for case in raw['cases']:
            p=refs[case['case_id']];rv=reviews[case['case_id']]
            for row in pack(h,case,p,rv,binding,provenance):
                key=(row['packet']['receiver_role'],p['evaluation_time']);groups.setdefault(key,[]).append(row)
                allsubjects.append({'subject':row['case_id'],'raw_case':case['case_id'],'expected_safe':p['decision']=='ALLOW' and p['floor_status']=='PASS','model_review_passed':rv['all_checks_passed'],'lower':row['calculation']['signed_current_lower_minor'],'floor':row['authority']['floor_cents']})
        for (role,instant),rows in groups.items():
            chunks=[];chunk=[]
            def bundle(values):return {'schema_version':'1.0','scope':'synthetic_offline','release_status':'not_authorized','receiver_role':role,'cases':values}
            for row in rows:
                if chunk and not fits(h,bundle(chunk+[row]),instant):chunks.append(chunk);chunk=[]
                if not fits(h,bundle([row]),instant):raise ValueError('Single reference subject exceeds supported input limit')
                chunk.append(row)
            if chunk:chunks.append(chunk)
            for chunk in chunks:
                index+=1;name=role+'-heldout-first-'+str(index);caller=out/(name+'.caller.json');dump(caller,bundle(chunk))
                pre=h.intake.prepare(role,h.shop_task(role),input_file=caller,evaluation_time=instant)
                expected=sum(r['calculation']['seller_cost_complete'] and r['calculation']['fees_complete'] and r['calculation']['lower_bound_cents']>=r['authority']['floor_cents'] for r in chunk)
                if sum(r['field_gate']=='pass' for r in pre['preflight'])!=expected:raise ValueError('Unexpected preflight differs from independent reference')
                dump(out/(name+'.preflight.json'),{'evaluation_time':instant,'caller_sha256':sha(caller),'preflight':pre['preflight']})
                plans.append({'name':name,'caller':str(caller),'caller_sha256':sha(caller),'role':role,'evaluation_time':instant,'expected_pass':expected,'subjects':[r['case_id'] for r in chunk]})
    dump(out/'subjects.json',allsubjects)
    dump(out/'plan.json',{'attempts':plans,'runner_sha256':sha(__file__),'created_at':datetime.now(timezone.utc).isoformat(),'scope':'synthetic_offline','release_status':'not_authorized'})
    print(json.dumps({'prepared':[p['name'] for p in plans],'subjects':len(allsubjects),'safe_subjects':sum(r['expected_safe'] for r in allsubjects),'model_calls':0}))


def fits(h,bundle,instant):
    normalized=[{**c,'receiver':bundle['receiver_role'],'evaluation_time':instant} for c in bundle['cases']]
    return len(h.intake.json_bytes(bundle))<=h.intake.LIMIT and len(h.shop_task(bundle['receiver_role']).encode('utf8'))+len(h.gate.batch_task_bundle(normalized).encode('utf8'))+4096<=h.intake.LIMIT


def execute(h,audit,out,name):
    plan=read(out/'plan.json')
    if plan['runner_sha256']!=sha(__file__):raise ValueError('Prepared runner changed')
    p=next(p for p in plan['attempts'] if p['name']==name);caller=Path(p['caller'])
    if sha(caller)!=p['caller_sha256']:raise ValueError('Caller changed')
    attempt=out/name;attempt.mkdir(exist_ok=False)
    dump(attempt/'invocation.json',dict(p,model='gpt-6.1-sol',reasoning_effort='xhigh',service_tier='inherited'))
    with (attempt/'stdout.txt').open('x',encoding='utf8') as so,(attempt/'wrapper-stderr.txt').open('x',encoding='utf8') as se,contextlib.redirect_stdout(so),contextlib.redirect_stderr(se):
        rc=h.entry.execute(p['role'],h.shop_task(p['role']),sandbox='read-only',root=h.ROOT,model='gpt-6.1-sol',reasoning_effort='xhigh',price_handoff_file=caller,price_evaluation_time=p['evaluation_time'],disabled_mcp_servers=h.disabled_servers())
    paths=[line[len('Evidence: '):] for line in (attempt/'stdout.txt').read_text(encoding='utf8').splitlines() if line.startswith('Evidence: ')]
    if len(paths)!=1:raise ValueError('Actual receiver run path missing')
    run=Path(paths[0]);actual=read(run/'result.json');runtime=audit(h,run)
    result={'name':name,'run_dir':str(run),'scope':'synthetic_offline','release_status':'not_authorized','wrapper_exit_code':rc,'actual_result_sha256':sha(run/'result.json'),'runtime':runtime,'actual_status':actual.get('status'),'price_intake_status':actual.get('price_intake_status'),'expected_accepted':p['expected_pass'],'actual_accepted':actual.get('price_accepted_count'),'actual_rejected':actual.get('price_rejected_count')}
    result['completed']=rc==0 and actual.get('status')=='completed' and actual.get('price_intake_status')=='completed' and runtime['passed']
    if result['completed']:
        downstream=h.intake.revalidate_run(caller,p['role'],p['evaluation_time'],run);dump(attempt/'downstream-same-instant.json',downstream)
        expired=h.intake.revalidate_run(caller,p['role'],read(caller)['cases'][0]['authority']['valid_until_exclusive'],run);dump(attempt/'downstream-expired.json',expired)
        result['expired_accepted']=sum(r['system_receipt']=='accepted' for r in expired['records'])
        result['expected_matches_actual']=actual.get('price_accepted_count')==p['expected_pass']
        result['passed']=result['expired_accepted']==0 and result['expected_matches_actual']
    else:result['passed']=False
    dump(attempt/'result.json',result);print(json.dumps(result,ensure_ascii=False));return 0 if result['passed'] else 2


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=('prepare','run'));ap.add_argument('--project',required=True);ap.add_argument('--output',required=True);ap.add_argument('--definitions');ap.add_argument('--name');a=ap.parse_args()
    h,audit=context(a.project);out=Path(a.output).resolve()
    if a.mode=='prepare':prepare(h,out,read(a.definitions))
    else:raise SystemExit(execute(h,audit,out,a.name))
