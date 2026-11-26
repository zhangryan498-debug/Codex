"""Local offline proposal receipt. No executable business operations."""
from fractions import Fraction
from live_window_review_v6 import time

LEDGER_FIELDS=('components','retained_revenue_minor','retained_fees_minor','retained_costs_minor','pre_ad_contribution_minor','post_ad_profit_minor','gross_roi','settlement_roi','net_profit_roi')

def ratio(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!=set(('numerator','denominator')) or any(type(value[k]) is not int for k in value) or value['denominator']<=0:raise ValueError('Invalid exact ratio')
    return Fraction(value['numerator'],value['denominator'])

def ledger_projection(value):
    if value is None:return None
    if not isinstance(value,dict):raise ValueError('Missing ledger')
    out={k:value[k] for k in LEDGER_FIELDS}
    for k in ('gross_roi','settlement_roi','net_profit_roi'):out[k]=ratio(out[k])
    return out

def decision_projection(value):
    fields=('case_id','action','inventory_available','budget_remaining_minor','proposed_increment_minor','reset_baseline','price_proven','scope','release_status')
    out={k:value[k] for k in fields}
    windows={}
    for w in value['windows']:
        key=(time(w['start']),time(w['end']),w['sku_id'])
        if key in windows:raise ValueError('Duplicate model window')
        windows[key]={k:ledger_projection(w[k]) for k in ('mature','estimated')}
    out['windows']=windows
    out['cumulative']={k:ledger_projection(value['cumulative'][k]) for k in ('mature','estimated')}
    return out

def receipt(case,model,verifier,review):
    system=review(case,verifier)
    try:agreement=decision_projection(model)==decision_projection(system)
    except (ValueError,TypeError,KeyError,ZeroDivisionError):agreement=False
    proposal=system['action'] in ('propose_continue','propose_switch','propose_increase')
    return {'case_id':case['case_id'],'system_receipt':'offline_proposal' if agreement and proposal else 'blocked' if agreement else 'rejected_model_mismatch','accounting_and_action_agreement':agreement,'system_action':system['action'],'model_action':model.get('action'),'price_proven':system['price_proven'],'financial_complete':system['current_windows_complete'],'proposed_increment_minor':system['proposed_increment_minor'] if agreement and proposal else 0,'system_result':system,'scope':'synthetic_offline','release_status':'not_authorized','execution_authorized':False,'price_acceptance_created':False}
