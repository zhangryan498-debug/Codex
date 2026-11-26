"""Finite synthetic role execution and immutable evidence; no business connector."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import importlib
import json
from pathlib import Path
import sqlite3
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))
from live_window_queue import Worker,read,dump,sha,runtime
from offline_role_queue import digest,normalize
from live_price_evidence_v2 import PriceVerifier
from live_window_review_v6 import review_cn
from live_window_ph_v3 import review_ph
from accept_live_windows_v5 import old_preservation

def stamp():return datetime.now(timezone.utc).isoformat()
def rows(blob):return blob['cases'] if isinstance(blob,dict) else blob
def root_paths():
    return [PROJECT/'scripts/live_compliance_review_v1.py',PROJECT/'scripts/run_live_compliance.py',PROJECT/'docs/live-compliance-contract-v1.md',PROJECT/'docs/live-compliance-rule-pack-v1.json',PROJECT/'docs/live-compliance-source-scope-note-v1.md']

class Infrastructure:
    def __init__(self,path):
        self.path=Path(path);self.data=read(self.path);self.cache={};self.financial_cache={}
        for p,h in self.data['source_sha256'].items():
            if sha(p)!=h:raise ValueError('Infrastructure bytes changed: '+p)
        self.catalogs={p:read(x['catalog_path']) for p,x in self.data['platforms'].items()}
        self.verifiers={p:PriceVerifier(c) for p,c in self.catalogs.items()}

    def price(self,case,alias):
        platform=case.get('platform');fault=case.get('price_fixture')
        if alias!='A':return {'proven':False,'reason':'new_sku_has_no_inherited_price_proof','logical_sku':alias}
        if platform not in self.catalogs or fault not in ('accepted','below_floor','expired','missing','identity_mismatch'):
            return {'proven':False,'reason':'missing_or_unsupported_price_fixture'}
        cachekey=(platform,fault)
        if cachekey not in self.cache:
            catalog=self.catalogs[platform];prefix='CN' if platform=='douyin' else 'PH';ref=prefix+('_BELOW_FLOOR' if fault=='below_floor' else '_ACCEPTED');entry=catalog[ref]
            call={'platform':platform,'country':'CN' if platform=='douyin' else 'PH','currency':'CNY' if platform=='douyin' else 'PHP','target_sku':entry['sku'],'quantity':entry['quantity'],'price_revision':entry['revision'],'price_ref':ref,'evaluation_time':entry['evaluation_time']}
            if fault=='expired':call['evaluation_time']=(datetime.fromisoformat(entry['valid_until'])+timedelta(seconds=1)).isoformat()
            if fault=='missing':call['price_ref']='NO_SUCH_PRICE_REFERENCE'
            if fault=='identity_mismatch':call['target_sku']+='-wrong-identity'
            proof=self.verifiers[platform](call)
            self.cache[cachekey]={**proof,'price_fixture':fault,'logical_sku':'A','original_sku':entry['sku'],'frozen_synthetic_evaluation_time':call['evaluation_time'],'source_catalog_sha256':sha(self.data['platforms'][platform]['catalog_path']),'new_price_model_called':False}
        return deepcopy(self.cache[cachekey])

    def financial(self,case,alias):
        platform=case.get('platform')
        if alias!='A' or case.get('price_fixture')!='accepted' or platform not in self.data['platforms']:
            return {'proven':False,'reason':'financial_scope_or_price_identity_not_prebound','logical_sku':alias}
        if platform not in self.financial_cache:
            source=self.data['platforms'][platform]['financial_fixture']
            result=(review_cn if platform=='douyin' else review_ph)(source,self.verifiers[platform])
            proven=result['action'] in ('propose_continue','propose_increase') and result.get('price_proven') is True and result.get('current_windows_complete') is True and not result.get('blocking_reasons')
            self.financial_cache[platform]={'proven':bool(proven),'reason':'existing_financial_window_module_replayed','source_case_id':source['case_id'],'original_sku':source['current_sku'],'original_session_id':source['session_id'],'synthetic_fixture_only':True,'fixture_sha256':digest(source),'system_result_sha256':digest(result),'post_ad_profit_minor':result.get('cumulative',{}).get('mature',{}).get('post_ad_profit_minor') if result.get('cumulative',{}).get('mature') else None,'not_new_financial_blind_test':True}
        return deepcopy(self.financial_cache[platform])

class ComplianceWorker(Worker):
    def references(self,role):
        return super().references(role)+[self.root/'docs/live-compliance-contract-v1.md',self.root/'docs/live-compliance-rule-pack-v1.json',self.root/'docs/live-compliance-source-scope-note-v1.md']

def infrastructure(out):
    out=Path(out);out.mkdir(exist_ok=False)
    prior=old_preservation(PROJECT);platforms={};sources={};replay=[]
    for platform,site,first,host in [('douyin','cn','raw-first-v2','host-v6-bound'),('tiktok','ph','raw-first','host-v3-closed-bound')]:
        base=PROJECT/('work/live-window-'+site+'-20261111');plan=read(base/first/'plan.json');public=Path(plan['public_input']);catalog=Path(plan['catalog']);original=read(base/host/'receipts.json')
        expected={r['case_id']:r['system_result'] for r in original['cases']};verifier=PriceVerifier(read(catalog));review=review_cn if platform=='douyin' else review_ph
        fixture=None
        for case in rows(read(public)):
            actual=review(case,verifier)
            equal=canonical_equal(actual,expected[case['case_id']])
            replay.append({'case_id':case['case_id'],'platform':platform,'same_original_system_result':equal,'action':actual['action'],'price_proven':actual['price_proven'],'execution_authorized':actual['execution_authorized']})
            if not equal:raise ValueError('Prior live calculation changed: '+case['case_id'])
            if fixture is None and actual['action']=='propose_continue' and actual.get('current_windows_complete') is True and actual.get('price_proven') is True and not actual['blocking_reasons']:
                fixture=deepcopy(case)
        if fixture is None:raise ValueError('No existing safe financial fixture')
        platforms[platform]={'catalog_path':str(catalog),'financial_fixture':fixture,'public_source_path':str(public),'original_host_path':str(base/host/'receipts.json')}
        for p in (public,catalog,base/host/'receipts.json'):sources[str(p)]=sha(p)
    for name in ('live_price_evidence_v2.py','live_window_review_v6.py','live_window_ph_v3.py','ad_decision_contract.py','campaign_price_intake.py','campaign_price_handoff.py'):
        sources[str(PROJECT/'scripts'/name)]=sha(PROJECT/'scripts'/name)
    dump(out/'infrastructure-fixtures.json',{'created_at':stamp(),'scope':'synthetic_offline','release_status':'not_authorized','source_sha256':sources,'platforms':platforms,'old_accepted_evidence_files_verified':len(prior)})
    dump(out/'existing-live-replay.json',{'cases':replay,'same_original_results':sum(r['same_original_system_result'] for r in replay),'new_model_calls':0,'new_blind_cases':0,'scope':'synthetic_offline'})
    print(json.dumps({'old_live_replay_cases':len(replay),'same_results':len(replay),'existing_price_model_calls':0,'new_price_model_calls':0,'old_delivery_files_verified':len(prior)}),flush=True)

def canonical_equal(a,b):return digest(a)==digest(b)

def prepare(out,public,platform,infrastructure_path,stage):
    out=Path(out);out.mkdir(exist_ok=False)
    # Freeze implementation before opening either independently authored input.
    paths=root_paths()+[PROJECT/'scripts'/n for n in ('live_window_queue.py','offline_role_queue.py','run_codex_role.py','campaign_price_intake.py','shop_process_guard.py')]
    dump(out/'implementation-before-inputs.json',{'files':{str(p):sha(p) for p in paths},'frozen_at':stamp(),'private_gold_read':False,'public_cases_opened_after_this_invocation_freeze':True,'stage':stage})
    infra=Infrastructure(infrastructure_path);public=Path(public).resolve();data=read(public);cases=data['cases']
    if len(cases)!=len({c['case_id'] for c in cases}) or any(c['platform']!=platform for c in cases):raise ValueError('Duplicate cases or station mismatch')
    seed=PROJECT/'work/offline-role-queue-20261111/actual-integration/queue.sqlite'
    with sqlite3.connect(seed.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(out/'queue.sqlite') as dst:src.backup(dst)
    worker=ComplianceWorker(PROJECT,out/'queue.sqlite',out/'attempts')
    batches=[];current=[]
    def task(batch):
        return 'OFFLINE LIVE COMPLIANCE REVIEW. The preloaded live-compliance-contract-v1.md and live-compliance-rule-pack-v1.json govern THIS task; historical role/window examples do not override them. Return the compliance case schema, not the older financial-window schema. No private key, implementation, prior answer or expected policy label is supplied. All speech/captions/visuals and platform notices are synthetic observations. A/B are logical product aliases. The price_fixture names refer to known existing frozen price infrastructure; safe words never override below_floor, expired, missing or identity_mismatch. The exact role queue outer envelope remains required. conclusion={suite_id,cases:[one output per public case]}. Each case must include scope, release_status, execution_authorized=false, explanation, final_recommendation and exactly one content segment per distinct event ID. No guaranteed punishment, no unpublished word dictionary.\nPUBLIC INDEPENDENT INPUT\n'+json.dumps({'suite_id':data['suite_id'],'cases':batch},ensure_ascii=False,separators=(',',':'))
    for case in cases:
        if current and (len(current)>=4 or len(task(current+[case]).encode('utf8'))>11500):batches.append(current);current=[]
        if len(task([case]).encode('utf8'))>11500:raise ValueError('One public case exceeds bounded task intake')
        current.append(case)
    if current:batches.append(current)
    prefix='CN' if platform=='douyin' else 'PH';dep=prefix+'-PRICE';role=platform+'-live-ops';catalog=infra.catalogs[platform];evaluation=catalog[prefix+'_ACCEPTED']['evaluation_time'];jobs=[]
    for i,batch in enumerate(batches,1):
        jobs.append({'job_id':prefix+'-LIVE-COMPLIANCE-'+stage.upper()+'-'+str(i),'idempotency_key':data['suite_id']+':'+stage+':'+str(i),'platform':platform,'country':'CN' if platform=='douyin' else 'PH','role_suffix':'live-ops','kind':'offline_review','task':task(batch),'priority':50,'requires':[dep],'requires_price':[dep],'evaluation_time':evaluation})
    worker.queue.submit(jobs);dump(out/'jobs.json',jobs)
    dump(out/'plan.json',{'suite_id':data['suite_id'],'platform':platform,'role':role,'public_input':str(public),'public_sha256':sha(public),'infrastructure':str(Path(infrastructure_path).resolve()),'infrastructure_sha256':sha(infrastructure_path),'jobs':[j['job_id'] for j in jobs],'case_ids':[c['case_id'] for c in cases],'scope':'synthetic_offline','release_status':'not_authorized','stage':stage,'independent_new_blind_test':stage=='first'})
    print(json.dumps({'platform':platform,'prepared_cases':len(cases),'batches':len(jobs),'model_calls':0,'stage':stage}),flush=True)

def run(out):
    out=Path(out);plan=read(out/'plan.json');frozen=read(out/'implementation-before-inputs.json')
    for p,h in frozen['files'].items():
        if sha(p)!=h:raise ValueError('Pre-inference implementation/reference changed: '+p)
    if sha(plan['public_input'])!=plan['public_sha256'] or sha(plan['infrastructure'])!=plan['infrastructure_sha256']:raise ValueError('Frozen input changed')
    if (out/'frozen-raw.json').exists():raise ValueError('Already completed; never rerun original batches')
    worker=ComplianceWorker(PROJECT,out/'queue.sqlite',out/'attempts');results=[]
    for job in plan['jobs']:
        record=worker.queue.get(job)
        if record['state']=='completed':result=record
        elif record['state']=='pending':result=worker.run_once('live-compliance-'+plan['stage'])
        else:raise ValueError('Task not safe to repeat: '+job+':'+record['state'])
        if result is None or result['job_id']!=job or result['state']!='completed':
            dump(out/('incomplete-'+datetime.now(timezone.utc).strftime('%H%M%S')+'.json'),result)
            raise ValueError('Incomplete or uncertain actual model task; preserve and inspect before recovery')
        results.append({'job_id':job,'state':'completed','evidence':result['evidence']});print(json.dumps({'completed_job':job,'run_dir':result['evidence']['run_dir']}),flush=True)
    dump(out/'run-results.json',results);cases=[];raw_files={}
    for r in results:
        directory=Path(r['evidence']['run_dir']);answer=read(directory/'answer.md');body=answer['conclusion']
        if body['suite_id']!=plan['suite_id']:raise ValueError('Model suite binding invalid')
        cases.extend(body['cases']);raw_files.update({str(p):sha(p) for p in directory.iterdir() if p.is_file()})
    if len(cases)!=len({c['case_id'] for c in cases}) or {c['case_id'] for c in cases}!=set(plan['case_ids']):raise ValueError('Actual model case coverage invalid')
    dump(out/'frozen-raw.json',{'suite_id':plan['suite_id'],'cases':cases,'raw_files_sha256':raw_files,'frozen_at':stamp(),'stage':plan['stage'],'actual_model_calls':len(results),'first_score_preserved':True})
    print(json.dumps({'raw_cases_frozen':len(cases),'actual_model_calls':len(results),'semantic_grade':'not yet asserted'}),flush=True)

def extract_actual(out):
    out=Path(out);plan=read(out/'plan.json');raw=read(out/'frozen-raw.json');worker=ComplianceWorker(PROJECT,out/'queue.sqlite',out/'attempts');actual=[];runs=[];files={}
    for r in read(out/'run-results.json'):
        directory=Path(r['evidence']['run_dir']);proof=worker.validate_completion(directory.parent)
        if not proof['runtime']['passed']:raise ValueError('Actual runtime did not satisfy offline model constraints')
        answer=read(directory/'answer.md');actual.extend(answer['conclusion']['cases']);runs.append({'job_id':r['job_id'],'run_dir':str(directory),**proof['runtime']})
        files.update({str(p):sha(p) for p in directory.iterdir() if p.is_file()})
    if files!=raw['raw_files_sha256'] or not canonical_equal(actual,raw['cases']):raise ValueError('Frozen extraction detached from actual raw answers')
    return actual,runs

def host(first,out,module):
    first,out=Path(first),Path(out);plan=read(first/'plan.json');actual,runs=extract_actual(first)
    if sha(plan['public_input'])!=plan['public_sha256'] or sha(plan['infrastructure'])!=plan['infrastructure_sha256']:raise ValueError('Host input provenance changed')
    infra=Infrastructure(plan['infrastructure']);models={x['case_id']:x for x in actual};pack=read(PROJECT/'docs/live-compliance-rule-pack-v1.json');review=importlib.import_module(module).review
    cases=read(plan['public_input'])['cases'];results=[review(c,models[c['case_id']],pack,infra.price,infra.financial) for c in cases]
    out.mkdir(exist_ok=False)
    dump(out/'receipts.json',{'suite_id':plan['suite_id'],'cases':results,'actual_runs':runs,'raw_sha256':sha(first/'frozen-raw.json'),'public_sha256':sha(plan['public_input']),'infrastructure_sha256':sha(plan['infrastructure']),'module':module,'module_sha256':sha(Path(importlib.import_module(module).__file__)),'scope':'synthetic_offline','release_status':'not_authorized','new_model_calls_for_host':0})
    summary={'cases':len(results),'offline_proposals':sum(r['proposal_eligible'] for r in results),'blocked':sum(not r['proposal_eligible'] for r in results),'model_control_agreement':sum(r['model_control_agreement'] for r in results),'model_evidence_binding_valid':sum(r['model_evidence_binding_valid'] for r in results),'actual_model_calls':len(runs),'new_model_calls_for_host':0,'real_business_actions':0,'stage':plan['stage']}
    dump(out/'summary.json',summary);print(json.dumps(summary),flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=('infrastructure','prepare','run','host'));ap.add_argument('--output',required=True);ap.add_argument('--public');ap.add_argument('--platform',choices=('douyin','tiktok'));ap.add_argument('--infrastructure');ap.add_argument('--first');ap.add_argument('--module',default='live_compliance_review_v1');ap.add_argument('--stage',default='first');a=ap.parse_args()
    if a.mode=='infrastructure':infrastructure(a.output)
    elif a.mode=='prepare':prepare(a.output,a.public,a.platform,a.infrastructure,a.stage)
    elif a.mode=='run':run(a.output)
    else:host(a.first,a.output,a.module)

if __name__=='__main__':main()
