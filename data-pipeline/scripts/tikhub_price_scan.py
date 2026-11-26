#!/usr/bin/env python3
"""扫描 TikHub 全部抖音端点的计费属性，找出可用免费额度（allow_free_credit=true）或低价的端点。"""
import json
from pathlib import Path
import os
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
TOK = os.environ['TIKHUB_TOKEN']
SPEC = os.environ.get('TIKHUB_OPENAPI_SPEC', str(Path(__file__).resolve().parents[2] / 'work/tk_openapi.json'))
OUT = str(Path(__file__).resolve().parents[2] / 'work/tikhub_pricing_scan.json')
Path(OUT).parent.mkdir(parents=True, exist_ok=True)


def price(ep):
    url = 'https://api.tikhub.io/api/v1/tikhub/user/calculate_price?endpoint=' + \
        urllib.parse.quote(ep, safe='')
    r = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {TOK}', 'Accept': 'application/json', 'User-Agent': UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                d = json.loads(resp.read().decode('utf-8', 'replace'))
                return d.get('data') or {}
        except urllib.error.HTTPError as e:
            code = e.code
            if code == 429:
                time.sleep(3 + attempt * 4)
                continue
            return {'error': code}
        except Exception:
            time.sleep(2)
    return {'error': 'retry-exhausted'}


def main():
    d = json.load(open(SPEC))
    eps = [p for p in d['paths'] if p.startswith('/api/v1/douyin')]
    print(f'扫描 {len(eps)} 个抖音端点…', flush=True)
    rows = []
    t0 = time.time()
    for i, ep in enumerate(eps):
        info = price(ep)
        if info and 'base_price' in info:
            rows.append({'endpoint': ep, 'price': info.get('base_price'),
                         'free': info.get('allow_free_credit'),
                         'discount': info.get('allow_discount')})
        if (i + 1) % 40 == 0:
            print(f'  … {i+1}/{len(eps)}  {int(time.time()-t0)}s', flush=True)
        time.sleep(0.35)
    json.dump(rows, open(OUT, 'w'), ensure_ascii=False, indent=1)
    free = [r for r in rows if r.get('free')]
    print(f'\n完成：{len(rows)} 个端点有报价，用时 {int(time.time()-t0)}s')
    print(f'\n=== 接受免费额度的端点（{len(free)} 个）===')
    for r in sorted(free, key=lambda x: x['price'] or 0):
        print(f"  {r['endpoint']:70s} ${r['price']}")
    print('\n=== 最便宜的 25 个（全部）===')
    for r in sorted(rows, key=lambda x: x['price'] or 9)[:25]:
        print(f"  {r['endpoint']:70s} ${r['price']}  free={r['free']}")


if __name__ == '__main__':
    main()
