"""Bounded synthetic LIVE content/notice state machine. No network or business I/O.

The semantic classifier is the actual role model, not a word dictionary. This
module validates its evidence and rules, and independently controls scope,
notices, remediation, telemetry, SKU and the existing financial/price gate.
"""
from copy import deepcopy
import hashlib
import json

VERSION='live-compliance-review-v1'
CHANNELS=('speech','captions','visual')
CLASSES=('prohibited','risk_review','evidence_missing','no_issue')
FINALS=('blocked_prohibited','hold_review','hold_evidence','hold_platform','session_terminated','eligible_offline')
SCOPE={'scope':'synthetic_offline','release_status':'not_authorized','execution_authorized':False}

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def digest(value): return hashlib.sha256(canonical(value).encode('utf8')).hexdigest()
def string(x): return isinstance(x,str) and bool(x.strip())
def uint(x): return type(x) is int and x>=0

def market(case):
    return (case.get('platform'),case.get('country'),case.get('currency')) in (('douyin','CN','CNY'),('tiktok','PH','PHP'))

def _review(case,model,pack,price_check,finance_check=None):
    """Return evidence-bound offline recommendations. Callbacks cannot be input data."""
    case,model=deepcopy(case),deepcopy(model)
    errors=[];duplicates=0;conflicts=[];conflict_records={};events_by={};ordering={}
    rules={r['rule_id']:r for r in pack.get('rules',[]) if r.get('platform')==case.get('platform') and r.get('country')==case.get('country')}
    if not market(case): errors.append('unsupported_market')
    if not string(case.get('case_id')) or not string(case.get('session_id')): errors.append('case_or_session_missing')
    if case.get('initial_sku')!='A': errors.append('initial_sku_not_prebound_A')
    if not isinstance(case.get('product_facts'),dict) or 'A' not in case['product_facts']: errors.append('product_facts_missing')
    if pack.get('scope')!='synthetic_offline' or not rules: errors.append('station_rule_pack_missing')
    if model.get('case_id')!=case.get('case_id') or any(model.get(k)!=v or (k=='execution_authorized' and type(model.get(k)) is not bool) for k,v in SCOPE.items()):
        errors.append('model_case_or_authority_binding_invalid')
    if not string(model.get('explanation')): errors.append('model_case_explanation_missing')
    raw_events=case.get('events')
    if not isinstance(raw_events,list) or not raw_events: raw_events=[];errors.append('event_stream_missing')
    for event in raw_events:
        if not isinstance(event,dict) or not string(event.get('event_id')):
            errors.append('event_identity_missing');continue
        key=event['event_id']
        if key in events_by:
            if canonical(event)==canonical(events_by[key]): duplicates+=1
            else:
                conflicts.append(key)
                siblings=conflict_records.setdefault(key,[deepcopy(events_by[key])])
                if canonical(event) not in [canonical(x) for x in siblings]: siblings.append(deepcopy(event))
            continue
        events_by[key]=event
        if not uint(event.get('at_second')) or not uint(event.get('sequence')):
            errors.append('event_order_missing:'+key);continue
        order=(event['at_second'],event['sequence'])
        if order in ordering: errors.append('ambiguous_event_order:'+key+':'+ordering[order])
        ordering[order]=key
    errors.extend('conflicting_duplicate:'+k for k in sorted(set(conflicts)))
    events=sorted((e for e in events_by.values() if uint(e.get('at_second')) and uint(e.get('sequence'))),key=lambda e:(e['at_second'],e['sequence']))
    content_ids={k for k,e in events_by.items() if e.get('type')=='content'}
    models={}
    if not isinstance(model.get('segments'),list): errors.append('model_segments_missing')
    else:
        for segment in model['segments']:
            if not isinstance(segment,dict) or not string(segment.get('event_id')):
                errors.append('model_segment_identity_missing');continue
            key=segment['event_id']
            if key in models: errors.append('duplicate_model_segment:'+key)
            else: models[key]=segment
    if set(models)!=content_ids: errors.append('model_content_coverage_not_exact')
    if model.get('final_recommendation') not in FINALS: errors.append('model_recommendation_invalid')
    active_sku=case.get('initial_sku');current_clean=False;terminal=False
    issues={};notices={};notice_history={};processed={};segments=[];timeline=[];operator_rejections=[]

    def issue(key,kind,event,reason,sku=None):
        issues[key]={'kind':kind,'event_id':event.get('event_id'),'sku':sku or active_sku,'reason':reason,'order':(event.get('at_second',0),event.get('sequence',0))}

    def controls():
        try: price=price_check(case,active_sku)
        except (ValueError,KeyError,TypeError,OSError) as exc: price={'proven':False,'reason':'price_check_failed:'+type(exc).__name__}
        if not isinstance(price,dict) or type(price.get('proven')) is not bool: price={'proven':False,'reason':'invalid_price_proof'}
        try: finance=finance_check(case,active_sku) if finance_check else {'proven':False,'reason':'financial_gate_missing'}
        except (ValueError,KeyError,TypeError,OSError) as exc: finance={'proven':False,'reason':'financial_check_failed:'+type(exc).__name__}
        if not isinstance(finance,dict) or type(finance.get('proven')) is not bool: finance={'proven':False,'reason':'invalid_financial_proof'}
        if terminal: state='session_terminated'
        elif notices: state='hold_platform'
        elif any(i['kind']=='prohibited' for i in issues.values()): state='blocked_prohibited'
        elif any(i['kind']=='risk_review' for i in issues.values()): state='hold_review'
        elif errors or issues or not current_clean or price['proven'] is not True or finance['proven'] is not True: state='hold_evidence'
        else: state='eligible_offline'
        return state,price,finance

    def assess(event):
        key=event['event_id'];seg=deepcopy(models.get(key,{}));validation=[]
        classification=seg.get('classification')
        if classification not in CLASSES: classification='evidence_missing';validation.append('classification_missing_or_invalid')
        if not string(seg.get('reason')): validation.append('context_reason_missing')
        cited=seg.get('rule_ids')
        if not isinstance(cited,list) or any(not isinstance(k,str) for k in cited): cited=[];validation.append('rule_ids_invalid')
        if len(cited)!=len(set(cited)) or any(k not in rules for k in cited): validation.append('rule_not_bound_to_station')
        if classification=='prohibited' and not any(k in rules and rules[k]['status']=='explicit' and rules[k]['category']!='enforcement' for k in cited): validation.append('no_explicit_applicable_prohibition')
        effects=seg.get('possible_enforcement')
        allowed={v for k in cited if k in rules for v in rules[k].get('possible_enforcement',[])}
        if not isinstance(effects,list) or any(not isinstance(x,str) or x not in allowed for x in effects): validation.append('unproven_enforcement')
        enforcement_rule='CN_ENFORCEMENT' if case.get('platform')=='douyin' else 'PH_ENFORCEMENT_RULE'
        if isinstance(effects,list) and effects and enforcement_rule not in cited: validation.append('general_enforcement_source_missing')
        if isinstance(effects,list) and effects and (classification!='prohibited' or not any(k in rules and rules[k]['status']=='explicit' and rules[k]['category']!='enforcement' for k in cited)): validation.append('unproven_content_has_enforcement_prediction')
        if seg.get('enforcement_certain') is not False: validation.append('guaranteed_punishment_claim')
        channels=event.get('channels');missing=False;texts={}
        if not isinstance(channels,dict) or set(channels)!=set(CHANNELS): validation.append('channel_schema_invalid');missing=True
        for channel in CHANNELS:
            data=channels.get(channel,{}) if isinstance(channels,dict) else {}
            if not isinstance(data,dict) or data.get('status') not in ('complete','missing','uncertain') or not isinstance(data.get('text'),str):
                missing=True;texts[channel]=''
            else:
                texts[channel]=data['text']
                if data['status']!='complete': missing=True
        evidence=seg.get('evidence')
        if not isinstance(evidence,list): evidence=[];validation.append('evidence_missing')
        for entry in evidence:
            if not isinstance(entry,dict) or entry.get('channel') not in CHANNELS or not string(entry.get('quote')) or entry['quote'] not in texts.get(entry.get('channel'),''):
                validation.append('quote_not_original_channel_substring')
        if any(texts.values()) and not evidence: validation.append('nonempty_scene_has_no_evidence')
        if validation:
            classification='evidence_missing'
        if missing and classification!='prohibited': classification='evidence_missing'
        identity=(event.get('sku'),event.get('shown_sku'),event.get('linked_sku'))
        if any(not string(x) for x in identity): classification='evidence_missing';validation.append('content_sku_identity_unknown')
        elif len(set(identity+(active_sku,)))!=1:
            applicable=[r['rule_id'] for r in rules.values() if r['category']=='product_mismatch' and r['status']=='explicit']
            if applicable: classification='prohibited';cited=sorted(set(cited+applicable))
            else: classification='evidence_missing'
        if validation: errors.append('model_segment_invalid:'+key)
        effective={**seg,'event_id':key,'classification':classification,'rule_ids':cited,'model_classification':models.get(key,{}).get('classification'),'validation_errors':sorted(set(validation)),'observation_complete':not missing,'evidence_event_sha256':digest(event)}
        return effective

    for event in events:
        key=event['event_id'];typ=event.get('type')
        if event.get('session_id')!=case.get('session_id'):
            issue('invalid:'+key,'evidence_missing',event,'foreign_or_missing_session');processed[key]={'event':event,'valid':False}
            if typ=='content': segments.append({'event_id':key,'classification':'evidence_missing','model_classification':models.get(key,{}).get('classification'),'rule_ids':[],'validation_errors':['foreign_or_missing_session'],'observation_complete':False})
        elif key in conflicts:
            issue('conflict:'+key,'evidence_missing',event,'conflicting_duplicate');processed[key]={'event':event,'valid':False}
            if typ=='content': segments.append({'event_id':key,'classification':'evidence_missing','model_classification':models.get(key,{}).get('classification'),'rule_ids':[],'validation_errors':['conflicting_duplicate'],'observation_complete':False,'conflicting_records':deepcopy(conflict_records[key])})
        elif typ=='content':
            seg=assess(event);segments.append(seg);processed[key]={'event':event,'segment':seg,'valid':True}
            current_clean=seg['classification']=='no_issue' and seg['observation_complete'] and event.get('sku')==active_sku
            if seg['classification']!='no_issue': issue(key,seg['classification'],event,seg.get('reason','content_unproven'),event.get('sku'))
        elif typ=='gap':
            if not isinstance(event.get('channels'),list) or not event['channels'] or any(c not in CHANNELS for c in event['channels']): errors.append('gap_channels_invalid:'+key)
            issue(key,'evidence_missing',event,'telemetry_gap');current_clean=False;processed[key]={'event':event,'valid':True}
        elif typ=='notice':
            action=event.get('action');nid=event.get('notice_id');scope=event.get('scope')
            valid=event.get('authenticated') is True and event.get('platform')==case.get('platform') and event.get('country')==case.get('country') and scope in ('session','account') and string(nid)
            targets=event.get('target_notice_ids')
            valid=valid and isinstance(targets,list) and all(string(x) for x in targets) and (not targets if action!='lift' else bool(targets))
            if action not in ('warning','restrict','terminate','suspend','lift','appeal_pending'): valid=False
            signature={'action':action,'scope':scope,'targets':targets}
            if string(nid) and nid in notice_history and notice_history[nid]['signature']!=signature: valid=False
            if not valid: issue('notice:'+key,'evidence_missing',event,'notice_not_authentic_or_bound')
            elif nid in notice_history: pass
            elif action=='lift':
                for target in targets:
                    previous=notice_history.get(target)
                    if previous is None or previous['signature']['action'] not in ('warning','restrict','terminate','suspend') or previous['signature']['scope']!=scope or previous['order']>=(event['at_second'],event['sequence']):
                        issue('lift:'+key+':'+target,'evidence_missing',event,'lift_target_or_scope_invalid')
                    else: notices.pop(target,None)
            elif action=='appeal_pending':
                if not notices: issue('appeal:'+key,'evidence_missing',event,'underlying_restriction_unknown')
            else:
                notices[nid]={'notice_id':nid,'action':action,'scope':scope,'source_event_id':key}
                current_clean=False
                if action=='terminate': terminal=True
            if valid and nid not in notice_history: notice_history[nid]={'signature':signature,'order':(event['at_second'],event['sequence'])}
            processed[key]={'event':event,'valid':valid}
        elif typ=='remediation':
            targets=event.get('resolved_event_ids');replacement=processed.get(event.get('replacement_event_id'),{})
            re= replacement.get('event',{}); rs=replacement.get('segment',{})
            valid=isinstance(targets,list) and bool(targets) and len(targets)==len(set(targets)) and all(string(x) and x in issues and issues[x]['event_id']==x for x in targets)
            valid=valid and event.get('removed_original') is True and event.get('human_review')=='approved_for_simulation' and string(event.get('reviewer_id'))
            valid=valid and replacement.get('valid') is True and re.get('type')=='content' and rs.get('classification')=='no_issue' and rs.get('observation_complete') is True
            if valid:
                order=(re['at_second'],re['sequence'])
                valid=order<(event['at_second'],event['sequence']) and all(issues[x]['order']<order and issues[x]['sku']==re.get('sku') for x in targets)
            if valid:
                for target in targets: issues.pop(target)
            else: issue('remediation:'+key,'evidence_missing',event,'remediation_not_complete_or_not_bound')
            processed[key]={'event':event,'valid':bool(valid)}
        elif typ=='switch':
            valid=event.get('from_sku')==active_sku and string(event.get('to_sku')) and event['to_sku'] in case.get('product_facts',{}) and event['to_sku']!=active_sku
            if valid: active_sku=event['to_sku'];current_clean=False
            else: issue('switch:'+key,'evidence_missing',event,'switch_identity_unproven')
            processed[key]={'event':event,'valid':bool(valid)}
        elif typ=='operator_request':
            if event.get('request') not in ('continue','resume','appeal','switch_account'): issue('operator:'+key,'evidence_missing',event,'operator_request_unknown')
            if event.get('request')=='switch_account': operator_rejections.append({'event_id':key,'reason':'never_bypass_or_evade_enforcement'})
            processed[key]={'event':event,'valid':True}
        else:
            issue('unknown:'+key,'evidence_missing',event,'event_type_unknown');processed[key]={'event':event,'valid':False}
        state,price,finance=controls()
        timeline.append({'event_id':key,'at_second':event['at_second'],'sequence':event['sequence'],'active_sku':active_sku,'state':state,'unresolved_issue_ids':sorted(issues),'active_notice_ids':sorted(notices),'price_proven':price['proven'],'financial_proven':finance['proven'],'input_event_sha256':digest(event)})
    final,price,finance=controls()
    agreement=model.get('final_recommendation')==final
    binding_ok=not errors and not any(s['validation_errors'] for s in segments)
    eligible=final=='eligible_offline' and agreement and binding_ok
    return {'case_id':case.get('case_id'),'version':VERSION,**SCOPE,'input_sha256':digest(case),'model_sha256':digest(model),'rule_pack_sha256':digest(pack),'segments':segments,'timeline':timeline,'active_sku':active_sku,'final_recommendation':final,'model_final_recommendation':model.get('final_recommendation'),'model_control_agreement':agreement,'model_evidence_binding_valid':binding_ok,'proposal_eligible':eligible,'system_receipt':'offline_proposal' if eligible else ('rejected_model_mismatch' if final=='eligible_offline' and not agreement else 'blocked'),'unresolved_issues':issues,'active_notices':notices,'terminal_session':terminal,'operator_requests_rejected':operator_rejections,'data_errors':sorted(set(errors)),'duplicate_event_count':duplicates,'conflicting_event_ids':sorted(set(conflicts)),'conflicting_event_evidence':deepcopy(conflict_records),'price_evidence':price,'financial_evidence':finance,'real_business_actions':0}

def review(case,model,pack,price_check,finance_check=None):
    """Malformed untrusted JSON fails closed without losing an entire batch."""
    try:
        return _review(case,model,pack,price_check,finance_check)
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:
        key=case.get('case_id') if isinstance(case,dict) else None
        return {'case_id':key,'version':VERSION,**SCOPE,'segments':[],'timeline':[],
                'final_recommendation':'hold_evidence','proposal_eligible':False,
                'system_receipt':'blocked','model_control_agreement':False,
                'model_evidence_binding_valid':False,'data_errors':['malformed_input:'+type(exc).__name__],
                'real_business_actions':0}
