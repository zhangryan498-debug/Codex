"""Complete offline run acceptance; caller supplies trusted expectations, not signatures."""
from pathlib import Path
import argparse,hashlib,json,platform,shutil,sys
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
def safe_path(root,relative):
 p=Path(relative)
 if p.is_absolute() or ".." in p.parts:raise ValueError("Unsafe artifact path")
 resolved=(root/p).resolve()
 if not resolved.is_relative_to(root.resolve()):raise ValueError("Artifact escapes run directory")
 return resolved
def prepare(out,run_id,images,previous,rule_paths,cli):
 out=Path(out);items=[]
 def copy(source,relative,expected=None):
  dest=safe_path(out,relative);dest.parent.mkdir(parents=True,exist_ok=True)
  shutil.copyfile(source,dest);digest=sha(dest)
  if expected and digest!=expected:raise ValueError("Snapshot changed while freezing")
  items.append({"path":relative,"sha256":digest})
 for name in ["task.txt","prompt.txt","images.json"]:
  items.append({"path":name,"sha256":sha(out/name)})
 if previous:items.append({"path":"previous-issues.json","sha256":sha(out/"previous-issues.json")})
 for name in ["review-goals.json","review-context.json"]:
  if (out/name).is_file():items.append({"path":name,"sha256":sha(out/name)})
 for i,row in enumerate(images):
  copy(row["path"],"frozen/images/%02d%s"%(i,Path(row["path"]).suffix.lower()),row["sha256"])
 rules=[]
 for i,path in enumerate(rule_paths):
  path=Path(path);relative="frozen/rules/%02d-%s"%(i,path.name)
  copy(path,relative);rules.append({"source":str(path.resolve()),"snapshot":relative,"sha256":sha(path)})
 import PIL
 contract={"contract_version":"2.0","run_id":run_id,"items":items,"rules":rules,
           "dependencies":{"python":sys.version,"python_binary_sha256":sha(sys.executable),"platform":platform.platform(),"pillow":PIL.__version__,"cli_path":str(cli),"cli_sha256":sha(cli)},
           "business_actions_authorized":False,"release_decision":"not_authorized"}
 dump(out/"contract.json",contract)
 return contract
def verify_contract(out,contract,check_live_rules=False):
 errors=[]
 for item in contract["items"]:
  p=safe_path(out,item["path"])
  if not p.is_file() or sha(p)!=item["sha256"]:errors.append("Contract item changed: "+item["path"])
 if check_live_rules:
  for row in contract["rules"]:
   p=Path(row["source"])
   if not p.is_file() or sha(p)!=row["sha256"]:errors.append("Rule changed during run: "+row["source"])
  deps=contract["dependencies"]
  if sha(sys.executable)!=deps["python_binary_sha256"] or sha(deps["cli_path"])!=deps["cli_sha256"]:errors.append("Runtime binary changed during run")
 return errors
def bind_outputs(out,result,contract):
 result.update(receipt_version="2.0",contract_sha256=sha(out/"contract.json"),
  artifacts={name:sha(out/name) for name in ["answer.raw.json","issues.json","answer.md","image-read-proof.json","input-integrity.json","schema-validation.json","events.jsonl","stderr.log"]},
  acceptance_ready=True)
def accept(run_dir,expected_run_id,expected_contract_sha256,expected_receipt_sha256):
 out=Path(run_dir).resolve();errors=[]
 try:
  if not all([expected_run_id,expected_contract_sha256,expected_receipt_sha256]):raise ValueError("Caller-pinned run/contract/receipt expectations are required")
  receipt=out/"result.json"
  if sha(receipt)!=expected_receipt_sha256:raise ValueError("Receipt does not match caller expectation")
  result=json.loads(receipt.read_text(encoding="utf-8-sig"))
  if result.get("receipt_version")!="2.0" or result.get("acceptance_ready") is not True:raise ValueError("Not a complete v2 receipt")
  if result.get("status")!="completed" or result.get("exit_code")!=0 or result.get("turn_completed") is not True:raise ValueError("Run did not complete successfully")
  if result.get("run_id")!=expected_run_id:raise ValueError("Run replay/context mismatch")
  if sha(out/"contract.json")!=expected_contract_sha256 or result.get("contract_sha256")!=expected_contract_sha256:raise ValueError("Contract does not match caller expectation")
  contract=json.loads((out/"contract.json").read_text(encoding="utf8"))
  if contract.get("run_id")!=expected_run_id or contract.get("contract_version")!="2.0":raise ValueError("Contract run/version mismatch")
  errors+=verify_contract(out,contract,check_live_rules=True)
  required={"answer.raw.json","issues.json","answer.md","image-read-proof.json","input-integrity.json","schema-validation.json","events.jsonl","stderr.log"}
  if set(result.get("artifacts",{}))!=required:errors.append("Missing complete artifact bindings")
  for relative,digest in result.get("artifacts",{}).items():
   p=safe_path(out,relative)
   if not p.is_file() or sha(p)!=digest:errors.append("Artifact changed: "+relative)
  if errors:return {"accepted":False,"errors":errors}
  report=json.loads((out/"issues.json").read_text(encoding="utf8"))
  raw=json.loads((out/"answer.raw.json").read_text(encoding="utf-8-sig"))
  if report.get("schema_version") not in ("1.3","1.4") and report!=raw:errors.append("Standard report differs from raw answer")
  if report.get("release_decision")!="not_authorized" or contract.get("business_actions_authorized") is not False or result.get("business_actions_authorized") is not False:errors.append("Business/release permission violation")
  from run_codex_role import validate_report,validate_proof,manifest_digest,render_report
  import shop_image_recheck as recheck
  import shop_image_recheck_v12 as v12
  images=json.loads((out/"images.json").read_text(encoding="utf8"))
  proof=json.loads((out/"image-read-proof.json").read_text(encoding="utf8"))
  if result.get("manifest_sha256")!=manifest_digest(images):errors.append("Manifest mismatch")
  if result.get("input_images_unchanged") is not True:errors.append("Inputs not verified unchanged")
  errors+=validate_proof(proof,images,expected_run_id)
  if any(row.get("read_status")!="read" for row in report.get("images",[])):errors.append("Unread input image")
  if report.get("schema_version") in ("1.3","1.4"):
   from run_codex_role import review_module
   v13=review_module(report["schema_version"])
   import shop_product_contract as facts
   required_inputs={"review-goals.json","review-context.json"}
   if not required_inputs<={x["path"] for x in contract["items"]}:raise ValueError("Missing bound v1.3 goal/context inputs")
   previous=recheck.load_previous(out/"previous-issues.json",images,validate_report) if result.get("review_mode")=="recheck" else None
   goals=facts.parse_bytes((out/"review-goals.json").read_bytes())
   task_bytes=(out/"task.txt").read_bytes();task_text=task_bytes.decode("utf8")
   bundle=facts.extract_task_bundle(task_bytes) if any(marker in task_text for marker in (facts.BEGIN,facts.END,facts.HASH)) else None
   context=v13.prepare(goals,images,bundle,previous)
   saved=facts.parse_bytes((out/"review-context.json").read_bytes())
   canonical=lambda value:json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
   if canonical(context)!=canonical(saved):errors.append("Review context differs from frozen inputs")
   errors+=v13.validate(raw,images,context,validate_report)
   if canonical(report)!=canonical(v13.decorate(raw,context)):errors.append("v1.3 report is not raw answer plus attributed system metadata")
   if result.get("review_version")!=report["schema_version"] or result.get("review_goals_unchanged") is not True:errors.append("Review goals/version integrity not established")
   if result.get("review_goals_sha256")!=sha(out/"review-goals.json") or result.get("review_context_sha256")!=sha(out/"review-context.json"):errors.append("Review goal/context receipt mismatch")
   if previous and (result.get("previous_report_sha256")!=previous["sha256"] or result.get("previous_report_unchanged") is not True or result.get("recheck_version")!=report["schema_version"]):errors.append("v1.3 historical mismatch")
  elif report.get("schema_version") in ("1.1","1.2"):
   previous=recheck.load_previous(out/"previous-issues.json",images,validate_report)
   errors+=(v12.validate(report,images,previous,validate_report) if report["schema_version"]=="1.2" else recheck.validate_recheck(report,images,previous,validate_report))
   if result.get("previous_report_sha256")!=previous["sha256"] or result.get("previous_report_unchanged") is not True:errors.append("Historical input mismatch")
   if result.get("recheck_version")!=report["schema_version"]:errors.append("Recheck version mismatch")
  else:errors+=validate_report(report,images)
  if (out/"answer.md").read_text(encoding="utf8")!=render_report(report):errors.append("Rendered report mismatch")
  if result.get("answer_sha256")!=sha(out/"answer.md"):errors.append("Rendered answer hash mismatch")
  if result.get("task_sha256")!=sha(out/"task.txt"):errors.append("Task mismatch")
 except Exception as exc:errors.append(type(exc).__name__+": "+str(exc))
 return {"accepted":not errors,"errors":errors,"scope":"Bound offline run integrity/contract, not semantic truth or publication authority"}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--run-dir",required=True)
 p.add_argument("--expected-run-id",required=True);p.add_argument("--expected-contract-sha256",required=True);p.add_argument("--expected-receipt-sha256",required=True)
 a=p.parse_args();result=accept(a.run_dir,a.expected_run_id,a.expected_contract_sha256,a.expected_receipt_sha256)
 print(json.dumps(result,ensure_ascii=False));return 0 if result["accepted"] else 1
if __name__=="__main__":raise SystemExit(main())
