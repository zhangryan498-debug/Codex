"""Host arithmetic and explicitly simulated permission. Never authenticates real costs/accounts.

No legacy gate import/fallback. Source claims/method flags never supply completeness or grants.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP,localcontext
import re
import strict_handoff_trace as trace

VERSION='strict-offline-price/1.0.3'
ROUTES={'douyin':('CN','CNY'),'tiktok':('PH','PHP')}
SUFFIXES=('shop-ops','campaign-ops','ad-buyer')
MARKER='offline_mock_not_real_auth'
COSTS=('goods','packaging','shipping','returns_reserve')
FIXED_FEES=('ad_cost_cents','creator_cost_cents','tax_delta_cents','other_variable_cost_cents')
PRICES=('list_cents','merchant_coupon_cents','platform_coupon_cents','confirmed_platform_reimbursement_cents')
MISSING=object()

def at(value):
    if type(value) is not str or not value or value!=value.strip():raise ValueError('OFFSET_TIMESTAMP_REQUIRED')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:raise ValueError('OFFSET_TIMESTAMP_REQUIRED')
    return result

def text(value):return type(value) is str and bool(value) and value==value.strip()
def role_ok(role):return type(role) is str and any(role==p+'-'+s for p in ROUTES for s in SUFFIXES)
def version_id(value):
    # Revisions are bound opaque identifiers, never numeric permission/confidence flags.
    return (type(value) is int and value>=1) or (type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}',value) is not None)

def service_fee(revenue,rate):
    with localcontext() as ctx:
        ctx.prec=max(80,len(str(revenue))+len(rate.as_tuple().digits)+16)
        return int((Decimal(revenue)*rate).quantize(Decimal(1),rounding=ROUND_HALF_UP))

def margin(profit,revenue):
    if profit is None or not revenue:return None
    with localcontext() as ctx:
        ctx.prec=max(80,len(str(abs(profit)))+len(str(revenue))+16)
        return str((Decimal(profit)/Decimal(revenue)).quantize(Decimal('0.00000001'),rounding=ROUND_HALF_UP))

@dataclass(frozen=True)
class SimulatedReviewContext:
    principal_id:str
    policy_digest:str
    policy_json:str
    mode:str=MARKER

def make_simulated_context(policy,expected_policy_digest,principal_id):
    """Only a host-pinned MOCK policy. This factory is not real authentication."""
    if trace.digest(policy)!=expected_policy_digest:raise ValueError('HOST_POLICY_DIGEST_MISMATCH')
    if type(policy) is not dict or set(policy)!={'policy_id','version','mode','principals'}:
        raise ValueError('MOCK_POLICY_SHAPE')
    if policy['mode']!=MARKER or not text(policy['policy_id']) or not version_id(policy['version']):
        raise ValueError('EXPLICIT_SIMULATION_POLICY_REQUIRED')
    if type(policy['principals']) is not list or not text(principal_id):raise ValueError('MOCK_PRINCIPAL_REQUIRED')
    seen=set()
    for principal in policy['principals']:
        if type(principal) is not dict or set(principal)!={'principal_id','grants'} or not text(principal['principal_id']):
            raise ValueError('MOCK_PRINCIPAL_SHAPE')
        if principal['principal_id'] in seen:raise ValueError('DUPLICATE_MOCK_PRINCIPAL')
        seen.add(principal['principal_id'])
        if type(principal['grants']) is not list:raise ValueError('MOCK_GRANT_LIST_REQUIRED')
        for grant in principal['grants']:
            required={'action','receiver_roles','platform','country','currency','skus','valid_from_inclusive','valid_until_exclusive'}
            if type(grant) is not dict or set(grant)!=required:raise ValueError('MOCK_GRANT_SHAPE')
            if grant['action'] not in ('offline_price_review','observe_only'):raise ValueError('REAL_ACTION_GRANTS_FORBIDDEN')
            if type(grant['receiver_roles']) is not list or not grant['receiver_roles'] or any(not role_ok(r) for r in grant['receiver_roles']):
                raise ValueError('MOCK_ROLE_LIST_REQUIRED')
            if type(grant['skus']) is not list or not grant['skus'] or any(not text(s) for s in grant['skus']):
                raise ValueError('MOCK_SKU_LIST_REQUIRED')
            if grant['platform'] not in ROUTES or (grant['country'],grant['currency'])!=ROUTES[grant['platform']]:
                raise ValueError('MOCK_ROUTE_MISMATCH')
            if any(not r.startswith(grant['platform']+'-') for r in grant['receiver_roles']):raise ValueError('MOCK_ROLE_SCOPE_MISMATCH')
            if at(grant['valid_from_inclusive'])>=at(grant['valid_until_exclusive']):raise ValueError('MOCK_GRANT_INTERVAL_INVALID')
    return SimulatedReviewContext(principal_id,expected_policy_digest,trace.canonical(policy))

def context_view(context):
    if type(context) is not SimulatedReviewContext or context.mode!=MARKER:raise ValueError('HOST_SIMULATED_CONTEXT_REQUIRED')
    policy=trace.parse_json(context.policy_json)
    if trace.digest(policy)!=context.policy_digest:raise ValueError('HOST_CONTEXT_CHANGED')
    # Validate all stored fields again; a dataclass label alone is not a permission check.
    make_simulated_context(policy,context.policy_digest,context.principal_id)
    principal=next((p for p in policy['principals'] if p['principal_id']==context.principal_id),None)
    return {'mode':MARKER,'policy_id':policy['policy_id'],'version':policy['version'],
            'policy_digest':context.policy_digest,'principal_id':context.principal_id,
            'principal_present':principal is not None,'grants':principal['grants'] if principal else []}

def authorize_simulated(context,source,receiver,evaluation_time):
    try:
        view=context_view(context);instant=at(evaluation_time)
        if not role_ok(receiver):raise ValueError('UNSUPPORTED_RECIPIENT')
        if not view['principal_present']:raise ValueError('MOCK_PRINCIPAL_NOT_FOUND')
        for grant in view['grants']:
            if (grant['action']=='offline_price_review' and receiver in grant['receiver_roles'] and
                all(grant[k]==source.get(k) for k in ('platform','country','currency')) and
                source.get('sku') in grant['skus'] and
                at(grant['valid_from_inclusive'])<=instant<at(grant['valid_until_exclusive'])):
                return {'allowed':True,'mode':MARKER,'real_identity_authenticated':False,'business_authorized':False,
                        'principal_id':view['principal_id'],'policy_digest':context.policy_digest}
        raise ValueError('NO_APPLICABLE_SIMULATED_REVIEW_GRANT')
    except (ValueError,TypeError,KeyError,AttributeError) as error:
        return {'allowed':False,'mode':MARKER,'real_identity_authenticated':False,'business_authorized':False,
                'reason':str(error)}

def compute_ledger(source):
    """Recompute from explicit raw amounts; this proves arithmetic, not supplier/source truth."""
    errors=[];unknowns=[]
    def minor(value,path):
        if value is MISSING or value is None:unknowns.append(path);return None
        if type(value) is not int or value<0:errors.append('INVALID_MINOR:'+path);return None
        return value
    if type(source) is not dict:
        return {'errors':['SOURCE_OBJECT_REQUIRED'],'unknowns':['source_input'],'numbers':{},'floor_status':'UNPROVEN'}
    if set(source).intersection((*PRICES,'seller_costs_cents','fees')):
        errors.append('UNSUPPORTED_TOP_LEVEL_AMOUNT_LAYOUT')
    for key,value in {'fixture_type':'synthetic_offline','scope':'synthetic_offline','release_status':'not_authorized',
                      'cost_unit':'order_total_do_not_multiply_by_quantity'}.items():
        if source.get(key)!=value:errors.append('SOURCE_MARKER:'+key)
    platform=source.get('platform')
    if type(platform) is not str or platform not in ROUTES or (source.get('country'),source.get('currency'))!=ROUTES.get(platform):errors.append('SOURCE_ROUTE_MISMATCH')
    if not text(source.get('sku')):errors.append('SOURCE_SKU_REQUIRED')
    if not version_id(source.get('revision')):errors.append('SOURCE_REVISION_REQUIRED')
    quantity=source.get('quantity')
    if type(quantity) is not int or not 1<=quantity<=1000000:errors.append('INVALID_PHYSICAL_QUANTITY');quantity=None
    basis=source.get('floor_basis')
    if (basis,source.get('floor_scope')) not in (('buyer_goods_per_unit','each_sku'),('buyer_goods_order','order')):
        errors.append('FLOOR_BASIS_SCOPE_UNSUPPORTED')
    floor=minor(source.get('floor_cents',MISSING),'floor_cents')
    amounts=source.get('amounts',{})
    if type(amounts) is not dict:errors.append('AMOUNTS_OBJECT_REQUIRED');amounts={}
    if set(amounts)-set(PRICES)-{'seller_costs_cents','fees'}:errors.append('UNSUPPORTED_AMOUNT_COMPONENT')
    prices={k:minor(amounts.get(k,MISSING),'amounts.'+k) for k in PRICES}
    raw_costs=amounts.get('seller_costs_cents',{})
    if type(raw_costs) is not dict:errors.append('COST_OBJECT_REQUIRED');raw_costs={}
    if set(raw_costs)-set(COSTS):errors.append('UNSUPPORTED_COST_COMPONENT')
    costs={k:minor(raw_costs.get(k,MISSING),'amounts.seller_costs_cents.'+k) for k in COSTS}
    raw_fees=amounts.get('fees',{})
    if type(raw_fees) is not dict:errors.append('FEES_OBJECT_REQUIRED');raw_fees={}
    if set(raw_fees)-set(FIXED_FEES)-{'platform_service_fee_rate'}:errors.append('UNSUPPORTED_FEE_COMPONENT')
    fees={k:minor(raw_fees.get(k,MISSING),'amounts.fees.'+k) for k in FIXED_FEES}
    rate_raw=raw_fees.get('platform_service_fee_rate',MISSING);rate=None
    if rate_raw is MISSING or rate_raw is None:unknowns.append('amounts.fees.platform_service_fee_rate')
    else:
        try:
            if type(rate_raw) is not str:raise ValueError()
            rate=Decimal(rate_raw)
            if not rate.is_finite() or not Decimal(0)<=rate<=Decimal(1):raise ValueError()
        except (InvalidOperation,ValueError):errors.append('INVALID_SERVICE_FEE_RATE');rate=None
    base=[prices[k] for k in PRICES[:3]]
    buyer=base[0]-base[1]-base[2] if all(x is not None for x in base) else None
    if buyer is not None and buyer<0:errors.append('COUPONS_EXCEED_LIST')
    reimbursement=prices['confirmed_platform_reimbursement_cents']
    revenue=buyer+reimbursement if buyer is not None and buyer>=0 and reimbursement is not None else None
    service=service_fee(revenue,rate) if revenue is not None and rate is not None else None
    components=list(costs.values())+list(fees.values())+[service]
    total=sum(components) if all(x is not None for x in components) else None
    profit=revenue-total if revenue is not None and total is not None else None
    comparison_floor=floor*quantity if basis=='buyer_goods_per_unit' and floor is not None and quantity is not None else floor if basis=='buyer_goods_order' else None
    floor_status='UNPROVEN' if buyer is None or comparison_floor is None else 'PASS' if buyer>=comparison_floor else 'FAIL'
    if floor_status=='FAIL':errors.append('BUYER_FLOOR_NOT_SATISFIED')
    if profit is not None and profit<0:errors.append('NEGATIVE_ORDER_CONTRIBUTION')
    known_subtotal=sum(x for x in components if x is not None)
    upper=revenue-known_subtotal if revenue is not None else None
    if profit is None and upper is not None and upper<0:
        errors.append('PROVEN_NEGATIVE_CONTRIBUTION_UPPER_BOUND')
    if revenue is None:unknowns.append('current_seller_revenue')
    if service is None:unknowns.append('current_service_fee')
    if total is None:unknowns.append('current_total_variable_cost')
    if profit is None:unknowns.append('current_order_contribution')
    return {'errors':sorted(set(errors)),'unknowns':sorted(set(unknowns)),
            'numbers':{'buyer_paid_cents':buyer,'seller_revenue_cents':revenue,'platform_service_fee_cents':service,
                       'known_cost_subtotal_cents':known_subtotal,
                       'known_contribution_upper_bound_cents':upper if profit is None else profit,
                       'total_variable_cost_cents':total,'contribution_profit_cents':profit,
                       'contribution_margin':margin(profit,revenue)},
            'floor_status':floor_status,'floor_order_equivalent_cents':comparison_floor,
            'computed_seller_cost_complete':all(v is not None for v in costs.values()),
            'computed_fee_inputs_complete':rate is not None and all(v is not None for v in fees.values()),
            'cost_unit':'order_total_do_not_multiply_by_quantity','source_authenticity_verified':False,
            'arithmetic_method':'host_integer_minor_and_Decimal_HALF_UP_not_input_method_label'}

def evidence_check(evidence,evaluation_time):
    errors=[];unknowns=[]
    if type(evidence) is not dict:return ['EVIDENCE_OBJECT_REQUIRED'],[]
    if not text(evidence.get('source_id')) or not version_id(evidence.get('version')):
        errors.append('EVIDENCE_BINDING_REQUIRED')
    try:instant=at(evaluation_time)
    except (ValueError,TypeError):return ['EVALUATION_TIME_REQUIRED'],[]
    first=evidence.get('valid_from_inclusive');last=evidence.get('valid_until_exclusive')
    if first is None or last is None:unknowns.append('evidence_validity')
    else:
        try:
            start,end=at(first),at(last)
            if start>=end or not start<=instant<end:errors.append('EVIDENCE_OUTSIDE_VALIDITY')
        except (ValueError,TypeError):errors.append('EVIDENCE_TIME_INVALID')
    return errors,unknowns

def review_source(source,evidence,context,receiver,evaluation_time):
    canonical_errors=[]
    try:source_hash=trace.digest(source)
    except (ValueError,TypeError,OverflowError,RecursionError):source_hash=None;canonical_errors.append('NONCANONICAL_SOURCE')
    try:evidence_hash=trace.digest(evidence)
    except (ValueError,TypeError,OverflowError,RecursionError):evidence_hash=None;canonical_errors.append('NONCANONICAL_EVIDENCE')
    ledger=compute_ledger(source) if source_hash is not None else {'errors':['NONCANONICAL_SOURCE'],'unknowns':[],
             'numbers':{'buyer_paid_cents':None,'seller_revenue_cents':None,'contribution_profit_cents':None},'floor_status':'UNPROVEN'}
    source=source if type(source) is dict else {}
    errors=list(ledger['errors'])+canonical_errors;unknowns=list(ledger['unknowns'])
    e,u=evidence_check(evidence,evaluation_time);errors+=e;unknowns+=u
    if not role_ok(receiver) or not receiver.startswith(str(source.get('platform'))+'-'):errors.append('RECIPIENT_ROUTE_MISMATCH')
    permission=authorize_simulated(context,source,receiver,evaluation_time)
    if not permission['allowed']:errors.append('SIMULATED_REVIEW_PERMISSION_DENIED')
    decision='BLOCK' if errors else 'PENDING' if unknowns else 'PASS'
    return {'schema_version':VERSION,'scope':'synthetic_offline','release_status':'not_authorized',
            'decision':decision,'strict_offline_review':'accepted' if decision=='PASS' else 'rejected',
            'receiver_role':receiver,'source_sha256':source_hash,'evidence_sha256':evidence_hash,
            'evaluation_time':evaluation_time,'errors':sorted(set(errors)),'unknowns':sorted(set(unknowns)),
            'host_calculation':ledger,'simulated_permission':permission,
            'real_source_truth_verified':False,'real_account_permission_verified':False,
            'business_execution_authorized':False,'legacy_gate_used_or_fallback':False,
            'ignored_claim_fields':[k for k in ('simulate_authorization','authority','calculation','seller_cost_complete','fees_complete','boundary_probe') if k in source]}
