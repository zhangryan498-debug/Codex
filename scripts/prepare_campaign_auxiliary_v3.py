"""Prepare explicitly labelled same-question supplemental coverage; no scoring key."""
import argparse
import json
from datetime import datetime,timezone
from pathlib import Path
from run_compound_heldout_v3 import context
from replay_campaign_ledger_v3 import dump,read,sha

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--project',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    h,_=context(a.project);out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=False)
    h.verify_originals()
    implementation={str(p.resolve()):sha(p) for p in [Path(__file__),h.ROOT/'scripts/run_compound_heldout_v3.py',h.ROOT/'scripts/campaign_ledger_semantics_v3.py']}
    plans=[]
    for file,platform,caseid in [('CN_raw_cases.json','douyin','CN-7B905E'),('PH_raw_cases.json','tiktok','PH-830EA3')]:
        raw=read(h.PUBLIC/file);case=next(c for c in raw['cases'] if c['case_id']==caseid)
        batch={**raw,'cases':[case]};role=platform+'-campaign-ops';attempt=out/(platform+'-auxiliary-same-question');attempt.mkdir()
        task='This is an explicitly labelled same-question supplemental offline coverage run, NOT a new blind test and NOT a replacement of the first answer. Review only the attached original synthetic facts. No tools, shell, apps, browser, filesystem, external sources or delegation. No expected answer supplied. '+h.SCHEMA+'\n'+h.STATE_SCHEMA+'\nAdditional structured coverage: report numbers.fallback_no_activity_net only when its own independent price, eligibility and costs are established; otherwise null and precise missing_fields. Do not assume an activity registration price remains a valid price after the activity expires. For voucher candidates report voucher_usage_projection keyed by tool_id: eligibility, before_remaining_uses, conditional_after_one_valid_redemption_remaining_uses, conditional_new_usage, actual_remaining_uses, actual_commit_count. This is an offline projection: no commit occurs unless original facts already prove one. Claimed inventory is distinct from redemption uses; do not deduct usage from an exhausted, ineligible, non-winning or unqualified candidate. Preserve every current refusal and known calculable ledger.\n'
        for p in h.refs(role)[2:]:task+='\nPRELOADED REFERENCE '+str(p.relative_to(h.ROOT))+'\n'+p.read_text(encoding='utf-8-sig')
        task+='\nPUBLIC RULES\n'+(h.PUBLIC/'public_rules.json').read_text(encoding='utf8')+'\nPUBLIC CONTRACT\n'+(h.PUBLIC/'execution_contract.json').read_text(encoding='utf8')+'\nPUBLIC ORIGINAL SUPPLEMENTAL BATCH\n'+json.dumps(batch,ensure_ascii=False)
        refs=h.refs(role);prompt=h.entry.build_prompt(role,task,refs[0].read_text(encoding='utf-8-sig'),refs[1].read_text(encoding='utf-8-sig'),[])
        h.textfile(attempt/'task.txt',task);h.textfile(attempt/'prompt.txt',prompt);dump(attempt/'input.json',batch)
        plans.append({'name':attempt.name,'role':role,'suite_id':batch['suite_id'],'market':batch['market'],'case_ids':[caseid],'public_sha256':{str((h.PUBLIC/n).resolve()):sha(h.PUBLIC/n) for n in [file,'public_rules.json','execution_contract.json']},'task_sha256':sha(attempt/'task.txt'),'prompt_sha256':sha(attempt/'prompt.txt'),'batch_sha256':sha(attempt/'input.json'),'model':'gpt-6.1-sol','reasoning_effort':'xhigh','new_independent_cases':False,'first_score_replaced':False})
    dump(out/'plan.json',{'implementation':implementation,'attempts':plans,'created_at':datetime.now(timezone.utc).isoformat(),'scope':'synthetic_offline','release_status':'not_authorized','kind':'same-question auxiliary coverage'})
    print(json.dumps({'prepared':[p['name'] for p in plans],'model_calls':0}))
