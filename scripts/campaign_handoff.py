"""Deterministic offline campaign handoff gate. Never authorizes live execution."""
from __future__ import annotations
import json, hashlib, argparse
from pathlib import Path
IDENTITY=('sku','revision','payable_cents')
TERMS=('currency','timezone','valid_from_inclusive','valid_until_exclusive','list_price_cents','coupon','shipping')
ROLES={'douyin-shop-ops','douyin-ad-buyer','douyin-video-director','douyin-live-ops'}
def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def validate(authority, packets, receiver):
 if receiver not in ROLES: raise ValueError('unsupported receiver')
 if not isinstance(authority,dict) or not isinstance(packets,list) or not packets:raise ValueError('authority object and nonempty packet list required')
 for key in IDENTITY+TERMS:
  if key not in authority: raise ValueError('authority missing '+key)
 for key in ('revision','payable_cents','list_price_cents'):
  if type(authority[key]) is not int or authority[key]<0:raise ValueError('authority invalid '+key)
 for key in ('sku',)+tuple(k for k in TERMS if k!='list_price_cents'):
  if not isinstance(authority[key],str) or not authority[key].strip():raise ValueError('authority invalid '+key)
 seen=set(); records=[]
 for item in packets:
  if not isinstance(item,dict) or not isinstance(item.get('packet_id'),str) or not item['packet_id'].strip():raise ValueError('invalid packet id')
  pid=item['packet_id']
  if pid in seen:raise ValueError('duplicate packet id')
  seen.add(pid);p=item.get('payload');errors=[]
  if not isinstance(p,dict):errors.append({'field':'payload','kind':'invalid_object'});p={}
  if p.get('role')!=receiver:errors.append({'field':'role','kind':'receiver_mismatch'})
  for k in IDENTITY:
   if k not in p:errors.append({'field':k,'kind':'missing'})
   elif type(p[k]) is not type(authority[k]) or p[k]!=authority[k]:errors.append({'field':k,'kind':'mismatch'})
  t=p.get('terms');t=t if isinstance(t,dict) else {}
  for k in TERMS:
   if k not in t:errors.append({'field':'terms.'+k,'kind':'missing'})
   elif type(t[k]) is not type(authority[k]) or t[k]!=authority[k]:errors.append({'field':'terms.'+k,'kind':'mismatch'})
  if not isinstance(t.get('evidence_scope'),str) or not t['evidence_scope'].strip():errors.append({'field':'terms.evidence_scope','kind':'missing'})
  records.append({'packet_id':pid,'packet_sha256':digest(item),'field_gate':'pass' if not errors else 'fail','errors':errors})
 return {'schema_version':'1.0','scope':'synthetic_offline','release_status':'not_authorized','receiver_role':receiver,'authority_sha256':digest(authority),'packets_sha256':digest(packets),'records':records}
def combine(authority,packets,receiver,model_receipts,binding):
 gate=validate(authority,packets,receiver)
 expected={'authority_sha256':digest(authority),'packets_sha256':digest(packets),'model_receipts_sha256':digest(model_receipts),'receiver_role':receiver}
 if not isinstance(binding,dict) or any(binding.get(k)!=v for k,v in expected.items()) or not binding.get('run_id') or not binding.get('task_sha256'):
  raise ValueError('missing or stale trusted run binding')
 if not isinstance(model_receipts,dict) or model_receipts.get('scope')!='synthetic_offline' or model_receipts.get('release_status')!='not_authorized' or model_receipts.get('receiver_role')!=receiver:raise ValueError('invalid receiver receipt scope')
 rows=model_receipts.get('receipts',[]);ids=[x.get('packet_id') for x in rows]
 if len(ids)!=len(set(ids)) or set(ids)!={x['packet_id'] for x in gate['records']}:raise ValueError('receiver coverage mismatch')
 mapped={x['packet_id']:x for x in rows}
 for x in gate['records']:
  m=mapped[x['packet_id']];status=m.get('offline_receipt')
  if status not in ('accepted','rejected'):raise ValueError('invalid receipt status')
  x['model_receipt']=status;x['system_receipt']='accepted' if x['field_gate']=='pass' and status=='accepted' else 'rejected'
  x['model_gate_conflict']=x['field_gate']=='fail' and status=='accepted'
 gate['model_receipts_sha256']=digest(model_receipts)
 gate['verified_run_binding']=binding
 return gate
def combine_run(authority,packets,receiver,run_dir):
 run=Path(run_dir);result=json.loads((run/'result.json').read_text(encoding='utf-8-sig'));task_bytes=(run/'task.txt').read_bytes();answer_bytes=(run/'answer.md').read_bytes()
 if result.get('status')!='completed' or result.get('run_id')!=run.name or result.get('task_sha256')!=hashlib.sha256(task_bytes).hexdigest() or result.get('answer_sha256')!=hashlib.sha256(answer_bytes).hexdigest():raise ValueError('incomplete or changed run evidence')
 task=task_bytes.decode('utf-8-sig').replace('\r\n','\n')
 try:
  task_authority=json.loads(task.split('AUTHORITY\n',1)[1].split('\nPACKETS\n',1)[0])
  task_packets=json.loads(task.split('\nPACKETS\n',1)[1].split('\nFIELD_CHECKS\n',1)[0])
 except (ValueError,IndexError) as exc:raise ValueError('run does not contain structured frozen handoff inputs') from exc
 if digest(task_authority)!=digest(authority) or digest(task_packets)!=digest(packets):raise ValueError('run inputs differ from current frozen inputs')
 model=json.loads(answer_bytes.decode('utf-8-sig'))
 binding={'authority_sha256':digest(task_authority),'packets_sha256':digest(task_packets),'model_receipts_sha256':digest(model),'receiver_role':receiver,'run_id':run.name,'task_sha256':result['task_sha256']}
 return combine(authority,packets,receiver,model,binding)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--receiver',required=True);ap.add_argument('--run-dir');ap.add_argument('--output',required=True);args=ap.parse_args()
 data=json.loads(Path(args.input).read_text(encoding='utf-8-sig'));out=combine_run(data['authority'],data['packets'],args.receiver,args.run_dir) if args.run_dir else validate(data['authority'],data['packets'],args.receiver)
 Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
