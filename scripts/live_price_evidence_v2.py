"""Original price caller identity and actual completed host, bound at review time."""
import hashlib
from pathlib import Path
import campaign_price_intake as intake

class PriceVerifier:
    def __init__(self,catalog):self.catalog=catalog
    def __call__(self,case):
        entry=self.catalog.get(case.get('price_ref'))
        def failed(reason):return {'proven':False,'reason':reason}
        if entry is None:return failed('unknown_price_reference')
        role={'douyin':'douyin-shop-ops','tiktok':'tiktok-shop-ops'}.get(case.get('platform'))
        if not role or entry['receiver']!=role:return failed('price_receiver_mismatch')
        path=Path(entry['input_file']);blob=path.read_bytes()
        if hashlib.sha256(blob).hexdigest()!=entry['input_sha256']:return failed('price_source_bytes_changed')
        source=intake.parse_bytes(blob)
        rows=[r for r in source['cases'] if r.get('packet',{}).get('packet_id')==entry['packet_id']]
        if len(rows)!=1:return failed('original_price_packet_not_unique')
        row=rows[0];a=row['authority'];original=row['source_input']
        actual=(a['sku'],original['quantity'],a['revision'])
        requested=(case.get('target_sku'),case.get('quantity'),case.get('price_revision'))
        catalog=(entry['sku'],entry['quantity'],entry['revision'])
        if any(type(v) is not int for v in (requested[1],requested[2],catalog[1],catalog[2],actual[1],actual[2])) or requested!=actual or catalog!=actual:return failed('original_sku_quantity_revision_mismatch')
        market=(case.get('platform'),case.get('country'),case.get('currency'))
        if market!=tuple(a[k] for k in ('platform','country','currency')) or market!=tuple(original[k] for k in ('platform','country','currency')) or source.get('receiver_role')!=role or row['packet'].get('receiver_role')!=role:return failed('original_market_or_receiver_mismatch')
        proof=intake.revalidate_run(path,role,case['evaluation_time'],Path(entry['run_dir']))
        records=[r for r in proof['records'] if r['packet_id']==entry['packet_id']]
        if len(records)!=1:return failed('price_packet_not_uniquely_bound')
        r=records[0]
        return {'proven':r['system_receipt']=='accepted','reason':'actual_price_host_accepted' if r['system_receipt']=='accepted' else 'actual_price_host_rejected_or_expired','packet_id':entry['packet_id'],'caller_sha256':entry['input_sha256'],'origin_price_receipt_sha256':proof['origin_price_receipt_sha256'],'evaluation_time':case['evaluation_time'],'lower_bound_minor':a['lower_bound_cents'],'floor_minor':a['floor_cents'],'target_sku':actual[0],'quantity':actual[1],'revision':actual[2],'evidence_kind':'actual_completed_shop_price_host','release_status':'not_authorized'}
