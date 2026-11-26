"""Explicit, finite offline queue dispatcher; no daemon, schedule or auto retry."""
import argparse
import contextlib
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from offline_role_queue import Queue,canonical,digest,ValidationError,LeaseError


def now():return datetime.now(timezone.utc).isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):
    import campaign_price_intake as intake
    return intake.parse_bytes(Path(path).read_bytes())
def dump(path,data):
    with Path(path).open('x',encoding='utf8',newline='\n') as stream:stream.write(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def runtime(run):
    events=[json.loads(line) for line in (run/'events.jsonl').read_text(encoding='utf8').splitlines() if line.strip()]
    log=(run/'stderr.log').read_text(encoding='utf8',errors='replace')
    lines=[s for s in log.splitlines() if re.match(r'^\d{4}-\d\d-\d\dT\S+\s+INFO\s',s) and 'SessionConfiguredEvent' in s]
    model=sorted(set(v for s in lines for v in re.findall(r'\bmodel:\s*"([^"\r\n]+)"',s)))
    effort=sorted(set(v for s in lines for v in re.findall(r'\breasoning_effort:\s*Some\(([^)]+)\)',s)))
    tier=sorted(set(v for s in lines for v in re.findall(r'\bservice_tier:\s*Some\("([^"\r\n]+)"\)',s)))
    mcp=re.findall(r'\bmcp_server_count\s*=\s*(\d+)',log)
    tool=[e for e in events if e.get('type') in ('item.started','item.updated','item.completed') and e.get('item',{}).get('type') not in ('agent_message','reasoning','error')]
    errors=[];ignored=0
    for e in events:
        if e.get('type') in ('error','turn.failed') or e.get('item',{}).get('type')=='error':
            text=canonical(e)
            if 'computer_use.windows.always_allowed_app_ids' in text and any(k in text.lower() for k in ('ignored','unrecognized','unknown config','unsupported')):ignored+=1
            else:errors.append(e.get('type'))
    result={'turn_completed':any(e.get('type')=='turn.completed' for e in events),'model':model,'reasoning':effort,'service_tier':tier,'mcp_counts':mcp,'tool_event_count':len(tool),'unexpected_error_types':errors,'known_ignored_warnings':ignored,'stderr_sha256':sha(run/'stderr.log')}
    result['passed']=result['turn_completed'] and model==['gpt-6.1-sol'] and effort==['XHigh'] and bool(mcp) and set(mcp)=={'0'} and not tool and not errors
    return result


class Worker:
    def __init__(self,project,db,artifacts):
        self.root=Path(project).resolve(strict=True);self.queue=Queue(db);self.artifacts=Path(artifacts).resolve()
        self.artifacts.mkdir(parents=True,exist_ok=True)
        sys.path.insert(0,str(self.root/'scripts'))
        import run_codex_role as entry
        import campaign_price_intake as intake
        import shop_process_guard as guard
        self.entry,self.intake,self.guard=entry,intake,guard

    def dependency_check(self,parent,evidence,child):
        if parent['kind']!='price_review' or not evidence.get('run_dir'):return False,'No completed bound upstream price run'
        caller=Path(parent['price_input']).resolve(strict=True)
        if sha(caller)!=parent['price_input_sha256']:return False,'Upstream caller changed after review'
        result=self.intake.revalidate_run(caller,parent['platform']+'-shop-ops',child['evaluation_time'],Path(evidence['run_dir']))
        ok=bool(result['records']) and all(r['system_receipt']=='accepted' for r in result['records'])
        return ok,'Accepted upstream price expired, rejected or no longer valid' if not ok else 'Current bound upstream price accepted'

    def references(self,role):
        paths=[self.root/f'runtime-agents/{role}/agent.md',self.root/f'.agents/skills/{role}-playbook/SKILL.md']
        body='\n'.join(p.read_text(encoding='utf-8-sig') for p in paths)
        names=['docs/campaign-price-floor-offline.md','docs/campaign-price-handoff.md']+re.findall(r'(?:docs|templates|research)/[^\s`<>]+?\.md',body)
        for name in names:
            if name=='docs/shop-offline-acceptance-v14-r1.md':continue
            path=(self.root/name).resolve()
            if path.is_relative_to(self.root) and path.is_file() and path not in paths:paths.append(path)
        if role.endswith('-live-ops'):
            contract=self.root/('docs/'+role+'-window-contract.json')
            if not contract.is_file():raise ValidationError('Station-specific live window contract missing')
            paths.append(contract)
        return paths

    def task(self,p,role):
        task='Offline queued task. No tools, shell, filesystem, browser, apps, business access or delegation. All references below are preloaded. Never change settings or perform publication, enrollment, spend, inventory or coupon writes. This review is synthetic_offline and release_status=not_authorized. Ordinary completion never creates a price acceptance.\n'+p['task']
        if p['kind']=='offline_review':
            task+='\nReturn plain JSON with job_id='+p['job_id']+', role='+role+', scope=synthetic_offline, release_status=not_authorized, conclusion and missing_fields. Preserve missing information; this is planning only.'
        for path in self.references(role)[2:]:task+='\nPRELOADED REFERENCE '+str(path.relative_to(self.root))+'\n'+path.read_text(encoding='utf-8-sig')
        return task

    def servers(self):
        import subprocess
        result=subprocess.run([self.entry.find_codex(),'mcp','list','--json'],capture_output=True,text=True,encoding='utf8',timeout=30,check=True)
        names=[r['name'] for r in self.intake.parse_bytes(result.stdout.encode('utf8'))]
        self.entry.mcp_disable_options(names);return names

    def preflight(self,p,role,task):
        for dep in p['requires_price']:
            parent=self.queue.get(dep);ok,why=self.dependency_check(parent['payload'],parent['evidence'],p)
            if not ok:raise ValidationError(why)
        if p['kind']=='price_review':
            caller=Path(p['price_input']).resolve(strict=True)
            if sha(caller)!=p['price_input_sha256']:raise ValidationError('Caller bytes differ from submitted frozen hash')
            pre=self.intake.prepare(role,task,input_file=caller,evaluation_time=p['evaluation_time'])
            if not pre['preflight'] or any(row['field_gate']!='pass' for row in pre['preflight']):raise ValidationError('Price preflight failed; no model called')
        else:self.intake.prepare(role,task)

    def ordinary(self,p,role,task,attempt,servers):
        run=attempt/'normal-run';run.mkdir()
        refs=self.references(role);prompt=self.entry.build_prompt(role,task,refs[0].read_text(encoding='utf-8-sig'),refs[1].read_text(encoding='utf-8-sig'),[])
        if len(prompt.encode('utf8'))>self.intake.LIMIT:raise ValidationError('Preloaded prompt exceeds bounded intake size')
        (run/'task.txt').write_text(task,encoding='utf8',newline='\n');(run/'prompt.txt').write_text(prompt,encoding='utf8',newline='\n')
        cmd=[self.entry.find_codex(),'-a','never','exec','--ephemeral','-C',str(self.root),'-s','read-only','--color','never','--json','-o',str(run/'answer.md')]+self.entry.model_options('gpt-6.1-sol','xhigh')+self.entry.mcp_disable_options(servers)+['-c','web_search="disabled"']
        for feature in ['shell_tool','unified_exec','apps','browser_use','browser_use_external','computer_use','multi_agent','remote_plugin','hooks']:cmd+=['--disable',feature]
        cmd+=['-'];dump(run/'command.json',cmd)
        with (run/'events.jsonl').open('x',encoding='utf8') as so,(run/'stderr.log').open('x',encoding='utf8') as se:
            proc=self.guard.run_process_tree(cmd,input=prompt,text=True,encoding='utf8',errors='replace',stdout=so,stderr=se,cwd=self.root,env=os.environ.copy(),timeout=1200)
        result={'role':role,'job_id':p['job_id'],'cli_exit_code':proc.returncode,'status':'completed' if proc.returncode==0 else 'failed','files_sha256':{n:sha(run/n) for n in ['task.txt','prompt.txt','answer.md','command.json','events.jsonl','stderr.log'] if (run/n).is_file()}}
        dump(run/'result.json',result);return run

    def validate_completion(self,attempt):
        frozen=read(attempt/'invocation.json');p=frozen['payload'];role=frozen['role']
        job=self.queue.get(p['job_id'])
        if digest(job['payload'])!=frozen['payload_sha256'] or job['attempt_id']!=frozen['attempt_id']:raise ValidationError('Completion detached from immutable queue attempt')
        for path,expected in frozen['source_sha256'].items():
            if sha(path)!=expected:raise ValidationError('Role/Skill/host source changed')
        marker=read(attempt/'completion.json');run=Path(marker['run_dir']).resolve(strict=True)
        expected_parent=attempt if p['kind']=='offline_review' else self.root/'work/codex-runs'
        if not run.is_relative_to(expected_parent.resolve()):raise ValidationError('Unexpected actual run directory')
        if sha(run/'result.json')!=marker['result_sha256']:raise ValidationError('Actual result changed')
        r=read(run/'result.json');a=runtime(run)
        if r.get('status')!='completed' or r.get('cli_exit_code')!=0 or r.get('role')!=role or not a['passed']:raise ValidationError('Incomplete actual role execution')
        for name,expected in r.get('files_sha256',{}).items():
            if not (run/name).resolve().is_relative_to(run) or sha(run/name)!=expected:raise ValidationError('Raw run byte binding changed')
        evidence={'scope':'synthetic_offline','release_status':'not_authorized','attempt_id':frozen['attempt_id'],'run_dir':str(run),'result_sha256':sha(run/'result.json'),'answer_sha256':sha(run/'answer.md'),'runtime':a,'model_called':True,'kind':p['kind']}
        if p['kind']=='price_review':
            if sha(p['price_input'])!=p['price_input_sha256']:raise ValidationError('Price source changed')
            result=self.intake.revalidate_run(p['price_input'],role,p['evaluation_time'],run)
            evidence.update(price_records=result['records'],price_accepted=bool(result['records']) and all(row['system_receipt']=='accepted' for row in result['records']),price_receipt_sha256=sha(run/'price-intake-receipt.json'))
        else:
            answer=read(run/'answer.md')
            if any(answer.get(k)!=v for k,v in {'job_id':p['job_id'],'role':role,'scope':'synthetic_offline','release_status':'not_authorized'}.items()) or not answer.get('conclusion') or not isinstance(answer.get('missing_fields'),list):raise ValidationError('Ordinary model response lacks the queue output contract')
            if (run/'task.txt').read_text(encoding='utf8')!=frozen['task'] or sha(run/'prompt.txt')!=frozen['prompt_sha256']:raise ValidationError('Ordinary task/prompt detached from queue invocation')
            evidence['price_accepted']=False
        for dep in p['requires_price']:
            parent=self.queue.get(dep);ok,why=self.dependency_check(parent['payload'],parent['evidence'],p)
            if not ok:raise ValidationError(why)
        return evidence

    def run_once(self,worker='offline-worker',simulate_lost_settlement=False):
        claim=self.queue.claim(worker,now(),1800,dependency_check=self.dependency_check)
        if claim is None:return None
        p,role=claim['payload'],claim['role']
        try:
            task=self.task(p,role);self.preflight(p,role,task)
        except (ValueError,OSError) as exc:
            self.queue.block_leased(p['job_id'],claim['token'],str(exc),now())
            return self.queue.get(p['job_id'])
        attempt_id=self.queue.start(p['job_id'],claim['token'],now())
        attempt=self.artifacts/(p['job_id']+'-'+attempt_id);attempt.mkdir(exist_ok=False)
        paths=self.references(role)+[self.root/'scripts'/name for name in ['offline_role_queue.py','live_window_queue.py','campaign_price_handoff.py','campaign_price_intake.py','run_codex_role.py','shop_process_guard.py']]
        refs=self.references(role);prompt=self.entry.build_prompt(role,task,refs[0].read_text(encoding='utf-8-sig'),refs[1].read_text(encoding='utf-8-sig'),[])
        frozen={'job_id':p['job_id'],'attempt_id':attempt_id,'payload':p,'payload_sha256':digest(p),'role':role,'task':task,'prompt_sha256':hashlib.sha256(prompt.encode('utf8')).hexdigest(),'source_sha256':{str(path):sha(path) for path in paths},'created_at':now(),'scope':'synthetic_offline','release_status':'not_authorized'}
        dump(attempt/'invocation.json',frozen)
        try:
            servers=self.servers()
            if p['kind']=='price_review':
                with (attempt/'stdout.txt').open('x',encoding='utf8') as so,(attempt/'wrapper-stderr.txt').open('x',encoding='utf8') as se,contextlib.redirect_stdout(so),contextlib.redirect_stderr(se):
                    rc=self.entry.execute(role,task,sandbox='read-only',root=self.root,model='gpt-6.1-sol',reasoning_effort='xhigh',price_handoff_file=p['price_input'],price_evaluation_time=p['evaluation_time'],disabled_mcp_servers=servers)
                paths=[line[len('Evidence: '):] for line in (attempt/'stdout.txt').read_text(encoding='utf8').splitlines() if line.startswith('Evidence: ')]
                if len(paths)!=1 or rc!=0:raise ValidationError('Dedicated host did not complete normally')
                run=Path(paths[0])
            else:run=self.ordinary(p,role,task,attempt,servers)
            dump(attempt/'completion.json',{'run_dir':str(run),'result_sha256':sha(run/'result.json'),'completed_at':now()})
            evidence=self.validate_completion(attempt)
            if simulate_lost_settlement is True or simulate_lost_settlement==p['job_id']:
                self.queue.abandon(p['job_id'],claim['token'],'controlled loss of queue settlement after actual CLI completion',now())
                return {'job_id':p['job_id'],'state':'uncertain','attempt_dir':str(attempt),'model_called':True,'reconcile_required':True}
            self.queue.settle(p['job_id'],claim['token'],'completed',evidence,now())
        except Exception as exc:
            dump(attempt/'uncertain-diagnostic.json',{'error':type(exc).__name__+': '+str(exc),'automatic_retry':False,'created_at':now()})
            self.queue.abandon(p['job_id'],claim['token'],'Execution result uncertain: '+type(exc).__name__,now())
        return self.queue.get(p['job_id'])

    def reconcile(self,attempt_dir):
        attempt=Path(attempt_dir).resolve(strict=True)
        if not attempt.is_relative_to(self.artifacts):raise ValidationError('Attempt outside this worker artifact root')
        frozen=read(attempt/'invocation.json');evidence=self.validate_completion(attempt)
        self.queue.resolve(frozen['job_id'],frozen['attempt_id'],'completed',evidence)
        return self.queue.get(frozen['job_id'])


def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=('submit','list','run-one','reconcile'));ap.add_argument('--project',required=True);ap.add_argument('--db',required=True);ap.add_argument('--artifacts',required=True);ap.add_argument('--input');ap.add_argument('--attempt');a=ap.parse_args()
    worker=Worker(a.project,a.db,a.artifacts)
    if a.action=='submit':result=worker.queue.submit(read(a.input))
    elif a.action=='list':result=worker.queue.list_jobs()
    elif a.action=='reconcile':result=worker.reconcile(a.attempt)
    else:result=worker.run_once()
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
