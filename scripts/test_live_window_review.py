from copy import deepcopy
import unittest
from live_window_review import review_cn,MONEY

START='2026-11-11T14:55:00+08:00';END='2026-11-11T15:00:00+08:00'
def sample():
    def rec(id,end,m):return {'event_id':id,'kind':'cumulative','start':None,'end':end,'session_id':'SESSION','sku_id':'SKU','currency':'CNY','maturity':'mature','state':'complete','costs_through':end,'metrics':m}
    a=dict.fromkeys(MONEY,0);a['buyer_paid_minor']=10000;b={'buyer_paid_minor':12000,'refunds_minor':200,'reimbursement_minor':100,'clawback_minor':50,'fees_minor':80,'fee_credits_minor':10,'costs_minor':700,'recoveries_minor':20,'ad_spend_minor':300}
    return {'case_id':'UNIT','platform':'douyin','country':'CN','currency':'CNY','session_id':'SESSION','evaluation_time':END,'current_sku':'SKU','target_sku':'SKU','requested_action':'continue','expected_windows':[{'start':START,'end':END}],'records':[rec('start',START,a),rec('end',END,b)],'inventory':{'on_hand':40,'reserved':7,'safety_stock':5,'stop_below':5,'resume_at':20,'paused':False,'snapshot_time':END,'max_age_seconds':60},'switch':{'preapproved':True,'identity_verified':True,'pricing_revision_verified':True},'price_ref':'GOOD','quantity':1,'price_revision':1,'budget':{'cap_minor':2000,'spent_minor':300,'committed_minor':100,'requested_increment_minor':0},'hard_risk':False,'restart_checks_verified':True,'increase_evidence':None}
def price(c):return {'proven':True,'reason':'explicit unit fixture, not actual price-host evidence'}

class LiveReviewTests(unittest.TestCase):
    def test_cumulative_differences_never_report_full_snapshot_as_window(self):
        r=review_cn(sample(),price);self.assertEqual(r['action'],'propose_continue');w=r['windows'][0]['mature'];self.assertEqual(w['components']['buyer_paid_minor'],2000);self.assertEqual(w['retained_revenue_minor'],1850);self.assertEqual(w['pre_ad_contribution_minor'],1100);self.assertEqual(w['post_ad_profit_minor'],800);self.assertEqual(r['cumulative']['mature']['post_ad_profit_minor'],800)
    def test_duplicates_out_of_order_and_refunds_count_once(self):
        c=sample();c['records']=[c['records'][1],deepcopy(c['records'][1]),c['records'][0]];r=review_cn(c,price);self.assertEqual(r['duplicate_event_count'],1);self.assertEqual(r['windows'][0]['mature']['post_ad_profit_minor'],800);self.assertEqual(r['inventory_available'],28)
    def test_conflicting_event_blocks_but_known_hard_risk_still_pauses(self):
        c=sample();bad=deepcopy(c['records'][1]);bad['metrics']['refunds_minor']+=1;c['records'].append(bad);self.assertEqual(review_cn(c,price)['action'],'hold');c['hard_risk']=True;self.assertEqual(review_cn(c,price)['action'],'pause')
    def test_estimated_does_not_fill_mature_ledger(self):
        c=sample()
        for r in c['records']:r['maturity']='estimated'
        r=review_cn(c,price);self.assertEqual(r['action'],'hold');self.assertIsNone(r['windows'][0]['mature']);self.assertEqual(r['windows'][0]['estimated']['post_ad_profit_minor'],800)
    def test_cost_cutoff_missing_and_boolean_money_never_zero(self):
        for key,value in [('costs_through',START),('metrics',dict(sample()['records'][1]['metrics'],fees_minor=None)),('metrics',dict(sample()['records'][1]['metrics'],fees_minor=False))]:
            c=sample();c['records'][1][key]=value;r=review_cn(c,price);self.assertEqual(r['action'],'hold');self.assertIsNone(r['windows'][0]['mature']);self.assertIsNone(r['cumulative']['mature'])
    def test_observed_zero_is_complete_and_zero_spend_ratio_is_null(self):
        c=sample()
        for r in c['records']:r['metrics']=dict.fromkeys(MONEY,0)
        out=review_cn(c,price);self.assertEqual(out['action'],'propose_continue');self.assertIsNone(out['windows'][0]['mature']['gross_roi'])
    def test_wide_cumulative_gap_does_not_invent_short_windows(self):
        c=sample();mid='2026-11-11T14:57:00+08:00';c['expected_windows']=[{'start':START,'end':mid},{'start':mid,'end':END}];r=review_cn(c,price);self.assertEqual(r['action'],'hold');self.assertTrue(all(w['mature'] is None for w in r['windows']))
    def test_matching_direct_and_cumulative_reconcile_once(self):
        c=sample();d=deepcopy(c['records'][1]);d.update(event_id='direct',kind='window',start=START);d['metrics']['buyer_paid_minor']=2000;c['records'].append(d);r=review_cn(c,price);self.assertEqual(r['windows'][0]['mature']['post_ad_profit_minor'],800);c['records'][-1]['metrics']['buyer_paid_minor']+=1;self.assertEqual(review_cn(c,price)['action'],'hold')
    def test_approved_switch_resets_baseline_without_inheriting_old_roi(self):
        c=sample();c.update(target_sku='NEW',requested_action='switch');r=review_cn(c,price);self.assertEqual(r['action'],'propose_switch');self.assertTrue(r['reset_baseline']);self.assertEqual(r['windows'][0]['sku_id'],'SKU');self.assertEqual(r['proposed_increment_minor'],0);c['switch']['identity_verified']=False;self.assertEqual(review_cn(c,price)['action'],'pause')
    def test_inventory_hysteresis_and_unknown_stock_are_independent_checks(self):
        c=sample();c['inventory'].update(on_hand=10,paused=True);self.assertEqual(review_cn(c,price)['action'],'pause');c['inventory'].update(on_hand=40,resume_at=5);self.assertEqual(review_cn(c,price)['action'],'pause');c['inventory']['resume_at']=20;self.assertEqual(review_cn(c,price)['action'],'propose_continue');c['inventory']['on_hand']=None;self.assertEqual(review_cn(c,price)['action'],'pause')
    def test_price_failure_or_mismatched_market_cannot_release(self):
        self.assertEqual(review_cn(sample(),lambda c:{'proven':False,'reason':'floor failed'})['action'],'pause');c=sample();c.update(platform='tiktok',country='PH',currency='PHP')
        with self.assertRaises(ValueError):review_cn(c,price)
    def test_budget_unknown_is_hold_and_exhausted_is_pause(self):
        c=sample();c['budget']['committed_minor']=None;self.assertEqual(review_cn(c,price)['action'],'hold');c['budget'].update(committed_minor=1700);self.assertEqual(review_cn(c,price)['action'],'pause')
    def test_average_roi_is_not_marginal_evidence(self):
        c=sample();c['requested_action']='increase';c['budget']['requested_increment_minor']=20;self.assertEqual(review_cn(c,price)['action'],'hold')
    def test_controlled_marginal_and_exact_integer_limits_allow_only_safe_increment(self):
        c=sample();c['requested_action']='increase';c['budget']['requested_increment_minor']=20;c['increase_evidence']={'source_kind':'controlled_incremental_trial','sku_id':'SKU','session_id':'SESSION','start':START,'end':END,'mature':True,'matched_basis':True,'incremental_pre_ad_contribution_lower_minor':100,'min_incremental_profit_minor':50,'approved':True,'budget_constraint_evidence':True,'control_limits_verified':True,'cooldown_satisfied':True,'absolute_increment_cap_minor':100,'balance_fraction_bps':500,'risk_spend_cap_minor':100};r=review_cn(c,price);self.assertEqual(r['action'],'propose_increase');self.assertEqual(r['increment_cap_minor'],80);c['increase_evidence']['incremental_pre_ad_contribution_lower_minor']=10;self.assertEqual(review_cn(c,price)['action'],'hold')
    def test_trial_wrong_window_or_old_sku_does_not_support_scale(self):
        c=sample();c['requested_action']='increase';c['budget']['requested_increment_minor']=1;c['increase_evidence']={'source_kind':'controlled_incremental_trial','sku_id':'OLD','session_id':'SESSION','start':START,'end':END,'mature':True,'matched_basis':True,'approved':True,'budget_constraint_evidence':True,'control_limits_verified':True,'cooldown_satisfied':True};self.assertEqual(review_cn(c,price)['action'],'hold')
    def test_no_input_mutation_and_no_execution_authorization(self):
        c=sample();before=deepcopy(c);r=review_cn(c,price);self.assertEqual(c,before);self.assertFalse(r['execution_authorized']);self.assertEqual(r['release_status'],'not_authorized')

if __name__=='__main__':unittest.main()
