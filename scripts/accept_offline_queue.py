"""Controlled infrastructure acceptance; reused price cases are not fresh blind data."""
import argparse
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sys
from offline_queue_worker import Worker,dump,read,sha
from offline_role_queue import digest


def preserve(project):
    b=project/'work/compound-ledger-v3-20261111';old=read(b/'delivery-manifest.json')
    checks={name:sha(project/name)==expected for name,expected in old['files'].items()}
    if not all(checks.values()):raise ValueError('Previous accepted evidence changed')
    sys.path.insert(0,str(project/'work/fresh-composite-blind-20261111'))
    import fresh_harness as h
    h.verify_originals()
    return checks


def prepare(root,b):
    b.mkdir(parents=True,exist_ok=False)
    before=preserve(root);dump(b/'prior-evidence-preservation.json',before)
    caller_dir=root/'work/fresh-composite-state-repair-20261111/replay-v2'
    cn=caller_dir/'douyin-shop-ops-directed-caller-1.json';ph=caller_dir/'tiktok-shop-ops-directed-caller-2.json'
    bad=root/'work/fresh-composite-blind-20261111/typed-callers/batch-2.json'
    def job(name,platform,role,task,priority,**extra):
        return dict(job_id=name,idempotency_key='queue-infrastructure:'+name,platform=platform,country='CN' if platform=='douyin' else 'PH',role_suffix=role,kind='offline_review',task=task,priority=priority,**extra)
    time='2026-11-11T15:00:00+08:00'
    jobs=[
        job('CN-PLAN','douyin','lead','给定合成项目：商品事实卡已齐；库存总数40、已预占7、安全库存5；尚未接真实店铺。只汇总可用数量及商城价审前置依赖，明确执行岗、交付件及离线/实盘边界。不要当成上架授权。',60),
        job('CN-PRICE','douyin','shop-ops','Review only the frozen synthetic price handoff and return the dedicated typed receipts. This is a labelled queue infrastructure replay, not a new blind case.',50,kind_override=None),
        job('PH-PRICE','tiktok','shop-ops','Review only the frozen synthetic Philippine price handoff using the dedicated typed receipts. Existing case replay tests queue infrastructure only.',55),
        job('PH-PLAN','tiktok','lead','给定菲律宾合成项目：上游是本次队列已完成的价审，仍没有真实店铺接入。只汇总安全排期前置条件、负责岗位和仍需资料，不得发布、支出、核销或将离线价格接收视为经营授权。',40,requires=['PH-PRICE'],requires_price=['PH-PRICE'],evaluation_time=time),
        job('PH-STALE','tiktok','lead','This dependent review must not run with expired price proof.',45,requires=['PH-PRICE'],requires_price=['PH-PRICE'],evaluation_time=(datetime.fromisoformat(time)+timedelta(microseconds=1)).isoformat()),
        job('BAD-CALLER','douyin','shop-ops','This submitted caller hash is intentionally stale; block before inference.',99),
        job('BAD-FLOOR','douyin','shop-ops','This frozen rejected bundle must be blocked by the unchanged price gate before inference.',98),
        job('BAD-CHILD','douyin','lead','This child cannot run when its price-review parent was blocked.',95,requires=['BAD-FLOOR'])]
    by={p['job_id']:p for p in jobs};by['CN-PRICE'].pop('kind_override',None)
    for name,path in [('CN-PRICE',cn),('PH-PRICE',ph),('BAD-CALLER',cn),('BAD-FLOOR',bad)]:
        by[name].update(kind='price_review',price_input=str(path),price_input_sha256='0'*64 if name=='BAD-CALLER' else sha(path),evaluation_time=time)
    by['CN-PRICE']['requires']=['CN-PLAN']
    dump(b/'jobs.json',jobs)
    worker=Worker(root,b/'queue.sqlite',b/'attempts');worker.queue.submit(jobs)
    worker.queue.submit(jobs)
    dump(b/'prepared.json',{'scope':'synthetic_offline','release_status':'not_authorized','jobs':len(jobs),'double_submission_row_count':len(worker.queue.list_jobs()),'new_model_calls':0,'reused_price_cases':'two existing repaired-safe cases and original rejected batch-2; never fresh blind model score','frozen_sources':{str(root/'scripts'/n):sha(root/'scripts'/n) for n in ['offline_role_queue.py','offline_queue_worker.py','accept_offline_queue.py']},'created_at':datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'prepared_jobs':len(jobs),'rows_after_duplicate_submission':len(worker.queue.list_jobs()),'model_calls':0}))


def drain(root,b):
    plan=read(b/'prepared.json')
    if any(sha(p)!=expected for p,expected in plan['frozen_sources'].items()):raise ValueError('Frozen queue source changed')
    preserve(root);worker=Worker(root,b/'queue.sqlite',b/'attempts')
    executed=[]
    for _ in range(12):
        result=worker.run_once('acceptance-worker',simulate_lost_settlement='PH-PLAN')
        if result is None:break
        executed.append({'job_id':result['job_id'],'state':result['state']})
        if result['state']=='uncertain':break
    dump(b/'drain-result.json',{'executed':executed,'queue':worker.queue.list_jobs(),'automatic_retry':False})
    print(json.dumps({'executed':executed,'states':{j['job_id']:j['state'] for j in worker.queue.list_jobs()}},ensure_ascii=False))


def finalize(root,b):
    worker=Worker(root,b/'queue.sqlite',b/'attempts');before=worker.queue.list_jobs();ph=worker.queue.get('PH-PLAN')
    if ph['state']!='uncertain' or ph['attempts']!=1:raise ValueError('Controlled uncertain settlement evidence missing')
    if worker.run_once('do-not-rerun') is not None:raise AssertionError('An uncertain or blocked dependency job was automatically rerun')
    attempts=[p for p in (b/'attempts').iterdir() if p.is_dir() and p.name.startswith('PH-PLAN-')]
    if len(attempts)!=1:raise AssertionError('Duplicate model attempt')
    existing=worker.validate_completion(attempts[0]);dump(b/'uncertain-existing-run-before-reconcile.json',existing)
    reconciled=worker.reconcile(attempts[0]);after=worker.queue.list_jobs()
    if reconciled['state']!='completed' or reconciled['attempts']!=1:raise AssertionError('Reconciliation changed the model-attempt count')
    if worker.run_once('no-duplicates-after-reconcile') is not None:raise AssertionError('Unexpected automatic duplicate task')
    states={j['job_id']:j['state'] for j in after}
    if sum(s=='completed' for s in states.values())!=4 or sum(s=='blocked' for s in states.values())!=3 or states['BAD-CHILD']!='pending':raise AssertionError('Queue safety state differs')
    actual=[]
    for row in after:
        if row['state']=='completed':
            matching=[p for p in (b/'attempts').iterdir() if p.is_dir() and p.name.startswith(row['job_id']+'-')]
            if len(matching)!=1:raise AssertionError('Actual run count differs')
            evidence=worker.validate_completion(matching[0]);actual.append({'job_id':row['job_id'],**evidence})
    source_checks=preserve(root)
    dump(b/'final-queue-state.json',after);dump(b/'actual-cli-evidence.json',actual)
    summary={'scope':'synthetic_offline','release_status':'not_authorized','jobs_submitted':8,'duplicate_submission_rows':8,'completed_jobs':4,'blocked_jobs':3,'pending_due_to_blocked_dependency':1,'actual_model_calls':len(actual),'actual_role_routes':sorted({j['role'] for j in after if j['state']=='completed'}),'price_host_runs':sum(r['kind']=='price_review' for r in actual),'price_accepted_subjects':sum(sum(p['system_receipt']=='accepted' for p in r.get('price_records',[])) for r in actual),'changed_caller_preflight_blocked':worker.queue.get('BAD-CALLER')['attempts']==0,'unsafe_floor_preflight_blocked':worker.queue.get('BAD-FLOOR')['attempts']==0,'expired_upstream_dependency_blocked':states['PH-STALE']=='blocked' and worker.queue.get('PH-STALE')['attempts']==0,'blocked_parent_child_model_calls':worker.queue.get('BAD-CHILD')['attempts'],'controlled_uncertain_job':{'job_id':'PH-PLAN','before':'uncertain','after':reconciled['state'],'actual_model_attempts':reconciled['attempts'],'extra_model_calls_for_reconciliation':0},'all_new_runtime_sol_xhigh_tool0':all(r['runtime']['passed'] for r in actual),'actual_service_tiers':sorted({v for r in actual for v in r['runtime']['service_tier']}),'prior_files_unchanged':len(source_checks),'all_prior_files_unchanged':all(source_checks.values()),'model_window_settings_changed':False,'no_business_connection':True,'created_at':datetime.now(timezone.utc).isoformat()}
    if summary['actual_model_calls']!=4 or summary['price_accepted_subjects']!=2:raise AssertionError('Actual model/price coverage mismatch')
    dump(b/'integration-summary.json',summary);print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=('prepare','drain','finalize'));ap.add_argument('--project',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();root=Path(a.project).resolve();b=Path(a.output).resolve()
    {'prepare':prepare,'drain':drain,'finalize':finalize}[a.mode](root,b)
