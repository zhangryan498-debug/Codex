"""Create traceable quality-keyword counts and Chinese word clouds from real reviews.

Inputs are collect_review_evidence.py's summary and optional Windows OCR output.
One review contributes at most once per keyword; this is not a defect-rate model.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re


# Normalized labels deliberately keep merchant claims out of review clouds.
RULES = [
    ('气味明显', 'risk', 'quality', r'有(?:点|好大)?(?:异味|味道|味)|刺鼻|难闻|浓郁橡胶味|味道.{0,4}(?:重|大)|臭|bau'),
    ('破裂／漏液／起皮', 'risk', 'quality', r'捏爆|起皮|裂缝|露馅|流汤|破(?:了|的)|就(?:坏|漏)|漏(?:了|液)|bocor|robek'),
    ('偏硬／难捏', 'risk', 'quality', r'特别硬|太硬|有点硬|很硬|硬邦邦|捏不动|keras'),
    ('质量／手感差', 'risk', 'quality', r'质量(?:太)?差|质感差|材质不是很好|手感不好|劣质胶'),
    ('表面脏', 'risk', 'quality', r'有点脏|kotor'),
    ('好玩／解压', 'positive', 'experience', r'好玩|解压|挺有意思'),
    ('手感好／好捏', 'positive', 'quality', r'手感(?:很|也)?好|好捏'),
    ('柔软', 'positive', 'quality', r'很软|lembut'),
    ('回弹／韧性', 'positive', 'quality', r'有韧劲|elastis'),
    ('清洗方便', 'positive', 'quality', r'好清洗|拿去洗的话'),
    ('无异味', 'positive', 'quality', r'没有异味|无异味|没(?:有)?味道|tidak\s+bau|gak\s+bau'),
    ('不漏液', 'positive', 'quality', r'不会漏液|没有漏液|不漏液|tidak\s+bocor'),
    ('外观认可', 'positive', 'appearance', r'漂亮|好看|颜色挺好|颜色透亮|cantik|lucu|menarik|elegan'),
    ('质量认可', 'positive', 'quality', r'质量(?:也)?不错|kualitas\s+produk\s*:\s*(?:bagus|mantap)'),
    ('香味（买家描述）', 'neutral', 'quality', r'wangi'),
    ('厚实（买家描述）', 'neutral', 'quality', r'tebal'),
    ('硅胶（买家描述）', 'neutral', 'material_opinion', r'silikon'),
    ('价格／性价比', 'neutral', 'price', r'价格实惠|物有所值|性价比高|harga\s+(?:promo|hoki)|7\s+ribu'),
    ('发货／服务认可', 'positive', 'fulfillment', r'发货速度(?:快|可以)|服务态度.{0,5}好|服务态度：好|挺快的|penjualnya\s+juga\s+ramah'),
]
NEGATIONS = [
    r'没有别人说的那么硬', r'没那么硬', r'不(?:是|太)?硬',
    r'没有异味', r'无异味', r'没(?:有)?味道',
    r'不会漏液', r'没有漏液', r'不漏液', r'不容易破', r'不会破',
    r'(?:tidak|gak)\s+(?:bau|bocor|keras)',
]
MATERIALS = [
    ('TPR', r'(?<![A-Z])TPR(?![A-Z])'), ('TPE', r'(?<![A-Z])TPE(?![A-Z])'), ('PU', r'(?<![A-Z])PU(?![A-Z])|polyurethane|poliuretan'),
    ('硅胶', r'硅胶|silicone|silikon'), ('海绵／泡沫', r'海绵|泡沫|sponge|busa'),
    ('麦芽糖填充（商家描述）', r'麦芽糖填充'),
]
COLORS = {'risk':'#bf413e','positive':'#16736c','neutral':'#3b679a'}


def normalized_text(text):
    text=text.lower()
    # Indonesian emphatic spelling: wangiiii -> wangi, bangeeet -> banget.
    return re.sub(r'([a-z])\1{2,}',r'\1',text)


def classify(text):
    original=normalized_text(text)
    risk_text=original
    for pattern in NEGATIONS:
        risk_text=re.sub(pattern,' ',risk_text)
    positive_text=re.sub(r'不好玩|不好捏|不解压|不柔软|tidak\s+lembut',' ',original)
    matches=[]
    for label,polarity,aspect,pattern in RULES:
        target=risk_text if polarity=='risk' else positive_text if polarity=='positive' else original
        match=re.search(pattern,target,flags=re.I)
        if match:
            matches.append({'keyword':label,'polarity':polarity,'aspect':aspect,'matched_phrase':match.group(0)})
    return matches


def is_usable(text):
    if text.strip() == '很好吃':
        return False
    return bool(re.search(r'[\u4e00-\u9fff]|[a-zA-Z]{2,}',text)) and not re.search(r'此用户没有填写|用户未填写',text)


def review_time(value):
    if isinstance(value,(int,float)) or (isinstance(value,str) and value.isdigit()):
        value=int(value)
        return datetime.fromtimestamp(value/(1000 if value>10**11 else 1),timezone.utc).isoformat()
    return str(value) if value else None


def analyze_product(product,ocr_rows):
    reviews=product.get('unique_reviews',[])
    # The collector already deduplicates, but imported summaries are guarded too.
    reviews=list({r['review_key']:r for r in reviews}.values())
    docs=[]; counts=Counter(); examples={}; polarity={}; aspects={}
    for row in reviews:
        usable=is_usable(row.get('text',''))
        hits=classify(row['text']) if usable else []
        docs.append({**row,'time':review_time(row.get('time')),'usable_text':usable,'keywords':hits})
        for hit in hits:
            k=hit['keyword']; counts[k]+=1;polarity[k]=hit['polarity'];aspects[k]=hit['aspect']
            examples.setdefault(k,[]).append({'review_key':row['review_key'],'quote':row['text'],'matched_phrase':hit['matched_phrase']})
    sources=[{'source_type':'merchant_description','source_path':f'description_blocks[{i}]','text':t}
             for i,t in enumerate(product.get('description_blocks',[]))]
    relevant_ocr=[r for r in ocr_rows if product['product_id'] in Path(r['image_path']).name]
    for row in relevant_ocr:
        # Chinese OCR inserts spaces between characters. Preserve the raw OCR as well.
        for i,line in enumerate(row.get('lines',[])):
            sources.append({'source_type':'merchant_image_ocr','source_path':row['image_path'],
                            'sha256':row['sha256'],'line':i,'raw_text':line,'text':re.sub(r'\s+','',line)})
    mentions=[]
    for source in sources:
        for label,pattern in MATERIALS:
            if re.search(pattern,source['text'],flags=re.I):
                mentions.append({'material_label':label,**source,'evidence_status':'merchant_claim'})
    times=sorted(t for r in reviews if (t:=review_time(r.get('time'))))
    quality_docs=[r for r in docs if any(h['aspect'] in {'quality','material_opinion'} for h in r['keywords'])]
    return {k:v for k,v in product.items() if k!='unique_reviews'} | {
        'sample_statistics':{'unique_reviews':len(reviews),'nonempty_text':sum(bool(r.get('text','').strip()) for r in reviews),
                             'usable_text':sum(r['usable_text'] for r in docs),'quality_keyword_reviews':len(quality_docs),
                             'risk_keyword_reviews':sum(any(h['polarity']=='risk' for h in r['keywords']) for r in docs),
                             'sample_star_distribution':dict(sorted(Counter(str(r.get('rating')) for r in reviews).items())),
                             'incentivized_reviews':sum(r.get('is_incentivized_review') is True for r in reviews),
                             'incentive_flag_known_reviews':sum(isinstance(r.get('is_incentivized_review'),bool) for r in reviews),
                             'time_min':times[0] if times else None,'time_max':times[-1] if times else None},
        'material_mentions':mentions,'description_and_ocr_sources':sources,'ocr_images_read':len(relevant_ocr),
        'keyword_frequencies':[{'keyword':k,'review_count':n,'polarity':polarity[k],'aspect':aspects[k],
                                'examples':examples[k]} for k,n in counts.most_common()], 'review_evidence':docs}


def render_panel(product,font_path):
    from PIL import Image, ImageDraw, ImageFont
    from wordcloud import WordCloud
    panel=Image.new('RGB',(1520,940),'#ffffff');draw=ImageDraw.Draw(panel)
    heading=ImageFont.truetype(font_path,38);body=ImageFont.truetype(font_path,25)
    s=product['sample_statistics'];title=product.get('display_name',product['product_id'])
    draw.text((44,28),title,font=heading,fill='#14283a')
    draw.text((44,90),f"去重 {s['unique_reviews']} 条 | 非空正文 {s['nonempty_text']} 条 | 质量关键词正文 {s['quality_keyword_reviews']} 条",font=body,fill='#4e5c69')
    warn=(f"展示样本仅 {s['unique_reviews']} 条；{s['incentivized_reviews']} 条带激励标记，不能外推整体质量" if product['platform']=='tiktok'
          else '最近若干页；各品时间跨度不同。关键词按评价条数计，不代表瑕疵率。')
    draw.text((44,134),warn,font=body,fill='#a24b27')
    entries=[x for x in product['keyword_frequencies'] if x['aspect'] in {'quality','experience','appearance','material_opinion'}]
    frequencies={x['keyword']:x['review_count'] for x in entries}
    mapping={x['keyword']:COLORS[x['polarity']] for x in entries}
    if frequencies:
        cloud=WordCloud(font_path=str(font_path),width=1432,height=520,background_color='white',random_state=41,
                        max_words=40,prefer_horizontal=1,relative_scaling=.7,repeat=False,min_font_size=24,
                        color_func=lambda word,**_:mapping[word]).generate_from_frequencies(frequencies)
        panel.paste(cloud.to_image(),(44,194))
    else:
        draw.text((60,350),'未获取到可分析的评价正文',font=heading,fill='#778899')
    draw.text((44,746),'红：风险反馈    绿：正向反馈    蓝：买家描述／主观材质称谓',font=body,fill='#263e52')
    # Exact counts accompany the cloud; independently fitted cloud sizes cannot be compared across products.
    for i,x in enumerate(entries[:8]):
        col=i%2; row=i//2
        draw.text((44+col*720,790+row*32),f"{x['keyword']}  {x['review_count']} 条",font=body,fill=COLORS[x['polarity']])
    return panel


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--ocr',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--font',type=Path,default=Path('C:/Windows/Fonts/msyh.ttc'))
    parser.add_argument('--labels',type=Path,help='Optional JSON mapping product IDs to display names')
    args=parser.parse_args()
    source=json.loads(args.input.read_text(encoding='utf-8-sig'))
    ocr=json.loads(args.ocr.read_text(encoding='utf-8-sig')) if args.ocr else []
    labels=json.loads(args.labels.read_text(encoding='utf-8-sig')) if args.labels else {}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    products=[]
    for product in source['products']:
        product={**product,'display_name':labels.get(product['product_id'],product['product_id'])}
        result=analyze_product(product,ocr);products.append(result)
        render_panel(result,str(args.font)).save(args.output_dir/f"词云-{product['platform']}-{product['product_id']}.png")
    payload={'schema_version':'1.0','generated_utc':datetime.now(timezone.utc).isoformat(),
             'method':'review_document_frequency; rule-based phrase grouping; negation aware; merchant text excluded from review clouds',
             'limits':['Direction only; non-random review samples','Cloud font sizes fit each panel independently','Material mentions are merchant claims or buyer opinions'],
             'products':products}
    target=args.output_dir/'商详材质与评价关键词-计算证据.json'
    target.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps([{'id':p['product_id'],**p['sample_statistics'],'material_labels':sorted({m['material_label'] for m in p['material_mentions']}),
                       'keywords':{r['keyword']:r['review_count'] for r in p['keyword_frequencies']}} for p in products],ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
