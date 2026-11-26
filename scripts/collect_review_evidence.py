"""Collect bounded, cached Just One review pages and TikTok product descriptions."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen


def normalize_comment(row):
    if 'review_key' in row:
        return row
    content = (row.get('Content') or row.get('content') or row.get('review_text') or '').strip()
    original_id = str(row.get('Id') or row.get('review_id') or '')
    identity = original_id or json.dumps([row.get('CommentTime'), content, row.get('Sku')], ensure_ascii=False)
    return {'review_key': hashlib.sha256(identity.encode()).hexdigest()[:20],
            'text': content, 'rating': row.get('RankProduct', row.get('rating', row.get('review_rating'))),
            'time': row.get('CommentTime', row.get('create_time', row.get('review_time'))),
            'sku': row.get('Sku', row.get('sku_specification')),
            'is_verified_purchase': row.get('is_verified_purchase'),
            'is_incentivized_review': row.get('is_incentivized_review')}


def normalize_tiktok_detail(response):
    """Parse rendered page components, not just the outer product_data wrapper."""
    components = (response.get('data') or {}).get('product_data',{}).get('page_config',{}).get('components_map',[])
    if isinstance(components,dict):
        components = list(components.values())
    component = next((c.get('component_data',{}) for c in components
                      if c.get('component_name') == 'product_info'),{})
    product = component.get('product_info') or {}
    descriptions=[]; images=[]
    for block in product.get('desc_blocks',[]):
        if block.get('type') == 'text' and block.get('text'):
            descriptions.append(block['text'])
        elif block.get('type') in {'ul','ol'}:
            descriptions.extend(x for x in block.get('content',[]) if isinstance(x,str))
        elif block.get('type') == 'image':
            urls=block.get('image',{}).get('url_list',[])
            if urls:
                images.append(urls[0])
    reviews = component.get('review_info') or {}
    shop=component.get('shop_info') or {}
    shop_profile={k:shop[k] for k in ['seller_id','shop_name','region','shop_rating','shop_link','sold_count',
                                     'on_sell_product_count','followers_count','video_count','shop_exp_scores'] if k in shop}
    rows = [normalize_comment(row) for row in reviews.get('product_reviews',[])]
    return {'title':product.get('title'), 'description_blocks':descriptions, 'detail_image_urls':images,
            'shop_profile':shop_profile,
            'review_summary':reviews.get('review_ratings',{}), 'review_total':reviews.get('total_reviews'),
            'reviews_have_more':reviews.get('has_more'), 'review_sample_type':'page_displayed_reviews',
            'unique_reviews':list({row['review_key']:row for row in rows}.values())}


def redact(value, token=''):
    if isinstance(value, dict):
        blocked = {'token', 'accesstoken', 'authorization', 'username', 'useravatar', 'nickname',
                   'buyer', 'userinfo', 'userid', 'uid', 'avatar', 'authorid', 'order', 'ordersnapshot',
                   'reviewerid','reviewername','revieweravatarurl'}
        return {key: redact(item, token) for key,item in value.items()
                if key.lower().replace('_', '') not in blocked}
    if isinstance(value, list):
        return [redact(item, token) for item in value]
    return value.replace(token, '[REDACTED]') if token and isinstance(value, str) else value


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--douyin-id', action='append', default=[])
    parser.add_argument('--tiktok-id', action='append', default=[])
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--seed-directory', type=Path)
    parser.add_argument('--max-pages', type=int, choices=range(1, 6), default=5)
    parser.add_argument('--region', default='ID', choices=['ID','TH','VN','MY','PH'])
    parser.add_argument('--cached-only', action='store_true', help='Rebuild normalized evidence without credentials or requests')
    args = parser.parse_args()
    if len(args.douyin_id) > 2 or len(args.tiktok_id) > 2 or not (args.douyin_id or args.tiktok_id):
        parser.error('Select one to two products per platform')
    token = '' if args.cached_only else (os.environ.get('JUSTONE_API_TOKEN') or getpass.getpass('Just One credential (hidden): ').strip())
    if not token and not args.cached_only:
        raise SystemExit('Missing Just One credential')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stopped_endpoints = set()
    request_count = 0

    def collect(name, endpoint, params):
        nonlocal request_count
        path = args.output_dir / (name+'.json')
        if path.exists():
            cached=redact(json.loads(path.read_text(encoding='utf-8-sig')),token)
            if cached.get('endpoint') != endpoint or cached.get('params') != params:
                raise ValueError('Cache parameters differ: '+str(path))
            write_json(path,cached)
            return cached
        if args.cached_only:
            return {'endpoint':endpoint,'params':params,'status':'cache_missing'}
        if endpoint in stopped_endpoints:
            return {'endpoint': endpoint, 'params':params, 'status':'skipped_after_account_error'}
        result = {'endpoint':endpoint,'params':params,'fetched_at_utc':datetime.now(timezone.utc).isoformat()}
        try:
            request_count += 1
            url = 'https://api.justoneapi.com/api/' + endpoint + '?' + urlencode({'token':token,**params})
            with urlopen(url, timeout=120) as response:
                data = json.load(response)
            if endpoint.endswith('get-item-comments/v1') and data.get('code') == 0:
                body = data.get('data') or {}
                comments = body.get('Comments') or []
                data['data'] = {'Comments':[normalize_comment(row) for row in comments],
                                'Count':body.get('Count'), 'HasMore':body.get('HasMore')}
            result['response'] = redact(data, token)
            if data.get('code') in {100, 101, 600, 601, 602, 303}:
                stopped_endpoints.add(endpoint)
        except Exception as error:
            result['error'] = type(error).__name__
        write_json(path, result)
        print(name, 'code',result.get('response',{}).get('code'),result.get('error',''),flush=True)
        return result

    products=[]
    for product_id in args.douyin_id:
        rows=[]; pages=[]
        detail=None
        if args.seed_directory:
            for path in args.seed_directory.glob('*-detail.json'):
                seed=json.loads(path.read_text(encoding='utf-8-sig'))
                if str(seed.get('params',{}).get('itemId')) == product_id:
                    detail=seed
                    break
            for path in args.seed_directory.glob('*-reviews.json'):
                seed=json.loads(path.read_text(encoding='utf-8-sig'))
                if str(seed.get('params',{}).get('itemId')) == product_id and seed.get('response',{}).get('code')==0:
                    data=seed['response']['data']
                    rows.extend(normalize_comment(row) for row in data.get('Comments',[]))
                    pages.append({'page':1,'source':str(path),'status':'seed_cache','fetched_at_utc':seed.get('fetched_at_utc'),
                                  'reported_total':data.get('Count')})
        for page in range(2 if pages else 1, args.max_pages+1):
            result=collect('dy-'+product_id+'-reviews-'+str(page),'douyin-ec/get-item-comments/v1',{'itemId':product_id,'page':str(page)})
            response=result.get('response',{})
            pages.append({'page':page,'code':response.get('code'),'error':result.get('error'),'status':result.get('status'),
                          'fetched_at_utc':result.get('fetched_at_utc'),'reported_total':(response.get('data') or {}).get('Count')})
            if response.get('code') != 0:
                break
            data=response.get('data') or {}
            comments=data.get('Comments') or []
            new={row['review_key'] for row in comments}-{row['review_key'] for row in rows}
            rows.extend(comments)
            if not comments or not new or data.get('HasMore') not in (True,1,'1'):
                break
        unique={row['review_key']:row for row in rows}
        if detail is None:
            detail=collect('dy-'+product_id+'-detail','douyin-ec/get-item-detail/v2',{'itemId':product_id})
        base=(detail.get('response',{}).get('data') or {}).get('model',{}).get('product',{}).get('product_base',{})
        products.append({'platform':'douyin','product_id':product_id,'pages':pages,
                         'title':base.get('title'),'description_blocks':[], 'detail_image_urls':base.get('big_imgs',[]),
                         'detail_source':detail.get('endpoint'), 'detail_fetched_at_utc':detail.get('fetched_at_utc'),
                         'review_total':next((p['reported_total'] for p in sorted(pages,key=lambda p:p.get('fetched_at_utc') or '',reverse=True) if p.get('reported_total') is not None),None),
                         'raw_row_count':len(rows),'unique_reviews':list(unique.values())})
    for product_id in args.tiktok_id:
        result=collect('tk-'+product_id+'-detail','tiktok-shop/get-product-detail/v1',{'productId':product_id,'region':args.region})
        products.append({'platform':'tiktok','region':args.region,'product_id':product_id,'code':result.get('response',{}).get('code'),
                         'fetched_at_utc':result.get('fetched_at_utc'), **normalize_tiktok_detail(result.get('response',{})),
                         'error':result.get('error'),'status':result.get('status')})
    write_json(args.output_dir/'collection-summary.json',{'actual_new_requests':request_count,'products':products})
    print('Saved bounded collection summary; requests:',request_count,flush=True)


if __name__ == '__main__':
    main()
