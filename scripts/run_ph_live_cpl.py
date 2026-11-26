"""Separately frozen new PH CPL suite through the original role/queue; finite."""
import argparse
from pathlib import Path
import os,sqlite3,sys
import run_live_compliance as base
from live_compliance_binding_v4 import extract_bound,BoundInfrastructure

DOCS=('docs/knowledge/platform-model.md','docs/knowledge/data-channel.md','templates/直播运营/直播排品表.md','templates/直播运营/直播脚本框架.md','templates/直播运营/直播复盘表.md','docs/tiktok-live-ops-window-contract.json','docs/live-compliance-contract-v1.md','docs/live-compliance-source-scope-note-v1.md','docs/live-compliance-rule-pack-v2.json','docs/ph-live-cpl-contract-v1.md','docs/ph-live-cpl-clarification-v1.md')

class CPLWorker(base.Worker):
    def references(self,role):
        if role!='tiktok-live-ops':raise ValueError('CPL follow-up supports PH role only')
        # Complete original role/Skill and relevant knowledge/templates. Archived
        # recruiting examples are traceability, not current policy instructions.
        return [self.root/f'runtime-agents/{role}/agent.md',self.root/f'.agents/skills/{role}-playbook/SKILL.md']+[self.root/p for p in DOCS]

    def ordinary(self,p,role,task,attempt,servers):
        run=attempt/'normal-run';run.mkdir();refs=self.references(role)
        prompt=self.entry.build_prompt(role,task,refs[0].read_text(encoding='utf-8-sig'),refs[1].read_text(encoding='utf-8-sig'),[])
        if len(prompt.encode('utf8'))>self.intake.LIMIT:raise ValueError('Bounded complete CPL prompt too long')
        (run/'task.txt').write_text(task,encoding='utf8',newline='\n');(run/'prompt.txt').write_text(prompt,encoding='utf8',newline='\n')
        cmd=[self.entry.find_codex(),'-a','never','exec','--ephemeral','-C',str(self.root),'-s','read-only','--color','never','--json','-o',str(run/'answer.md')]+self.entry.model_options('gpt-6.1-sol','xhigh')+self.entry.mcp_disable_options(servers)+['-c','web_search="disabled"','-c','notify=[]']
        for feature in ['shell_tool','unified_exec','apps','browser_use','browser_use_external','computer_use','multi_agent','remote_plugin','hooks']:cmd+=['--disable',feature]
        cmd+=['-'];base.dump(run/'command.json',cmd)
        with (run/'events.jsonl').open('x',encoding='utf8') as so,(run/'stderr.log').open('x',encoding='utf8') as se:
            proc=self.guard.run_process_tree(cmd,input=prompt,text=True,encoding='utf8',errors='replace',stdout=so,stderr=se,cwd=self.root,env=os.environ.copy(),timeout=1200)
        result={'role':role,'job_id':p['job_id'],'cli_exit_code':proc.returncode,'status':'completed' if proc.returncode==0 else 'failed','files_sha256':{n:base.sha(run/n) for n in ['task.txt','prompt.txt','answer.md','command.json','events.jsonl','stderr.log'] if (run/n).is_file()}}
        base.dump(run/'result.json',result);return run

def prepare(out,public,infra):
    out=Path(out);out.mkdir(exist_ok=False)
    references=CPLWorker(base.PROJECT,out/'queue.sqlite',out/'attempts').references('tiktok-live-ops')
    sources=references+[base.PROJECT/'scripts'/n for n in ('ph_live_cpl_v1.py','run_ph_live_cpl.py','live_compliance_binding_v4.py','live_compliance_review_v1.py','live_compliance_review_v2.py','run_live_compliance.py','live_window_queue.py','offline_role_queue.py','run_codex_role.py','shop_process_guard.py','live_price_evidence_v2.py')]
    base.dump(out/'implementation-before-inputs.json',{'files':{str(p):base.sha(p) for p in sources},'frozen_at':base.stamp(),'private_gold_read':False,'public_cases_opened_after_this_invocation_freeze':True,'stage':'cpl-new'})
    # Constructor above created an empty local DB; replace only this owned empty
    # database via SQLite backup, never the original seed or earlier queues.
    seed=base.PROJECT/'work/offline-role-queue-20261111/actual-integration/queue.sqlite'
    with sqlite3.connect(seed.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(out/'queue.sqlite') as dst:src.backup(dst)
    public=Path(public).resolve();data=base.read(public);cases=data['cases'];infrastructure=BoundInfrastructure(infra)
    if len(cases)!=len({c['case_id'] for c in cases}) or any((c['platform'],c['country'],c['currency'])!=('tiktok','PH','PHP') for c in cases):raise ValueError('New suite exact PH scope invalid')
    worker=CPLWorker(base.PROJECT,out/'queue.sqlite',out/'attempts');batches=[];current=[]
    def task(group):
        return 'NEW INDEPENDENT POST-FIRST-RUN PH CPL SCENARIOS. Apply the complete original role/Skill with the preloaded official rule-pack-v2 and ph-live-cpl-contract-v1 plus clarification. Do not read any previous answer, implementation or private key. Return the ordinary queue outer envelope; conclusion={suite_id,cases:[...]} using full base compliance fields PLUS publishing_limit_review. The new CPL source is capability policy, never a substantive prohibition by itself. Exact video quota is not a LIVE shutdown; unknown relevant rights/price do not permit a proposal. All evidence is synthetic. No tools or operations.\nPUBLIC INDEPENDENT INPUT\n'+base.json.dumps({'suite_id':data['suite_id'],'cases':group},ensure_ascii=False,separators=(',',':'))
    for case in cases:
        if current and (len(current)>=3 or len(task(current+[case]).encode('utf8'))>11500):batches.append(current);current=[]
        if len(task([case]).encode('utf8'))>11500:raise ValueError('One new CPL case too large')
        current.append(case)
    if current:batches.append(current)
    jobs=[];evaluation=infrastructure.catalogs['tiktok']['PH_ACCEPTED']['evaluation_time']
    for i,group in enumerate(batches,1):jobs.append({'job_id':'PH-LIVE-CPL-NEW-'+str(i),'idempotency_key':data['suite_id']+':cpl-new:'+str(i),'platform':'tiktok','country':'PH','role_suffix':'live-ops','kind':'offline_review','task':task(group),'priority':50,'requires':['PH-PRICE'],'requires_price':['PH-PRICE'],'evaluation_time':evaluation})
    worker.queue.submit(jobs);base.dump(out/'jobs.json',jobs)
    base.dump(out/'plan.json',{'suite_id':data['suite_id'],'platform':'tiktok','role':'tiktok-live-ops','public_input':str(public),'public_sha256':base.sha(public),'infrastructure':str(Path(infra).resolve()),'infrastructure_sha256':base.sha(infra),'jobs':[j['job_id'] for j in jobs],'case_ids':[c['case_id'] for c in cases],'stage':'cpl-new','independent_new_blind_test':True,'earlier_first_runs_replaced':False,'scope':'synthetic_offline','release_status':'not_authorized'})
    print(base.json.dumps({'new_cases':len(cases),'batches':len(jobs),'model_calls':0}),flush=True)

def run(out):
    out=Path(out);plan=base.read(out/'plan.json');frozen=base.read(out/'implementation-before-inputs.json')
    if any(base.sha(p)!=h for p,h in frozen['files'].items()):raise ValueError('Pre-inference frozen source changed')
    if base.sha(plan['public_input'])!=plan['public_sha256'] or base.sha(plan['infrastructure'])!=plan['infrastructure_sha256']:raise ValueError('Frozen public/infra changed')
    if (out/'frozen-raw.json').exists():raise ValueError('Never repeat completed original new batches')
    w=CPLWorker(base.PROJECT,out/'queue.sqlite',out/'attempts');results=[]
    for jid in plan['jobs']:
        old=w.queue.get(jid)
        if old['state']=='completed':r=old
        elif old['state']=='pending':r=w.run_once('ph-cpl-independent-new')
        else:raise ValueError('Not safe to retry '+jid+':'+old['state'])
        if r is None or r['job_id']!=jid or r['state']!='completed':
            base.dump(out/('incomplete-'+base.datetime.now(base.timezone.utc).strftime('%H%M%S')+'.json'),r)
            raise ValueError('New actual run incomplete/uncertain; retain and inspect')
        actual_log=(Path(r['evidence']['run_dir'])/'stderr.log').read_text(encoding='utf8',errors='replace')
        if 'hook_name=legacy_notify' in actual_log:
            base.dump(out/('unexpected-notification-hook-'+jid+'.json'),{'job_id':jid,'run_dir':r['evidence']['run_dir'],'notify_override_requested':True,'additional_batches_dispatched':False,'global_configuration_modified':False})
            raise ValueError('Unexpected legacy notification hook despite one-off override; stop before another batch')
        results.append({'job_id':jid,'state':r['state'],'evidence':r['evidence']});print(base.json.dumps({'completed_job':jid,'run_dir':r['evidence']['run_dir']}),flush=True)
    base.dump(out/'run-results.json',results);models=[];files={}
    for row in results:
        directory=Path(row['evidence']['run_dir']);answer=base.read(directory/'answer.md')
        if answer['conclusion']['suite_id']!=plan['suite_id']:raise ValueError('New raw suite mismatch')
        models.extend(answer['conclusion']['cases']);files.update({str(p):base.sha(p) for p in directory.iterdir() if p.is_file()})
    if len(models)!=len(plan['case_ids']) or {m['case_id'] for m in models}!=set(plan['case_ids']):raise ValueError('New raw exact case coverage invalid')
    base.dump(out/'frozen-raw.json',{'suite_id':plan['suite_id'],'cases':models,'raw_files_sha256':files,'frozen_at':base.stamp(),'stage':plan['stage'],'actual_model_calls':len(results),'first_score_preserved':True})
    print(base.json.dumps({'new_original_cases_frozen':len(models),'actual_role_cli_invocations':len(results),'semantic_grade':'not asserted'}),flush=True)

def host(first,out,module='ph_live_cpl_v1'):
    first,out=Path(first),Path(out);plan=base.read(first/'plan.json');public,actual,runs,proofs=extract_bound(first,CPLWorker)
    if base.sha(plan['infrastructure'])!=plan['infrastructure_sha256']:raise ValueError('Frozen infrastructure bytes changed after preparation')
    infra=BoundInfrastructure(plan['infrastructure']);pack=base.read(base.PROJECT/'docs/live-compliance-rule-pack-v2.json')
    import importlib
    mod=importlib.import_module(module);by={m['case_id']:m for m in actual};results=[mod.review(c,by[c['case_id']],pack,infra.price,infra.financial) for c in public]
    out.mkdir(exist_ok=False)
    base.dump(out/'receipts.json',{'suite_id':plan['suite_id'],'cases':results,'actual_runs':runs,'original_prompt_input_binding':proofs,'input_exactly_bound_to_actual_model':True,'raw_sha256':base.sha(first/'frozen-raw.json'),'public_sha256':base.sha(plan['public_input']),'infrastructure_sha256':base.sha(plan['infrastructure']),'rule_pack_sha256':base.sha(base.PROJECT/'docs/live-compliance-rule-pack-v2.json'),'module':module,'module_sha256':base.sha(Path(mod.__file__)),'receiver_version':'cpl-new-binding-v4','receiver_sha256':base.sha(Path(__file__)),'scope':'synthetic_offline','release_status':'not_authorized','new_model_calls_for_host':0})
    summary={'new_cases':len(results),'offline_proposals':sum(r['proposal_eligible'] for r in results),'blocked':sum(not r['proposal_eligible'] for r in results),'model_control_agreement':sum(r['model_control_agreement'] for r in results),'model_CPL_agreement':sum(r.get('model_publishing_limit_agreement',False) for r in results),'actual_role_cli_invocations':len(runs),'new_model_calls_for_host':0,'real_business_actions':0}
    base.dump(out/'summary.json',summary);print(base.json.dumps(summary),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare','run','host'));p.add_argument('--output',required=True);p.add_argument('--public');p.add_argument('--infrastructure');p.add_argument('--first');p.add_argument('--module',default='ph_live_cpl_v1');a=p.parse_args()
    if a.mode=='prepare':prepare(a.output,a.public,a.infrastructure)
    elif a.mode=='run':run(a.output)
    else:host(a.first,a.output,a.module)
