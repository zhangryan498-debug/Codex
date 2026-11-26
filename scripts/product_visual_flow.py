import argparse,hashlib,json,pathlib
from product_visual_workflow import CopyRouter
ROOT=pathlib.Path(__file__).resolve().parents[1]
SKILLS={'A':'ecommerce-detail-page-generator-zh','B':'ecommerce-visual-planning-skill'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','complete','copy']);p.add_argument('--task-id',required=True);p.add_argument('--state',required=True);p.add_argument('--user-copy-file');p.add_argument('--receipt');p.add_argument('--output',required=True);a=p.parse_args()
 state=pathlib.Path(a.state);state.parent.mkdir(parents=True,exist_ok=True);r=CopyRouter(state)
 if a.action=='prepare':
  user=pathlib.Path(a.user_copy_file).read_text(encoding='utf-8-sig') if a.user_copy_file else None
  route=r.assign(a.task_id,user)
  out={'task_id':a.task_id,'route':route,'layout_allowed':route=='USER','status':'USER_COPY_READY' if route=='USER' else 'SKILL_EXECUTION_REQUIRED'}
  if route!='USER':
   skill=ROOT/'.agents/skills'/SKILLS[route]/'SKILL.md'
   if not skill.is_file():raise ValueError('Assigned Skill not installed')
   out.update(skill_path=str(skill),skill_sha256=sha(skill),instruction='Read this installed Skill and its relevant references, inspect input product images, then produce structured selling-point copy with provenance. Layout must wait for complete. Do not invent product facts; preserve explicit copy. Execution is performed by the assigned agent, not by this preparation script.')
  else:out['copy']=r.copy_for_layout(a.task_id)
 elif a.action=='complete':
  receipt_path=pathlib.Path(a.receipt);v=json.loads(receipt_path.read_text(encoding='utf-8-sig'));route=v.get('variant')
  if route not in SKILLS or not isinstance(v.get('copy'),dict):raise ValueError('Need normalized A/B live receipt with copy')
  provenance=v.get('provenance',{});sources=provenance.get('read_sources',[])
  skill=ROOT/'.agents/skills'/SKILLS[route]/'SKILL.md';expected=sha(skill)
  if not any(row.get('sha256')==expected for row in sources):raise ValueError('Live receipt does not identify current installed Skill bytes')
  if not provenance.get('execution_method'):raise ValueError('Missing honest execution method')
  out=r.complete(a.task_id,route,v['copy'],'sha256:'+sha(receipt_path));out['source_receipt']=str(receipt_path);out['execution_method']=provenance['execution_method']
 else:out=r.copy_for_layout(a.task_id)
 dest=pathlib.Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
 if dest.exists():
  old=json.loads(dest.read_text(encoding='utf-8-sig'))
  if old!=out:raise ValueError('Output exists with different content; choose a new revision')
 else:dest.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'status':'ok','output':str(dest),'route':out.get('route')}))
if __name__=='__main__':main()
