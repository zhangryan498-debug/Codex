"""PH-only collected/order basis adapter over shared integer risk primitives."""
from copy import deepcopy
from live_window_review_v6 import review_core,financial,conflict_evidence,unique_records

VERSION='philippines-live-window-v3'
BASES=('confirmed_collected','pending_order_value')

def review_ph(case,price_verifier):
    if (case.get('platform'),case.get('country'),case.get('currency'))!=('tiktok','PH','PHP'):raise ValueError('This adapter accepts tiktok/PH/PHP only')
    original_conflicts=conflict_evidence(case);_,_,duplicates=unique_records(case)
    raw_errors=financial(case)['data_errors']
    unproven=[];foreign=False;estimated_bases=set()
    for r in case['records']:
        if r.get('site')=='PH' and r.get('maturity')=='estimated' and r.get('sku_id')==case['current_sku'] and r.get('session_id')==case['session_id'] and r.get('currency')==case['currency']:estimated_bases.add(r.get('settlement_basis'))
    mixed_estimated=len(estimated_bases)>1 or bool(estimated_bases-set(BASES))
    normalized=deepcopy(case);kept=[]
    for r in normalized['records']:
        bad_site=r.get('site')!='PH';foreign=foreign or bad_site
        bad_mature=r.get('maturity')=='mature' and r.get('settlement_basis')!='confirmed_collected'
        bad_estimated=r.get('maturity')=='estimated' and mixed_estimated
        conflict=r.get('event_id') in original_conflicts
        if bad_site or bad_mature or bad_estimated or conflict:
            unproven.append({'event_id':r.get('event_id'),'site_valid':not bad_site,'mature_basis_valid':not bad_mature,'estimated_basis_consistent':not bad_estimated,'original_event_conflict':conflict,'original_record':deepcopy(r)})
        else:kept.append(r)
    normalized['records']=kept
    result=review_core(normalized,price_verifier);result['version']=VERSION;result['duplicate_event_count']=duplicates
    result['conflicting_event_evidence']=deepcopy(original_conflicts)
    result['data_errors']+=raw_errors
    if raw_errors:result['current_windows_complete']=False
    if original_conflicts:
        result['data_errors']+=['original_conflicting_duplicate:'+str(k) for k in original_conflicts];result['current_windows_complete']=False
    by_basis={}
    if mixed_estimated:
        for basis in BASES:
            scoped=deepcopy(case);scoped['records']=[r for r in scoped['records'] if r.get('site')=='PH' and r.get('maturity')=='estimated' and r.get('settlement_basis')==basis and r.get('event_id') not in original_conflicts]
            by_basis[basis]=financial(scoped)
    controls=case.get('ph_controls') or {};result['ph_controls']=deepcopy(controls)
    result['ph_record_basis']={'unproven_records':unproven,'raw_source_validation_errors':raw_errors,'mature_requires':'confirmed_collected','estimated_basis_conflict':mixed_estimated,'estimated_by_basis':by_basis,'records':[{'event_id':r.get('event_id'),'site':r.get('site'),'maturity':r.get('maturity'),'settlement_basis':r.get('settlement_basis')} for r in case['records']]}
    # Preserve known pause priority from the shared current price/stock/risk gate.
    if result['action']=='pause':return result
    states=[controls.get(k) for k in ('fulfillment_state','cashflow_state')];reason=None
    if controls.get('site')=='PH' and 'unsafe' in states:result['action']='pause';reason='ph_fulfillment_or_cashflow_unsafe'
    elif raw_errors or original_conflicts or foreign or controls.get('site')!='PH' or states!=['safe','safe']:result['action']='hold';reason='ph_original_event_conflict_or_site_or_controls_unproven'
    if reason:
        result['blocking_reasons'].append(reason);result['proposed_increment_minor']=0;result['reset_baseline']=False
    return result
