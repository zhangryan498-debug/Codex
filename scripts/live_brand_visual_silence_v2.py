"""Ryan's scoped visual-brand silence policy. Pure metadata; no media or I/O."""
import math
import re

POLICY_VERSION = 'brand-visual-silent/2.0'

def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)

def _valid_scope(value):
    if not isinstance(value, dict): return False
    if type(value.get('recording_sha256')) is not str or not re.fullmatch(r'[a-f0-9]{64}', value['recording_sha256']): return False
    if (value.get('platform'), value.get('country')) not in {('douyin','CN'),('tiktok','PH'),('tiktok','ID')}: return False
    a,b = value.get('start_seconds'),value.get('end_seconds')
    return _finite(a) and _finite(b) and 0 <= a <= b

def evaluate(scope, observation, alerts):
    """Silence only explicit brand-identity/authorization alerts without other risk.

    observation is supplied by a trusted local human or a separately verified
    local observation pipeline. This function does not perceive, authenticate,
    or score that pipeline. The caller retains the evidence and its hashes.
    """
    if not _valid_scope(scope): raise ValueError('BRAND_REVIEW_SCOPE_INVALID')
    if not isinstance(alerts, list): raise ValueError('BRAND_REVIEW_ALERTS_INVALID')
    branch='insufficient_evidence'
    bound = _valid_scope(observation)
    if bound:
        bound = all(observation[key]==scope[key] for key in ('recording_sha256','platform','country')) and observation['start_seconds'] <= scope['start_seconds'] <= scope['end_seconds'] <= observation['end_seconds']
    if bound:
        sufficient = observation.get('origin') in ('human_local','verified_local_observation') and observation.get('visual_observed') is True and observation.get('speech_observed') is True
        if sufficient and observation.get('visual_status') == 'conflicting':
            branch='conflicting_visual_evidence'
        elif sufficient and observation.get('visual_status') == 'sufficient':
            if observation.get('relation')=='consistent': branch='consistent_visual_brand'
            elif observation.get('relation')=='inconsistent': branch='brand_mismatch'
    decisions=[];seen=set()
    for alert in alerts:
        if not isinstance(alert,dict) or type(alert.get('id')) is not str or not alert['id'] or alert['id'] in seen: raise ValueError('BRAND_REVIEW_ALERT_ID_INVALID')
        seen.add(alert['id'])
        if alert.get('scope')!='brand_identity_authorization':
            action,reason='keep','independent_or_other_alert_scope'
        elif alert.get('independent_risk') is not False:
            action,reason='keep','independent_risk_present_or_not_explicitly_excluded_for_this_alert'
        elif branch=='consistent_visual_brand':
            action,reason='silent','brand_consistent_under_Ryan_visual_criterion'
        else:
            action,reason='keep',branch
        decisions.append({'id':alert['id'],'action':action,'reason':reason})
    return {'policy_version':POLICY_VERSION,'brand_evidence_branch':branch,'scope':dict(scope),'decisions':decisions,'silent_ids':[x['id'] for x in decisions if x['action']=='silent'],'kept_ids':[x['id'] for x in decisions if x['action']=='keep'],'request_authorization_documents':False,'legal_authorization_status':'not_determined_by_visual_policy','price_gate_evaluated_or_overridden':False,'execution_authorized':False,'original_alerts_mutated':False}
