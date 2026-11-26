"""Versioned read-only financial windows and CN live proposals; no business I/O."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
import ad_decision_contract as ads

VERSION='douyin-live-window-v6'
MONEY=('buyer_paid_minor','refunds_minor','reimbursement_minor','clawback_minor','fees_minor','fee_credits_minor','costs_minor','recoveries_minor','ad_spend_minor')

def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()
def integer(x):return type(x) is int and x>=0
def time(x):
    value=datetime.fromisoformat(x)
    if value.utcoffset() is None:raise ValueError('Explicit synthetic offset required')
    return value
def same_time(a,b):
    try:return time(a)==time(b)
    except (ValueError,TypeError):return False

def semantic_record(r):
    keys=('kind','start','end','session_id','sku_id','currency','maturity','state','costs_through','metrics')
    projection={k:r.get(k) for k in keys}
    for key in ('start','end','costs_through'):
        if projection[key] is not None:
            try:projection[key]=time(projection[key]).astimezone(timezone.utc).isoformat()
            except (TypeError,ValueError):pass
    return projection

def ratio(n,d):return None if d==0 else {'numerator':n,'denominator':d}

def ledger(components):
    if not all(type(components.get(k)) is int for k in MONEY):return None
    m=components;r=m['buyer_paid_minor']-m['refunds_minor']+m['reimbursement_minor']-m['clawback_minor'];f=m['fees_minor']-m['fee_credits_minor'];c=m['costs_minor']-m['recoveries_minor'];pre=r-f-c;post=pre-m['ad_spend_minor']
    return {'components':deepcopy(m),'retained_revenue_minor':r,'retained_fees_minor':f,'retained_costs_minor':c,'pre_ad_contribution_minor':pre,'post_ad_profit_minor':post,'gross_roi':ratio(m['buyer_paid_minor'],m['ad_spend_minor']),'settlement_roi':ratio(r,m['ad_spend_minor']),'net_profit_roi':ratio(post,m['ad_spend_minor'])}

def unique_records(case):
    by={};conflicts=set();reasons=[];duplicates=0
    for record in case['records']:
        key=record.get('event_id')
        if not isinstance(key,str) or not key:reasons.append('record.event_id_missing');continue
        if key in by:
            if digest(record)==digest(by[key]):duplicates+=1
            else:conflicts.add(key)
        else:by[key]=record
    reasons+=['conflicting_duplicate:'+key for key in sorted(conflicts)]
    return [r for key,r in by.items() if key not in conflicts],reasons,duplicates

def select(records):
    if not records:return None,[]
    base=semantic_record(records[0])
    if any(semantic_record(r)!=base for r in records[1:]):return None,['conflicting_same_cutoff_or_window']
    return records[0],[]

def record_components(r):
    reasons=[];m={}
    for key in MONEY:
        value=r.get('metrics',{}).get(key)
        m[key]=value if integer(value) else None
        if m[key] is None:reasons.append('missing_or_invalid:'+key)
    if r.get('state')!='complete':reasons.append('record_state_not_complete')
    if not same_time(r.get('costs_through'),r.get('end')):reasons.append('cost_cutoff_mismatch')
    return m,reasons

def source_candidates(case,start,end,maturity):
    # Preserve conflicting observations as evidence, never as approved totals.
    records=[];seen=set()
    for r in case['records']:
        if r.get('session_id')!=case['session_id'] or r.get('sku_id')!=case['current_sku'] or r.get('currency')!=case['currency'] or r.get('maturity')!=maturity:continue
        if digest(r) not in seen:records.append(r);seen.add(digest(r))
    direct=[r for r in records if r.get('kind')=='window' and same_time(r.get('start'),start) and same_time(r.get('end'),end)]
    before=[r for r in records if r.get('kind')=='cumulative' and same_time(r.get('end'),start)]
    after=[r for r in records if r.get('kind')=='cumulative' and same_time(r.get('end'),end)]
    result=[]
    for r in direct:
        values,why=record_components(r);result.append({'kind':'direct','event_ids':[r.get('event_id')],'components':values,'source_record':deepcopy(r),'issues':why})
    for a in before:
        for b in after:
            x,xr=record_components(a);y,yr=record_components(b)
            delta={k:y[k]-x[k] if x[k] is not None and y[k] is not None else None for k in MONEY}
            why=xr+yr+(['cumulative_component_decreased'] if any(v is not None and v<0 for v in delta.values()) else [])
            result.append({'kind':'cumulative_delta','event_ids':[a.get('event_id'),b.get('event_id')],'components':delta,'source_records':[deepcopy(a),deepcopy(b)],'issues':why})
    return result

def conflict_evidence(case):
    groups={}
    for r in case['records']:groups.setdefault(r.get('event_id'),{})[digest(r)]=r
    return {key:list(versions.values()) for key,versions in groups.items() if len(versions)>1}

def financial(case):
    conflicts=conflict_evidence(case)
    records,errors,duplicates=unique_records(case);now=time(case['evaluation_time']);filtered=[]
    for r in records:
        try:
            if time(r['end'])>now:errors.append('future_observation');continue
            if r.get('kind') not in ('cumulative','window') or r.get('maturity') not in ('mature','estimated'):errors.append('unsupported_record_kind_or_maturity');continue
            if r.get('session_id')!=case['session_id'] or r.get('currency')!=case['currency']:errors.append('session_or_currency_mismatch');continue
            if r.get('sku_id')==case['current_sku']:filtered.append(r)
        except (TypeError,ValueError,KeyError):errors.append('invalid_record_time')
    # Inspect every supplied cumulative cutoff, including intermediate ones.
    resets=set()
    for maturity in ('mature','estimated'):
        snapshots=[r for r in filtered if r['kind']=='cumulative' and r['maturity']==maturity]
        # Cost/recovery counters are ordered by their stated cost cutoff.
        # Other event counters retain the observation cutoff. Partial fields
        # remain independently useful; absent fields never erase known facts.
        for key in MONEY:
            points={}
            for r in snapshots:
                value=r.get('metrics',{}).get(key)
                if not integer(value):continue
                stamp=r.get('costs_through') if key in ('costs_minor','recoveries_minor') else r.get('end')
                try:cutoff=time(stamp)
                except (TypeError,ValueError):continue
                if cutoff>time(r['end']):continue
                points.setdefault(cutoff,set()).add(value)
            previous=None
            for cutoff,values in sorted(points.items()):
                if len(values)!=1:resets.add(maturity);continue
                value=next(iter(values))
                if previous is not None and value<previous:resets.add(maturity)
                previous=value
    expected_unique=[];seen=set()
    for expected in case['expected_windows']:
        interval=(time(expected['start']),time(expected['end']))
        if interval in seen:continue
        seen.add(interval);expected_unique.append(expected)
    ordered=sorted(seen)
    if any(left[1]>right[0] for left,right in zip(ordered,ordered[1:])):errors.append('overlapping_expected_windows')
    windows=[]
    for expected in expected_unique:
        start,end=expected['start'],expected['end']
        if time(start)>=time(end) or time(end)>now:raise ValueError('Invalid expected window')
        row={'start':start,'end':end,'sku_id':case['current_sku'],'mature':None,'estimated':None,'state':'unproven','known_mature_components':{},'known_estimated_components':{},'source_candidates':{},'blocking_reasons':[]}
        for maturity in ('mature','estimated'):
            current=[r for r in filtered if r['maturity']==maturity]
            direct,why=select([r for r in current if r['kind']=='window' and same_time(r.get('start'),start) and same_time(r['end'],end)]);pending=list(why);candidates=[];partial={}
            if maturity in resets:pending.append('cumulative_intermediate_reset_or_conflict')
            if direct:
                m,reasons=record_components(direct);partial=m;pending+=reasons
                if not reasons:candidates.append(m)
            before,bwhy=select([r for r in current if r['kind']=='cumulative' and same_time(r['end'],start)]);after,awhy=select([r for r in current if r['kind']=='cumulative' and same_time(r['end'],end)]);pending+=bwhy+awhy
            if before and after:
                a,ar=record_components(before);b,br=record_components(after);pending+=ar+br
                delta={key:b[key]-a[key] if type(a[key]) is int and type(b[key]) is int else None for key in MONEY}
                if any(type(v) is int and v<0 for v in delta.values()):pending.append('cumulative_component_decreased')
                if not direct:partial=delta
                if not ar and not br and not any(type(v) is int and v<0 for v in delta.values()):candidates.append(delta)
            if len(candidates)>1 and any(c!=candidates[0] for c in candidates[1:]):pending.append('direct_and_cumulative_disagree')
            evidence=source_candidates(case,start,end,maturity);row['source_candidates'][maturity]=evidence
            for item in evidence:
                item['trusted_event_identity']=not any(key in conflicts for key in item['event_ids'])
                item['conflicting_event_records']={key:deepcopy(conflicts[key]) for key in item['event_ids'] if key in conflicts}
            values=[item['components'] for item in evidence if item['trusted_event_identity']]
            row['known_'+maturity+'_components']={key:values[0][key] if values and integer(values[0][key]) and all(v[key]==values[0][key] for v in values) else None for key in MONEY} if values else {}
            if candidates and not pending:row[maturity]=ledger(candidates[0])
            if maturity=='mature' and row[maturity] is None:row['blocking_reasons']+=pending or ['missing_exact_window']
        if row['mature'] is not None:row['state']='complete'
        windows.append(row)
    cumulative={'mature':None,'estimated':None,'basis':'sum_of_expected_windows_only'}
    for maturity in ('mature','estimated'):
        if windows and 'overlapping_expected_windows' not in errors and all(row[maturity] is not None for row in windows):cumulative[maturity]=ledger({key:sum(row[maturity]['components'][key] for row in windows) for key in MONEY})
    return {'conflicting_event_evidence':deepcopy(conflicts),'windows':windows,'cumulative':cumulative,'data_errors':sorted(set(errors)),'duplicate_event_count':duplicates,'current_windows_complete':bool(windows) and all(w['mature'] is not None for w in windows) and not errors}

def review_core(case,price_verifier):
    f=financial(case);reasons=list(f['data_errors']);inv=case.get('inventory',{});budget=case.get('budget',{})
    vals=[inv.get(k) for k in ('on_hand','reserved','safety_stock')];available=vals[0]-vals[1]-vals[2] if all(integer(v) for v in vals) else None
    remaining=ads.remaining(budget.get('cap_minor'),budget.get('spent_minor'),budget.get('committed_minor')) if all(integer(budget.get(k)) for k in ('cap_minor','spent_minor','committed_minor')) else None
    try:price=price_verifier(case)
    except (ValueError,OSError,KeyError,TypeError) as exc:price={'proven':False,'reason':type(exc).__name__+': '+str(exc)}
    proven=price.get('proven') is True
    out={'case_id':case['case_id'],'action':'hold',**f,'inventory_available':available,'budget_remaining_minor':remaining,'proposed_increment_minor':0,'reset_baseline':False,'price_proven':proven,'price_evidence':price,'blocking_reasons':reasons,'scope':'synthetic_offline','release_status':'not_authorized','execution_authorized':False,'version':VERSION}
    changed=case['target_sku']!=case['current_sku'];request=case['requested_action'];switch=case.get('switch',{})
    def done(action,reason):
        out['action']=action
        if reason:out['blocking_reasons'].append(reason)
        return out
    if case.get('hard_risk') is True:return done('pause','hard_risk')
    if changed and (request!='switch' or not all(switch.get(k) is True for k in ('preapproved','identity_verified','pricing_revision_verified'))):return done('pause','unverified_or_unapproved_product_change')
    if not proven:return done('pause','price_not_proven:'+str(price.get('reason','unknown')))
    if available is not None and integer(inv.get('stop_below')) and available<inv['stop_below']:return done('pause','inventory_below_stop')
    if remaining is not None and remaining<=0:return done('pause','cumulative_budget_exhausted')
    if inv.get('paused') is True:
        if available is None or not integer(inv.get('resume_at')) or not integer(inv.get('stop_below')) or inv['resume_at']<=inv['stop_below'] or available<inv['resume_at'] or case.get('restart_checks_verified') is not True:return done('pause','resume_hysteresis_or_checks_missing')
    if case.get('hard_risk') is not False or type(inv.get('paused')) is not bool:return done('hold','risk_or_paused_state_unknown')
    if available is None or not integer(inv.get('stop_below')) or not integer(inv.get('max_age_seconds')):return done('hold','inventory_or_freshness_unknown')
    try:
        age=(time(case['evaluation_time'])-time(inv['snapshot_time'])).total_seconds()
        if age<0 or age>inv['max_age_seconds']:return done('hold','inventory_snapshot_stale_or_future')
    except (KeyError,TypeError,ValueError):return done('hold','inventory_timestamp_unknown')
    if remaining is None:return done('hold','cumulative_budget_or_commitments_unknown')
    if not f['current_windows_complete']:return done('hold','mature_same_basis_windows_incomplete')
    increment=budget.get('requested_increment_minor')
    if not integer(increment):return done('hold','increment_unknown_or_invalid')
    if request!='increase':
        if increment!=0:return done('hold','non_increase_request_contains_unapproved_increment')
        if request=='switch' and changed:out['reset_baseline']=True;return done('propose_switch',None)
        if request=='continue' and not changed:return done('propose_continue',None)
        return done('hold','unsupported_or_inconsistent_request')
    trial=case.get('increase_evidence') or {}
    checks=(trial.get('source_kind')=='controlled_incremental_trial',trial.get('sku_id')==case['current_sku']==case['target_sku'],trial.get('session_id')==case['session_id'],trial.get('mature') is True,trial.get('matched_basis') is True,trial.get('approved') is True,trial.get('budget_constraint_evidence') is True,trial.get('control_limits_verified') is True,trial.get('cooldown_satisfied') is True)
    if not all(checks):return done('hold','controlled_marginal_or_approval_evidence_missing')
    try:
        if time(trial['start'])>=time(trial['end']) or time(trial['end'])>time(case['evaluation_time']):return done('hold','marginal_trial_time_invalid')
    except (KeyError,TypeError,ValueError):return done('hold','marginal_trial_time_unknown')
    if not any(same_time(w['start'],trial['start']) and same_time(w['end'],trial['end']) for w in case['expected_windows']):return done('hold','marginal_trial_window_mismatch')
    fields=('min_incremental_profit_minor','absolute_increment_cap_minor','balance_fraction_bps','risk_spend_cap_minor')
    if type(trial.get('incremental_pre_ad_contribution_lower_minor')) is not int or not all(integer(trial.get(k)) for k in fields) or not 0<trial['balance_fraction_bps']<=10000:return done('hold','approved_increment_limits_unknown')
    cap=min(remaining,trial['absolute_increment_cap_minor'],remaining*trial['balance_fraction_bps']//10000,trial['risk_spend_cap_minor'])
    out['increment_cap_minor']=cap;out['incremental_profit_lower_minor']=trial['incremental_pre_ad_contribution_lower_minor']-increment
    if increment<=0 or increment>cap or out['incremental_profit_lower_minor']<trial['min_incremental_profit_minor']:return done('hold','increment_exceeds_cap_or_marginal_profit_floor')
    # The caller-approved fractional limit is already applied with integer floor.
    # Reuse the existing advertising review's independent caps and restart guard.
    checked=ads.review({'requested_action':'increase','hard_risk':False,'product_changed_unverified':False,'stock':available,'stop_stock':inv['stop_below'],'resume_stock':inv.get('resume_at'),'paused':inv.get('paused',False),'restart_checks_verified':case.get('restart_checks_verified'),'data_state':'complete','same_basis_verified':True,'budget_constraint_evidence_verified':True,'marginal_evidence_verified':True,'control_limits_verified':True,'cooldown_satisfied':True,'trial_or_scale_approval_verified':True,'absolute_increment_cap':cap,'available_balance':remaining,'balance_fraction':1,'risk_spend_cap':trial['risk_spend_cap_minor'],'proposed_increment':increment})
    out['ad_risk_review']=checked
    if checked['action']!='propose_increase':return done('hold','existing_ad_risk_gate_blocked')
    out['proposed_increment_minor']=increment
    return done('propose_increase',None)

def review_cn(case,price_verifier):
    if (case.get('platform'),case.get('country'),case.get('currency'))!=('douyin','CN','CNY'):raise ValueError('This version supports Douyin CN only')
    return review_core(deepcopy(case),price_verifier)
