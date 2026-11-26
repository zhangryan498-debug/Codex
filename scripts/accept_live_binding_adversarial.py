import argparse
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from live_window_queue import read,dump
from accept_live_host_v2 import extract_actual

def run(first,output):
    raw=read(first/'frozen-raw-first.json');plan=read(first/'plan.json');checks=[]
    actual=extract_actual(first,raw,plan);checks.append({'mutation':'unchanged_actual_answers','passed':actual==raw['cases']})
    for name in ('changed_action','duplicate_case','raw_file_hash'):
        altered=deepcopy(raw)
        if name=='changed_action':altered['cases'][0]['action']='FORGED'
        elif name=='duplicate_case':altered['cases'].append(deepcopy(altered['cases'][0]))
        else:altered['raw_files_sha256'][next(iter(altered['raw_files_sha256']))]='0'*64
        try:extract_actual(first,altered,plan);passed=False
        except ValueError:passed=True
        checks.append({'mutation':name,'passed':passed})
    for name in ('invocation_payload','task_prompt_hash','completion_result'):
        def changed(path):
            value=read(path)
            if Path(path).name=='invocation.json':
                if name=='invocation_payload':value['payload']['task']+=' changed'
                if name=='task_prompt_hash':value['prompt_sha256']='0'*64
            if Path(path).name=='completion.json' and name=='completion_result':value['result_sha256']='0'*64
            return value
        with patch('accept_live_host_v2.read',side_effect=changed):
            try:extract_actual(first,raw,plan);passed=False
            except ValueError:passed=True
        checks.append({'mutation':name,'passed':passed})
    dump(output,{'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),'new_model_calls':0,'same_question_infrastructure_verification':True,'scope':'synthetic_offline','release_status':'not_authorized'})
    assert all(c['passed'] for c in checks);print({'passed':len(checks)})

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--first',required=True,type=Path);ap.add_argument('--output',required=True,type=Path);a=ap.parse_args();run(a.first.resolve(),a.output.resolve())
