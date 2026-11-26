from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from live_window_review_v3 import review_cn,MONEY
from live_price_evidence_v2 import PriceVerifier
from test_live_window_review import sample,price,START,END

class WindowV2Tests(unittest.TestCase):
    def test_intermediate_raw_counter_reset_blocks(self):
        c=sample();middle=deepcopy(c['records'][1]);middle.update(event_id='middle',end='2026-11-11T14:57:00+08:00',costs_through='2026-11-11T14:57:00+08:00');middle['metrics']['buyer_paid_minor']=13000;c['records'].insert(1,middle)
        r=review_cn(c,price);self.assertEqual(r['action'],'hold');self.assertIsNone(r['windows'][0]['mature']);self.assertIn('cumulative_intermediate_reset_or_conflict',r['windows'][0]['blocking_reasons'])
    def test_partial_intermediate_known_counter_reset_still_blocks(self):
        for key,value in [('state','partial'),('costs_through',START),('metrics',None)]:
            c=sample();m=deepcopy(c['records'][1]);m.update(event_id='partial-mid',end='2026-11-11T14:57:00+08:00',costs_through='2026-11-11T14:57:00+08:00');m['metrics']['buyer_paid_minor']=13000
            if key=='metrics':m['metrics']['fees_minor']=None
            else:m[key]=value
            c['records'].append(m);self.assertEqual(review_cn(c,price)['action'],'hold')
    def test_timestamp_format_and_offset_equivalence(self):
        c=sample();c['records'][0]['end']='2026-11-11T06:55:00Z';c['records'][1]['end']='2026-11-11T15:00:00.000+08:00';r=review_cn(c,price);self.assertEqual(r['action'],'propose_continue');self.assertEqual(r['cumulative']['mature']['post_ad_profit_minor'],800)
    def test_semantic_same_cutoff_not_false_conflict(self):
        c=sample();d=deepcopy(c['records'][1]);d['event_id']='same-cutoff';d['end']='2026-11-11T07:00:00Z';c['records'].append(d);self.assertEqual(review_cn(c,price)['action'],'propose_continue')
    def test_duplicate_expected_window_counted_once(self):
        c=sample();c['expected_windows']*=2;r=review_cn(c,price);self.assertEqual(len(r['windows']),1);self.assertEqual(r['cumulative']['mature']['post_ad_profit_minor'],800);self.assertEqual(r['action'],'propose_continue')
    def test_overlapping_windows_never_sum(self):
        c=sample();c['expected_windows'].append({'start':'2026-11-11T14:56:00+08:00','end':END});r=review_cn(c,price);self.assertIsNone(r['cumulative']['mature']);self.assertEqual(r['action'],'hold');self.assertIn('overlapping_expected_windows',r['data_errors'])
    def test_refund_negative_net_is_not_raw_counter_reset(self):
        c=sample();c['records'][1]['metrics']['refunds_minor']=5000;r=review_cn(c,price);self.assertEqual(r['action'],'propose_continue');self.assertLess(r['cumulative']['mature']['post_ad_profit_minor'],0)
    def test_estimated_reset_does_not_invalidate_mature(self):
        c=sample();estimated=deepcopy(c['records']);estimated[0]['metrics']['buyer_paid_minor']=13000
        for i,r in enumerate(estimated):r.update(event_id='estimated'+str(i),maturity='estimated')
        c['records']+=estimated;r=review_cn(c,price);self.assertEqual(r['action'],'propose_continue');self.assertIsNone(r['cumulative']['estimated'])

class PriceV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'caller.json'
        source={'receiver_role':'douyin-shop-ops','cases':[{'authority':{'platform':'douyin','country':'CN','currency':'CNY','sku':'A','revision':2,'lower_bound_cents':123,'floor_cents':120},'source_input':{'platform':'douyin','country':'CN','currency':'CNY','quantity':3},'packet':{'packet_id':'P','receiver_role':'douyin-shop-ops'}}]}
        self.path.write_text(json.dumps(source),encoding='utf8');self.entry={'receiver':'douyin-shop-ops','sku':'A','quantity':3,'revision':2,'input_file':str(self.path),'input_sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),'run_dir':str(self.path.parent/'run'),'packet_id':'P','lower_bound_minor':999,'floor_minor':0}
        self.case={'platform':'douyin','country':'CN','currency':'CNY','target_sku':'A','quantity':3,'price_revision':2,'price_ref':'P','evaluation_time':END}
    def tearDown(self):self.tmp.cleanup()
    def test_actual_authority_not_redundant_catalog_money(self):
        with patch('live_price_evidence_v2.intake.revalidate_run',return_value={'records':[{'packet_id':'P','system_receipt':'accepted'}],'origin_price_receipt_sha256':'a'*64}):
            r=PriceVerifier({'P':self.entry})(self.case)
        self.assertTrue(r['proven']);self.assertEqual((r['lower_bound_minor'],r['floor_minor']),(123,120))
    def test_catalog_relabel_attack_blocked_before_host(self):
        for field,target in [('sku','target_sku'),('quantity','quantity'),('revision','price_revision')]:
            e=deepcopy(self.entry);c=deepcopy(self.case);value='B' if field=='sku' else 4;e[field]=value;c[target]=value
            with patch('live_price_evidence_v2.intake.revalidate_run',side_effect=AssertionError('must stop before host')):self.assertFalse(PriceVerifier({'P':e})(c)['proven'])
    def test_wrong_country_currency_and_bool_revision_block(self):
        for k,v in [('country','PH'),('currency','PHP'),('price_revision',True)]:
            c=dict(self.case,**{k:v})
            with patch('live_price_evidence_v2.intake.revalidate_run',side_effect=AssertionError('must stop before host')):self.assertFalse(PriceVerifier({'P':self.entry})(c)['proven'])

if __name__=='__main__':unittest.main()
