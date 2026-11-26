import copy, hashlib, json, tempfile, unittest
from pathlib import Path
import campaign_price_handoff as g

def fixture(receiver="douyin-shop-ops"):
    platform,country,currency=g.ROUTES[receiver]
    raw={"fixture_type":"synthetic_offline","platform":platform,"country":country,"currency":currency,
         "sku":"SYN-ONE","revision":3,"floor_scope":"each_sku","floor_basis":"buyer_goods_per_unit",
         "price_cents":1250,"discount_cents":50}
    calc={"method":"deterministic_calculation","producer":"synthetic-test-arithmetic",
          "input_sha256":g.digest(raw),"rule_versions":{"SYN-LOCAL":"1"},
          "lower_bound_cents":1200,"lower_bound_basis":"buyer_goods_per_unit",
          "seller_cost_complete":True,"fees_complete":True,"unknowns":[],"conflicts":[]}
    calc.update({k:raw[k] for k in g.SUBJECT_FIELDS})
    a={"schema_version":"1.1","platform":platform,"country":country,"currency":currency,
       "sku":"SYN-ONE","revision":3,"timezone":"Asia/Shanghai" if country=="CN" else "Asia/Manila",
       "valid_from_inclusive":"2026-11-11T00:00:00+08:00",
       "valid_until_exclusive":"2026-11-12T00:00:00+08:00",
       "floor_cents":1200,"floor_scope":"each_sku","floor_basis":"buyer_goods_per_unit","floor_inclusive":True,
       "lower_bound_verified":True,"evidence_scope":"synthetic_offline",
       "calculation_sha256":g.digest(calc)}
    a.update({k:calc[k] for k in ("input_sha256","rule_versions","lower_bound_cents",
              "lower_bound_basis","seller_cost_complete","fees_complete","unknowns","conflicts")})
    p={"packet_id":"SYN-H1","receiver_role":receiver,"authority_sha256":g.digest(a),
       "input_sha256":g.digest(raw),"payload":copy.deepcopy(a)}
    return {"authority":a,"packet":p,"receiver":receiver,"evaluation_time":"2026-11-11T12:00:00+08:00",
            "source_input":raw,"calculation":calc}

def model(f,status="accepted"):
    return {"scope":"synthetic_offline","release_status":"not_authorized",
        "receiver_role":f["receiver"],"packet_id":f["packet"]["packet_id"],
        "authority_sha256":g.digest(f["authority"]),"input_sha256":g.digest(f["source_input"]),
        "offline_receipt":status}

def resync(f):
    f["authority"]["calculation_sha256"]=g.digest(f["calculation"])
    f["packet"]["payload"]=copy.deepcopy(f["authority"])
    f["packet"]["authority_sha256"]=g.digest(f["authority"])

class Tests(unittest.TestCase):
    def check_fail(self,f):
        r=g.validate(**f)
        self.assertEqual(r["field_gate"],"fail");self.assertEqual(r["system_receipt"],"rejected")
        self.assertEqual(r["release_status"],"not_authorized")
        self.assertTrue(r["errors"])
    def test_valid_both_routes(self):
        for route in g.ROUTES:
            with self.subTest(route=route):self.assertEqual(g.validate(**fixture(route))["field_gate"],"pass")
    def test_missing_each_authority_field(self):
        for key in g.AUTH_FIELDS:
            f=fixture();f["authority"].pop(key)
            with self.subTest(key=key):self.check_fail(f)
    def test_missing_each_calculation_field(self):
        for key in g.CALC_FIELDS:
            f=fixture();f["calculation"].pop(key)
            with self.subTest(key=key):self.check_fail(f)
    def test_wrong_route_country_currency_identity_revision(self):
        for k,v in (("country","US"),("currency","PHP"),("platform","tiktok"),("sku","OTHER"),("revision",4)):
            f=fixture();f["packet"]["payload"][k]=v
            with self.subTest(k=k):self.check_fail(f)
        for k,v in (("country","US"),("currency","PHP"),("platform","tiktok")):
            f=fixture();f["authority"][k]=v;resync(f);self.check_fail(f)
    def test_invalid_integer_money(self):
        for k in ("revision","floor_cents","lower_bound_cents"):
            for v in (True,-1,None,"1200",1.2,float("nan"),float("inf")):
                f=fixture();f["authority"][k]=v
                with self.subTest(k=k,v=v):self.check_fail(f)
    def test_low_floor_and_equality(self):
        f=fixture();f["authority"]["lower_bound_cents"]=1199;f["calculation"]["lower_bound_cents"]=1199;resync(f);self.check_fail(f)
        f=fixture();f["authority"]["floor_inclusive"]=False;resync(f);self.check_fail(f)
    def test_unknown_conflict_and_incomplete(self):
        for k,v in (("unknowns",["shipping"]),("conflicts",["source version"]),
                    ("seller_cost_complete",False),("fees_complete",False),
                    ("lower_bound_verified",False),("floor_inclusive",None),
                    ("rule_versions",{})):
            f=fixture();f["authority"][k]=v;resync(f)
            with self.subTest(k=k):self.check_fail(f)
    def test_model_verification_is_not_independent(self):
        for method in ("model_verified","LLM","",None):
            f=fixture();f["calculation"]["method"]=method;resync(f);self.check_fail(f)
    def test_basis_must_match(self):
        f=fixture();f["authority"]["floor_basis"]="merchant_net_per_unit";resync(f);self.check_fail(f)
    def test_time_boundaries(self):
        for now in ("2026-11-10T23:59:59+08:00","2026-11-12T00:00:00+08:00",
                    "2026-11-11T12:00:00","garbage"):
            f=fixture();f["evaluation_time"]=now;self.check_fail(f)
        f=fixture();f["evaluation_time"]="2026-11-11T00:00:00+08:00"
        self.assertEqual(g.validate(**f)["field_gate"],"pass")
    def test_bad_zone_and_interval(self):
        for k,v in (("timezone","Etc/Invented"),("valid_until_exclusive","2026-11-10T00:00:00+08:00"),
                    ("valid_from_inclusive","2026-11-11T00:00:00Z")):
            f=fixture();f["authority"][k]=v;resync(f);self.check_fail(f)
    def test_changed_raw_input_and_calculation(self):
        f=fixture();f["source_input"]["discount_cents"]=100;self.check_fail(f)
        f=fixture();f["calculation"]["lower_bound_cents"]=999;self.check_fail(f)
    def test_stale_packet_hash(self):
        f=fixture();f["packet"]["authority_sha256"]="a"*64;self.check_fail(f)
    def test_payload_extra_missing(self):
        f=fixture();f["packet"]["payload"]["extra"]=1;self.check_fail(f)
        f=fixture();f["packet"]["payload"].pop("timezone");self.check_fail(f)
    def write_run(self,run,f,m,crlf=False):
        task=g.task_bundle(f["authority"],f["packet"],f["calculation"],f["source_input"],f["evaluation_time"],f["receiver"])
        tb=task.replace("\n","\r\n").encode() if crlf else task.encode()
        ab=json.dumps(m).encode()
        (run/"task.txt").write_bytes(tb);(run/"answer.md").write_bytes(ab)
        (run/"result.json").write_text(json.dumps({"status":"completed","exit_code":0,"turn_completed":True,"run_id":run.name,
          "task_sha256":hashlib.sha256(tb).hexdigest(),"answer_sha256":hashlib.sha256(ab).hexdigest()}))
    def test_run_binding_and_crlf(self):
        for crlf in (False,True):
            with tempfile.TemporaryDirectory() as td:
                run=Path(td);f=fixture();self.write_run(run,f,model(f),crlf)
                self.assertEqual(g.combine_run(**f,run_dir=run)["system_receipt"],"accepted")
    def test_model_cannot_override_invalid_authority(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td);f=fixture();f["authority"]["floor_cents"]=1300;resync(f)
            self.write_run(run,f,model(f))
            r=g.combine_run(**f,run_dir=run)
            self.assertTrue(r["model_gate_conflict"]);self.assertEqual(r["system_receipt"],"rejected")
    def test_changed_answer_task_or_inputs(self):
        for kind in ("answer","task","input","status"):
            with tempfile.TemporaryDirectory() as td:
                run=Path(td);f=fixture();self.write_run(run,f,model(f))
                if kind=="answer":(run/"answer.md").write_text("{}")
                if kind=="task":(run/"task.txt").write_text("{}")
                if kind=="input":f["authority"]["revision"]=4;resync(f)
                if kind=="status":(run/"result.json").write_text("{}")
                with self.subTest(kind=kind),self.assertRaises(ValueError):g.combine_run(**f,run_dir=run)
    def test_duplicate_task_marker_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td);f=fixture();self.write_run(run,f,model(f))
            tb=(run/"task.txt").read_bytes()+b"PRICE_AUTHORITY\n{}\n";(run/"task.txt").write_bytes(tb)
            r=json.loads((run/"result.json").read_text());r["task_sha256"]=hashlib.sha256(tb).hexdigest()
            (run/"result.json").write_text(json.dumps(r))
            with self.assertRaises(ValueError):g.combine_run(**f,run_dir=run)
    def test_unsupported_receiver_types(self):
        for receiver in (None, [], {}, "other"):
            f=fixture();f["receiver"]=receiver;self.check_fail(f)
    def test_no_trusted_binding(self):
        f=fixture()
        with self.assertRaises(ValueError):g.combine(**f,model_receipt=model(f),binding=None)


    def test_batch_binding_mixed_receipts(self):
        cases=[]
        for idx,route in enumerate(g.ROUTES):
            f=fixture(route);f["case_id"]="B"+str(idx);cases.append(f)
        cases[1]["packet"]["payload"].pop("timezone")
        with tempfile.TemporaryDirectory() as td:
            run=Path(td);tb=g.batch_task_bundle(cases).encode()
            receipts=[dict(model(f),case_id=f["case_id"]) for f in cases]
            ab=json.dumps({"scope":"synthetic_offline","release_status":"not_authorized","receipts":receipts}).encode()
            (run/"task.txt").write_bytes(tb);(run/"answer.md").write_bytes(ab)
            (run/"result.json").write_text(json.dumps({"status":"completed","exit_code":0,"turn_completed":True,
              "run_id":run.name,"task_sha256":hashlib.sha256(tb).hexdigest(),"answer_sha256":hashlib.sha256(ab).hexdigest()}))
            r=g.combine_batch_run(cases,run)
            self.assertEqual([x["system_receipt"] for x in r["records"]],["accepted","rejected"])
            self.assertTrue(r["records"][1]["model_gate_conflict"])
            changed=copy.deepcopy(cases);changed[0]["authority"]["revision"]=4
            with self.assertRaises(ValueError):g.combine_batch_run(changed,run)
            receipts.append(receipts[0])
            ab=json.dumps({"scope":"synthetic_offline","release_status":"not_authorized","receipts":receipts}).encode()
            (run/"answer.md").write_bytes(ab)
            result=json.loads((run/"result.json").read_text());result["answer_sha256"]=hashlib.sha256(ab).hexdigest()
            (run/"result.json").write_text(json.dumps(result))
            with self.assertRaises(ValueError):g.combine_batch_run(cases,run)
    def test_duplicate_batch_ids(self):
        f=fixture();f["case_id"]="same"
        with self.assertRaises(ValueError):g.batch_task_bundle([f,f])


    def test_subject_replay_cannot_rehash_authority_only(self):
        for key,value in (("sku","OTHER"),("revision",4),("platform","tiktok"),("country","PH"),("currency","PHP")):
            f=fixture();f["authority"][key]=value;resync(f)
            with self.subTest(key=key):self.check_fail(f)
    def test_stale_calculation_cannot_follow_changed_source_subject(self):
        for key,value in (("sku","OTHER"),("revision",4)):
            f=fixture();f["source_input"][key]=value;f["authority"][key]=value
            h=g.digest(f["source_input"]);f["authority"]["input_sha256"]=h
            f["calculation"]["input_sha256"]=h;f["packet"]["input_sha256"]=h;resync(f)
            self.check_fail(f)
    def test_null_or_empty_source_cannot_be_rehashed_as_evidence(self):
        for raw in (None,{},[],{"other":"no subject"}):
            f=fixture();f["source_input"]=raw;h=g.digest(raw)
            f["authority"]["input_sha256"]=h;f["calculation"]["input_sha256"]=h
            f["packet"]["input_sha256"]=h;resync(f);self.check_fail(f)
    def test_floor_scope_is_explicit_and_basis_constrained(self):
        for scope,basis in ((None,"buyer_goods_per_unit"),("order","buyer_goods_per_unit"),("each_sku","arbitrary")):
            f=fixture();f["authority"]["floor_scope"]=scope;f["authority"]["floor_basis"]=basis
            f["authority"]["lower_bound_basis"]=basis
            for obj in (f["source_input"],f["calculation"]):
                obj["floor_scope"]=scope;obj["floor_basis"]=basis
            f["calculation"]["lower_bound_basis"]=basis
            h=g.digest(f["source_input"]);f["authority"]["input_sha256"]=h
            f["calculation"]["input_sha256"]=h;f["packet"]["input_sha256"]=h;resync(f)
            self.check_fail(f)
    def test_explicit_order_scope_does_not_claim_per_sku(self):
        f=fixture()
        for obj in (f["authority"],f["source_input"],f["calculation"]):
            obj["floor_scope"]="order";obj["floor_basis"]="buyer_goods_order"
        f["authority"]["lower_bound_basis"]=f["calculation"]["lower_bound_basis"]="buyer_goods_order"
        h=g.digest(f["source_input"]);f["authority"]["input_sha256"]=h
        f["calculation"]["input_sha256"]=h;f["packet"]["input_sha256"]=h;resync(f)
        self.assertEqual(g.validate(**f)["field_gate"],"pass")


    def batch_scope_run(self,run,cases,top=None,receipt_changes=None):
        receipts=[]
        for f in cases:
            m=model(f);m.pop("scope");m.pop("release_status");m["case_id"]=f["case_id"]
            if receipt_changes:receipt_changes(m)
            receipts.append(m)
        output={"scope":"synthetic_offline","release_status":"not_authorized","receipts":receipts}
        if top:top(output)
        tb=g.batch_task_bundle(cases).encode();ab=json.dumps(output).encode()
        (run/"task.txt").write_bytes(tb);(run/"answer.md").write_bytes(ab)
        (run/"result.json").write_text(json.dumps({"status":"completed","exit_code":0,"turn_completed":True,
          "run_id":run.name,"task_sha256":hashlib.sha256(tb).hexdigest(),"answer_sha256":hashlib.sha256(ab).hexdigest()}))
        return ab
    def test_batch_top_scope_inheritance_preserves_raw_row(self):
        f=fixture();f["case_id"]="top-only";cases=[f]
        with tempfile.TemporaryDirectory() as td:
            run=Path(td);original=self.batch_scope_run(run,cases)
            r=g.combine_batch_run(cases,run);row=r["records"][0]
            self.assertEqual(row["system_receipt"],"accepted")
            self.assertNotIn("scope",row["raw_model_receipt"])
            self.assertEqual(row["effective_model_receipt"]["scope"],"synthetic_offline")
            self.assertNotEqual(row["raw_model_receipt_sha256"],row["effective_model_receipt_sha256"])
            self.assertEqual(r["raw_answer_sha256"],hashlib.sha256(original).hexdigest())
            self.assertEqual((run/"answer.md").read_bytes(),original)
    def test_batch_nested_scope_conflicts_fail(self):
        f=fixture();f["case_id"]="conflict";cases=[f]
        for key,value in (("scope","live"),("release_status","approved"),("scope",None)):
            with tempfile.TemporaryDirectory() as td:
                run=Path(td);self.batch_scope_run(run,cases,receipt_changes=lambda m:m.update({key:value}))
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):g.combine_batch_run(cases,run)
    def test_batch_missing_top_scope_fails(self):
        f=fixture();f["case_id"]="missing-top";cases=[f]
        for key in ("scope","release_status"):
            with tempfile.TemporaryDirectory() as td:
                run=Path(td);self.batch_scope_run(run,cases,top=lambda m:m.pop(key))
                with self.subTest(key=key),self.assertRaises(ValueError):g.combine_batch_run(cases,run)
    def test_batch_missing_decision_is_not_inferred(self):
        f=fixture();f["case_id"]="missing-status";cases=[f]
        with tempfile.TemporaryDirectory() as td:
            run=Path(td);self.batch_scope_run(run,cases,receipt_changes=lambda m:m.pop("offline_receipt"))
            with self.assertRaises(ValueError):g.combine_batch_run(cases,run)

if __name__=="__main__":unittest.main()

