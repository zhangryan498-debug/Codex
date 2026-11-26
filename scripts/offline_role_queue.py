"""Local offline queue. SQLite transactions protect claims; no auto model retry.

Queue state is local consistency evidence, never business authorization. Actual
CLI and price credentials are independently validated by offline_queue_worker.
"""
import contextlib
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

VERSION='offline-role-queue-v1'
ROLES={'lead','selection','ad-buyer','video-teardown','video-director','campaign-ops','shop-ops','live-ops','competitor-analyst','visual-designer'}
COUNTRIES={'douyin':'CN','tiktok':'PH'}
FIELDS={'job_id','idempotency_key','platform','country','role_suffix','kind','task','requires','requires_price','priority','evaluation_time','price_input','price_input_sha256'}

class QueueError(ValueError):pass
class ValidationError(QueueError):pass
class IdentityConflict(QueueError):pass
class DependencyError(QueueError):pass
class LeaseError(QueueError):pass


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode('utf8')).hexdigest()


def instant(value):
    if not isinstance(value,str):raise ValidationError('Explicit offset-aware time required')
    try:parsed=datetime.fromisoformat(value)
    except ValueError as exc:raise ValidationError('Invalid explicit time') from exc
    if parsed.utcoffset() is None:raise ValidationError('Explicit offset required')
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


def normalize(job):
    if not isinstance(job,dict) or set(job)-FIELDS:raise ValidationError('Unknown job fields; settings and execution options are not task inputs')
    p=dict(requires=[],requires_price=[],priority=0,evaluation_time=None,price_input=None,price_input_sha256=None,**{k:v for k,v in job.items() if k not in ('requires','requires_price','priority','evaluation_time','price_input','price_input_sha256')})
    p.update({k:job[k] for k in ('requires','requires_price','priority','evaluation_time','price_input','price_input_sha256') if k in job})
    if not isinstance(p.get('job_id'),str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',p['job_id']):raise ValidationError('Invalid path-safe job identity')
    key=p.get('idempotency_key')
    if not isinstance(key,str) or not key.strip() or len(key.encode('utf8'))>256 or any(ord(c)<32 for c in key):raise ValidationError('Invalid opaque idempotency key')
    if not all(isinstance(p.get(k),str) for k in ('platform','country','role_suffix')) or p['platform'] not in COUNTRIES or p['country']!=COUNTRIES[p['platform']] or p['role_suffix'] not in ROLES:raise ValidationError('Unsupported platform/country/role')
    if p.get('kind') not in ('offline_review','price_review'):raise ValidationError('Unsupported queue kind')
    if not isinstance(p.get('task'),str) or not p['task'].strip() or len(p['task'].encode('utf8'))>12000:raise ValidationError('Task must be nonempty bounded text')
    if type(p['priority']) is not int or not 0<=p['priority']<=100:raise ValidationError('Priority requires integer 0..100')
    for key in ('requires','requires_price'):
        if not isinstance(p[key],list) or any(not isinstance(x,str) or not x for x in p[key]) or len(set(p[key]))!=len(p[key]) or p['job_id'] in p[key]:raise DependencyError('Invalid/self/duplicate dependency')
    if not set(p['requires_price'])<=set(p['requires']):raise DependencyError('Price dependency must also be an execution dependency')
    if p['evaluation_time'] is not None:instant(p['evaluation_time'])
    if p['requires_price'] and p['evaluation_time'] is None:raise ValidationError('Downstream price dependency requires explicit evaluation time')
    if p['kind']=='price_review':
        if p['role_suffix']!='shop-ops' or not isinstance(p['price_input'],str) or not p['price_input'] or not isinstance(p['price_input_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',p['price_input_sha256']) or p['evaluation_time'] is None:raise ValidationError('Price review requires shop route, frozen caller hash and explicit time')
    elif p['price_input'] is not None or p['price_input_sha256'] is not None:raise ValidationError('Ordinary reviews cannot acquire a price-intake lane')
    return p


class Queue:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with contextlib.closing(self.connection()) as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS jobs(
                job_id TEXT PRIMARY KEY, idem TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, digest TEXT NOT NULL,
                state TEXT NOT NULL, owner TEXT, token TEXT, lease_until TEXT, attempt_id TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,evidence TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,event TEXT,data TEXT);''')

    def connection(self):
        con=sqlite3.connect(self.path,timeout=15,isolation_level=None)
        con.row_factory=sqlite3.Row
        return con

    @contextlib.contextmanager
    def transaction(self):
        con=self.connection()
        try:
            con.execute('BEGIN IMMEDIATE');yield con;con.execute('COMMIT')
        except BaseException:
            if con.in_transaction:con.execute('ROLLBACK')
            raise
        finally:con.close()

    @staticmethod
    def event(con,job,event,data):con.execute('INSERT INTO events(job_id,event,data) VALUES(?,?,?)',(job,event,canonical(data)))

    def submit(self,jobs):
        if not isinstance(jobs,list) or not jobs:raise ValidationError('Nonempty atomic batch required')
        by_id={};by_key={}
        for p in map(normalize,jobs):
            if p['job_id'] in by_id or p['idempotency_key'] in by_key:
                if by_id.get(p['job_id'])==p and by_key.get(p['idempotency_key'])==p:continue
                raise IdentityConflict('Conflicting identity in atomic batch')
            by_id[p['job_id']]=p;by_key[p['idempotency_key']]=p
        values=list(by_id.values())
        with self.transaction() as con:
            allrows={r['job_id']:json.loads(r['payload']) for r in con.execute('SELECT * FROM jobs')}
            for p in values:
                old=con.execute('SELECT * FROM jobs WHERE job_id=? OR idem=?',(p['job_id'],p['idempotency_key'])).fetchall()
                if old and (len(old)!=1 or old[0]['job_id']!=p['job_id'] or old[0]['digest']!=digest(p)):raise IdentityConflict('Immutable job or idempotency key conflict')
                allrows[p['job_id']]=p
            for p in allrows.values():
                for dependency in p['requires']:
                    if dependency not in allrows:raise DependencyError('Unknown dependency')
                for dependency in p['requires_price']:
                    parent=allrows[dependency]
                    if parent['kind']!='price_review' or (parent['platform'],parent['country'])!=(p['platform'],p['country']):raise DependencyError('Price credentials cannot cross route or country')
            visited=set();active=set()
            def walk(job):
                if job in active:raise DependencyError('Cyclic task graph')
                if job in visited:return
                active.add(job)
                for dep in allrows[job]['requires']:walk(dep)
                active.remove(job);visited.add(job)
            for job in allrows:walk(job)
            for p in values:
                cur=con.execute('INSERT OR IGNORE INTO jobs(job_id,idem,payload,digest,state) VALUES(?,?,?,?,?)',(p['job_id'],p['idempotency_key'],canonical(p),digest(p),'pending'))
                if cur.rowcount:self.event(con,p['job_id'],'submitted',{'digest':digest(p)})
        return [p['job_id'] for p in values]

    def reap(self,now_iso):
        now=instant(now_iso);changed=[]
        with self.transaction() as con:
            for row in con.execute("SELECT * FROM jobs WHERE state IN ('leased','running') AND lease_until<=?",(now,)).fetchall():
                state='pending' if row['state']=='leased' else 'uncertain'
                con.execute('UPDATE jobs SET state=?,owner=NULL,token=NULL,lease_until=NULL WHERE job_id=?',(state,row['job_id']))
                self.event(con,row['job_id'],'lease_expired',{'from':row['state'],'to':state,'now':now})
                changed.append({'job_id':row['job_id'],'state':state})
        return changed

    def claim(self,worker,now_iso,ttl_seconds,dependency_check=None):
        now=instant(now_iso)
        if not isinstance(worker,str) or not worker or type(ttl_seconds) is not int or not 1<=ttl_seconds<=3600:raise ValidationError('Worker and bounded integer lease required')
        self.reap(now)
        end=(datetime.fromisoformat(now)+timedelta(seconds=ttl_seconds)).isoformat(timespec='microseconds')
        with self.transaction() as con:
            for row in con.execute("SELECT * FROM jobs WHERE state='pending' ORDER BY json_extract(payload,'$.priority') DESC,job_id ASC").fetchall():
                p=json.loads(row['payload']);parents={dep:con.execute('SELECT * FROM jobs WHERE job_id=?',(dep,)).fetchone() for dep in p['requires']}
                if any(v['state']!='completed' for v in parents.values()):continue
                reason=None
                for dep in p['requires_price']:
                    if dependency_check is None:reason='No trusted downstream price revalidator';break
                    try:
                        ok,why=dependency_check(json.loads(parents[dep]['payload']),json.loads(parents[dep]['evidence']),p)
                    except Exception as exc:ok,why=False,type(exc).__name__+': '+str(exc)
                    if not ok:reason=why;break
                if reason:
                    con.execute("UPDATE jobs SET state='blocked',evidence=? WHERE job_id=?",(canonical({'reason':reason,'model_called':False}),p['job_id']))
                    self.event(con,p['job_id'],'dependency_price_block',{'reason':reason});continue
                token=uuid.uuid4().hex
                con.execute("UPDATE jobs SET state='leased',owner=?,token=?,lease_until=? WHERE job_id=?",(worker,token,end,p['job_id']))
                self.event(con,p['job_id'],'claimed',{'owner':worker,'until':end,'token_sha256':hashlib.sha256(token.encode()).hexdigest()})
                return {'job_id':p['job_id'],'token':token,'payload':p,'role':p['platform']+'-'+p['role_suffix'],'lease_until':end}
        return None

    def start(self,job_id,token,now_iso):
        self.reap(now_iso)
        with self.transaction() as con:
            row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            if row is None or row['state']!='leased' or row['token']!=token:raise LeaseError('Start needs current owned lease')
            attempt=uuid.uuid4().hex
            con.execute("UPDATE jobs SET state='running',attempt_id=?,attempts=attempts+1 WHERE job_id=?",(attempt,job_id))
            self.event(con,job_id,'started',{'attempt_id':attempt});return attempt

    def settle(self,job_id,token,status,evidence,now_iso):
        if status not in ('completed','blocked','failed') or not isinstance(evidence,dict):raise ValidationError('Invalid terminal state/evidence')
        self.reap(now_iso)
        with self.transaction() as con:
            row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            if row is None or row['state']!='running' or row['token']!=token:raise LeaseError('Settle needs current owned running attempt')
            con.execute('UPDATE jobs SET state=?,evidence=?,owner=NULL,token=NULL,lease_until=NULL WHERE job_id=?',(status,canonical(evidence),job_id))
            self.event(con,job_id,'settled',{'state':status,'evidence_sha256':digest(evidence)})

    def block_leased(self,job_id,token,reason,now_iso):
        self.reap(now_iso)
        with self.transaction() as con:
            row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            if row is None or row['state']!='leased' or row['token']!=token:raise LeaseError('Preflight block needs current owned lease')
            evidence={'scope':'synthetic_offline','release_status':'not_authorized','reason':reason,'model_called':False}
            con.execute("UPDATE jobs SET state='blocked',evidence=?,owner=NULL,token=NULL,lease_until=NULL WHERE job_id=?",(canonical(evidence),job_id))
            self.event(con,job_id,'preflight_blocked',{'reason':reason})

    def abandon(self,job_id,token,reason,now_iso):
        instant(now_iso)
        with self.transaction() as con:
            row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            if row is None or row['state'] not in ('running','uncertain') or row['state']=='running' and row['token']!=token:raise LeaseError('Uncertain marker needs current running attempt')
            con.execute("UPDATE jobs SET state='uncertain',owner=NULL,token=NULL,lease_until=NULL WHERE job_id=?",(job_id,))
            self.event(con,job_id,'uncertain_no_auto_retry',{'reason':reason})

    def resolve(self,job_id,attempt_id,status,evidence):
        """Trusted worker only, after revalidating an existing actual run. No retry."""
        if status not in ('completed','blocked','failed') or not isinstance(evidence,dict):raise ValidationError('Invalid reconciliation')
        with self.transaction() as con:
            row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            if row is None or row['state']!='uncertain' or row['attempt_id']!=attempt_id:raise LeaseError('Reconcile needs the same uncertain attempt')
            con.execute('UPDATE jobs SET state=?,evidence=? WHERE job_id=?',(status,canonical(evidence),job_id))
            self.event(con,job_id,'reconciled_existing_run',{'attempt_id':attempt_id,'state':status,'evidence_sha256':digest(evidence)})

    def get(self,job_id):
        with contextlib.closing(self.connection()) as con:row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
        if row is None:raise ValidationError('Unknown job')
        p=json.loads(row['payload'])
        return {'job_id':row['job_id'],'role':p['platform']+'-'+p['role_suffix'],'state':row['state'],'payload':p,'attempts':row['attempts'],'attempt_id':row['attempt_id'],'lease_until':row['lease_until'],'evidence':json.loads(row['evidence'])}

    def list_jobs(self):
        with contextlib.closing(self.connection()) as con:ids=[r[0] for r in con.execute('SELECT job_id FROM jobs ORDER BY job_id')]
        return [self.get(job) for job in ids]
