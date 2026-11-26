"""Separate Philippine adapter; no historical foreign-site ratios or rules."""
from copy import deepcopy
from live_window_review_v6 import review_core

VERSION='philippines-live-window-v1'

def review_ph(case,price_verifier):
    if (case.get('platform'),case.get('country'),case.get('currency'))!=('tiktok','PH','PHP'):raise ValueError('This adapter accepts tiktok/PH/PHP only')
    normalized=deepcopy(case);unproven=[];foreign=False
    for raw,record in zip(case['records'],normalized['records']):
        bad_site=raw.get('site')!='PH';foreign=foreign or bad_site
        bad_basis=raw.get('maturity')=='mature' and raw.get('settlement_basis')!='confirmed_collected'
        if bad_site or bad_basis:
            record['state']='partial';unproven.append({'event_id':raw.get('event_id'),'site_valid':not bad_site,'mature_basis_valid':not bad_basis,'original_record':deepcopy(raw)})
    result=review_core(normalized,price_verifier);result['version']=VERSION
    controls=case.get('ph_controls') or {};result['ph_controls']=deepcopy(controls)
    result['ph_record_basis']={'unproven_records':unproven,'mature_requires':'confirmed_collected','records':[{'event_id':r.get('event_id'),'site':r.get('site'),'maturity':r.get('maturity'),'settlement_basis':r.get('settlement_basis')} for r in case['records']]}
    if result['action']=='pause':return result
    states=[controls.get(k) for k in ('fulfillment_state','cashflow_state')]
    reason=None
    if controls.get('site')=='PH' and 'unsafe' in states:result['action']='pause';reason='ph_fulfillment_or_cashflow_unsafe'
    elif foreign or controls.get('site')!='PH' or states!=['safe','safe']:result['action']='hold';reason='ph_site_or_fulfillment_or_cashflow_unproven'
    if reason:
        result['blocking_reasons'].append(reason);result['proposed_increment_minor']=0;result['reset_baseline']=False
    return result
