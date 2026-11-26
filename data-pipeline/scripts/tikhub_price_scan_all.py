#!/usr/bin/env python3
"""扫描 TikHub 全平台端点的计费属性，产出「充值要多少钱」的准数。

⚠️ 计费结论（2026-11-05 实测）：
- `calculate_price` 本身**不扣费**（连打 3 次，`balance`/`free_credit` 纹丝不动），
  响应里的 "will incur a charge" 是通用文案，不针对该接口。
- 因此**整表扫描是零成本的**，可以放心把全部端点扫一遍。

用法：
    export TIKHUB_TOKEN=xxx
    python3 scripts/tikhub_price_scan_all.py --family tiktok
    python3 scripts/tikhub_price_scan_all.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
SPEC = pathlib.Path(os.environ.get('TIKHUB_OPENAPI_SPEC', str(pathlib.Path(__file__).resolve().parents[2] / 'work/tk_openapi.json')))
OUT = pathlib.Path(__file__).resolve().parents[2] / 'work/tikhub_pricing_all.json'


def price(ep: str) -> dict:
    url = ('https://api.tikhub.io/api/v1/tikhub/user/calculate_price?endpoint='
           + urllib.parse.quote(ep, safe=''))
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {os.environ["TIKHUB_TOKEN"]}',
        'Accept': 'application/json', 'User-Agent': UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode('utf-8', 'replace')).get('data') or {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3 + attempt * 4)
                continue
            return {'error': e.code}
        except Exception:
            time.sleep(2)
    return {'error': 'retry-exhausted'}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--family', default='tiktok',
                    help='家族前缀：tiktok / douyin / instagram …，或 all')
    ap.add_argument('--all', action='store_true', help='扫全部 1058 个端点')
    ap.add_argument('--out', help='输出 JSON 路径')
    args = ap.parse_args()

    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    paths = list(spec['paths'])
    if args.all:
        eps = paths
    else:
        eps = [p for p in paths if p.startswith(f'/api/v1/{args.family}')]

    out = pathlib.Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    jl = out.with_suffix('.jsonl')

    # 断点续扫：已扫过的端点直接跳过，进程被超时打断也不丢进度
    done = set()
    if jl.exists():
        for line in jl.read_text(encoding='utf-8').splitlines():
            if line.strip():
                try:
                    done.add(json.loads(line)['endpoint'])
                except Exception:
                    pass
        print(f'断点续扫：已有 {len(done)} 条记录', flush=True)

    todo = [p for p in eps if p not in done]
    print(f'扫描 {len(todo)} 个 {args.all and "全部" or args.family} 端点…（calculate_price 不计费）', flush=True)

    rows, t0 = [], time.time()
    with jl.open('a', encoding='utf-8') as fh:
        for i, ep in enumerate(todo):
            info = price(ep)
            if 'base_price' in info:
                rec = {'endpoint': ep, 'price': info.get('base_price'),
                       'free': bool(info.get('allow_free_credit')),
                       'discount': bool(info.get('allow_discount')),
                       'request_per_day': info.get('request_per_day')}
                rows.append(rec)
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
                fh.flush()
            if (i + 1) % 30 == 0:
                print(f'  … {i+1}/{len(todo)}  {int(time.time()-t0)}s', flush=True)
            time.sleep(1.2)   # TikHub 限流 1 req/s/路由，留余量

    # 汇总
    allrows = []
    for line in jl.read_text(encoding='utf-8').splitlines():
        if line.strip():
            try:
                allrows.append(json.loads(line))
            except Exception:
                pass
    out.write_text(json.dumps(allrows, ensure_ascii=False, indent=1), encoding='utf-8')

    free = [r for r in allrows if r['free']]
    print(f'\n完成：{len(allrows)} 个端点有报价，用时 {int(time.time()-t0)}s → {out}')
    print(f'\n=== 接受免费额度的端点：{len(free)}/{len(allrows)} ===')
    by_price: dict = {}
    for r in free:
        by_price.setdefault(r['price'], []).append(r['endpoint'])
    for p in sorted(by_price):
        print(f'  ${p}: {len(by_price[p])} 个')
    print('\n=== 非免费端点按价格 ===')
    nf: dict = {}
    for r in allrows:
        if not r['free']:
            nf.setdefault(r['price'], []).append(r['endpoint'])
    for p in sorted(nf):
        print(f'  ${p}: {len(nf[p])} 个')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
