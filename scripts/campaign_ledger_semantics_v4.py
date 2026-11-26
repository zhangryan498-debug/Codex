"""Preserve a proven failure when unresolved incremental costs cannot improve it.

V3 and its inference freeze remain immutable. A numeric BASE/quote projection is
never upgraded into a current upper bound. This wrapper requires the independent
calculator's explicit nonnegative-cost bound and the original REJECT/FAIL.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import campaign_ledger_semantics_v3 as base

VERSION='ledger-scopes-and-rule-trace-v4-preserve-proven-failure'
BASE_SHA256='32bfc31859d6c695770f49bca321d380d63386cca2b74d69db985f155fcb0f56'
BOUND_BASIS='Only unresolved nonnegative incremental costs remain; known/base net is an upper bound, not a guaranteed lower bound.'
digest=base.digest
required_rule_trace=base.required_rule_trace
citation_check=base.citation_check
validate_scopes=base.validate_scopes


def proven_failure(case,calculation):
    f=case['facts']
    unresolved={key.removeprefix('cost_ledger_complete_except_') for key,value in f.items() if key.startswith('cost_ledger_complete_except_') and value is True}
    if not unresolved or 'plus' in unresolved:return False
    if f.get('mdp_plus',{}).get('enrolled') is True:return False
    if calculation.get('decision')!='REJECT' or calculation.get('floor_status')!='FAIL' or calculation.get('bound_basis')!=BOUND_BASIS:return False
    allowed={'unresolved_cost.'+name for name in unresolved}|{'allocation_contract.tie_break_rule','cross_tool_winner_algorithm','seller_shipping_discount.threshold_basis'}
    if any(field not in allowed for field in calculation.get('pending_fields',[])):return False
    floor=f.get('floor',{})
    if floor.get('scope')=='whole_order':checks=[(calculation.get('numbers',{}),floor.get('amount_minor'))]
    elif floor.get('scope')=='each_sku_total' and set(floor.get('amounts_minor',{}))==set(calculation.get('sku_numbers',{})):
        checks=[(calculation['sku_numbers'][sku],minimum) for sku,minimum in floor['amounts_minor'].items()]
    else:return False
    return any(type(minimum) is int and minimum>=0 and type(row.get('net_upper_minor')) is int and row['net_upper_minor']<minimum for row,minimum in checks)


def normalize(case,independently_calculated,cards):
    if sha256(Path(base.__file__).read_bytes()).hexdigest()!=BASE_SHA256:raise ValueError('Frozen v3 semantic dependency changed')
    result=base.normalize(case,independently_calculated,cards)
    preserved=False
    if result['decision']=='HOLD' and result['floor_status']=='UNPROVEN' and proven_failure(case,independently_calculated):
        result['decision'],result['floor_status']='REJECT','FAIL'
        result['numbers']['net_upper_minor']=independently_calculated['numbers'].get('net_upper_minor')
        for sku,row in result.get('sku_numbers',{}).items():row['net_upper_minor']=independently_calculated['sku_numbers'][sku].get('net_upper_minor')
        result['known_failure_preserved']={'basis':BOUND_BASIS,'reason':'At least one declared subject fails even at its independently proven current upper bound. Unknown nonnegative incremental costs cannot cure it.','new_cost_values_invented':False}
        preserved=True
    result['semantic_policy']=VERSION
    result['semantic_dependency_sha256']=BASE_SHA256
    result['current_amount_status']=base.amount_status(result)
    if result['current_amount_status']=='exact_current_ledger' and any(type(row.get('net_minor')) is not int for row in result.get('sku_numbers',{}).values()):
        result['current_amount_status']='exact_order_net_partial_sku_ledger'
    result['current_floor_proven']=result['decision']=='ALLOW' and result['floor_status']=='PASS'
    result['proven_failure_not_erased_by_missing_cost']=preserved
    errors=validate_scopes(case,result,cards)
    if errors:raise ValueError('Invalid v4 semantic result: '+'; '.join(errors))
    return result
