"""Explicit manifest ID tests; synthetic inputs only."""
import json,copy,unittest
import run_codex_role as m
import shop_image_recheck as r
from test_codex_role_entry import EntryTests
class IdTests(EntryTests):
 def manifest(self,rows):
  p=self.root/"manifest.json";p.write_text(json.dumps(rows),encoding="utf8");return str(p)
 def obj(self,id="CARD-01"):return {"image_id":id,"path":self.png.name}
 def test_explicit_preserved(self):
  rows=m.resolve_images([],self.manifest([self.obj("CARD-01")]))
  self.assertEqual(rows[0]["image_id"],"CARD-01");self.assertEqual(rows[0]["path"],str(self.png.resolve()))
 def test_unicode_id(self):self.assertEqual(m.resolve_images([],self.manifest([self.obj("\u9762\u677f-A")]))[0]["image_id"],"\u9762\u677f-A")
 def test_duplicate_id(self):
  with self.assertRaises(ValueError):m.resolve_images([],self.manifest([self.obj(),self.obj()]))
 def test_missing_keys(self):
  for row in [{"path":self.png.name},{"image_id":"CARD-01"},{"image_id":"CARD-01","path":self.png.name,"other":1}]:
   with self.assertRaises(ValueError):m.resolve_images([],self.manifest([row]))
 def test_invalid_id(self):
  for id in [""," "," x","x ","a\nb","A\u0085B","A\u202eB","A\ud800B",None,12]:
   with self.assertRaises(ValueError):m.resolve_images([],self.manifest([self.obj(id)]))
 def test_mixed_manifest(self):
  with self.assertRaises(ValueError):m.resolve_images([],self.manifest([self.obj(),self.png.name]))
 def test_objects_plus_images(self):
  with self.assertRaises(ValueError):m.resolve_images([str(self.png)],self.manifest([self.obj()]))
 def test_duplicate_path(self):
  with self.assertRaises(ValueError):m.resolve_images([],self.manifest([self.obj("A"),self.obj("B")]))
 def test_invalid_object_path(self):
  for path in ["",None,"missing.png"]:
   row=self.obj();row["path"]=path
   with self.assertRaises((ValueError,FileNotFoundError)):m.resolve_images([],self.manifest([row]))
 def test_legacy_auto(self):
  self.assertEqual(m.resolve_images([],self.manifest([self.png.name]))[0]["image_id"],"img-01")
 def test_unknown_report_reference(self):
  rows=m.resolve_images([],self.manifest([self.obj()]))
  self.assertTrue(m.validate_report(self.report,rows))
 def test_valid_reference(self):
  rows=m.resolve_images([],self.manifest([self.obj()]))
  self.report["images"][0]["image_id"]="CARD-01";self.assertEqual(m.validate_report(self.report,rows),[])
 def test_prior_order_mismatch(self):
  self.report["images"][0]["image_id"]="OTHER"
  p=self.root/"prior.json";p.write_text(json.dumps(self.report),encoding="utf8")
  rows=m.resolve_images([],self.manifest([self.obj()]))
  with self.assertRaises(ValueError):r.load_previous(p,rows,m.validate_report)
 def test_two_ids_preserve_order(self):
  second=self.root/"second.png";second.write_bytes(self.png.read_bytes())
  rows=m.resolve_images([],self.manifest([self.obj("B"),{"image_id":"A","path":second.name}]))
  self.assertEqual([x["image_id"] for x in rows],["B","A"])
 def test_reversed_prior_order_rejected(self):
  second=self.root/"second.png";second.write_bytes(self.png.read_bytes())
  rows=m.resolve_images([],self.manifest([self.obj("B"),{"image_id":"A","path":second.name}]))
  first=dict(self.report["images"][0]);first.update(image_id="B")
  other=dict(first);other.update(image_id="A",image_file=second.name)
  self.report["images"]=[other,first];p=self.root/"prior.json";p.write_text(json.dumps(self.report),encoding="utf8")
  with self.assertRaises(ValueError):r.load_previous(p,rows,m.validate_report)
 def test_manifest_hash_binds_id(self):
  rows=m.resolve_images([],self.manifest([self.obj()]));other=copy.deepcopy(rows);other[0]["image_id"]="OTHER"
  self.assertNotEqual(m.manifest_digest(rows),m.manifest_digest(other))
def load_tests(loader,tests,pattern):
 return unittest.TestSuite(IdTests(n) for n in IdTests.__dict__ if n.startswith("test_"))
if __name__=="__main__":unittest.main(verbosity=2)
