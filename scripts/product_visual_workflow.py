"""Independent workflow adapter. No vendor calls, image edits or global config changes."""
import hashlib, json, sqlite3
from contextlib import contextmanager
@contextmanager
def connection(database):
    db=sqlite3.connect(database, timeout=15)
    try:
        with db:
            yield db
    finally:
        db.close()

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

class CopyRouter:
    def __init__(self, database):
        self.database = str(database)
        with connection(self.database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS rounds (task TEXT PRIMARY KEY, route TEXT NOT NULL, user_copy TEXT, receipt TEXT)')
    def assign(self, task, user_copy=None):
        if not isinstance(task, str) or not task.strip():
            raise ValueError('A stable full-task ID is required')
        if user_copy is not None and (not isinstance(user_copy, str) or not user_copy.strip()):
            raise ValueError('Explicit copy cannot be blank')
        with connection(self.database) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT route,user_copy FROM rounds WHERE task=?',(task,)).fetchone()
            if row:
                if row[1] != user_copy:
                    raise ValueError('Task input changed; use an explicit revised task ID')
                return row[0]
            if user_copy is None and db.execute("SELECT 1 FROM rounds WHERE route IN ('A','B') AND receipt IS NULL").fetchone():
                raise ValueError('Another full round is pending; finish it before advancing')
            n = db.execute("SELECT count(*) FROM rounds WHERE route IN ('A','B') AND receipt IS NOT NULL").fetchone()[0]
            route = 'USER' if user_copy is not None else ('A' if n % 2 == 0 else 'B')
            db.execute('INSERT INTO rounds(task,route,user_copy) VALUES(?,?,?)',(task,route,user_copy))
            return route
    def complete(self, task, route, copy, invocation_id):
        if not isinstance(copy, dict) or not copy or not invocation_id:
            raise ValueError('Require actual copy output and invocation receipt')
        receipt = {'route':route,'copy':copy,'invocation_id':invocation_id,'sha256':digest(copy)}
        with connection(self.database) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT route,receipt FROM rounds WHERE task=?',(task,)).fetchone()
            if not row or row[0] != route or route=='USER':
                raise ValueError('Wrong or unassigned Skill route')
            payload=json.dumps(receipt,ensure_ascii=False,sort_keys=True)
            if row[1] and row[1]!=payload:
                raise ValueError('Receipt already frozen; create a revision instead')
            db.execute('UPDATE rounds SET receipt=? WHERE task=?',(payload,task))
        return receipt
    def copy_for_layout(self, task):
        with connection(self.database) as db:
            row=db.execute('SELECT route,user_copy,receipt FROM rounds WHERE task=?',(task,)).fetchone()
        if not row: raise ValueError('Task not assigned')
        if row[0]=='USER': return {'route':'USER','copy':{'body':row[1]},'sha256':digest({'body':row[1]})}
        if not row[2]: raise ValueError('Selling-point Skill has not completed; layout is blocked')
        return json.loads(row[2])

def prepare_layout(layout, copy_receipt):
    """Keep dt-layout-v1 geometry for the existing PSD engine; add provenance only."""
    if copy_receipt.get('sha256') != digest(copy_receipt.get('copy')):
        raise ValueError('Copy receipt hash mismatch')
    if layout.get('schema') != 'dt-layout-v1': raise ValueError('Unsupported existing layout')
    result=json.loads(json.dumps(layout))
    ids=set()
    for e in result['elements']:
        if e['id'] in ids: raise ValueError('Duplicate element ID')
        ids.add(e['id'])
    result['copy_plan']={'route':copy_receipt['route'],'sha256':copy_receipt['sha256'],'invocation_id':copy_receipt.get('invocation_id')}
    return result

def edit_single(layout, element_id, changes):
    """Preflight scoped patch; actual asset generation/render stays with existing modules."""
    allowed={'text':{'text','fontFamily','fontSize','fill','align'},'image':{'path','fit','sourceRect'}}
    out=json.loads(json.dumps(layout)); matches=[e for e in out['elements'] if e['id']==element_id]
    if len(matches)!=1: raise ValueError('Target must resolve to one stable ID')
    e=matches[0]
    if not changes or not set(changes)<=allowed.get(e['type'],set()): raise ValueError('Unsupported scoped changes')
    e.update(changes)
    if 'path' in changes: e.pop('asset_sha256',None)
    return out


