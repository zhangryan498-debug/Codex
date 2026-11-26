"""Finite delivery hashes; excludes existing client stores and all credentials."""
import argparse
from pathlib import Path
from datetime import datetime,timezone
from live_window_queue import read,dump,sha

def freeze(root):
    base=root/'work/live-window-ph-20261111';files=set()
    for directory in (root/'work/live-window-cn-20261111',base):
        files.update(p for p in directory.rglob('*') if p.is_file() and p.name not in ('delivery-manifest.json','delivery-verification.json'))
    for pattern in ('live_window*.py','live_price_evidence*.py','accept_live*.py','test_live*.py','replay_live_windows.py','review_live_window.py','collect_live_delivery.py','render_live_acceptance.py','freeze_live_delivery.py'):
        files.update((root/'scripts').glob(pattern))
    files.update(root/p for p in ('docs/douyin-live-ops-window-contract.json','docs/tiktok-live-ops-window-contract.json','docs/直播逐窗离线复盘与换品工作流.md','docs/双平台直播逐窗离线开发与独立验收-2026-11-11.md'))
    earlier=[]
    for path in (root/'work/compound-ledger-v3-20261111/delivery-manifest.json',root/'work/offline-role-queue-20261111/delivery-manifest.json'):
        expected=read(path)['files']
        if any(sha(root/k)!=v for k,v in expected.items()):raise ValueError('Earlier accepted evidence changed')
        earlier.append({'path':str(path.relative_to(root)),'sha256':sha(path),'file_hashes_verified':len(expected)})
    manifest={'generated_at':datetime.now(timezone.utc).isoformat(),'files':{str(p.relative_to(root)):sha(p) for p in sorted(files)},'earlier_acceptance_preservation':earlier,'scope':'synthetic_offline','release_status':'not_authorized','actual_business_actions':0}
    dump(base/'delivery-manifest.json',manifest)
    if any(sha(root/k)!=v for k,v in manifest['files'].items()):raise ValueError('Delivery bytes changed during freeze')
    dump(base/'delivery-verification.json',{'manifest_sha256':sha(base/'delivery-manifest.json'),'verified_file_count':len(files),'mismatches':[],'earlier_acceptance_preservation':earlier,'scope':'synthetic_offline','release_status':'not_authorized'})
    print({'verified_delivery_files':len(files),'previous':earlier})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--project',required=True,type=Path);a=p.parse_args();freeze(a.project.resolve())
