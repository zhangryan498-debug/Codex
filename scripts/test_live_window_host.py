from copy import deepcopy
import unittest
from live_window_review_v6 import review_cn
from live_window_host import receipt
from test_live_window_review import sample,price

class HostTests(unittest.TestCase):
    def test_forged_profit_or_action_does_not_create_proposal(self):
        for field in ('profit','action'):
            c=sample();m=review_cn(c,price)
            if field=='profit':m['windows'][0]['mature']['post_ad_profit_minor']+=1
            else:m['action']='propose_increase'
            r=receipt(c,m,price,review_cn);self.assertEqual(r['system_receipt'],'rejected_model_mismatch');self.assertFalse(r['execution_authorized'])
    def test_fresh_price_recheck_overrides_old_model_permission(self):
        c=sample();m=review_cn(c,price);r=receipt(c,m,lambda _: {'proven':False,'reason':'expired'},review_cn);self.assertEqual(r['system_action'],'pause');self.assertEqual(r['system_receipt'],'rejected_model_mismatch');self.assertEqual(r['proposed_increment_minor'],0)
    def test_equal_ratio_reduction_does_not_false_reject(self):
        c=sample();m=review_cn(c,price)
        for location in [m['windows'][0]['mature'],m['cumulative']['mature']]:location['gross_roi']={'numerator':20,'denominator':3}
        self.assertEqual(receipt(c,m,price,review_cn)['system_receipt'],'offline_proposal')
    def test_partial_known_cannot_replace_null_total(self):
        c=sample();c['records'][1]['metrics']['fees_minor']=None;m=review_cn(c,price);self.assertEqual(receipt(c,m,price,review_cn)['system_receipt'],'blocked');m['cumulative']['mature']={};self.assertEqual(receipt(c,m,price,review_cn)['system_receipt'],'rejected_model_mismatch')

if __name__=='__main__':unittest.main()
