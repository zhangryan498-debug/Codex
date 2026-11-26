import argparse
import importlib
from offline_role_queue import digest,normalize
from pathlib import Path
from live_window_queue import read,dump,sha,runtime
from live_price_evidence_v2 import PriceVerifier
from live_window_host_v5 import receipt
from accept_live_windows_v2 import envelope,old_preservation

def extract_actual(first,raw,plan):
    actual=[];files={};seen=set();results=read(first/'first-run-results.json')
    jobs={j['job_id']:normalize(j) for j in read(first/'jobs.json')}
    if [r['job_id'] for r in results]!=plan['jobs']:raise ValueError('Actual batch jobs changed')
    for result in results:
        run=Path(result['evidence']['run_dir']).resolve();job=result['job_id']
        if run in seen or result['state']!='completed':raise ValueError('Actual run duplicate or incomplete')
        seen.add(run);inv=read(run.parent/'invocation.json');completed=read(run.parent/'completion.json');r=read(run/'result.json')
        if inv['payload']!=jobs[job] or inv['payload_sha256']!=digest(jobs[job]) or inv['job_id']!=job:raise ValueError('Original invocation/input binding changed')
        if Path(completed['run_dir']).resolve()!=run or completed['result_sha256']!=sha(run/'result.json') or result['evidence']['result_sha256']!=sha(run/'result.json'):raise ValueError('Actual result marker mismatch')
        if r['status']!='completed' or r['cli_exit_code']!=0 or r['job_id']!=job or r['role']!=plan['role']:raise ValueError('Actual role result incomplete')
        for name,expected in r['files_sha256'].items():
            if sha(run/name)!=expected:raise ValueError('Actual original file changed')
        if (run/'task.txt').read_text(encoding='utf8')!=inv['task'] or sha(run/'prompt.txt')!=inv['prompt_sha256']:raise ValueError('Original task/prompt changed')
        answer=read(run/'answer.md');body=answer['conclusion']
        if answer['job_id']!=job or answer['role']!=plan['role'] or body['suite_id']!=raw['suite_id']:raise ValueError('Actual answer job/role/suite mismatch')
        actual.extend(body['cases']);files.update({str(p):sha(p) for p in run.iterdir() if p.is_file()})
    if files!=raw['raw_files_sha256'] or digest(actual)!=digest(raw['cases']):raise ValueError('Derived first cases detached from actual model answers')
    return actual

def run(root,first,output,module):
    raw=read(first/'frozen-raw-first.json');plan=read(first/'plan.json')
    if any(sha(p)!=v for p,v in raw['raw_files_sha256'].items()):raise ValueError('First raw files changed')
    if sha(plan['public_input'])!=plan['public_sha256'] or sha(plan['catalog'])!=plan['catalog_sha256']:raise ValueError('Public input/catalog changed')
    rows=envelope(plan['public_input'],plan['platform'])['cases'];models={m['case_id']:m for m in extract_actual(first,raw,plan)}
    if len(models)!=len(rows) or set(models)!=set(c['case_id'] for c in rows):raise ValueError('Exact case binding failed')
    review=getattr(importlib.import_module(module),'review_cn' if plan['platform']=='douyin' else 'review_ph');verifier=PriceVerifier(read(plan['catalog']))
    results=[receipt(c,models[c['case_id']],verifier,review) for c in rows]
    actual=[]
    for r in read(first/'first-run-results.json'):
        proof=runtime(Path(r['evidence']['run_dir']))
        if not proof['passed']:raise ValueError('Actual runtime validation failed')
        actual.append({'job_id':r['job_id'],'run_dir':r['evidence']['run_dir'],**proof})
    old_preservation(root)
    output.mkdir(exist_ok=False)
    dump(output/'receipts.json',{'cases':results,'actual_runs':actual,'raw_first_sha256':sha(first/'frozen-raw-first.json'),'source_sha256':{n:sha(root/'scripts'/n) for n in (module+'.py','live_window_review_v6.py','live_window_host.py','live_window_host_v5.py','live_price_evidence_v2.py','accept_live_host_v6.py')},'scope':'synthetic_offline','release_status':'not_authorized'})
    summary={'cases':len(results),'agreement':sum(r['accounting_and_action_agreement'] for r in results),'offline_proposals':sum(r['system_receipt']=='offline_proposal' for r in results),'blocked':sum(r['system_receipt']=='blocked' for r in results),'mismatch':sum(r['system_receipt']=='rejected_model_mismatch' for r in results),'actual_model_calls':len(actual),'new_model_calls_for_host':0,'real_business_actions':0}
    dump(output/'summary.json',summary);print(summary)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--project',required=True,type=Path);ap.add_argument('--first',required=True,type=Path);ap.add_argument('--output',required=True,type=Path);ap.add_argument('--module',required=True);a=ap.parse_args();run(a.project.resolve(),a.first.resolve(),a.output.resolve(),a.module)
