from copy import deepcopy
import unittest
from live_window_review_v6 import review_cn
from live_window_host_v3 import receipt
from test_live_window_review import sample,price

def outgoing():
    c=sample();c.update(target_sku='NEW',requested_action='switch');s=review_cn(c,price);m=deepcopy(s);w=m['windows'][0]
    old={'sku_id':c['current_sku'],'session_id':c['session_id'],'currency':c['currency'],'start':w['start'],'end':w['end'],'maturity':'mature','state':'complete','costs_through':w['end'],'event_ids':['start','end'],'ledger':deepcopy(w['mature'])}
    m['windows']=[{'start':w['start'],'end':w['end'],'sku_id':'NEW','mature':None,'estimated':None}];m['cumulative']={'mature':None,'estimated':None};m['separate_current_observation']=old
    return c,m

class SwitchLayoutTests(unittest.TestCase):
    def test_lossless_scoped_layout_no_raw_mutation(self):
        c,m=outgoing();before=deepcopy(m);r=receipt(c,m,price,review_cn);self.assertEqual(r['system_receipt'],'offline_proposal');self.assertTrue(r['layout_normalization']['applied']);self.assertEqual(m,before);self.assertFalse(r['execution_authorized'])
    def test_wrong_identity_time_or_profit_cannot_be_lifted(self):
        for k,v in [('sku_id','WRONG'),('session_id','WRONG'),('currency','PHP'),('end','2026-11-11T14:59:00+08:00')]:
            c,m=outgoing();m['separate_current_observation'][k]=v;r=receipt(c,m,price,review_cn);self.assertEqual(r['system_receipt'],'rejected_model_mismatch')
        c,m=outgoing();m['separate_current_observation']['ledger']['post_ad_profit_minor']+=1;self.assertEqual(receipt(c,m,price,review_cn)['system_receipt'],'rejected_model_mismatch')
    def test_target_old_profit_or_nonzero_increment_never_normalized(self):
        c,m=outgoing();m['windows'][0]['mature']=m['separate_current_observation']['ledger'];self.assertEqual(receipt(c,m,price,review_cn)['system_receipt'],'rejected_model_mismatch')
        c,m=outgoing();m['extra_target_claim']=dict(m['separate_current_observation'],sku_id='NEW');self.assertEqual(receipt(c,m,price,review_cn)['system_receipt'],'rejected_model_mismatch')
        c,m=outgoing();m['proposed_increment_minor']=1;self.assertEqual(receipt(c,m,price,review_cn)['system_receipt'],'rejected_model_mismatch')
    def test_partial_stale_cost_or_fake_source_cannot_be_lifted(self):
        for key,value in [('state','partial'),('costs_through','2026-11-11T14:59:00+08:00'),('event_ids',['invented'])]:
            c,m=outgoing();m['separate_current_observation'][key]=value;self.assertEqual(receipt(c,m,price,review_cn)['system_receipt'],'rejected_model_mismatch')
    def test_extra_target_window_cannot_inherit_old_performance(self):
        c,m=outgoing();m['extra_target_window']={'sku_id':'NEW','mature':m['separate_current_observation']['ledger'],'estimated':None};self.assertEqual(receipt(c,m,price,review_cn)['system_receipt'],'rejected_model_mismatch')
    def test_price_failure_and_unknown_inventory_still_prevent_switch(self):
        c,m=outgoing();self.assertEqual(receipt(c,m,lambda _: {'proven':False,'reason':'expired'},review_cn)['system_receipt'],'rejected_model_mismatch')
        c['inventory']['on_hand']=None;self.assertEqual(receipt(c,m,price,review_cn)['system_receipt'],'rejected_model_mismatch')

if __name__=='__main__':unittest.main()
