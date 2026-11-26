from copy import deepcopy
import unittest
from live_window_ph_v2 import review_ph
from test_live_window_review import sample,price

def ph():
    c=sample();c.update(platform='tiktok',country='PH',currency='PHP',ph_controls={'site':'PH','fulfillment_state':'safe','cashflow_state':'safe'})
    for r in c['records']:r.update(currency='PHP',site='PH',settlement_basis='confirmed_collected')
    return c

class PhilippineTests(unittest.TestCase):
    def test_confirmed_collection_exact_components_without_foreign_rates(self):
        c=ph();r=review_ph(c,price);self.assertEqual(r['action'],'propose_continue');self.assertEqual(r['cumulative']['mature']['post_ad_profit_minor'],800)
    def test_cod_or_pending_order_value_cannot_become_mature(self):
        for basis in ('pending_order_value','unknown',None):
            c=ph();c['records'][1]['settlement_basis']=basis;r=review_ph(c,price);self.assertEqual(r['action'],'hold');self.assertIsNone(r['cumulative']['mature']);self.assertTrue(r['ph_record_basis']['unproven_records'])
    def test_estimated_pending_value_remains_separate(self):
        c=ph()
        for r in c['records']:r.update(maturity='estimated',settlement_basis='pending_order_value')
        r=review_ph(c,price);self.assertEqual(r['action'],'hold');self.assertIsNone(r['cumulative']['mature']);self.assertEqual(r['cumulative']['estimated']['post_ad_profit_minor'],800)
    def test_unsafe_and_unknown_ph_controls(self):
        for field in ('fulfillment_state','cashflow_state'):
            for value,action in [('unsafe','pause'),('unknown','hold'),(None,'hold')]:
                c=ph();c['ph_controls'][field]=value;self.assertEqual(review_ph(c,price)['action'],action)
    def test_country_currency_and_record_site_not_mixed(self):
        for field,value in [('country','ID'),('currency','CNY'),('platform','douyin')]:
            c=ph();c[field]=value
            with self.assertRaises(ValueError):review_ph(c,price)
        c=ph();c['records'][1]['site']='ID';r=review_ph(c,price);self.assertEqual(r['action'],'hold');self.assertIsNone(r['cumulative']['mature'])
    def test_wrong_control_site_not_treated_as_ph_approval(self):
        c=ph();c['ph_controls']['site']='ID';self.assertEqual(review_ph(c,price)['action'],'hold')
    def test_known_price_or_stock_failure_keeps_pause_priority(self):
        c=ph();c['ph_controls']['cashflow_state']='unknown';r=review_ph(c,lambda _: {'proven':False,'reason':'price failed'});self.assertEqual(r['action'],'pause')
    def test_pending_intermediate_not_confirmed_counter_reset(self):
        c=ph();r=deepcopy(c['records'][1]);r.update(event_id='pending-mid',end='2026-11-11T14:57:00+08:00',costs_through='2026-11-11T14:57:00+08:00',settlement_basis='pending_order_value');r['metrics']['buyer_paid_minor']=13000;c['records'].append(r)
        out=review_ph(c,price);self.assertEqual(out['action'],'propose_continue');self.assertEqual(out['cumulative']['mature']['components']['buyer_paid_minor'],2000);self.assertTrue(out['ph_record_basis']['unproven_records'])
    def test_raw_original_event_conflict_survives_basis_filtering(self):
        c=ph();a=deepcopy(c['records'][0]);a.update(event_id='conflict-pending',end='2026-11-11T14:57:00+08:00',costs_through='2026-11-11T14:57:00+08:00',settlement_basis='pending_order_value');b=deepcopy(a);b['state']='partial';c['records']+=[a,b]
        out=review_ph(c,price);self.assertEqual(out['action'],'hold');self.assertEqual(len(out['conflicting_event_evidence']['conflict-pending']),2);self.assertFalse(out['current_windows_complete'])
    def test_estimated_different_bases_never_difference_or_sum(self):
        c=ph()
        for i,r in enumerate(c['records']):r.update(maturity='estimated',settlement_basis='confirmed_collected' if i==0 else 'pending_order_value')
        out=review_ph(c,price);self.assertEqual(out['action'],'hold');self.assertIsNone(out['cumulative']['estimated']);self.assertTrue(out['ph_record_basis']['estimated_basis_conflict'])
    def test_ambiguous_estimates_do_not_erase_independent_confirmed_mature(self):
        c=ph();e=deepcopy(c['records'])
        for i,r in enumerate(e):r.update(event_id='estimate'+str(i),maturity='estimated',settlement_basis='confirmed_collected' if i==0 else 'pending_order_value')
        c['records']+=e;out=review_ph(c,price);self.assertEqual(out['action'],'propose_continue');self.assertIsNone(out['cumulative']['estimated']);self.assertIsNotNone(out['cumulative']['mature'])
    def test_other_sku_estimate_basis_does_not_pollute_current(self):
        c=ph();e=deepcopy(c['records'])
        for i,r in enumerate(e):r.update(event_id='estimate'+str(i),maturity='estimated',settlement_basis='pending_order_value')
        other=deepcopy(e[0]);other.update(event_id='other',sku_id='OTHER',settlement_basis='confirmed_collected');c['records']+=e+[other]
        out=review_ph(c,price);self.assertIsNotNone(out['cumulative']['estimated']);self.assertFalse(out['ph_record_basis']['estimated_basis_conflict'])
    def test_no_implicit_fee_refund_or_stock_recovery(self):
        c=ph();c['records'][1]['metrics']['refunds_minor']=5000;r=review_ph(c,price);w=r['windows'][0]['mature'];self.assertEqual(w['retained_fees_minor'],70);self.assertEqual(r['inventory_available'],28);self.assertLess(w['post_ad_profit_minor'],0);self.assertFalse(r['execution_authorized'])

if __name__=='__main__':unittest.main()
