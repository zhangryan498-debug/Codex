"""Read original actual shop-host price credentials at each live review instant."""
import hashlib
from pathlib import Path
import campaign_price_intake as intake

class PriceVerifier:
    def __init__(self,catalog):self.catalog=catalog
    def __call__(self,case):
        ref=case.get('price_ref');entry=self.catalog.get(ref)
        if entry is None:return {'proven':False,'reason':'unknown_price_reference'}
        target=case.get('target_sku');quantity=case.get('quantity');revision=case.get('price_revision')
        expected_role={'douyin':'douyin-shop-ops','tiktok':'tiktok-shop-ops'}.get(case.get('platform'))
        if entry['receiver']!=expected_role:return {'proven':False,'reason':'price_receiver_mismatch'}
        if type(quantity) is not int or type(revision) is not int or (target,quantity,revision)!=(entry['sku'],entry['quantity'],entry['revision']):return {'proven':False,'reason':'target_sku_quantity_or_revision_mismatch'}
        path=Path(entry['input_file']);blob=path.read_bytes()
        if hashlib.sha256(blob).hexdigest()!=entry['input_sha256']:return {'proven':False,'reason':'price_source_bytes_changed'}
        proof=intake.revalidate_run(path,entry['receiver'],case['evaluation_time'],Path(entry['run_dir']))
        records=[r for r in proof['records'] if r['packet_id']==entry['packet_id']]
        if len(records)!=1:return {'proven':False,'reason':'price_packet_not_uniquely_bound'}
        r=records[0]
        return {'proven':r['system_receipt']=='accepted','reason':'actual_price_host_accepted' if r['system_receipt']=='accepted' else 'actual_price_host_rejected_or_expired','packet_id':entry['packet_id'],'caller_sha256':entry['input_sha256'],'origin_price_receipt_sha256':proof['origin_price_receipt_sha256'],'evaluation_time':case['evaluation_time'],'lower_bound_minor':entry['lower_bound_minor'],'floor_minor':entry['floor_minor'],'evidence_kind':'actual_completed_shop_price_host','release_status':'not_authorized'}
