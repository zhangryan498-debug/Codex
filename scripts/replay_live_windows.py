"""Explicit same-input system replay; never invokes a model or overwrites first."""
import argparse
import importlib
from pathlib import Path
from live_window_queue import read,dump,sha
from live_price_evidence_v2 import PriceVerifier
from accept_live_windows_v2 import envelope,old_preservation

def replay(root,base,module,output):
    raw=read(base/'frozen-raw-first.json');assert all(sha(p)==v for p,v in raw['raw_files_sha256'].items())
    plan=read(base/'plan.json');v=PriceVerifier(read(plan['catalog']))
    platform=plan['platform'];review=getattr(importlib.import_module(module),'review_cn' if platform=='douyin' else 'review_ph')
    rows=[review(c,v) for c in envelope(plan['public_input'],platform)['cases']]
    dump(output,{'cases':rows,'same_question_regression':True,'new_model_calls':0,'raw_first_sha256':sha(base/'frozen-raw-first.json'),'source_sha256':{n:sha(root/'scripts'/n) for n in (module+'.py','live_price_evidence_v2.py','replay_live_windows.py')},'scope':'synthetic_offline','release_status':'not_authorized'})
    old_preservation(root);print('system replay:',len(rows))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--project',required=True,type=Path);ap.add_argument('--first',required=True,type=Path);ap.add_argument('--module',required=True);ap.add_argument('--output',required=True,type=Path);a=ap.parse_args();replay(a.project.resolve(),a.first.resolve(),a.module,a.output.resolve())
