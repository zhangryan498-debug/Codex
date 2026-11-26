"""Read-only offline arithmetic and conservative proposal checks; never operates ads."""
import argparse,json,math
from pathlib import Path

def number(x, nonnegative=True):
    if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or (nonnegative and x<0):
        raise ValueError("finite numeric value required")
    return x

def ratio(n,d):
    if n is None or d is None: return None
    n,d=number(n),number(d)
    return None if d==0 else n/d

def ledger(g,s,r,c):
    g,s,r,c=[number(v) for v in (g,s,r,c)]
    p=r-c
    return dict(contribution=p,post_ad=p-s,gross_roi=ratio(g,s),settled_roi=ratio(r,s),break_even_gross=g/p if p>0 else None,break_even_settled=r/p if p>0 else None)

def window(g,s,state):
    if state not in ('complete','partial','missing','stale','zero'): raise ValueError('invalid data state')
    if state=='zero' and (g!=0 or s!=0): raise ValueError('zero state must contain zeros')
    return dict(roi=None if state in ('missing','stale') else ratio(g,s),decision_eligible=state=='complete' and g is not None and s is not None and number(s)>0,state=state)

def remaining(cap,spent,committed):
    if None in (cap,spent,committed): return None
    cap,spent,committed=[number(v) for v in (cap,spent,committed)]
    return max(0,cap-spent-committed)

def safe_loss_spend(loss_remaining, latency_reserve, break_even, roi_lower_bound=None, assumptions_verified=False):
    """Conditional model only. Unknown return floor -> zero, not forecast mean.
    Caller must establish all variable/extra costs bounded. No guaranteed platform lock implied.
    """
    if not assumptions_verified or latency_reserve is None: return None
    loss_remaining,latency_reserve,break_even=[number(v) for v in (loss_remaining,latency_reserve,break_even)]
    if break_even<=0: raise ValueError('positive break-even required')
    risk=max(0,loss_remaining-latency_reserve)
    r=0 if roi_lower_bound is None else number(roi_lower_bound)
    return None if r>=break_even else risk/(1-r/break_even)

def organic(e0,p0,e1,p1):
    a,b=ratio(p0,e0),ratio(p1,e1)
    if a is None or b is None: return dict(before_efficiency=a,after_efficiency=b,scale=None,efficiency=None,causal=False)
    return dict(before_efficiency=a,after_efficiency=b,scale=(e1-e0)*a,efficiency=e1*(b-a),causal=False)

def review(x):
    """Explicit facts are supplied by caller; booleans are not independently verified evidence."""
    reasons=[]
    def result(action): return dict(action=action,reasons=reasons,execution_authorized=False)
    if x.get('hard_risk') is True or x.get('product_changed_unverified') is True:
        reasons.append('hard_risk_or_unverified_product_change'); return result('pause')
    stock=x.get('stock'); stop=x.get('stop_stock'); resume=x.get('resume_stock')
    if stock is not None and stop is not None and number(stock)<number(stop):
        reasons.append('inventory_below_stop'); return result('pause')
    if x.get('paused'):
        if stock is None or stop is None or resume is None or number(resume)<=number(stop) or number(stock)<number(resume) or x.get('restart_checks_verified') is not True:
            reasons.append('restart_hysteresis_or_checks_missing'); return result('pause')
    if x.get('data_state')!='complete':
        reasons.append('data_not_complete'); return result('freeze')
    if x.get('requested_action')=='increase':
        for key in ('same_basis_verified','budget_constraint_evidence_verified','marginal_evidence_verified','control_limits_verified','cooldown_satisfied','trial_or_scale_approval_verified'):
            if x.get(key) is not True: reasons.append(key+'_missing')
        for key in ('absolute_increment_cap','available_balance','balance_fraction','risk_spend_cap','proposed_increment'):
            if x.get(key) is None: reasons.append(key+'_missing')
        if reasons: return result('hold')
        absolute,balance,fraction,risk,proposed=[number(x[k]) for k in ('absolute_increment_cap','available_balance','balance_fraction','risk_spend_cap','proposed_increment')]
        if not 0<fraction<=1: raise ValueError('fraction must be in (0,1]')
        cap=min(absolute,balance,balance*fraction,risk)
        if proposed<=0 or proposed>cap: reasons.append('increment_exceeds_cap_or_nonpositive'); return result('hold')
        reasons.append('conditional_offline_proposal_with_cap='+str(cap)); return result('propose_increase')
    return result('hold')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('input'); args=ap.parse_args()
    x=json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
    print(json.dumps(review(x),ensure_ascii=False,indent=2))
if __name__=='__main__': main()
