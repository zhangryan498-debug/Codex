"""Fresh independent cases: freeze public inputs and implementation before inference.

This runner reads only the explicitly named public files. No gold/author oracle is
loaded. Already completed attempts cannot be run twice with the same name.
"""
import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from replay_campaign_ledger_v3 import dump, read, sha


def context(project):
    project=Path(project).resolve(strict=True)
    sys.path.insert(0,str(project/'work/fresh-composite-blind-20261111'))
    sys.path.insert(0,str(project/'scripts'))
    import fresh_harness as h
    p=project/'work/fresh-composite-receiver-retest-20261111/v2/run_receiver_retest_v2.py'
    spec=importlib.util.spec_from_file_location('prior_runtime_audit',p)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return h,module.audit_runtime


def prepare(h,out,author_root):
    h.verify_originals()
    out.mkdir(parents=True,exist_ok=False)
    implementation={str(p.resolve()):sha(p) for p in [Path(__file__),*(h.ROOT/'scripts'/n for n in ('campaign_ledger_semantics_v3.py','replay_campaign_ledger_v3.py','campaign_workflow_state.py','replay_campaign_state_repair_v2.py'))]}
    # Must exist on disk before the new numerical case files are opened.
    dump(out/'implementation-before-new-inputs.json',{'frozen_at':datetime.now(timezone.utc).isoformat(),'sha256':implementation,'public_case_values_previously_read_by_root':False,'private_key_read':False})
    plans=[]
    for platform,folder,case_name,rules_name,protocol_name in [
        ('douyin','heldout-cn-author','CN_new_cases.json','frozen_public_rules.json','CN_output_protocol.json'),
        ('tiktok','heldout-ph-author','PH_new_cases.json','PH_public_rules.json','Public_output_protocol.json')]:
        public=Path(author_root)/folder/'public'
        named=[public/case_name,public/rules_name,public/protocol_name]
        if platform=='douyin':named.append(public/'frozen_execution_contract.json')
        else:named.append(h.PUBLIC/'execution_contract.json')
        hashes={str(p.resolve()):sha(p) for p in named}
        batch=read(public/case_name)
        role=platform+'-campaign-ops'
        task='Analyze ONLY the independent NEW synthetic raw proposals below. No tools, filesystem, shell, apps, browser, external sources or delegation. All references are preloaded. Do not read other work directories, old answers, scores, implementation or private keys. This is compatibility CLI single-role inference. Current request decisions, arithmetic, unknowns, counterfactuals and exact money fields follow the attached public protocol. No expected values or calculator result is supplied. No live execution is authorized.\n'
        for p in h.refs(role)[2:]:
            task+='\nPRELOADED REFERENCE '+str(p.relative_to(h.ROOT))+'\n'+p.read_text(encoding='utf-8-sig')
        task+='\nPUBLIC OUTPUT PROTOCOL\n'+(public/protocol_name).read_text(encoding='utf-8-sig')
        task+='\nPUBLIC RULE SNAPSHOT\n'+(public/rules_name).read_text(encoding='utf-8-sig')
        task+='\nPUBLIC BASE CONTRACT\n'+named[-1].read_text(encoding='utf-8-sig')
        task+='\nPUBLIC NEW ORIGINAL BATCH\n'+json.dumps(batch,ensure_ascii=False)
        attempt=out/(platform+'-activity-first');attempt.mkdir(exist_ok=False)
        refs=h.refs(role)
        prompt=h.entry.build_prompt(role,task,refs[0].read_text(encoding='utf-8-sig'),refs[1].read_text(encoding='utf-8-sig'),[])
        h.textfile(attempt/'task.txt',task);h.textfile(attempt/'prompt.txt',prompt);dump(attempt/'input.json',batch)
        plans.append({'name':attempt.name,'role':role,'suite_id':batch['suite_id'],'market':batch['market'],'case_ids':[c['case_id'] for c in batch['cases']], 'public_sha256':hashes,'task_sha256':sha(attempt/'task.txt'),'prompt_sha256':sha(attempt/'prompt.txt'),'batch_sha256':sha(attempt/'input.json'),'model':'gpt-6.1-sol','reasoning_effort':'xhigh','service_tier':'inherited; actual runtime reported after completion','new_independent_cases':True,'private_key_read':False})
    dump(out/'plan.json',{'created_at':datetime.now(timezone.utc).isoformat(),'implementation':implementation,'attempts':plans,'window_settings_changed':False,'scope':'synthetic_offline','release_status':'not_authorized'})
    print(json.dumps({'prepared':[p['name'] for p in plans],'model_calls':0}))


def execute(h,audit,out,name):
    frozen=read(out/'plan.json')
    if any(sha(p)!=v for p,v in frozen['implementation'].items()):raise ValueError('Implementation changed after freeze')
    h.verify_originals()
    plan=next(p for p in frozen['attempts'] if p['name']==name)
    attempt=out/name
    if (attempt/'started.json').exists():raise ValueError('Attempt already started; do not duplicate')
    for path,digest in plan['public_sha256'].items():
        if sha(path)!=digest:raise ValueError('Public author input changed')
    for path,key in [('task.txt','task_sha256'),('prompt.txt','prompt_sha256'),('input.json','batch_sha256')]:
        if sha(attempt/path)!=plan[key]:raise ValueError('Prepared inference bytes changed')
    servers=h.disabled_servers()
    cmd=[h.entry.find_codex(),'-a','never','exec','--ephemeral','-C',str(h.ROOT),'-s','read-only','--color','never','--json','-o',str(attempt/'answer.md')]+h.entry.model_options('gpt-6.1-sol','xhigh')+h.entry.mcp_disable_options(servers)+['-c','web_search="disabled"']
    for feature in ['shell_tool','unified_exec','apps','browser_use','browser_use_external','computer_use','multi_agent','remote_plugin','hooks']:cmd+=['--disable',feature]
    cmd+=['-']
    dump(attempt/'command.json',cmd)
    dump(attempt/'started.json',dict(plan,started_at=datetime.now(timezone.utc).isoformat(),disabled_mcp_servers=servers))
    result={'name':name,'status':'failed','started_at':datetime.now(timezone.utc).isoformat(),'scope':'synthetic_offline','release_status':'not_authorized'}
    try:
        with (attempt/'events.jsonl').open('x',encoding='utf8') as so,(attempt/'stderr.log').open('x',encoding='utf8') as se:
            proc=h.run_process_tree(cmd,input=(attempt/'prompt.txt').read_text(encoding='utf8'),text=True,encoding='utf8',errors='replace',stdout=so,stderr=se,cwd=h.ROOT,env=os.environ.copy(),timeout=1200)
        result['cli_exit_code']=proc.returncode
        result['runtime']=audit(h,attempt)
        answer=h.read(attempt/'answer.md')
        ids=[r['case_id'] for r in answer['cases']]
        result['exact_case_coverage']=len(ids)==len(set(ids)) and set(ids)==set(plan['case_ids']) and answer['suite_id']==plan['suite_id'] and answer['market']==plan['market']
        if proc.returncode==0 and result['runtime']['passed'] and result['exact_case_coverage']:result['status']='completed'
    except Exception as exc:
        result['error']=type(exc).__name__+': '+str(exc)
    result['finished_at']=datetime.now(timezone.utc).isoformat()
    result['files_sha256']={p.name:sha(p) for p in attempt.iterdir() if p.is_file()}
    dump(attempt/'result.json',result)
    h.verify_originals()
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status']=='completed' else 2


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=('prepare','run'));ap.add_argument('--project',required=True);ap.add_argument('--output',required=True);ap.add_argument('--author-root');ap.add_argument('--name');a=ap.parse_args()
    h,audit=context(a.project);out=Path(a.output).resolve()
    if a.mode=='prepare':prepare(h,out,a.author_root)
    else:raise SystemExit(execute(h,audit,out,a.name))
