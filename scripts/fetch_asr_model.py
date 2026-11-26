"""Download the pinned public ASR model, with verified resumable byte ranges."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib,json,re,threading,time
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'interface/video-asr-model.lock.json'

def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  for chunk in iter(lambda:stream.read(4*1024*1024),b''):h.update(chunk)
 return h.hexdigest()

def valid(path,entry):
 if not path.is_file() or path.stat().st_size!=entry['size']:return False
 if entry.get('sha256'):return digest(path)==entry['sha256']
 raw=path.read_bytes()
 return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==entry['git_blob_sha1']

def check_content_range(value,start,end,total):
 match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',value or '')
 if not match or tuple(map(int,match.groups()))!=(start,end,total):
  raise ValueError('Server returned a different byte range; existing partial data preserved')

def fetch_model():
 spec=json.loads(LOCK.read_text(encoding='utf-8'));dest=ROOT/spec['model_directory']
 assert dest.resolve().is_relative_to((ROOT/'work').resolve())
 dest.mkdir(parents=True,exist_ok=True)
 receipt_path=dest/'download-receipt.json'
 state={'status':'downloading','repository':spec['repository'],'revision':spec['revision'],
        'started_at_utc':datetime.now(timezone.utc).isoformat(),'credentials_used':False,'media_uploaded':False,'files':[]}
 mutex=threading.Lock();last=[0.0]
 def save():
  temp=receipt_path.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(receipt_path)
 save()
 try:
  for entry in spec['files']:
   name=entry['name'];target=dest/name
   url=f"https://huggingface.co/{spec['repository']}/resolve/{spec['revision']}/{name}?download=true"
   if valid(target,entry):
    state['files'].append({'name':name,'bytes':entry['size'],'sha256':digest(target),'reused':True,'verified':True});save();continue
   if target.exists():raise ValueError('Unverified existing target; refusing to overwrite '+str(target))
   if entry['size']<8*1024*1024:
    with urlopen(Request(url,headers={'User-Agent':'DT-public-ASR/1.0'}),timeout=40) as response:raw=response.read(entry['size']+1)
    partial=dest/(name+'.partial');partial.write_bytes(raw)
    if not valid(partial,entry):raise ValueError('File checksum/size mismatch: '+name)
    partial.replace(target)
   else:
    parts_dir=dest/(name+'.parts');parts_dir.mkdir(exist_ok=True)
    part_size=16*1024*1024
    chunks=[(index,start,min(start+part_size,entry['size'])-1) for index,start in enumerate(range(0,entry['size'],part_size))]
    def progress(force=False):
     with mutex:
      if force or time.monotonic()-last[0]>15:
       got=sum(p.stat().st_size for p in parts_dir.glob('*.part'))
       state['current_file']={'name':name,'bytes':got,'expected_bytes':entry['size']};save()
       print(name,round(got/1024**2,1),'/',round(entry['size']/1024**2,1),'MiB',flush=True);last[0]=time.monotonic()
    def download_part(job):
     index,start,end=job;part=parts_dir/(str(index).zfill(4)+'.part');size=end-start+1
     for attempt in range(4):
      got=part.stat().st_size if part.exists() else 0
      if got==size:return
      if got>size:raise ValueError('Oversized existing partial file')
      try:
       headers={'User-Agent':'DT-public-ASR/1.0','Range':f'bytes={start+got}-{end}'}
       with urlopen(Request(url,headers=headers),timeout=40) as response:
        if response.status!=206:raise ValueError('Server did not honor Range')
        check_content_range(response.headers.get('Content-Range'),start+got,end,entry['size'])
        with part.open('ab') as handle:
         while got<size:
          block=response.read(min(1024*1024,size-got))
          if not block:break
          handle.write(block);got+=len(block);progress()
       if got==size:return
      except ValueError:raise
      except Exception:
       if attempt==3:raise
     raise ValueError('Incomplete ranged download')
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(download_part,chunks))
    progress(True)
    partial=dest/(name+'.partial')
    with partial.open('wb') as output:
     for index,_,_ in chunks:
      with (parts_dir/(str(index).zfill(4)+'.part')).open('rb') as source:
       for block in iter(lambda:source.read(4*1024*1024),b''):output.write(block)
    if not valid(partial,entry):raise ValueError('Final official model SHA256 mismatch')
    partial.replace(target)
    for part in parts_dir.glob('*.part'):part.unlink()
    parts_dir.rmdir()
   state['files'].append({'name':name,'bytes':entry['size'],'sha256':digest(target),'source_url':url,'verified':True});save()
   print('Verified',name,entry['size'],'bytes',flush=True)
  state.update(status='completed',completed_at_utc=datetime.now(timezone.utc).isoformat(),model_directory=str(dest.resolve()))
  state.pop('current_file',None);save();return state
 except Exception as error:
  state.update(status='failed',error_type=type(error).__name__,error=str(error)[:700]);save();raise

if __name__=='__main__':fetch_model()
