"""Finite PH CPL capability assessment; no network, publication or account I/O."""
from copy import deepcopy
from datetime import datetime,timedelta
from live_compliance_review_v2 import review as base_review
from live_compliance_review_v1 import digest,uint,string

FIELDS=('qualifying_video_count_7d','known_qualifying_video_count_7d','threshold_condition','observed_ticket_status','affected_formats','live_access','live_shop_anchor','new_video_shop_anchor','historical_video_anchor','cpl_forces_stream_stop','live_effect')
VERSION='ph-live-cpl-v1'

def instant(value):
    if not isinstance(value,str):raise ValueError('explicit timestamp missing')
    d=datetime.fromisoformat(value)
    if d.utcoffset() is None:raise ValueError('explicit offset missing')
    return d

def initial():
    return {'rule_ids':['PH_CPL_RULE'],'qualifying_video_count_7d':None,'known_qualifying_video_count_7d':0,'threshold_condition':'unknown','observed_ticket_status':'not_supplied','affected_formats':None,'live_access':'unknown','live_shop_anchor':'unknown','new_video_shop_anchor':'unknown','historical_video_anchor':'not_supplied','cpl_forces_stream_stop':False,'live_effect':'none','reason':'No CPL assessment was requested.','data_errors':[],'source_rule_id':'PH_CPL_RULE'}

def assess(case,pack):
    out=initial();context=case.get('cpl_context')
    if 'cpl_context' not in case:return out
    if not isinstance(context,dict):out.update(live_effect='hold_evidence',reason='CPL context invalid.',data_errors=['cpl_context_invalid']);return out
    errors=[];out['reason']='CPL capabilities assessed only from supplied synthetic, scoped evidence.'
    if (case.get('platform'),case.get('country'),case.get('currency'))!=('tiktok','PH','PHP') or not any(r.get('rule_id')=='PH_CPL_RULE' and r.get('platform')=='tiktok' and r.get('country')=='PH' and r.get('status')=='capability' for r in pack.get('rules',[])):errors.append('PH_CPL_source_or_market_missing')
    account=context.get('account_id')
    account_valid=string(account)
    if not account_valid:errors.append('account_missing')
    try:now=instant(context.get('evaluation_time'))
    except (ValueError,TypeError):out.update(live_effect='hold_evidence',reason='Evaluation timestamp missing.',data_errors=['evaluation_timestamp_invalid']);return out
    history=context.get('history');complete=context.get('history_complete');by={};conflicts=set();unknown=False;count=0;duplicates=0
    if not isinstance(history,list):history=[];errors.append('history_schema_invalid')
    if type(complete) is not bool:errors.append('history_complete_not_boolean')
    for record in history:
        if not isinstance(record,dict) or not string(record.get('event_id')):errors.append('history_event_identity_invalid');continue
        key=record['event_id']
        if key in by:
            if digest(record)!=digest(by[key]):conflicts.add(key)
            else:duplicates+=1
        else:by[key]=record
    for key,record in by.items():
        if key in conflicts:errors.append('conflicting_history:'+key);continue
        if not account_valid or record.get('account_id')!=account:errors.append('history_account_mismatch:'+key);continue
        try:at=instant(record.get('created_at'))
        except (ValueError,TypeError):errors.append('history_timestamp_invalid:'+key);continue
        if at>now:errors.append('future_history:'+key);continue
        if record.get('format') not in ('video','live') or record.get('qualifying_status') not in ('confirmed','not_qualifying','unknown'):errors.append('history_record_schema_invalid:'+key);continue
        if not now-timedelta(days=7)<=at<now or record['format']!='video':continue
        if record['qualifying_status']=='confirmed':count+=1
        elif record['qualifying_status']=='unknown':unknown=True
    exact=count if complete is True and not unknown and not errors else None
    out.update(qualifying_video_count_7d=exact,known_qualifying_video_count_7d=count,threshold_condition='met' if count>=5 else 'not_met' if exact is not None else 'unknown')
    snapshot=context.get('status_snapshot');snapshot=snapshot if isinstance(snapshot,dict) else {}
    fresh=account_valid and snapshot.get('authenticated') is True and snapshot.get('account_id')==account and snapshot.get('country')=='PH'
    try:fresh=fresh and instant(snapshot.get('as_of'))==now
    except (ValueError,TypeError):fresh=False
    for field,allowed in [('live_access',('available','restricted','unknown')),('live_shop_anchor',('visible','hidden','unknown')),('new_video_shop_anchor',('visible','hidden','unknown'))]:
        value=snapshot.get(field)
        if fresh and value in allowed:out[field]=value
    ticket=context.get('ticket');valid_ticket=False;start=end=None;active=False;formats=None;exhausted=None
    if ticket is not None:
        if not isinstance(ticket,dict):errors.append('ticket_schema_invalid');out['observed_ticket_status']='invalid'
        elif ticket.get('authenticated') is not True:errors.append('present_ticket_unauthenticated');out['observed_ticket_status']='unconfirmed'
        else:
            valid_ticket=account_valid and string(ticket.get('ticket_id')) and ticket.get('account_id')==account and ticket.get('country')=='PH' and ticket.get('shop_only') is True
            formats=ticket.get('applies_to')
            valid_ticket=valid_ticket and isinstance(formats,list) and bool(formats) and all(f in ('video','live') for f in formats) and len(set(formats))==len(formats)
            try:start,end=instant(ticket.get('start_time')),instant(ticket.get('end_time'));valid_ticket=valid_ticket and start<end
            except (ValueError,TypeError):valid_ticket=False
            mechanism=ticket.get('mechanism');quota=ticket.get('quota');posted=ticket.get('posted_in_period')
            if mechanism=='video_quality_5_in_7':valid_ticket=valid_ticket and formats==['video'] and type(quota) is int and quota==7 and start is not None and end is not None and end-start==timedelta(days=7)
            elif mechanism=='other':valid_ticket=valid_ticket and (quota is None or uint(quota))
            else:valid_ticket=False
            if posted is not None and not uint(posted):valid_ticket=False
            if not valid_ticket:errors.append('ticket_identity_scope_or_period_invalid');out['observed_ticket_status']='invalid'
            else:
                out['affected_formats']=deepcopy(formats);active=start<=now<end
                out['observed_ticket_status']='active' if active else 'ended' if now>=end else 'scheduled'
                exhausted=posted>=quota if uint(posted) and uint(quota) else None
                if active and 'video' in formats:
                    expected='hidden' if exhausted is True else 'visible' if exhausted is False else 'unknown'
                    if expected!='unknown' and fresh and out['new_video_shop_anchor'] not in ('unknown',expected):errors.append('video_anchor_contradicts_quota')
                    out['new_video_shop_anchor']=expected
                if active and 'live' in formats:
                    if exhausted is None:errors.append('LIVE_quota_or_count_unknown')
                    elif exhausted and fresh and out['live_shop_anchor']=='visible':errors.append('LIVE_anchor_contradicts_exhaustion')
    historical=context.get('historical_anchor');historical_errors=[]
    if historical is not None:
        out['historical_video_anchor']='unknown'
        if isinstance(historical,dict) and historical.get('authenticated') is True and string(historical.get('video_id')):
            observed=historical.get('observed_state');previous=historical.get('previously_hidden_by_this_ticket')
            try:created=instant(historical.get('created_at'))
            except (ValueError,TypeError):created=None;historical_errors.append('historical_timestamp_invalid')
            if previous is not None and type(previous) is not bool:historical_errors.append('historical_previous_flag_not_boolean')
            if created is not None and created>now:historical_errors.append('historical_timestamp_future')
            if previous is True and (not valid_ticket or created is None or not start<=created<end):historical_errors.append('historical_ticket_period_not_proven')
            if historical_errors:pass
            elif valid_ticket and created is not None and start<=created<end and previous is True:
                out['historical_video_anchor']='hidden' if observed in ('hidden','unknown') else 'unknown'
                if observed=='visible':historical_errors.append('historical_current_visibility_conflict')
            elif observed in ('visible','hidden','unknown'):out['historical_video_anchor']=observed
        else:historical_errors.append('historical_identity_or_authentication_missing')
    known_live_restriction=fresh and (snapshot.get('other_live_restrictions') is True or out['live_access']=='restricted' or out['live_shop_anchor']=='hidden')
    if known_live_restriction:effect='hold_platform'
    elif errors:effect='hold_evidence'
    elif not fresh:effect='hold_evidence'
    elif snapshot.get('other_live_restrictions') is not False or out['live_access']!='available' or out['live_shop_anchor']!='visible':effect='hold_evidence'
    elif valid_ticket and active and 'live' in formats and exhausted is True:effect='hold_platform'
    else:effect='none'
    if fresh and snapshot.get('other_live_restrictions') is True:reason='Another independently observed LIVE restriction remains; CPL expiry does not clear it and CPL itself is not a forced stop.'
    elif known_live_restriction:reason='Current LIVE access or shoppable anchor is restricted; distinguish that capability from a forced stream stop.'
    elif errors:reason='CPL source, identity, chronology or history has unresolved evidence errors.'
    elif not fresh:reason='Current LIVE capability evidence is missing, stale or not authenticated.'
    elif valid_ticket and active and formats==['video']:reason='This verified video-only CPL does not constrain LIVE; independent current LIVE permissions and all ordinary gates still apply.'
    elif valid_ticket and out['observed_ticket_status']=='ended':reason='The verified CPL period ended; new-content rights use current evidence, while historical affected anchors are assessed separately.'
    elif ticket is None and out['threshold_condition']=='met':reason='Local qualifying-video evidence meets a conditional policy threshold but proves no issued ticket or LIVE penalty.'
    else:reason='Separate observed format-specific posting/anchor capabilities from ordinary restrictions and stream access.'
    out.update(live_effect=effect,reason=reason,data_errors=sorted(set(errors)),historical_anchor_data_errors=sorted(set(historical_errors)),history_duplicate_count=duplicates,conflicting_history_ids=sorted(conflicts),input_cpl_sha256=digest(context))
    return out

def _review(case,model,pack,price_check,finance_check=None):
    out=base_review(case,model,pack,price_check,finance_check);out['base_version']=out['version'];out['version']=VERSION
    if not isinstance(case,dict) or not isinstance(model,dict):return out
    if 'cpl_context' not in case and 'publishing_limit_review' not in model:
        out['cpl_applied']=False
        return out
    try:cap=assess(case,pack)
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as exc:
        cap=initial();cap.update(live_effect='hold_evidence',reason='Malformed CPL input.',data_errors=['malformed_CPL:'+type(exc).__name__])
    supplied=model.get('publishing_limit_review',{}) if isinstance(model,dict) else {};differences=[]
    if not isinstance(supplied,dict):supplied={};differences.append('publishing_limit_review_invalid')
    for key in FIELDS:
        if key=='affected_formats' and key in supplied and cap[key] is not None:
            value=supplied[key]
            equal=isinstance(value,list) and all(x in ('video','live') for x in value) and len(value)==len(set(value)) and set(value)==set(cap[key])
        else:equal=key in supplied and digest(supplied[key])==digest(cap[key])
        if not equal:differences.append(key)
    if supplied.get('rule_ids')!=['PH_CPL_RULE'] or not string(supplied.get('reason')):differences.append('rule_or_context_reason_invalid')
    base_final=out['final_recommendation'];effect=cap['live_effect']
    if base_final in ('session_terminated','hold_platform','blocked_prohibited','hold_review'):final=base_final
    elif effect=='hold_platform':final='hold_platform'
    elif effect=='hold_evidence' or base_final=='hold_evidence':final='hold_evidence'
    else:final='eligible_offline'
    expected=final
    if differences and final=='eligible_offline':final='hold_evidence'
    agree=model.get('final_recommendation')==expected
    eligible=final=='eligible_offline' and agree and out['model_evidence_binding_valid'] and not differences
    out.update(publishing_limit_review=cap,model_publishing_limit_review=deepcopy(supplied),model_publishing_limit_agreement=not differences,publishing_limit_model_differences=differences,model_format_order_equivalent=supplied.get('affected_formats')!=cap['affected_formats'] and 'affected_formats' not in differences,policy_final_recommendation=expected,final_recommendation=final,model_control_agreement=agree,proposal_eligible=eligible,system_receipt='offline_proposal' if eligible else 'blocked')
    return out

def review(case,model,pack,price_check,finance_check=None):
    try:return _review(case,model,pack,price_check,finance_check)
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as exc:
        out=base_review(case,model,pack,price_check,finance_check)
        if out['final_recommendation']=='eligible_offline':out['final_recommendation']='hold_evidence'
        cap=initial();cap.update(live_effect='hold_evidence',reason='Malformed CPL model/input evidence.',data_errors=['malformed_CPL_model_or_input:'+type(exc).__name__])
        out.update(version=VERSION,publishing_limit_review=cap,model_publishing_limit_agreement=False,proposal_eligible=False,system_receipt='blocked',model_control_agreement=False)
        return out
