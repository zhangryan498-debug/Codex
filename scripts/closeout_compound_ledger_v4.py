"""Final read/rehash/revalidation and generated coverage. No model calls."""
import argparse
import copy
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from replay_campaign_ledger_v3 import dump,read,sha
from run_compound_heldout_v3 import context
from verify_compound_reference_v3 import audit_reference,audit_auxiliary


def run(project):
    h,audit=context(project);b=h.ROOT/'work/compound-ledger-v3-20261111'
    out=b/'final-verification-v4';out.mkdir(exist_ok=False)
    h.verify_originals()
    before=read(b/'v4-same-input-regression/preservation-before.json')
    unchanged={path:sha(path)==expected for path,expected in before.items()}
    if not all(unchanged.values()):raise AssertionError('Original/supplemental evidence changed')
    dump(out/'original-preservation.json',unchanged)
    frozen=read(b/'heldout-first/implementation-before-new-inputs.json')
    if any(sha(p)!=v for p,v in frozen['sha256'].items()):raise AssertionError('Pre-heldout implementation changed')
    original_score=read(b/'original-first-score-import/score_manifest.json')
    if any(sha(b/'original-first-score-import'/n)!=v for n,v in original_score['files'].items()):raise AssertionError('Original independent score changed')
    source_zip=h.B/'DT_FRESH_COMPOSITE_B7E91D_EVIDENCE_FIRST.zip'
    if sha(source_zip)!=original_score['source_evidence_zip_sha256']:raise AssertionError('Independent score source archive mismatch')
    cn_defs=read(b/'cn-receiver-definitions.json');ph_defs=read(b/'ph-receiver-definitions.json');definitions=cn_defs+ph_defs
    decimal_audits=[];coverage=[];activity_runtime=[];scores=[]
    for d in definitions:
        raw,reference,score=map(read,[d['raw'],d['reference'],d['review']])
        decimal_audits.extend(audit_reference(raw,reference))
        original_answer=Path(d['activity_run'])/'answer.md'
        if score['answer_sha256']!=sha(original_answer):raise AssertionError('Score detached from original answer')
        models={r['case_id']:r for r in read(original_answer)['cases']}
        grades={r['case_id']:r for r in score['cases']}
        for reference_case in reference['cases']:
            caseid=reference_case['case_id'];grade=grades[caseid];model=models[caseid]
            if not all(grade[k] is True for k in ['decision_correct','floor_correct','money_correct','state_correct','necessary_rules_complete','all_checks_passed']):raise AssertionError('Independent semantic score is not complete: '+caseid)
            nums=reference_case['numbers'];mode=reference_case.get('mode',reference_case.get('accounting_status','unknown'))
            status='not_executable' if reference_case['floor_status']=='NOT_APPLICABLE' else 'exact_current' if type(nums.get('net_minor')) is int else 'bounded_current' if type(nums.get('net_lower_minor')) is int and type(nums.get('net_upper_minor')) is int else 'partial_known_missing_information'
            if status=='exact_current' and any(type(row.get('net_minor')) is not int for row in reference_case.get('sku_numbers',{}).values()):status='exact_order_net_partial_sku_ledger'
            coverage.append({'suite':'independent_heldout_first','case_id':caseid,'market':raw['market'],'reference_mode':mode,'amount_coverage':status,'decision':model['decision'],'floor_status':model['floor_status'],'exact_current_net':None if status=='not_executable' else nums.get('net_minor'),'guaranteed_lower':None if status=='not_executable' else nums.get('net_lower_minor'),'guaranteed_upper':None if status=='not_executable' else nums.get('net_upper_minor'),'sku_net_exact_count':0 if status=='not_executable' else sum(type(row.get('net_minor')) is int for row in reference_case.get('sku_numbers',{}).values()),'sku_ledger_count':len(reference_case.get('sku_numbers',{})),'all_sku_nets_exact':status!='not_executable' and bool(reference_case.get('sku_numbers')) and all(type(row.get('net_minor')) is int for row in reference_case.get('sku_numbers',{}).values()),'independent_semantic_pass':True,'new_model_inference':True,'same_input_regression':False})
        result=read(Path(d['activity_run'])/'result.json');runtime=audit(h,Path(d['activity_run']))
        if result['status']!='completed' or not runtime['passed']:raise AssertionError('New activity inference incomplete')
        for name,digest in result['files_sha256'].items():
            if sha(Path(d['activity_run'])/name)!=digest:raise AssertionError('Activity raw file changed')
        activity_runtime.append({'attempt':Path(d['activity_run']).name,'runtime':runtime})
        scores.append({'market':raw['market'],'score_path':d['review'],'score_sha256':sha(d['review']),'reference_sha256':sha(d['reference']),'cases':len(score['cases']),'all_semantic_pass':True})
    auxiliary=audit_auxiliary(h.ROOT)
    for name in ['douyin-auxiliary-same-question','tiktok-auxiliary-same-question']:
        p=b/'auxiliary-same-question'/name;result=read(p/'result.json');runtime=audit(h,p)
        if result['status']!='completed' or not runtime['passed']:raise AssertionError('Auxiliary run incomplete')
        for name,digest in result['files_sha256'].items():
            if sha(p/name)!=digest:raise AssertionError('Auxiliary raw file changed')
        activity_runtime.append({'attempt':p.name,'runtime':runtime})
    dump(out/'separate-decimal-and-auxiliary-audit.json',{'reference_cases':len(decimal_audits),'reference_checks':sum(len(row['checks']) for row in decimal_audits),'all_passed':True,'cases':decimal_audits,'auxiliary':auxiliary,'audit_source_sha256':sha(h.ROOT/'scripts/verify_compound_reference_v3.py')})
    records=[];receiver_runs=[];mutations=[]
    mutate_dir=out/'changed-caller-reuse';mutate_dir.mkdir()
    for folder in ['cn-receivers-first','ph-receivers-first']:
        plan=read(b/folder/'plan.json');subjects={s['subject']:s for s in read(b/folder/'subjects.json')}
        for p in plan['attempts']:
            attempt=b/folder/p['name'];result=read(attempt/'result.json');run=Path(result['run_dir']);caller=Path(p['caller'])
            if not result['completed'] or not result['passed'] or result['actual_result_sha256']!=sha(run/'result.json'):raise AssertionError('Receiver host incomplete')
            runtime=audit(h,run)
            if not runtime['passed']:raise AssertionError('Actual receiver runtime failed')
            fresh=h.intake.revalidate_run(caller,p['role'],p['evaluation_time'],run)
            expired=h.intake.revalidate_run(caller,p['role'],read(caller)['cases'][0]['authority']['valid_until_exclusive'],run)
            if any(r['system_receipt']=='accepted' for r in expired['records']):raise AssertionError('Expired packet accepted')
            if h.gate.digest(fresh)!=h.gate.digest(read(attempt/'downstream-same-instant.json')):raise AssertionError('Saved downstream results differ from actual independent revalidation')
            for r in fresh['records']:
                subject=subjects[r['case_id']];expected=subject['expected_safe'];accepted=r['system_receipt']=='accepted'
                if accepted!=expected:raise AssertionError('False acceptance or rejection: '+r['case_id'])
                records.append({'case_id':r['case_id'],'raw_case':subject['raw_case'],'role':p['role'],'expected_safe':expected,'field_gate':r['field_gate'],'model_receipt':r['model_receipt'],'system_receipt':r['system_receipt'],'run_dir':str(run),'host_receipt_sha256':sha(run/'price-intake-receipt.json'),'answer_sha256':sha(run/'answer.md'),'caller_sha256':sha(caller),'expired_accepted':False})
            receiver_runs.append({'name':p['name'],'run_dir':str(run),'runtime':runtime,'accepted':result['actual_accepted'],'rejected':result['actual_rejected']})
            positive=[i for i,r in enumerate(fresh['records']) if r['system_receipt']=='accepted']
            if positive:
                for kind in ['changed_source','changed_fee_version','changed_revision','changed_calculation']:
                    changed=read(caller);target=changed['cases'][positive[0]]
                    if kind=='changed_source':target['source_input']['new_unreviewed_source_fact']=1
                    elif kind=='changed_fee_version':target['calculation']['rule_versions']['fee_snapshot']='changed-after-review'
                    elif kind=='changed_revision':target['authority']['revision']+=1
                    else:target['calculation']['lower_bound_cents']+=1
                    path=mutate_dir/(p['name']+'-'+kind+'.json');dump(path,changed)
                    blocked=False;reason=None
                    try:h.intake.revalidate_run(path,p['role'],p['evaluation_time'],run)
                    except (ValueError,OSError) as exc:blocked=True;reason=type(exc).__name__+': '+str(exc)
                    mutations.append({'batch':p['name'],'kind':kind,'blocked':blocked,'reason':reason})
                    if not blocked:raise AssertionError('A changed caller reused an old accepted receipt')
    dump(out/'receivers-independent-revalidation.json',records)
    dump(out/'changed-receipt-reuse-blocks.json',mutations)
    dump(out/'coverage-matrix.json',coverage)
    with (out/'coverage-matrix.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(coverage[0]));writer.writeheader();writer.writerows(coverage)
    dump(out/'actual-runtime.json',activity_runtime+receiver_runs)
    log=(b/'engineering-v4-final.log').read_text(encoding='utf-8-sig')
    counts=re.findall(r'Ran (\d+) tests in',log)
    if not counts or counts[-1]!='476' or not re.search(r'(?m)^OK\s*$',log):raise AssertionError('Required engineering regression log incomplete')
    regression=read(b/'v4-same-input-regression/summary.json')
    summary={'conclusion':'controlled_offline_scope_completed','scope':'synthetic_offline','release_status':'not_authorized','created_at':datetime.now(timezone.utc).isoformat(),'first_original_cases':24,'original_first_host_subjects':28,'original_missing_host_subjects_unchanged':3,'prior_separate_infrastructure_supplement_completed':3,'original_independent_score_import_files':5,'original_activity_atomic_score':'353/353; all-fields completion not claimed','same_input_regression':regression,'new_independent_cases':len(coverage),'new_independent_decisions_correct':len(coverage),'new_independent_semantic_passes':len(coverage),'heldout_literal_first_scores':{'douyin':'6/8','tiktok_ph':'7/8'},'semantic_first_scores':{'douyin':'8/8','tiktok_ph':'8/8'},'new_heldout_coverage':dict(Counter(r['amount_coverage'] for r in coverage)),'new_host_batches':len(receiver_runs),'new_host_subjects':len(records),'new_host_accepted':sum(r['system_receipt']=='accepted' for r in records),'new_host_rejected':sum(r['system_receipt']=='rejected' for r in records),'new_host_false_accepts':sum(r['system_receipt']=='accepted' and not r['expected_safe'] for r in records),'new_host_false_rejects':sum(r['system_receipt']!='accepted' and r['expected_safe'] for r in records),'receiver_model_false_accepts':sum(r['model_receipt']=='accepted' and not r['expected_safe'] for r in records),'receiver_model_false_rejects':sum(r['model_receipt']!='accepted' and r['expected_safe'] for r in records),'expired_subjects_blocked':len(records),'changed_caller_receipt_reuse_blocked':len(mutations),'engineering_tests_passed':int(counts[-1]),'separate_reference_decimal_checks':sum(len(r['checks']) for r in decimal_audits),'auxiliary_checks':len(auxiliary['checks']),'new_model_calls':len(activity_runtime)+len(receiver_runs),'actual_model':'gpt-6.1-sol','actual_reasoning':'XHigh','actual_service_tier':'priority','zero_tool_events_all_new_runs':True,'window_settings_changed':False,'originals_and_prior_attempts_preserved':all(unchanged.values()),'preserved_files':len(unchanged),'independent_scores':scores,'limits':['New heldout model inference and unchanged price gate use independently authored deterministic/review references. This does not prove the old fixture engine supports every new raw JSON dialect.','Original strict and semantic first-run scores, post-hoc reference exports, new receiving inference and same-question supplements are separate evidence.','Unknown fee, shipping, allocation and eligibility facts remain blocked; no live shop data or activity execution occurred.','Observed backend priority is not independent verification of the window fast-mode UI.','Sixteen new compound cases are a bounded controlled sample, not a universal or statistical business guarantee.']}
    if summary['new_host_subjects']!=21 or summary['new_host_accepted']!=8 or summary['new_host_rejected']!=13:raise AssertionError('Unexpected independent new handoff coverage')
    dump(out/'summary.json',summary)
    manifest={str(p.relative_to(b)):sha(p) for p in b.rglob('*') if p.is_file() and p.name!='package-manifest.json' and '__pycache__' not in p.parts}
    scripts=['campaign_ledger_semantics_v3.py','campaign_ledger_semantics_v4.py','replay_campaign_ledger_v3.py','replay_campaign_ledger_v4.py','compound_receiver_v3.py','verify_compound_reference_v3.py','closeout_compound_ledger_v4.py']
    dump(out/'package-manifest.json',{'created_at':datetime.now(timezone.utc).isoformat(),'files':manifest,'sha256':{str((h.ROOT/'scripts'/n).resolve()):sha(h.ROOT/'scripts'/n) for n in scripts}})
    print(json.dumps({k:v for k,v in summary.items() if k not in ['same_input_regression','independent_scores','limits']},ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--project',required=True);a=ap.parse_args();run(a.project)
