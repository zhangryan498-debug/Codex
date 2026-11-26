"""Download bounded product-detail image evidence; cache downloads and record hashes."""
import argparse
import json,hashlib,concurrent.futures
from pathlib import Path
from urllib.request import urlopen
from PIL import Image,ImageOps,ImageDraw,ImageFont
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input',type=Path,required=True)
parser.add_argument('--output-dir',type=Path,required=True)
parser.add_argument('--max-images',type=int,choices=range(1,41),default=40)
args=parser.parse_args()
root=args.output_dir;root.mkdir(parents=True,exist_ok=True)
out=root/'images';out.mkdir(exist_ok=True)
s=json.loads(args.input.read_text(encoding='utf-8-sig'))
jobs=[(p['platform'],p['product_id'],i,u) for p in s['products'] for i,u in enumerate(p.get('detail_image_urls',[])[:args.max_images])]
def get(j):
 platform,pid,i,url=j; file=out/f'{platform}-{pid}-{i:02}.jpg';r={'platform':platform,'product_id':pid,'index':i,'url':url,'local_path':str(file)}
 try:
  if not file.exists():
   with urlopen(url,timeout=35) as resp: data=resp.read(12*1024*1024+1)
   if len(data)>12*1024*1024: raise ValueError('image_too_large')
   file.write_bytes(data)
  with Image.open(file) as im: r['size']=list(im.size)
  r['sha256']=hashlib.sha256(file.read_bytes()).hexdigest();r['status']='ok'
 except Exception as e: r['status']='error';r['error']=type(e).__name__
 return r
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: rows=list(pool.map(get,jobs))
(root/'image-manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
print('images',len(rows),'success',sum(r['status']=='ok' for r in rows),flush=True)
if any(r['status']!='ok' for r in rows):
 print('Some images failed; inspect image-manifest.json before claiming coverage.',flush=True)
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',16)
for p in s['products']:
 group=[r for r in rows if r['product_id']==p['product_id'] and r['status']=='ok']
 if not group: continue
 board=Image.new('RGB',(5*250,((len(group)+4)//5)*280),'#e9eef3');draw=ImageDraw.Draw(board)
 for n,r in enumerate(group):
  with Image.open(r['local_path']) as im: thumb=ImageOps.contain(im.convert('RGB'),(240,246))
  x=(n%5)*250+5;y=(n//5)*280+25;board.paste(thumb,(x,y));draw.text((x,y-22),f"{r['index']:02} / {r['size'][0]}x{r['size'][1]}",font=font,fill='#111111')
 board.save(root/f"contact-{p['product_id']}.png")
