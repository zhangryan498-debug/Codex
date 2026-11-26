"""Safety properties of the supplemental independent-review caller builder."""
import copy
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
sys.path.append(str(Path.cwd()/'scripts'))
import campaign_price_handoff as gate
from compound_receiver_v3 import pack


class IndependentCallerTests(unittest.TestCase):
    def setUp(self):
        self.h=SimpleNamespace(gate=gate,POLICY={'clock':{'origin':'explicit synthetic +08 clock'}})
        self.instant='2026-11-11T13:42:00+08:00'
        self.case={'case_id':'NEW_CASE','market':'tiktok_ph','currency':'PHP','facts':{'evaluation_time':self.instant,'items':[{'sku_id':'A','quantity':2},{'sku_id':'B','quantity':1}],'floor':{'scope':'each_sku_total','basis':'net_profit_after_all_listed_costs','amounts_minor':{'A':800,'B':300}}}}
        self.ref={'case_id':'NEW_CASE','market':'tiktok_ph','currency':'PHP','evaluation_time':self.instant,'decision':'ALLOW','floor_status':'PASS','numbers':{'net_minor':1200},'sku_numbers':{'A':{'net_minor':900},'B':{'net_minor':300}},'pending_fields':[]}
        self.review={'all_checks_passed':True};self.provenance={'rule_versions':{'PH05':'frozen-synthetic-v1'},'review_sha256':'a'*64,'reference_sha256':'b'*64}

    def rows(self):return pack(self.h,self.case,self.ref,self.review,{'answer_sha256':'c'*64},self.provenance)
    def status(self,row,instant=None):return gate.validate(row['authority'],row['packet'],'tiktok-shop-ops',instant or self.instant,row['source_input'],row['calculation'])['field_gate']

    def test_exact_per_sku_equality_passes_without_cross_subsidy(self):
        rows=self.rows();self.assertEqual([self.status(r) for r in rows],['pass','pass']);self.assertEqual(rows[0]['source_input']['quantity'],2)
        self.ref['sku_numbers']['B']['net_minor']=299
        self.assertEqual([self.status(r) for r in self.rows()],['pass','fail'])

    def test_guaranteed_closed_bound_preserves_unknown_exact_value(self):
        self.ref['numbers']={'net_minor':None,'net_lower_minor':1100,'net_upper_minor':1200}
        self.ref['sku_numbers']['A']={'net_minor':None,'net_lower_minor':800,'net_upper_minor':900}
        self.ref['pending_fields']=['allocation.tie_break_with_all_outcomes_proven']
        row=self.rows()[0];self.assertEqual(self.status(row),'pass');self.assertIsNone(row['calculation']['independent_result_summary']['numbers']['net_minor'])

    def test_unknown_lower_bound_is_not_zero_or_safe(self):
        for missing in (None,False,800.0,'800',-1):
            self.ref['sku_numbers']['A']['net_minor']=missing
            self.assertEqual(self.status(self.rows()[0]),'fail')

    def test_failed_independent_model_review_blocks_even_safe_money(self):
        self.review['all_checks_passed']=False
        self.assertEqual([self.status(r) for r in self.rows()],['fail','fail'])

    def test_missing_information_blocks(self):
        self.ref.update(decision='HOLD',floor_status='UNPROVEN',pending_fields=['plus.joint_fee'])
        self.assertEqual([self.status(r) for r in self.rows()],['fail','fail'])

    def test_nonexecutable_observed_quote_is_not_a_current_lower_bound(self):
        self.ref.update(decision='REJECT',floor_status='NOT_APPLICABLE',current_scope='displayed_current_account_quote_economics_only_not_executable')
        rows=self.rows()
        self.assertTrue(all(r['calculation']['signed_current_lower_minor'] is None for r in rows))
        self.assertEqual([self.status(r) for r in rows],['fail','fail'])

    def test_wrong_identity_and_clock_cannot_reuse_reference(self):
        for key,value in [('case_id','OTHER'),('market','douyin_cn'),('currency','CNY'),('evaluation_time','2026-11-11T13:42:01+08:00')]:
            old=self.ref[key];self.ref[key]=value
            with self.assertRaises(ValueError):self.rows()
            self.ref[key]=old

    def test_expiry_and_changed_source_block(self):
        row=self.rows()[0]
        self.assertEqual(self.status(row,row['authority']['valid_until_exclusive']),'fail')
        row['source_input']['original_proposal']['facts']['items'][0]['quantity']=3
        self.assertEqual(self.status(row),'fail')

    def test_no_mutation_of_reference_or_original(self):
        expected=copy.deepcopy((self.case,self.ref));rows=self.rows();rows[0]['source_input']['original_proposal']['facts']['items'][0]['quantity']=9
        rows[0]['calculation']['independent_result_summary']['numbers']['net_minor']=1
        self.assertEqual((self.case,self.ref),expected)

if __name__=='__main__':unittest.main()
