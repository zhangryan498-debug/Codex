"""Proposal-state consistency only; never authenticates source, cost, rights or permission."""
VERSION='research-handoff-state/1.0'
PROPOSAL={'PASS_RESEARCH':0,'PENDING_EVIDENCE':1,'BLOCK_COMPARISON':2}
ROUTE={'RESEARCH_ONLY':0,'PENDING':1,'BLOCKED':2}
KEYS=('selection','materials','advertising')
def known(value,table):return type(value) is str and value in table
def producer_state_errors(assessment):
    if type(assessment) is not dict or not known(assessment.get('disposition'),PROPOSAL):
        return ['PROPOSAL_STATE_REQUIRED']
    handoffs=assessment.get('handoffs')
    if type(handoffs) is not dict or set(handoffs)!=set(KEYS):return ['ALL_HANDOFF_ROUTES_REQUIRED']
    minimum=PROPOSAL[assessment['disposition']];errors=[]
    for key,handoff in handoffs.items():
        state=handoff.get('status') if type(handoff) is dict else None
        if not known(state,ROUTE):errors.append('ROUTE_STATE_REQUIRED:'+key)
        elif ROUTE[state]<minimum:errors.append('ROUTE_RELAXES_CURRENT_PROPOSAL:'+key)
    return errors
def consumer_state_errors(assessment,receiver,response):
    if type(assessment) is not dict or not known(assessment.get('disposition'),PROPOSAL):return ['PROPOSAL_STATE_REQUIRED']
    if receiver not in KEYS:return ['RECEIVER_ROUTE_REQUIRED']
    handoffs=assessment.get('handoffs')
    selected=handoffs.get(receiver) if type(handoffs) is dict else None
    if type(selected) is not dict or not known(selected.get('status'),ROUTE):return ['UPSTREAM_ROUTE_STATE_REQUIRED']
    own=response.get('own_review_state') if type(response) is dict else None
    if not known(own,ROUTE):return ['RECEIVER_STATE_REQUIRED']
    minimum=max(PROPOSAL[assessment['disposition']],ROUTE[selected['status']])
    return ['RECEIVER_RELAXES_CURRENT_PROPOSAL_OR_ROUTE'] if ROUTE[own]<minimum else []
