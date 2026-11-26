import argparse
import importlib
from pathlib import Path
from live_window_queue import read,dump,sha,runtime
from live_price_evidence_v2 import PriceVerifier
from live_window_host import receipt
from accept_live_windows_v2 import envelope,old_preservation

def run(root,first,output,module):
    raw=read(first/'frozen-raw-first.json');plan=read(first/'plan.json')
    if any(sha(p)!=v for p,v in raw['raw_files_sha256'].items()):raise ValueError('First raw files changed')
    if sha(plan['public_input'])!=plan['public_sha256'] or sha(plan['catalog'])!=plan['catalog_sha256']:raise ValueError('Public input/catalog changed')
    rows=envelope(plan['public_input'],plan['platform'])['cases'];models={m['case_id']:m for m in raw['cases']}
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
    dump(output/'receipts.json',{'cases':results,'actual_runs':actual,'raw_first_sha256':sha(first/'frozen-raw-first.json'),'source_sha256':{n:sha(root/'scripts'/n) for n in (module+'.py','live_window_host.py','live_price_evidence_v2.py','accept_live_host.py')},'scope':'synthetic_offline','release_status':'not_authorized'})
    summary={'cases':len(results),'agreement':sum(r['accounting_and_action_agreement'] for r in results),'offline_proposals':sum(r['system_receipt']=='offline_proposal' for r in results),'blocked':sum(r['system_receipt']=='blocked' for r in results),'mismatch':sum(r['system_receipt']=='rejected_model_mismatch' for r in results),'actual_model_calls':len(actual),'new_model_calls_for_host':0,'real_business_actions':0}
    dump(output/'summary.json',summary);print(summary)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--project',required=True,type=Path);ap.add_argument('--first',required=True,type=Path);ap.add_argument('--output',required=True,type=Path);ap.add_argument('--module',required=True);a=ap.parse_args();run(a.project.resolve(),a.first.resolve(),a.output.resolve(),a.module)
