"""Stage-ordered raw live-role inference through the existing local queue.

First runs see public inputs only. Independent reference/calculation is invoked
only after raw answers are completed and frozen. Never overwrites old attempts.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import sys
from live_window_queue import Worker,read,dump,sha
from offline_role_queue import digest


def envelope(path,platform):
    raw=read(path)
    return raw if isinstance(raw,dict) else {'suite_id':platform+'-live-'+sha(path)[:16],'cases':raw}


def old_preservation(root):
    manifests=[root/'work/compound-ledger-v3-20261111/delivery-manifest.json',root/'work/offline-role-queue-20261111/delivery-manifest.json']
    expected={}
    for path in manifests:expected.update(read(path)['files'])
    if any(sha(root/p)!=v for p,v in expected.items()):raise ValueError('Earlier accepted source/evidence changed')
    return expected


def prepare(root,out,public,catalog,platform):
    out.mkdir(parents=True,exist_ok=False)
    original=old_preservation(root)
    source=[root/'scripts'/n for n in ['live_window_review_v6.py','live_window_ph.py','live_price_evidence_v2.py','live_window_queue.py','accept_live_windows_v3.py','ad_decision_contract.py','campaign_price_intake.py','campaign_price_handoff.py','offline_role_queue.py']]
    source.append(root/'docs'/(platform+'-live-ops-window-contract.json'))
    # Persist implementation before opening independently authored live values.
    dump(out/'implementation-before-inputs.json',{'source_sha256':{str(p):sha(p) for p in source},'frozen_at':datetime.now(timezone.utc).isoformat(),'private_gold_read':False,'scope':'synthetic_offline'})
    public=Path(public).resolve();catalog=Path(catalog).resolve();cases=envelope(public,platform);rows=cases['cases'];price=read(catalog)
    compact={key:{k:v for k,v in value.items() if k not in ('input_file','run_dir')} for key,value in price.items()}
    seed=root/'work/offline-role-queue-20261111/actual-integration/queue.sqlite'
    with sqlite3.connect(seed.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(out/'queue.sqlite') as dest:src.backup(dest)
    worker=Worker(root,out/'queue.sqlite',out/'attempts')
    role=platform+'-live-ops';dependency='CN-PRICE' if platform=='douyin' else 'PH-PRICE'
    groups=[];current=[]
    def task(batch):
        return 'Independent NEW synthetic live-window cases. Apply the preloaded station-specific contract over historical percentage examples. Do not read any prior answer, private key, calculator or implementation. No expected answer or calculation supplied. The outer queue JSON conclusion MUST be an object {suite_id,cases:[all required live case outputs]}. Outer missing_fields lists unresolved supplied facts; do not fabricate data.\nPUBLIC PRICE HOST CATALOG (known existing infrastructure proof, not live-window gold)\n'+json.dumps(compact,ensure_ascii=False,separators=(',',':'))+'\nPUBLIC NEW LIVE CASES\n'+json.dumps({'suite_id':cases['suite_id'],'cases':batch},ensure_ascii=False,separators=(',',':'))
    for case in rows:
        if current and (len(current)>=3 or len(task(current+[case]).encode('utf8'))>12000):groups.append(current);current=[]
        if len(task([case]).encode('utf8'))>12000:raise ValueError('One public case exceeds queue task intake')
        current.append(case)
    if current:groups.append(current)
    jobs=[]
    for index,batch in enumerate(groups,1):
        job_id=('CN' if platform=='douyin' else 'PH')+'-LIVE-FIRST-'+str(index)
        jobs.append({'job_id':job_id,'idempotency_key':cases['suite_id']+':first:'+str(index),'platform':platform,'country':'CN' if platform=='douyin' else 'PH','role_suffix':'live-ops','kind':'offline_review','task':task(batch),'priority':50,'requires':[dependency],'requires_price':[dependency],'evaluation_time':'2026-11-11T15:00:00+08:00'})
    worker.queue.submit(jobs);dump(out/'jobs.json',jobs)
    dump(out/'plan.json',{'platform':platform,'role':role,'public_input':str(public),'public_sha256':sha(public),'catalog':str(catalog),'catalog_sha256':sha(catalog),'jobs':[j['job_id'] for j in jobs],'case_ids':[c['case_id'] for c in rows],'case_count':len(rows),'previous_evidence':original,'scope':'synthetic_offline','release_status':'not_authorized','new_live_cases':True,'price_proofs_reused':True})
    print(json.dumps({'prepared_cases':len(rows),'queue_jobs':len(jobs),'model_calls':0}))


def run(root,out):
    plan=read(out/'plan.json');freeze=read(out/'implementation-before-inputs.json')
    if any(sha(p)!=v for p,v in freeze['source_sha256'].items()):raise ValueError('Pre-inference implementation changed')
    if sha(plan['public_input'])!=plan['public_sha256']:raise ValueError('Public raw inputs changed')
    worker=Worker(root,out/'queue.sqlite',out/'attempts');results=[]
    for _ in plan['jobs']:
        result=worker.run_once('live-window-raw-first')
        if result is None or result['job_id'] not in plan['jobs']:raise ValueError('Unexpected queue dispatch')
        results.append({'job_id':result['job_id'],'state':result['state'],'evidence':result['evidence']})
        if result['state']!='completed':break
    dump(out/'first-run-results.json',results)
    if len(results)!=len(plan['jobs']) or any(r['state']!='completed' for r in results):raise ValueError('Incomplete first live batch; preserve before recovery')
    models=[];files={}
    for result in results:
        run_dir=Path(result['evidence']['run_dir']);answer=read(run_dir/'answer.md');body=answer['conclusion']
        if body['suite_id']!=envelope(plan['public_input'],plan['platform'])['suite_id']:raise ValueError('Raw model suite binding failed')
        models.extend(body['cases'])
        files.update({str(p):sha(p) for p in run_dir.iterdir() if p.is_file()})
    if len(models)!=len({c['case_id'] for c in models}) or {c['case_id'] for c in models}!=set(plan['case_ids']):raise ValueError('Raw first exact case coverage failed')
    dump(out/'frozen-raw-first.json',{'suite_id':envelope(plan['public_input'],plan['platform'])['suite_id'],'cases':models,'raw_files_sha256':files,'frozen_at':datetime.now(timezone.utc).isoformat(),'new_model_calls':len(results),'first_score_not_replaced':True})
    old_preservation(root)
    print(json.dumps({'raw_cases_frozen':len(models),'actual_model_calls':len(results),'all_completed':True,'semantic_grade':'not yet asserted'}))


def calculate(root,out):
    plan=read(out/'plan.json');raw=read(out/'frozen-raw-first.json')
    if any(sha(p)!=v for p,v in raw['raw_files_sha256'].items()):raise ValueError('Raw answers changed')
    from live_price_evidence_v2 import PriceVerifier
    if plan['platform']=='douyin':
        from live_window_review_v6 import review_cn as review
    else:
        from live_window_ph import review_ph as review
    cases=envelope(plan['public_input'],plan['platform'])['cases'];verifier=PriceVerifier(read(plan['catalog']))
    results=[review(c,verifier) for c in cases]
    dump(out/'system-calculation-first.json',{'cases':results,'calculated_after_raw_freeze':True,'model_source_sha256':sha(out/'frozen-raw-first.json'),'scope':'synthetic_offline','release_status':'not_authorized'})
    print(json.dumps({'calculated_cases':len(results),'new_model_calls':0}))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=('prepare','run','calculate'));ap.add_argument('--project',required=True);ap.add_argument('--output',required=True);ap.add_argument('--public');ap.add_argument('--catalog');ap.add_argument('--platform',choices=('douyin','tiktok'));a=ap.parse_args();root=Path(a.project).resolve();out=Path(a.output).resolve()
    if a.mode=='prepare':prepare(root,out,Path(a.public),Path(a.catalog),a.platform)
    elif a.mode=='run':run(root,out)
    else:calculate(root,out)
