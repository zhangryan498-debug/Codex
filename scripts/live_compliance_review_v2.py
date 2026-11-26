"""Versioned receipt-schema repair; preserves original model and v1 decisions."""
from live_compliance_review_v1 import review as review_v1

VERSION='live-compliance-review-v2'

def review(case,model,pack,price_check,finance_check=None):
    out=review_v1(case,model,pack,price_check,finance_check)
    out['base_version']=out['version'];out['version']=VERSION
    repaired=[]
    for segment in out.get('segments',[]):
        errors=segment.get('validation_errors',[])
        if segment.get('classification')=='evidence_missing' and errors in (['conflicting_duplicate'],['foreign_or_missing_session']):
            # These are system placeholders, not a newly inferred model finding.
            # Incomplete/conflicting identity establishes no predicted penalty.
            segment.update(origin='system_evidence_gap_placeholder',evidence=[],possible_enforcement=[],enforcement_certain=False,reason='Conflicting or foreign-session observations cannot establish a safe scene or an enforcement prediction.')
            repaired.append(segment['event_id'])
    out['receipt_schema_repairs']={'kind':'explicit_empty_non_prediction_for_system_gap_placeholders','event_ids':repaired,'raw_model_answer_modified':False,'decision_changed':False}
    return out
