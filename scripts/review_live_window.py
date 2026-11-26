"""Finite, read-only-source offline window calculation; no model or business I/O."""
import argparse
from pathlib import Path
from live_window_queue import read,dump,sha
from live_price_evidence_v2 import PriceVerifier

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--platform',required=True,choices=('douyin','tiktok-ph'));ap.add_argument('--input',required=True,type=Path);ap.add_argument('--catalog',required=True,type=Path);ap.add_argument('--output',required=True,type=Path);a=ap.parse_args()
    if a.platform=='douyin':
        from live_window_review_v6 import review_cn as review
        adapter='live_window_review_v6.py'
    else:
        from live_window_ph_v3 import review_ph as review
        adapter='live_window_ph_v3.py'
    source=read(a.input);rows=source if isinstance(source,list) else source['cases'] if 'cases' in source else [source]
    if len(rows)!=len({c['case_id'] for c in rows}):raise ValueError('Duplicate case_id')
    verifier=PriceVerifier(read(a.catalog));result=[review(c,verifier) for c in rows]
    dump(a.output,{'cases':result,'input_sha256':sha(a.input),'catalog_sha256':sha(a.catalog),'source_sha256':{n:sha(Path(__file__).parent/n) for n in set((adapter,'live_window_review_v6.py','live_price_evidence_v2.py','review_live_window.py'))},'model_called':False,'scope':'synthetic_offline','release_status':'not_authorized','execution_authorized':False})
    print({'calculated_cases':len(result),'model_calls':0,'business_actions':0})
