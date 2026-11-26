"""New receiver attempt: exact original-input binding and explicit fixture rebasing.

Preserves the first receiver attempt and every original role answer. No model
calls. Financial values are known reused synthetic infrastructure, not new gold.
"""
import argparse
from copy import deepcopy
import importlib
from pathlib import Path
import re
import sqlite3
import sys

import run_live_compliance as base

MARKER='PUBLIC INDEPENDENT INPUT\n'
KNOWN_CONFIG_WARNING='Codex is ignoring 1 unrecognized configuration setting. Check for typos or deprecated settings.\n  user (<Codex-config>): `computer_use.windows.always_allowed_app_ids` is ignored.'

def runtime_reconciled(directory):
    """A completed original final answer may survive transport reconnect logs.

    Original strict evidence stays visible. Never permits a failed/incomplete
    turn, a second final JSON, tools, another runtime, or any unrecognised error.
    """
    original=base.runtime(directory);proof=deepcopy(original)
    events=[base.json.loads(s) for s in (directory/'events.jsonl').read_text(encoding='utf8').splitlines() if s.strip()]
    message_positions=[(i,e['item'].get('text','')) for i,e in enumerate(events) if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='agent_message']
    messages=[text for i,text in message_positions]
    finals=[]
    for text in messages:
        try:
            parsed=base.json.loads(text)
            if isinstance(parsed,dict) and 'conclusion' in parsed:finals.append(text)
        except (ValueError,TypeError):pass
    exact=len(finals)==1 and bool(messages) and finals[0]==messages[-1] and finals[0].rstrip('\r\n')==(directory/'answer.md').read_text(encoding='utf8').rstrip('\r\n')
    errors=[];unknown=[];transport_positions=[]
    for index,e in enumerate(events):
        if e.get('type')=='error' or e.get('item',{}).get('type')=='error':
            message=e.get('message') or e.get('item',{}).get('message','')
            if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='error' and message==KNOWN_CONFIG_WARNING:continue
            errors.append(message)
            if not (e.get('type')=='error' and re.fullmatch(r'Reconnecting\.\.\. [1-5]/5 \(stream disconnected before completion: tls handshake eof\)',message)):
                unknown.append(message)
            else:transport_positions.append(index)
    completed=sum(e.get('type')=='turn.completed' for e in events)
    failed=sum(e.get('type')=='turn.failed' for e in events)
    final_position=message_positions[-1][0] if message_positions else -1
    completion_positions=[i for i,e in enumerate(events) if e.get('type')=='turn.completed']
    chronology=bool(exact and completed==1 and final_position<completion_positions[0] and all(i<final_position for i in transport_positions))
    safe=exact and chronology and completed==1 and failed==0 and original['model']==['gpt-6.1-sol'] and original['reasoning']==['XHigh'] and original['mcp_counts'] and set(original['mcp_counts'])=={'0'} and original['tool_event_count']==0 and not unknown
    proof.update(original_strict_passed=original['passed'],transport_reconnect_events=errors,unrecognised_errors=unknown,unique_final_queue_json=exact,completed_turn_count=completed,failed_turn_count=failed,final_chronology_verified=chronology,nonfinal_agent_message_count=len(messages)-len(finals),reconciliation_kind='completed_original_answer_after_recovered_transport' if not original['passed'] and safe else 'strict_or_rejected',passed=bool(safe))
    return proof

def validate_actual(first,record,plan):
    directory=Path(record['evidence']['run_dir']).resolve();attempt=directory.parent
    inv=base.read(attempt/'invocation.json');completion=base.read(attempt/'completion.json');result=base.read(directory/'result.json')
    worker=base.ComplianceWorker(base.PROJECT,Path(first)/'queue.sqlite',Path(first)/'attempts')
    job=worker.queue.get(inv['job_id']);p=inv['payload'];role=inv['role']
    if job['attempt_id']!=inv['attempt_id'] or base.digest(job['payload'])!=inv['payload_sha256'] or job['payload']!=p:raise ValueError('Original queue/invocation detached')
    if job['state'] not in ('completed','uncertain'):raise ValueError('Original attempt not completed or reconcilable')
    for path,h in inv['source_sha256'].items():
        if base.sha(path)!=h:raise ValueError('Actual invocation source changed')
    if Path(completion['run_dir']).resolve()!=directory or not directory.is_relative_to(attempt) or completion['result_sha256']!=base.sha(directory/'result.json'):raise ValueError('Original completion/result detached')
    if result.get('status')!='completed' or result.get('cli_exit_code')!=0 or result.get('job_id')!=p['job_id'] or result.get('role')!=role:raise ValueError('Actual role did not complete')
    if record.get('job_id')!=p['job_id'] or record['evidence'].get('result_sha256')!=base.sha(directory/'result.json') or record['evidence'].get('answer_sha256')!=base.sha(directory/'answer.md'):raise ValueError('Recorded actual result/answer identity detached')
    for name,h in result['files_sha256'].items():
        target=(directory/name).resolve()
        if not target.is_relative_to(directory) or base.sha(target)!=h:raise ValueError('Original run bytes changed')
    task=worker.task(p,role);refs=worker.references(role)
    prompt=worker.entry.build_prompt(role,task,refs[0].read_text(encoding='utf-8-sig'),refs[1].read_text(encoding='utf-8-sig'),[])
    if task!=inv['task'] or task!=(directory/'task.txt').read_text(encoding='utf8') or prompt!=(directory/'prompt.txt').read_text(encoding='utf8') or base.sha(directory/'prompt.txt')!=inv['prompt_sha256']:raise ValueError('Payload/task/prompt exact chain detached')
    answer=base.read(directory/'answer.md')
    if any(answer.get(k)!=v for k,v in {'job_id':p['job_id'],'role':role,'scope':'synthetic_offline','release_status':'not_authorized'}.items()) or not isinstance(answer.get('conclusion'),dict) or not isinstance(answer.get('missing_fields'),list):raise ValueError('Actual outer answer envelope invalid')
    proof=runtime_reconciled(directory)
    if not proof['passed']:raise ValueError('Runtime reconciliation rejected actual run')
    for dep in p['requires_price']:
        parent=worker.queue.get(dep);ok,why=worker.dependency_check(parent['payload'],parent['evidence'],p)
        if not ok:raise ValueError('Original upstream price dependency no longer valid: '+why)
    return inv,answer,{'job_id':p['job_id'],'run_dir':str(directory),**proof}

def extract_bound(first):
    first=Path(first);plan=base.read(first/'plan.json');raw=base.read(first/'frozen-raw.json')
    records=base.read(first/'run-results.json')
    if [r['job_id'] for r in records]!=plan['jobs']:raise ValueError('Actual jobs differ from original plan')
    prepared=base.read(first/'jobs.json');prepared_ids=[j['job_id'] for j in prepared]
    if prepared_ids!=plan['jobs'] or len(prepared_ids)!=len(set(prepared_ids)):raise ValueError('Prepared jobs are not the exact unique ordered original set')
    jobs={j['job_id']:base.normalize(j) for j in prepared}
    public=[];proofs=[];actual=[];runs=[];files={}
    for record in records:
        directory=Path(record['evidence']['run_dir']);inv,answer,runtime=validate_actual(first,record,plan);job=record['job_id'];runs.append(runtime);actual.extend(answer['conclusion']['cases'])
        files.update({str(p):base.sha(p) for p in directory.iterdir() if p.is_file()})
        if job not in jobs or inv['payload']!=jobs[job] or inv['payload_sha256']!=base.digest(jobs[job]):raise ValueError('Prepared jobs differ from actual invocation')
        if inv['role']!=plan['role'] or inv['payload']['platform']!=plan['platform'] or plan['role']!=plan['platform']+'-live-ops':raise ValueError('Role/platform differs from actual model')
        task=inv['payload']['task']
        if task.count(MARKER)!=1:raise ValueError('Original public input locator not unique')
        original=base.json.loads(task.split(MARKER,1)[1])
        idem=inv['payload']['idempotency_key'].rsplit(':',2)
        if len(idem)!=3 or idem[0]!=original['suite_id'] or idem[1]!=plan['stage'] or not idem[2].isdigit():raise ValueError('Attempt stage is not bound to original idempotent payload')
        if original['suite_id']!=plan['suite_id'] or answer['conclusion']['suite_id']!=plan['suite_id'] or raw['suite_id']!=plan['suite_id']:raise ValueError('Suite differs from actual prompt/answer')
        public.extend(original['cases'])
        proofs.append({'job_id':job,'original_invocation_sha256':base.sha(directory.parent/'invocation.json'),'original_task_sha256':base.sha(directory/'task.txt'),'original_public_batch_sha256':base.digest(original),'actual_answer_sha256':base.sha(directory/'answer.md')})
    supplied=base.read(plan['public_input'])
    if base.sha(plan['public_input'])!=plan['public_sha256'] or supplied['suite_id']!=plan['suite_id'] or not base.canonical_equal(public,supplied['cases']):raise ValueError('Supplied input does not equal public cases in actual model prompts')
    ids=[c['case_id'] for c in public]
    if ids!=plan['case_ids'] or len(ids)!=len(set(ids)) or set(ids)!={m['case_id'] for m in actual} or len(actual)!=len(ids):raise ValueError('Exact original case coverage differs')
    if any(c.get('platform')!=plan['platform'] for c in public):raise ValueError('Original station binding invalid')
    if files!=raw['raw_files_sha256'] or not base.canonical_equal(actual,raw['cases']):raise ValueError('Frozen extraction detached from actual raw answers')
    if raw['stage']!=plan['stage']:raise ValueError('Attempt stage metadata detached')
    return public,actual,runs,proofs

class BoundInfrastructure(base.Infrastructure):
    def __init__(self,path):
        super().__init__(path)
        for platform,info in self.data['platforms'].items():
            site='cn' if platform=='douyin' else 'ph';old_first='raw-first-v2' if platform=='douyin' else 'raw-first';old_host='host-v6-bound' if platform=='douyin' else 'host-v3-closed-bound'
            prior=base.PROJECT/('work/live-window-'+site+'-20261111');plan=base.read(prior/old_first/'plan.json')
            if Path(info['catalog_path']).resolve()!=Path(plan['catalog']).resolve() or Path(info['public_source_path']).resolve()!=Path(plan['public_input']).resolve() or Path(info['original_host_path']).resolve()!=(prior/old_host/'receipts.json').resolve():raise ValueError('Infrastructure sources differ from frozen accepted project fixtures')
            if any(str(Path(p)) not in self.data['source_sha256'] for p in (info['catalog_path'],info['public_source_path'],info['original_host_path'])):raise ValueError('Infrastructure source not included in frozen source map')
            originals=[c for c in base.rows(base.read(info['public_source_path'])) if c['case_id']==info['financial_fixture']['case_id']]
            if len(originals)!=1 or not base.canonical_equal(originals[0],info['financial_fixture']):raise ValueError('Embedded financial fixture differs from its original frozen public source')

    def financial(self,case,alias):
        platform=case.get('platform')
        if alias!='A' or case.get('price_fixture')!='accepted' or platform not in self.data['platforms']:
            return {'proven':False,'reason':'financial_scope_or_price_identity_not_prebound','logical_sku':alias}
        original=self.data['platforms'][platform]['financial_fixture'];source_session=original['session_id'];target_session=case.get('session_id')
        if not isinstance(target_session,str) or not target_session:return {'proven':False,'reason':'simulation_session_missing'}
        projected=deepcopy(original);projected['case_id']=case['case_id']+'-synthetic-financial-fixture'
        transforms=[];changes=[{'path':'/case_id','old':original['case_id'],'new':projected['case_id'],'purpose':'synthetic_case_binding'}]
        def map_session(node,path=()):
            if isinstance(node,dict):
                for key,value in list(node.items()):
                    if key=='session_id':
                        if value!=source_session:raise ValueError('Unmapped source session in fixture')
                        node[key]=target_session;transforms.append('/'.join(map(str,path+(key,))))
                        changes.append({'path':'/'+'/'.join(map(str,path+(key,))),'old':value,'new':target_session,'purpose':'synthetic_session_binding'})
                    else:map_session(value,path+(key,))
            elif isinstance(node,list):
                for i,value in enumerate(node):map_session(value,path+(i,))
        map_session(projected)
        result=(base.review_cn if platform=='douyin' else base.review_ph)(projected,self.verifiers[platform])
        proven=result['action'] in ('propose_continue','propose_increase') and result.get('price_proven') is True and result.get('current_windows_complete') is True and not result.get('blocking_reasons')
        return {'proven':bool(proven),'reason':'existing_module_replayed_on_explicitly_rebound_synthetic_fixture','source_case_id':original['case_id'],'source_session_id':source_session,'simulation_session_id':target_session,'logical_sku':'A','original_sku':original['current_sku'],'source_fixture_sha256':base.digest(original),'derived_fixture_sha256':base.digest(projected),'derived_fixture':projected,'derivation_changes':changes,'system_result_sha256':base.digest(result),'session_fields_rebound':transforms,'numeric_financial_values_reused_without_change':True,'synthetic_fixture_only':True,'not_new_financial_blind_test':True}

def host(first,out,module='live_compliance_review_v1'):
    first,out=Path(first),Path(out);plan=base.read(first/'plan.json');public,models,runs,proofs=extract_bound(first)
    if base.sha(plan['infrastructure'])!=plan['infrastructure_sha256']:raise ValueError('Frozen infrastructure changed')
    infra=BoundInfrastructure(plan['infrastructure']);pack=base.read(base.PROJECT/'docs/live-compliance-rule-pack-v1.json');review=importlib.import_module(module).review;by={m['case_id']:m for m in models}
    results=[review(c,by[c['case_id']],pack,infra.price,infra.financial) for c in public]
    out.mkdir(exist_ok=False)
    base.dump(out/'receipts.json',{'suite_id':plan['suite_id'],'cases':results,'actual_runs':runs,'raw_sha256':base.sha(first/'frozen-raw.json'),'public_sha256':base.sha(plan['public_input']),'original_prompt_input_binding':proofs,'input_exactly_bound_to_actual_model':True,'infrastructure_sha256':base.sha(plan['infrastructure']),'module':module,'module_sha256':base.sha(Path(importlib.import_module(module).__file__)),'receiver_version':'bound-live-compliance-host-v3','receiver_sha256':base.sha(Path(__file__)),'scope':'synthetic_offline','release_status':'not_authorized','new_model_calls_for_host':0})
    summary={'cases':len(results),'offline_proposals':sum(r['proposal_eligible'] for r in results),'blocked':sum(not r['proposal_eligible'] for r in results),'model_control_agreement':sum(r['model_control_agreement'] for r in results),'model_evidence_binding_valid':sum(r['model_evidence_binding_valid'] for r in results),'actual_model_calls':len(runs),'new_model_calls_for_host':0,'real_business_actions':0,'stage':plan['stage'],'receiver_version':'v3','original_receiver_preserved':True}
    base.dump(out/'summary.json',summary);print(base.json.dumps(summary),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--first',required=True);ap.add_argument('--output',required=True);ap.add_argument('--module',default='live_compliance_review_v1');a=ap.parse_args();host(a.first,a.output,a.module)
