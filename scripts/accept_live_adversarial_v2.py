"""Same-input safety mutations, explicitly not independent new blind cases."""
import argparse
from copy import deepcopy
import importlib
from pathlib import Path
from live_window_queue import read,dump,sha
from live_price_evidence_v2 import PriceVerifier
from live_window_host import receipt
from accept_live_windows_v2 import envelope,old_preservation

def run(root,first,module,output):
    plan=read(first/'plan.json');rows=envelope(plan['public_input'],plan['platform'])['cases'];catalog=read(plan['catalog']);v=PriceVerifier(catalog)
    review=getattr(importlib.import_module(module),'review_cn' if plan['platform']=='douyin' else 'review_ph');checks=[]
    for row in rows:
        baseline=review(row,v)
        variants={}
        c=deepcopy(row);c['evaluation_time']=catalog[c['price_ref']]['valid_until'];variants['exact_price_expiry']=c
        c=deepcopy(row);c['target_sku']='UNBOUND-SYNTHETIC-TARGET';variants['target_changed']=c
        c=deepcopy(row);c['price_revision']+=1;variants['revision_changed']=c
        c=deepcopy(row);c['quantity']=True;variants['boolean_quantity']=c
        c=deepcopy(row);c['inventory']['on_hand']=None;variants['unknown_inventory']=c
        c=deepcopy(row);c['budget']['committed_minor']=None;variants['unknown_commitment']=c
        c=deepcopy(row)
        for r in c['records']:r['metrics']['fees_minor']=None
        variants['unknown_fees']=c
        if plan['platform']=='tiktok':
            c=deepcopy(row);c['ph_controls']={'site':'PH','cashflow_state':'unknown','fulfillment_state':'safe'};variants['ph_cashflow_unknown']=c
            c=deepcopy(row);c['ph_controls']={'site':'PH','cashflow_state':'safe','fulfillment_state':'unknown'};variants['ph_fulfillment_unknown']=c
            c=deepcopy(row);c['ph_controls']={'site':'PH','cashflow_state':'unsafe','fulfillment_state':'safe'};variants['ph_cashflow_unsafe']=c
            c=deepcopy(row)
            for r in c['records']:
                if r.get('maturity')=='mature':r['settlement_basis']='pending_order_value'
            variants['ph_pending_never_mature']=c
            c=deepcopy(row);c['records'][0]['currency']='CNY';variants['ph_wrong_record_currency']=c
            c=deepcopy(row);sibling=deepcopy(c['records'][0]);sibling['state']='partial' if sibling.get('state')=='complete' else 'complete';c['records'].append(sibling);variants['ph_original_event_conflict']=c
        for name,c in variants.items():
            actual=review(c,v);ok=actual['action'] in ('hold','pause') and actual['proposed_increment_minor']==0 and not actual['execution_authorized'];checks.append({'case_id':row['case_id'],'mutation':name,'baseline_action':baseline['action'],'action':actual['action'],'passed':ok})
        tampered=deepcopy(baseline);tampered['action']='propose_increase';tampered['proposed_increment_minor']=999999999
        result=receipt(row,tampered,v,review);checks.append({'case_id':row['case_id'],'mutation':'forged_model_scale','receipt':result['system_receipt'],'passed':result['system_receipt']=='rejected_model_mismatch' and result['proposed_increment_minor']==0})
        altered=deepcopy(catalog);altered[row['price_ref']]['sku']='CATALOG-RELABEL';c=deepcopy(row);c['target_sku']='CATALOG-RELABEL'
        result=PriceVerifier(altered)(c);checks.append({'case_id':row['case_id'],'mutation':'catalog_relabel','reason':result['reason'],'passed':not result['proven']})
    old_preservation(root)
    dump(output,{'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),'same_question_adversarial_regression':True,'independent_new_blind_cases':False,'new_model_calls':0,'first_raw_sha256':sha(first/'frozen-raw-first.json'),'scope':'synthetic_offline','release_status':'not_authorized'})
    if not all(c['passed'] for c in checks):raise ValueError('Adversarial safety regression failed')
    print({'passed':len(checks),'new_model_calls':0})

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--project',required=True,type=Path);ap.add_argument('--first',required=True,type=Path);ap.add_argument('--module',required=True);ap.add_argument('--output',required=True,type=Path);a=ap.parse_args();run(a.project.resolve(),a.first.resolve(),a.module,a.output.resolve())
