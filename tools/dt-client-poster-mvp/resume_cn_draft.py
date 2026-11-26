"""Copy one reviewed-scope CN draft into an offline D&T profile; never import its runs."""
from argparse import ArgumentParser
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import stat
import tempfile

ID = re.compile(r"[a-f0-9]{32}")
HASH = re.compile(r"[a-f0-9]{64}")
SKU = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MODULES = ("fit", "quality", "spec", "use")
ASSETS = "foreground-detail-assets"
PNG = b"\x89PNG\r\n\x1a\n"
MIB = 1024 * 1024
SECRET = re.compile(r"(?i)(?:\bsk-[A-Za-z0-9_-]{10,}|\bBearer\s+\S+|\b(?:api[_ -]?key|password|token|secret)\s*[:=]\s*\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
FIXTURE = re.compile(r"(?i)\b(?:test|fixture|synthetic|demo)\b|测试|回归|合成|预检")


def require(condition, code):
    if not condition:
        raise ValueError(code)


def checked(path, *, directory):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT, "linked_path")
    require((stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode), "wrong_path_type")
    return path


def read(path, limit):
    with checked(path, directory=False).open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "oversize_file")
    return raw


def load(path):
    return json.loads(read(path, 128 * 1024).decode("utf-8-sig"))


def public_text(value, limit, *, empty=False):
    require(isinstance(value, str) and len(value) <= limit and
            (empty or bool(value.strip())) and not SECRET.search(value), "invalid_product_text")
    return value


def pin_image(path, expected):
    require(isinstance(expected, str) and HASH.fullmatch(expected), "bad_image_hash")
    raw = read(path, 16 * MIB)
    require(raw.startswith(PNG) and sha256(raw).hexdigest() == expected, "image_hash_or_format_mismatch")
    return raw


def load_draft(source, ident, expected_hash, *, synthetic=False):
    require(ID.fullmatch(ident) and HASH.fullmatch(expected_hash), "expected_task_and_hash_required")
    folder = checked(checked(checked(source, directory=True) / "tasks", directory=True) / ident, directory=True)
    original = load(folder / "task.json")
    old_source = original["source"]
    sku = old_source["sku"]
    require(original["id"] == ident and original["site"] == "CN" and not original.get("kind")
            and old_source["kind"] == "local_upload" and old_source["simulated"] is False
            and original["status"] == "awaiting_selling_points" and original["versions"] == []
            and not original.get("confirmed_copy") and original["run"]["mode"] == "awaiting_skill"
            and not original.get("test_scope") and original["run"].get("simulated") is not True
            and not original["run"].get("workflow", {}).get("execution_kind")
            and not original.get("agent_session", {}).get("mock")
            and SKU.fullmatch(sku) and sku != "BAVA-OWNER-SINGLE-HEADREST", "not_eligible_cn_draft")
    title = public_text(original["title"], 46)
    facts = public_text(original["facts"], 2000)
    require(synthetic or not (FIXTURE.search(title) or FIXTURE.search(sku)), "fixture_not_eligible")
    require(old_source["sha256"] == expected_hash, "primary_hash_changed")
    intake = original["copy_intake"]
    require((intake["task_id"], intake["site"], intake["target_version"], intake["asset_sha256"],
             intake["product_ref"], intake["layout_allowed"]) ==
            (ident, "CN", 1, expected_hash, sku, False), "copy_binding_mismatch")
    images = {"product.png": pin_image(folder / "product.png", expected_hash)}
    task = {
        "id": ident, "site": "CN", "title": title, "facts": facts,
        "source": {"kind": "local_upload", "simulated": False, "sku": sku, "sha256": expected_hash},
        "run": {"mode": "awaiting_skill", "provider": "local", "model": None, "workflow": {
            "copy_route": "WAITING", "model_called": False, "imagegen_called": False,
            "native_skill_execution_verified": False, "production_design_verified": False}},
        "status": "awaiting_selling_points", "message": "Imported local draft. Continue from your own model connection.",
        "created_at": original["created_at"], "updated_at": original["updated_at"],
        "request_id": original["request_id"], "request_hash": original["request_hash"],
        "versions": [], "revision_requests": {},
        "copy_intake": {key: intake[key] for key in ("task_id", "site", "target_version", "asset_sha256",
                                                      "product_ref", "layout_allowed")},
    }
    task["copy_intake"]["state"] = "executor_unavailable"
    require(re.fullmatch(r"[A-Za-z0-9_.-]{8,100}", task["request_id"]) and
            HASH.fullmatch(task["request_hash"]) and all(
                re.fullmatch(r"[0-9TZ:+.\-]{10,36}", task[key]) for key in ("created_at", "updated_at")),
            "invalid_draft_metadata")
    if "foreground_detail" in original:
        old_fields = original["foreground_detail"]["fields"]
        require(set(old_fields) == set(MODULES), "wrong_detail_modules")
        fields, missing = {}, []
        for module in MODULES:
            old = old_fields[module]
            fact = public_text(old["fact"], 160, empty=True)
            image_kind, digest = old["image_kind"], old["asset_sha256"]
            if image_kind == "evidence":
                require(isinstance(digest, str) and HASH.fullmatch(digest), "bad_detail_hash")
                relative = f"{ASSETS}/{digest}.png"
                if relative not in images:
                    files = checked(folder / ASSETS, directory=True)
                    images[relative] = pin_image(files / f"{digest}.png", digest)
            else:
                require((image_kind, digest) in {(None, None), ("original", expected_hash)}, "wrong_detail_binding")
            fields[module] = {"fact": fact, "image_kind": image_kind, "asset_sha256": digest}
            if not fact:
                missing.append({"module": module, "field": "fact"})
            if digest is None:
                missing.append({"module": module, "field": "image"})
        require(sum(len(raw) for raw in images.values()) <= 16 * MIB, "detail_bundle_oversize")
        require(missing == original["foreground_detail"]["missing"], "wrong_detail_missing_state")
        task["foreground_detail"] = {"fields": fields, "missing": missing}
    return task, images


@contextmanager
def offline_profile(profile):
    require(os.name == "nt", "windows_only")
    import msvcrt
    path = profile / ".app.lock"
    try:
        stream = checked(path, directory=False).open("r+b")
    except FileNotFoundError:
        stream = path.open("x+b")
    with stream as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ValueError("destination_workbench_running") from None
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def copy_draft(source, destination, ident, expected_hash, *, apply=False, synthetic=False):
    require(source != destination and not source.is_relative_to(destination) and
            not destination.is_relative_to(source) and source.is_absolute() and destination.is_absolute(),
            "distinct_local_profiles_required")
    require(not str(source).startswith("\\\\") and not str(destination).startswith("\\\\"),
            "network_path_rejected")
    for root in (source, destination):
        checked(root, directory=True)
        require(root.resolve() == root, "aliased_profile")
    tasks = checked(destination / "tasks", directory=True)
    task, images = load_draft(source, ident, expected_hash, synthetic=synthetic)
    final = tasks / ident

    def require_new():
        try:
            final.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError("destination_task_exists")
        for folder in tasks.iterdir():
            if ID.fullmatch(folder.name):
                other = load(checked(folder, directory=True) / "task.json")
                require(other["request_id"] != task["request_id"], "destination_request_id_exists")

    if apply:
        with offline_profile(destination):
            require_new()
            with tempfile.TemporaryDirectory(dir=tasks, prefix=".resume-") as stage:
                staged = Path(stage) / "task"
                staged.mkdir()
                for relative, raw in images.items():
                    file = staged / relative
                    file.parent.mkdir(exist_ok=True)
                    file.write_bytes(raw)
                    require(read(file, 16 * MIB) == raw, "staged_image_verification_failed")
                (staged / "task.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
                require(load(staged / "task.json") == task, "staged_task_verification_failed")
                staged.rename(final)  # Windows rename raises if the destination already exists.
    else:
        require_new()
    return {"result": "imported" if apply else "eligible_only", "task_id": ident,
            "site": "CN", "target_version": 1, "local_images": len(images),
            "detail_missing": len(task.get("foreground_detail", {}).get("missing", [])),
            "has_detail": "foreground_detail" in task, "imported_receipts": 0, "imported_credentials": 0}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--source-profile", type=Path, required=True)
    parser.add_argument("--destination-profile", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--photo-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    arg = parser.parse_args()
    try:
        report = copy_draft(arg.source_profile, arg.destination_profile, arg.task_id, arg.photo_sha256, apply=arg.apply)
    except (OSError, TypeError, KeyError, ValueError) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch("[a-z_]+", str(error)) else type(error).__name__
        print(json.dumps({"result": "blocked", "reason": code}))
        return 2
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
