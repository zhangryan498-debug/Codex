"""Separate Decimal ledger audit; does not call the author oracle or pricing engine."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
from replay_campaign_ledger_v3 import dump,read,sha


def audit_reference(raw,reference):
    records=[]
    raw_by_id={c['case_id']:c for c in raw['cases']}
    if set(raw_by_id)!={r['case_id'] for r in reference['cases']}:raise ValueError('Reference exact case coverage')
    for result in reference['cases']:
        case=raw_by_id[result['case_id']];facts=case['facts'];checks=[]
        def check(name,passed):
            checks.append({'check':name,'passed':bool(passed)})
            if not passed:raise AssertionError(result['case_id']+': '+name)
        expected_currency='CNY' if raw['market']=='douyin_cn' else 'PHP'
        check('identity',result['market']==raw['market'] and result['currency']==expected_currency)
        check('evaluation clock from source',result['evaluation_time']==case.get('evaluation_time',facts.get('evaluation_time')))
        rows=[('ORDER',result['numbers']),*result.get('sku_numbers',{}).items()]
        for label,row in rows:
            for key,value in row.items():
                if key.endswith('_minor'):
                    check(label+'.'+key+' exact integer/null',value is None or type(value) is int)
            keys=['buyer_goods_minor','platform_reimbursement_minor','fees_minor','listed_costs_minor','net_minor']
            if all(type(row.get(key)) is int for key in keys):
                expected=Decimal(row[keys[0]])+Decimal(row[keys[1]])-Decimal(row[keys[2]])-Decimal(row[keys[3]])
                check(label+' retained current net reconciliation',expected==Decimal(row['net_minor']))
            if all(type(row.get(k)) is int for k in ('net_lower_minor','net_upper_minor')):
                check(label+' ordered guaranteed bounds',row['net_lower_minor']<=row['net_upper_minor'])
        if result.get('sku_numbers'):
            for field in ['buyer_goods_minor','platform_reimbursement_minor','technical_fee_minor','mdp_fee_minor','other_service_fee_minor','fees_minor','listed_costs_minor','net_minor']:
                values=[row.get(field) for row in result['sku_numbers'].values()]
                if type(result['numbers'].get(field)) is int and all(type(v) is int for v in values):
                    check('SKU conservation '+field,sum(map(Decimal,values))==Decimal(result['numbers'][field]))
        floor=facts['floor']
        if floor['scope']=='each_sku_total':
            check('exact SKU floor subject coverage',set(floor['amounts_minor'])==set(result['sku_numbers']))
            floor_checks=[(result['sku_numbers'][sku],minimum) for sku,minimum in floor['amounts_minor'].items()]
        else:
            floor_checks=[(result['numbers'],floor['amount_minor'])]
        if result['decision']=='ALLOW':
            check('ALLOW has current PASS',result['floor_status']=='PASS')
            for index,(row,minimum) in enumerate(floor_checks):
                lower=row.get('net_minor')
                if lower is None:lower=row.get('net_lower_minor')
                check('every requested floor proved '+str(index),type(lower) is int and type(minimum) is int and lower>=minimum)
        if result['floor_status']=='FAIL':
            check('known floor violation preserved',any(type(row.get('net_minor')) is int and row['net_minor']<minimum or type(row.get('net_upper_minor')) is int and row['net_upper_minor']<minimum for row,minimum in floor_checks))
        if result['floor_status']=='NOT_APPLICABLE':
            snapshot=facts.get('current_account_snapshot',{})
            observed_quote=(snapshot.get('source_kind')=='synthetic_current_displayed_quote_not_eligibility_certificate' and snapshot.get('settlement_components_confirmed_for_economic_reconciliation') is True and result.get('current_scope')=='displayed_current_account_quote_economics_only_not_executable')
            check('invalid configuration has no executable guarantee; an explicitly observed quote remains observation only',result['decision']=='REJECT' and (result['numbers'].get('net_minor') is None or observed_quote))
        if facts.get('mdp_plus',{}).get('enrolled') and facts['mdp_plus'].get('current_joint_fee_table') is None:
            check('unknown PLUS total fee and current net stay unknown',result['numbers'].get('fees_minor') is None and result['numbers'].get('net_minor') is None and result['decision']!='ALLOW')
        records.append({'case_id':result['case_id'],'checks':checks,'all_passed':True})
    return records


def audit_auxiliary(project):
    root=Path(project)/'work/compound-ledger-v3-20261111/auxiliary-same-question'
    cn=read(root/'douyin-auxiliary-same-question/answer.md')['cases'][0]
    ph=read(root/'tiktok-auxiliary-same-question/answer.md')['cases'][0]
    cn_source=read(root/'douyin-auxiliary-same-question/input.json')['cases'][0]
    ph_source=read(root/'tiktok-auxiliary-same-question/input.json')['cases'][0]
    f=ph_source['facts'];qty=f['sku']['quantity'];gross=f['sku']['selected_product_discount_unit_price_minor']*qty
    candidates=f['seller_voucher_candidates'];eligible=[]
    for voucher in candidates:
        valid=voucher.get('redemption_valid',True) and voucher['redemption_remaining_uses']>0 and gross>=voucher['threshold_minor']
        if voucher['type']=='new_customer' and f['buyer']['prior_store_order']:valid=False
        if valid:eligible.append(voucher)
    winner=max(eligible,key=lambda v:v['discount_minor'])
    projection=ph['voucher_usage_projection']
    assertions=[]
    def check(name,passed):
        assertions.append({'check':name,'passed':bool(passed)})
        if not passed:raise AssertionError(name)
    for voucher in candidates:
        row=projection[voucher['tool_id']];new=int(voucher['tool_id']==winner['tool_id'])
        check('conditional counter '+voucher['tool_id'],row['conditional_new_usage']==new and row['conditional_after_one_valid_redemption_remaining_uses']==voucher['redemption_remaining_uses']-new)
        check('no actual commit '+voucher['tool_id'],row['actual_commit_count']==0 and row['actual_remaining_uses']==voucher['redemption_remaining_uses'])
    # PH05 frozen daily plan rate 4%, HALF_UP, 20000-centavo per-physical-unit cap.
    # Source references are preserved; this is not a current live fee assertion.
    unit=Decimal(gross-winner['discount_minor'])/qty
    mdp=min(Decimal(20000),(unit*Decimal('0.04')).quantize(Decimal(1),rounding='ROUND_HALF_UP'))*qty
    fee=(unit*Decimal(f['other_service_fee']['rate_bps'])/10000).quantize(Decimal(1),rounding='ROUND_HALF_UP')*qty
    cost=sum(f['costs_per_unit'].values())*qty
    net=gross-winner['discount_minor']-mdp-fee-cost
    check('separate Decimal full net equals model',Decimal(ph['numbers']['net_minor'])==net)
    check('inclusive exact floor',net==f['floor']['amounts_minor'][f['sku']['sku_id']] and ph['decision']=='ALLOW' and ph['floor_status']=='PASS')
    check('expired baseline remains unsupported',cn['decision']=='REJECT' and cn['numbers']['net_minor'] is None and cn['numbers']['fallback_no_activity_net'] is None and bool(cn.get('missing_fields')))
    w=cn_source['facts']['workflow'];state=cn['state']
    check('expiry releases reservation once without charge',state['new_commit_count']==0 and state['new_charge_count']==0 and state['new_voucher_usage']==0 and state['reserved_minor']==0 and state['released_minor']==w['current_reserved_minor'] and state['release_count']==1 and state['budget_remaining_minor']==w['budget_initial_minor'])
    return {'checks':assertions,'independent_decimal_net_minor':int(net),'first_score_unchanged':True,'kind':'same_question_auxiliary_supplement','actual_commit_count':0}


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--project',required=True);ap.add_argument('--definitions',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    records=[]
    for definition in read(a.definitions):
        raw=read(definition['raw']);reference=read(definition['reference'])
        records.extend(audit_reference(raw,reference))
    aux=audit_auxiliary(a.project)
    output={'reference_cases':len(records),'reference_checks':sum(len(r['checks']) for r in records),'all_passed':True,'cases':records,'auxiliary':aux,'audit_source_sha256':sha(__file__),'limitation':'This separate Decimal audit checks current ledger algebra, SKU conservation, floors, identities, null scope and supplemental counters. Full price selection, allocation/rounding and frozen-gold semantic grading remain separately authored evidence, not duplicated claims of this auditor.'}
    dump(a.output,output);print(json.dumps({k:v for k,v in output.items() if k not in ('cases','auxiliary')},ensure_ascii=False))
