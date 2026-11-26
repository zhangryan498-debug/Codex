import copy
import unittest
import campaign_ledger_semantics_v3 as old
import campaign_ledger_semantics_v4 as policy


class PreserveFailureTests(unittest.TestCase):
    def setUp(self):
        self.case={'case_id':'SYNTHETIC_OTHER_ID','market':'douyin_cn','evaluation_time':'2026-11-11T15:00:00+08:00','facts':{'cost_ledger_complete_except_tax':True,'floor':{'scope':'whole_order','amount_minor':1000}}}
        self.p={'decision':'REJECT','floor_status':'FAIL','numbers':{'net_minor':None,'net_upper_minor':900,'listed_costs_minor':700},'sku_numbers':{'A':{'net_minor':None,'net_upper_minor':900,'listed_costs_minor':700}},'pending_fields':['unresolved_cost.tax'],'selected_tools':[],'state':{},'bound_basis':policy.BOUND_BASIS}
        self.cards=[{'id':'DY09','title':'Synthetic floor and retained fees'}]

    def normalize(self):return policy.normalize(self.case,self.p,self.cards)

    def test_known_failure_is_preserved_with_unknown_nonnegative_increment(self):
        self.assertEqual(old.normalize(self.case,self.p,self.cards)['decision'],'HOLD')
        result=self.normalize();self.assertEqual((result['decision'],result['floor_status']),('REJECT','FAIL'));self.assertEqual(result['numbers']['net_upper_minor'],900);self.assertIsNone(result['numbers']['net_minor']);self.assertIsNone(result['numbers']['listed_costs_minor']);self.assertEqual(result['numbers']['known_costs_subtotal_minor'],700)

    def test_an_order_surplus_cannot_cure_one_sku_proven_failure(self):
        self.case['facts']['floor']={'scope':'each_sku_total','amounts_minor':{'A':1000,'B':2000}}
        self.p['numbers']['net_upper_minor']=9900;self.p['sku_numbers']['B']={'net_minor':None,'net_upper_minor':9000,'listed_costs_minor':400}
        result=self.normalize();self.assertEqual(result['decision'],'REJECT');self.assertEqual(result['sku_numbers']['A']['net_upper_minor'],900)

    def test_equality_or_reversible_outcome_does_not_prove_failure(self):
        for upper in (1000,1100,None,False,900.0,'900'):
            self.p['numbers']['net_upper_minor']=upper;self.p['sku_numbers']['A']['net_upper_minor']=upper
            self.assertEqual(self.normalize()['decision'],'HOLD')

    def test_base_plus_projection_cannot_become_current_upper_bound(self):
        self.case['facts']['cost_ledger_complete_except_plus']=True
        self.case['facts']['mdp_plus']={'enrolled':True,'current_joint_fee_table':None,'current_interaction_with_base_cap':None}
        self.p['pending_fields'].append('mdp_plus.current_joint_fee_table')
        result=self.normalize();self.assertEqual(result['decision'],'HOLD');self.assertIsNone(result['numbers']['net_upper_minor'])

    def test_missing_bound_proof_or_unresolved_non_cost_term_stays_pending(self):
        original=copy.deepcopy(self.p)
        for patch in ({'bound_basis':'base_plan_only'},{'decision':'HOLD','floor_status':'UNPROVEN'},{'pending_fields':['unresolved_cost.tax','coupon.funding']}):
            self.p=copy.deepcopy(original);self.p.update(patch);self.assertEqual(self.normalize()['decision'],'HOLD')

    def test_no_input_mutation_and_current_amounts_remain_unknown(self):
        before=copy.deepcopy((self.case,self.p));result=self.normalize();self.assertEqual((self.case,self.p),before);self.assertIsNone(result['numbers']['net_minor']);self.assertFalse(result['current_floor_proven'])

    def test_a_fully_proven_safe_configuration_remains_allowed(self):
        self.case['facts']={'floor':{'scope':'whole_order','amount_minor':1000}}
        self.p.update(decision='ALLOW',floor_status='PASS',pending_fields=[],numbers={'net_minor':1000,'listed_costs_minor':700},sku_numbers={'A':{'net_minor':1000,'listed_costs_minor':700}})
        result=self.normalize();self.assertEqual(result['decision'],'ALLOW');self.assertTrue(result['current_floor_proven'])

    def test_exact_order_net_does_not_claim_exact_sku_coverage(self):
        self.case['facts']={'floor':{'scope':'each_sku_total','amounts_minor':{'A':1000}}}
        self.p.update(decision='HOLD',floor_status='UNPROVEN',pending_fields=['allocation.tail'],numbers={'net_minor':2000},sku_numbers={'A':{'net_minor':None}})
        result=self.normalize();self.assertEqual(result['current_amount_status'],'exact_order_net_partial_sku_ledger');self.assertFalse(result['current_floor_proven'])

if __name__=='__main__':unittest.main()
