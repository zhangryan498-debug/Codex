"""Conservative current/target layout adapter; retains the original model view."""
from copy import deepcopy
from live_window_host import decision_projection,ledger_projection,LEDGER_FIELDS,receipt as strict_receipt
from live_window_review_v6 import time,ledger,MONEY
from offline_role_queue import digest

def walk(value,path=()):
    if isinstance(value,dict):
        yield path,value
        for k,v in value.items():yield from walk(v,path+(k,))
    elif isinstance(value,list):
        for i,v in enumerate(value):yield from walk(v,path+(i,))

def normalize_switch(case,model,system):
    unchanged=(model,{'applied':False})
    if system['action']!='propose_switch' or model.get('action')!='propose_switch' or case['current_sku']==case['target_sku'] or model.get('reset_baseline') is not True or type(model.get('proposed_increment_minor')) is not int or model['proposed_increment_minor']!=0:return unchanged
    try:
        expected={(time(w['start']),time(w['end'])):w for w in system['windows']}
        target=model['windows']
        if len(target)!=len(expected) or {(time(w['start']),time(w['end'])) for w in target}!=set(expected):return unchanged
        if any(w['sku_id']!=case['target_sku'] or w.get('mature') is not None or w.get('estimated') is not None for w in target):return unchanged
        if any(model['cumulative'][m] is not None for m in ('mature','estimated')):return unchanged
        found={};paths=[]
        for path,node in walk(model):
            if node.get('sku_id')==case['target_sku'] and any(node.get(k) is not None for k in ('ledger','mature','estimated')+LEDGER_FIELDS):return unchanged
            if 'ledger' not in node or not all(k in node for k in ('sku_id','session_id','currency','start','end','maturity')):continue
            if node['sku_id']!=case['current_sku'] or node['session_id']!=case['session_id'] or node['currency']!=case['currency']:continue
            maturity=node['maturity'];key=(time(node['start']),time(node['end']))
            if maturity not in ('mature','estimated') or key not in expected:continue
            if node.get('state')!='complete' or time(node.get('costs_through'))!=time(node['end']):return unchanged
            claimed_ids=node.get('event_ids') if isinstance(node.get('event_ids'),list) else [node['event_id']] if isinstance(node.get('event_id'),str) else None
            candidates=expected[key].get('source_candidates',{}).get(maturity,[])
            if not claimed_ids or not any(candidate.get('trusted_event_identity') is True and not candidate.get('issues') and candidate['event_ids']==claimed_ids for candidate in candidates):return unchanged
            if case['country']=='PH' and (node.get('site')!='PH' or maturity=='mature' and node.get('settlement_basis')!='confirmed_collected'):return unchanged
            values=node['ledger']['components']
            if set(values)!=set(MONEY) or any(type(v) is not int for v in values.values()):return unchanged
            if ledger_projection(node['ledger'])!=ledger_projection(expected[key][maturity]):return unchanged
            identity=(key,maturity)
            if identity in found and digest(found[identity])!=digest(node['ledger']):return unchanged
            found[identity]=deepcopy(node['ledger']);paths.append({'path':list(path),'start':node['start'],'end':node['end'],'sku_id':node['sku_id'],'maturity':maturity,'state':node['state'],'costs_through':node['costs_through'],'source_event_ids':claimed_ids,'ledger_sha256':digest(node['ledger'])})
        windows=[]
        for key,w in expected.items():
            row={'start':w['start'],'end':w['end'],'sku_id':case['current_sku']}
            for maturity in ('mature','estimated'):
                if w[maturity] is not None and (key,maturity) not in found:return unchanged
                row[maturity]=deepcopy(found.get((key,maturity)))
            windows.append(row)
        cumulative={'basis':'sum_of_expected_windows_only'}
        for maturity in ('mature','estimated'):
            cumulative[maturity]=ledger({k:sum(w[maturity]['components'][k] for w in windows) for k in MONEY}) if windows and all(w[maturity] is not None for w in windows) else None
        normalized=deepcopy(model);normalized['windows']=windows;normalized['cumulative']=cumulative
        if decision_projection(normalized)!=decision_projection(system):return unchanged
        return normalized,{'applied':True,'kind':'scoped_outgoing_ledger_and_separate_null_target_baseline','source_paths':paths,'original_target_view':{'windows':deepcopy(model['windows']),'cumulative':deepcopy(model['cumulative'])},'derived_current_aggregate_from_supplied_scoped_ledgers':True,'original_model_sha256':digest(model),'raw_answer_changed':False}
    except (KeyError,TypeError,ValueError,ZeroDivisionError):return unchanged

def receipt(case,model,verifier,review):
    system=review(case,verifier);normalized,trace=normalize_switch(case,model,system)
    result=strict_receipt(case,normalized,verifier,review)
    result['layout_normalization']=trace
    return result
