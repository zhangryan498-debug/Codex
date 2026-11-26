"""Copy immutable independent reports and render acceptance from JSON evidence."""
import argparse
from pathlib import Path
from datetime import datetime,timezone
import json
import re
from live_window_queue import read,dump,sha
from accept_live_windows_v2 import old_preservation

def coverage(receipts):
    cases=[r['system_result'] for r in receipts['cases']]
    return {'case_count':len(cases),'complete_mature_totals':sum(c['cumulative']['mature'] is not None for c in cases),'unproven_mature_totals':sum(c['cumulative']['mature'] is None for c in cases),'complete_estimated_totals':sum(c['cumulative']['estimated'] is not None for c in cases),'mature_windows_exact':sum(w['mature'] is not None for c in cases for w in c['windows']),'estimated_windows_exact':sum(w['estimated'] is not None for c in cases for w in c['windows']),'expected_windows':sum(len(c['windows']) for c in cases),'current_windows_complete':sum(c['current_windows_complete'] for c in cases),'decisions':{action:sum(c['action']==action for c in cases) for action in ('propose_continue','propose_switch','propose_increase','hold','pause')},'cases':[{'case_id':c['case_id'],'action':c['action'],'mature_total_exact':c['cumulative']['mature'] is not None,'estimated_total_exact':c['cumulative']['estimated'] is not None,'current_windows_complete':c['current_windows_complete'],'unproven_reasons':c['blocking_reasons']} for c in cases]}

def finalize(root,ph_summary):
    cn=root/'work/live-window-cn-20261111';ph=root/'work/live-window-ph-20261111'
    pairs={'CN':(cn,cn/'host-v6-bound'),'PH':(ph,ph/'host-v3-closed-bound')};platforms={}
    for key,(base,host) in pairs.items():
        result=read(host/'summary.json');raw=read(host/'receipts.json');cov=coverage(raw)
        if result['cases']!=12 or result['agreement']!=12 or result['mismatch']!=0:raise ValueError('Final actual host not fully closed')
        platforms[key]={'host':result,'coverage':cov,'host_receipts_sha256':sha(host/'receipts.json')}
    cn_review=read(cn/'independent-reports/closure-cn-bound/closure.json');ph_review=read(ph_summary)
    if ph_review['host_final']['receipt_sha256']!=sha(ph/'host-v3-closed-bound/receipts.json') or ph_review['host_final']['passed']!=12 or ph_review.get('remaining_blockers'):raise ValueError('Final independent PH review not bound or not closed')
    log=ph/'engineering-final-closed.log';content=log.read_text(encoding='utf-8-sig');tests=int(re.search(r'Ran (\d+) tests',content).group(1))
    if not content.rstrip().endswith('OK'):raise ValueError('Engineering suite not successful')
    summary={'generated_at':datetime.now(timezone.utc).isoformat(),'platforms':platforms,'independent_cn':cn_review,'independent_ph':ph_review,'independent_ph_summary_sha256':sha(ph_summary),'engineering_tests':tests,'engineering_log':str(log),'engineering_log_sha256':sha(log),'old_preserved_file_hashes':len(old_preservation(root)),'scope':'synthetic_offline','release_status':'not_authorized','real_business_actions':0,'client_settings_operated':False,'client_actual_fast_mode_independently_verified':False}
    dump(ph/'final-acceptance-summary.json',summary)
    return summary

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--project',type=Path,required=True);ap.add_argument('--ph-summary',type=Path,required=True);a=ap.parse_args();finalize(a.project.resolve(),a.ph_summary.resolve())
