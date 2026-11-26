"""Pinned historical aggregate reader; never opens archives or authorizes execution."""
import argparse, hashlib, json, re, stat, unicodedata
from pathlib import Path, PurePosixPath

STATES={'passed','failed','pending','not_assessed'}
LIMITATIONS={'offline_only','not_current_acceptance','no_business_execution','semantic_pending','retry_not_first_attempt','hash_match_not_acceptance','archive_contents_not_read','historical_only'}

def require(ok,message):
    if not ok: raise ValueError(message)
def exact(obj,keys):
    require(type(obj) is dict and set(obj)==set(keys),'Invalid object fields')
def sha(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None,'Invalid SHA256')
def label(value):
    require(type(value) is str and re.fullmatch('[A-Za-z0-9_.-]{1,100}',value) is not None,'Invalid public identifier')
def pairs(rows):
    out={}
    for k,v in rows:
        require(k not in out,'Duplicate JSON key');out[k]=v
    return out
def relative_parts(relative):
    require(type(relative) is str and len(relative)<=500 and '\\' not in relative and ':' not in relative,'Unsafe relative path')
    require(not any(unicodedata.category(c) in ('Cc','Cf','Cs') for c in relative),'Unsafe path characters')
    parts=relative.split('/')
    require(all(p and p not in ('.','..') and p==p.strip() and not p.endswith('.') for p in parts),'Unsafe relative path components')
    require(not PurePosixPath(relative).is_absolute(),'Absolute path forbidden')
    return parts

def safe_path(root,relative):
    parts=relative_parts(relative)
    root=Path(root).absolute();current=root
    # Reject reparse points in the entire root ancestry as well as the target chain.
    for item in list(reversed(root.parents))+[root]:
        st=item.lstat();require(not stat.S_ISLNK(st.st_mode) and not getattr(st,'st_file_attributes',0)&0x400,'Reparse/symlink forbidden')
    for part in parts:
        current=current/part;st=current.lstat()
        require(not stat.S_ISLNK(st.st_mode) and not getattr(st,'st_file_attributes',0)&0x400,'Reparse/symlink forbidden')
    require(current.is_file() and current.resolve().is_relative_to(root.resolve()),'Not a contained file')
    return current
def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def counts(obj):
    exact(obj,('total','passed','failed','pending'))
    require(all(type(v) is int and 0<=v<=1000000 for v in obj.values()),'Invalid count')
    require(obj['passed']+obj['failed']+obj['pending']==obj['total'],'Inconsistent totals')
def validate(data):
    exact(data,('kind','schema_version','is_current_runtime_acceptance','release_decision','rounds','artifacts','runtime_files','limitations'))
    require(data['kind']=='historical_offline_acceptance' and data['schema_version']=='1.0','Unknown kind/version')
    require(data['is_current_runtime_acceptance'] is False and data['release_decision']=='not_authorized','Historical permissions cannot be elevated')
    require(type(data['rounds']) is list and 1<=len(data['rounds'])<=100,'Invalid rounds')
    seen=set()
    for r in data['rounds']:
        exact(r,('round_id','engineering','semantic','first_attempt','retries'));label(r['round_id'])
        require(r['round_id'] not in seen,'Duplicate round');seen.add(r['round_id'])
        require(r['engineering'] in STATES and r['semantic'] in STATES,'Unknown status')
        counts(r['first_attempt']);counts(r['retries'])
        if r['engineering']=='passed':require(r['first_attempt']['total']>0 and r['first_attempt']['pending']==0 and r['retries']['pending']==0 and r['first_attempt']['passed']+r['retries']['passed']>=r['first_attempt']['total'],'Passed engineering requires completed attempts')
    for key in ('artifacts','runtime_files'):
        require(type(data[key]) is list and len(data[key])<=200,'Invalid pointer count')
    seen=set()
    for a in data['artifacts']:
        exact(a,('artifact_id','library_id','version','relative_path','sha256'));label(a['artifact_id'])
        require(a['artifact_id'] not in seen,'Duplicate artifact');seen.add(a['artifact_id'])
        require(type(a['library_id']) is str and re.fullmatch('libfile_[A-Za-z0-9_-]{1,200}',a['library_id']) is not None,'Invalid Library ID')
        require(type(a['version']) is int and a['version']>=0,'Invalid artifact version')
        require((a['relative_path'] is None)==(a['sha256'] is None),'Incomplete local pointer')
        if a['relative_path'] is not None:
            require(type(a['relative_path']) is str and a['relative_path'].startswith('artifacts/') and Path(a['relative_path']).suffix in ('.zip','.json','.md','.pdf','.docx'),'Artifact must be supported file under artifacts/');relative_parts(a['relative_path']);sha(a['sha256'])
    seen=set()
    for r in data['runtime_files']:
        exact(r,('relative_path','sha256'));sha(r['sha256'])
        require(type(r['relative_path']) is str and r['relative_path'] not in seen,'Duplicate/invalid runtime pointer');seen.add(r['relative_path']);relative_parts(r['relative_path'])
    require(type(data['limitations']) is list and len(data['limitations'])<=30 and all(type(x) is str and 1<=len(x)<=1000 and not any(unicodedata.category(c) in ('Cc','Cf','Cs') for c in x) for x in data['limitations']),'Invalid reviewed aggregate limitation')

def query(index,archive_root,expected_index_sha,*,runtime_root=None):
    sha(expected_index_sha)
    path=safe_path(archive_root,index)
    require(path.stat().st_size<=262144,'Index exceeds 256KiB')
    raw=path.read_bytes();require(hashlib.sha256(raw).hexdigest()==expected_index_sha,'Index SHA mismatch')
    data=json.loads(raw.decode('utf8'),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    validate(data)
    artifacts=[]
    for a in data['artifacts']:
        matched=None
        if a['relative_path'] is not None:
            actual=file_hash(safe_path(archive_root,a['relative_path']));require(actual==a['sha256'],'Archive SHA mismatch');matched=True
        artifacts.append({k:a[k] for k in ('artifact_id','library_id','version','sha256')}|{'hash_verified':matched})
    matches=[]
    if runtime_root is not None:
        for r in data['runtime_files']:
            matches.append(file_hash(safe_path(runtime_root,r['relative_path']))==r['sha256'])
    statuses=[r[k] for r in data['rounds'] for k in ('engineering','semantic')]
    overall='passed' if all(s=='passed' for s in statuses) else 'failed' if 'failed' in statuses else 'pending' if 'pending' in statuses else 'not_assessed'
    return {'kind':'historical_offline_acceptance','schema_version':'1.0','is_current_runtime_acceptance':False,'release_decision':'not_authorized','historical_status':overall,'rounds':data['rounds'],'artifacts':artifacts,'index_sha256':expected_index_sha,'runtime_match':all(matches) if matches else None,'runtime_files_checked':len(matches),'limitations':sorted(set(data['limitations'])|{'historical_only','not_current_acceptance','hash_match_not_acceptance','archive_contents_not_read'})}

def build_preload(index,archive_root,expected_index_sha,*,runtime_root=None):
    summary=query(index,archive_root,expected_index_sha,runtime_root=runtime_root)
    return 'HISTORICAL AGGREGATE ONLY. Not current runtime acceptance or permission. Do not open private archive contents.\n'+json.dumps(summary,ensure_ascii=True,sort_keys=True,indent=2)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--index',required=True);p.add_argument('--archive-root',required=True);p.add_argument('--expected-index-sha',required=True);p.add_argument('--runtime-root');p.add_argument('--preload',action='store_true');a=p.parse_args()
    try:
        result=(build_preload if a.preload else query)(a.index,a.archive_root,a.expected_index_sha,runtime_root=a.runtime_root)
        print(result if isinstance(result,str) else json.dumps(result,ensure_ascii=True,sort_keys=True))
    except (ValueError,OSError,TypeError,KeyError) as e:
        print(json.dumps({'valid':False,'error_type':type(e).__name__,'release_decision':'not_authorized'}));raise SystemExit(2)
