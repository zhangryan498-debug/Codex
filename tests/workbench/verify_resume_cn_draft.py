import base64, hashlib, importlib.util, json, pathlib, sys, tempfile

staging = pathlib.Path(__file__).resolve().parents[2] / "tools" / "dt-client-poster-mvp"
spec = importlib.util.spec_from_file_location("resume", staging/"resume_cn_draft.py")
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)
checklist=[]
def test_raises(fn, reason):
    try:
        fn()
    except ValueError as error:
        assert str(error)==reason, str(error)
        checklist.append(reason)
    else:
        raise AssertionError(reason)
with tempfile.TemporaryDirectory(dir=staging, prefix="synthetic-") as tmp:
    work=pathlib.Path(tmp).resolve()
    source,dest=work/"source",work/"destination"
    task_id="1"*32
    task_folder=source/"tasks"/task_id
    task_folder.mkdir(parents=True)
    (dest/"tasks").mkdir(parents=True)
    raw=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jr0sAAAAASUVORK5CYII=")
    extra=raw+b"synthetic original bytes"
    digest=hashlib.sha256(raw).hexdigest()
    more=hashlib.sha256(extra).hexdigest()
    (task_folder/"product.png").write_bytes(raw)
    refs=task_folder/resume.ASSETS
    refs.mkdir()
    (refs/f"{more}.png").write_bytes(extra)
    (refs/"unreferenced.png").write_bytes(raw)
    (task_folder/"customer-agent-session.json").write_text("SYNTHETIC_DO_NOT_COPY", encoding="utf-8")
    task={"id":task_id,"site":"CN","title":"Sample group","facts":"Sample facts",
          "source":{"kind":"local_upload","simulated":False,"sku":"SAMPLE-01","sha256":digest},
          "status":"awaiting_selling_points","versions":[],"run":{"mode":"awaiting_skill"},
          "copy_intake":{"task_id":task_id,"site":"CN","target_version":1,"asset_sha256":digest,
                        "product_ref":"SAMPLE-01","layout_allowed":False},
          "request_id":"rq.12345","request_hash":"e"*64,"created_at":"2026-11-26T12:00:00Z",
          "updated_at":"2026-11-26T12:00:00Z","agent_session":{"private":"SYNTHETIC_DO_NOT_COPY"},
          "settings":{"api_key":"SYNTHETIC_DO_NOT_COPY"},"foreground_detail":{
              "fields":{"fit":{"fact":"Sample fit","image_kind":"original","asset_sha256":digest},
                        "quality":{"fact":"Sample facts","image_kind":"evidence","asset_sha256":more},
                        "spec":{"fact":"Sample spec","image_kind":"evidence","asset_sha256":more},
                        "use":{"fact":"","image_kind":None,"asset_sha256":None}},
              "missing":[{"module":"use","field":"fact"},{"module":"use","field":"image"}]}}
    def save():
        (task_folder/"task.json").write_text(json.dumps(task),encoding="utf-8")
    save()
    source_json=(task_folder/"task.json").read_bytes()
    def run(**kwargs):
        return resume.copy_draft(source,dest,task_id,digest,**kwargs)
    assert run()["result"]=="eligible_only" and list((dest/"tasks").iterdir())==[]
    checklist.append("dry_run_no_write")
    test_raises(lambda: resume.copy_draft(source,dest,task_id,"7"*64), "primary_hash_changed")
    (refs/f"{more}.png").write_bytes(raw)
    test_raises(run, "image_hash_or_format_mismatch")
    (refs/f"{more}.png").write_bytes(extra)
    task["site"]="PH"
    save()
    test_raises(run, "not_eligible_cn_draft")
    task["site"]="CN"
    task["title"]="Synthetic test"
    save()
    test_raises(run, "fixture_not_eligible")
    task["title"]="Sample group"
    task["facts"]="api_key=SYNTHETIC_DO_NOT_COPY"
    save()
    test_raises(run, "invalid_product_text")
    task["facts"]="Sample facts"
    save()
    with resume.offline_profile(dest):
        test_raises(lambda: run(apply=True), "destination_workbench_running")
    assert not list((dest/"tasks").iterdir())
    result=run(apply=True)
    final=dest/"tasks"/task_id
    new=resume.load(final/"task.json")
    assert new["id"]==task_id and new["source"]==task["source"] and new["foreground_detail"]==task["foreground_detail"]
    assert new["title"]==task["title"] and new["facts"]==task["facts"] and new["request_id"]==task["request_id"]
    assert new["versions"]==[] and not (final/"customer-agent-session.json").exists()
    assert b"SYNTHETIC_DO_NOT_COPY" not in (final/"task.json").read_bytes()
    assert (final/"product.png").read_bytes()==raw and (final/resume.ASSETS/f"{more}.png").read_bytes()==extra
    assert not (final/resume.ASSETS/"unreferenced.png").exists()
    assert (task_folder/"task.json").read_bytes()==source_json
    assert (task_folder/"product.png").read_bytes()==raw and (refs/f"{more}.png").read_bytes()==extra
    checklist.append("same_identity_refs_bytes_source_preserved_only_allowlist_loaded")
    before=(final/"task.json").read_bytes()
    test_raises(lambda: run(apply=True),"destination_task_exists")
    assert (final/"task.json").read_bytes()==before
    print(json.dumps({"passed":checklist, "receipt":result}))
