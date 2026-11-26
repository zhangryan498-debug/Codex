import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from live_price_evidence import PriceVerifier


class PriceProofTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'caller.json';self.path.write_bytes(b'{}')
        self.entry={'receiver':'douyin-shop-ops','sku':'SKU','quantity':1,'revision':1,'input_file':str(self.path),'input_sha256':hashlib.sha256(b'{}').hexdigest(),'run_dir':str(self.path.parent/'run'),'packet_id':'PACKET','status':'accepted','lower_bound_minor':100,'floor_minor':100}
        self.v=PriceVerifier({'GOOD':self.entry});self.case={'platform':'douyin','country':'CN','price_ref':'GOOD','target_sku':'SKU','quantity':1,'price_revision':1,'evaluation_time':'2026-11-11T15:00:00+08:00'}
    def tearDown(self):self.temp.cleanup()
    def proof(self,status='accepted'):return {'origin_price_receipt_sha256':'a'*64,'records':[{'packet_id':'PACKET','system_receipt':status}]}
    def test_actual_revalidation_overrides_cached_accepted_label(self):
        with patch('live_price_evidence.intake.revalidate_run',return_value=self.proof('rejected')):self.assertFalse(self.v(self.case)['proven'])
    def test_exact_original_sku_quantity_and_revision_required(self):
        for key,value in [('target_sku','OTHER'),('quantity',True),('quantity',2),('price_revision',False),('price_revision',2)]:
            c=dict(self.case,**{key:value})
            with patch('live_price_evidence.intake.revalidate_run',side_effect=AssertionError('must not query unmatched proof')):self.assertFalse(self.v(c)['proven'])
    def test_changed_source_bytes_fail_before_host(self):
        self.path.write_bytes(b'{"changed":1}')
        with patch('live_price_evidence.intake.revalidate_run',side_effect=AssertionError('must not query changed proof')):self.assertFalse(self.v(self.case)['proven'])
    def test_host_packet_must_match_uniquely(self):
        for rows in [[],self.proof()['records']*2]:
            with patch('live_price_evidence.intake.revalidate_run',return_value={'records':rows}):self.assertFalse(self.v(self.case)['proven'])
    def test_exact_equality_and_new_downstream_time(self):
        c=dict(self.case,evaluation_time='2026-11-11T15:00:00.000001+08:00')
        with patch('live_price_evidence.intake.revalidate_run',return_value=self.proof()) as checked:
            result=self.v(c)
        self.assertTrue(result['proven']);self.assertEqual(checked.call_args.args[2],c['evaluation_time']);self.assertEqual(result['release_status'],'not_authorized')
    def test_other_station_cannot_reuse_cn_receiver(self):
        c=dict(self.case,platform='tiktok',country='PH')
        with patch('live_price_evidence.intake.revalidate_run',side_effect=AssertionError('must not query foreign proof')):self.assertFalse(self.v(c)['proven'])

if __name__=='__main__':unittest.main()
