"""Fixed offline D&T mall role. One assignment in, one verified scoped response out."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
from poster_copy_executor import FRESH_TEST, INTERACTIVE_TEST
import workflow_poster_delivery as delivery

ROLE = "douyin-shop-ops"
PROTOCOL = "isolated_mall_role_v1"
LIMIT = 24 * 1024 * 1024
ID = re.compile(r"[0-9a-f]{32}")
HASH = re.compile(r"[0-9a-f]{64}")
RAW_FILES = {*delivery.ARTWORK, "poster-chain-release.json", "product.png"}
FIELDS = {"protocol", "subsession_id", "parent_session_id", "parent_task_id", "child_task_id", "site", "role", "tool", "version"}


class Rejected(delivery.WorkflowError):
    pass


def demand(condition, reason):
    if not condition:
        raise Rejected(reason)


def sha(raw):
    return sha256(raw).hexdigest()


def doc(data):
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def allowed_file(folder, name, max_bytes):
    demand(Path(name).name == name and folder.is_dir(), "role_artifact_path")
    file = folder / name
    demand(file.is_file() and not file.is_symlink() and file.resolve(strict=True) == file
           and 0 < file.stat().st_size <= max_bytes, "role_artifact_missing")
    return file.read_bytes()


def checked_binding(binding):
    return (isinstance(binding, dict) and set(binding) == FIELDS and binding["protocol"] == PROTOCOL
            and binding["role"] == ROLE and binding["site"] == "CN" and binding["tool"] == delivery.SHOP_RECEIVE
            and type(binding["version"]) is int and binding["version"] == 1
            and all(isinstance(binding[k], str) and ID.fullmatch(binding[k])
                    for k in ("subsession_id", "parent_session_id", "parent_task_id", "child_task_id")))


def snapshot_hashes(raw):
    demand(set(raw) == RAW_FILES and sum(map(len,raw.values())) <= LIMIT, "role_inputs_incomplete")
    return {name:sha(value) for name,value in raw.items()}


def allowed_pins(binding, raw):
    pinned = {"poster_task_id":binding["parent_task_id"],"version":binding["version"],
              "source_hashes":snapshot_hashes(raw)}
    return (pinned, {**pinned,"evidence_scope":INTERACTIVE_TEST}, {**pinned,"evidence_scope":FRESH_TEST})


def execute(stage):
    packet_raw = allowed_file(stage, "assignment.json", 64000)
    packet = json.loads(packet_raw)
    extra = {"combined"} if "combined" in packet else set()
    demand(set(packet) == {"binding","parameters","source","entry","pins","role_sha256"}|extra, "role_assignment_format")
    binding, parameters = packet["binding"], packet["parameters"]
    demand(checked_binding(binding) and stage.parent.name == binding["child_task_id"], "role_assignment_binding")
    demand(parameters["site"] == binding["site"] and parameters["poster_task_id"] == binding["parent_task_id"]
           and type(parameters["version"]) is int and parameters["version"] == binding["version"]
           and parameters["piece"] == "P01", "role_wrong_parent_or_version")
    role_bytes = allowed_file(stage, "role.md", 128000)
    demand(b"name: douyin-shop-ops" in role_bytes and sha(role_bytes) == packet["role_sha256"],
           "role_definition_mismatch")
    source_dir = stage / "in"
    demand(not source_dir.is_symlink() and source_dir.resolve(strict=True) == source_dir, "role_artifact_path")
    demand({p.name for p in source_dir.iterdir()} == RAW_FILES, "role_inputs_incomplete")
    raw = {name:allowed_file(source_dir,name,LIMIT) for name in sorted(RAW_FILES)}
    demand(packet["pins"] in allowed_pins(binding, raw), "role_inputs_mismatch")
    attachment = packet.get("combined")
    if attachment:
        key = attachment["binding"]
        sku = parameters["product_ref"]
        demand(key == {"session_id":binding["parent_session_id"],"task_id":binding["parent_task_id"],
                       "version":binding["version"],"product_id":sku,"sku_id":sku}
               and attachment["poster_release_sha256"]==sha(raw["poster-chain-release.json"])
               and set(attachment["source_tasks"])==set(attachment["archive_sha256"])=={"main","detail"}
               and len(set(attachment["source_tasks"].values()))==2
               and all(ID.fullmatch(n) for n in attachment["source_tasks"].values()),"role_combined_binding")
        for kind in ("main","detail"):
            content = allowed_file(stage,kind+"-source.zip",64*1024*1024)
            demand(sha(content)==attachment["archive_sha256"][kind],"role_combined_archive")

    def reviewed(site, ident, rev, *, reviewed=False):
        demand(reviewed and (site, ident, rev) == (binding["site"], binding["parent_task_id"],binding["version"]),
               "role_wrong_parent_or_version")
        return packet["entry"], packet["source"], raw

    linked = delivery.bind(binding["site"], parameters, reviewed, tool=binding["tool"])
    demand(linked[0] == packet["pins"], "role_inputs_mismatch")
    output = stage / "out" / binding["child_task_id"]
    output.mkdir(parents=True)
    result, paths = delivery.run_workflow(None, binding["site"], binding["tool"], parameters, output,
                                          {}, None, linked=linked)
    demand(result["upstream"] == packet["pins"], "role_result_binding")
    response = {"status":"ready","binding":binding,"pid":os.getpid(),"parent_pid":os.getppid(),
                "packet_sha256":sha(packet_raw),"role_sha256":packet["role_sha256"],
                "output_sha256":{key:sha(path.read_bytes()) for key,path in paths.items()}}
    if attachment:
        response["combined_sha256"] = sha(doc(attachment))
    return response


def run(project, task, parameters, folder, snapshot, linked, attachments=None):
    """Create a private immutable assignment, authenticate the local worker result and only then publish."""
    stage = folder / "agent"
    stage.mkdir()
    entry, source, raw = snapshot
    parent_source = task["source"]
    bindings = linked[0]
    scope = {"protocol":PROTOCOL,"subsession_id":os.urandom(16).hex(),
             "parent_session_id":parent_source["agent_session_id"],"parent_task_id":parameters["poster_task_id"],
             "child_task_id":task["id"],"site":task["site"],"role":ROLE,"tool":parent_source["tool_id"],
             "version":parameters["version"]}
    role_lookup = project / "runtime-agents/douyin-shop-ops"
    role = allowed_file(role_lookup, "agent.md", 128000)
    packet = {"binding":scope,"parameters":parameters,"pins":bindings,"entry":entry,
              "source":{key:source[key] for key in ("sku","sha256")},"role_sha256":sha(role)}
    if attachments:
        pin, archives = attachments
        packet["combined"] = pin
    expected = {"binding":scope,"packet_sha256":sha(doc(packet)),"role_sha256":sha(role)}
    if attachments:
        expected["combined_sha256"] = sha(doc(pin))
    write_state(stage, {"state":"started", **expected})
    worker_pid = None
    try:
        demand(checked_binding(scope) and bindings in allowed_pins(scope, raw), "role_assignment_binding")
        inputs = stage / "in"
        inputs.mkdir()
        for name in sorted(raw):
            (inputs/name).write_bytes(raw[name])
        (stage/"role.md").write_bytes(role)
        if attachments:
            for kind in ("main","detail"):
                (stage/(kind+"-source.zip")).write_bytes(archives[kind])
        (stage/"assignment.json").write_bytes(doc(packet))
        exe = Path(sys._base_executable).resolve(strict=True)
        allow = ("SYSTEMROOT","WINDIR","SYSTEMDRIVE","PATH","PATHEXT","TEMP","TMP","LOCALAPPDATA","USERPROFILE")
        env = {key:os.environ[key] for key in allow if key in os.environ}
        command = [str(exe), "-I", "-B", str(APP / "mall_agent_worker.py"), str(stage)]
        with subprocess.Popen(command, cwd=stage, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)) as proc:
            worker_pid = proc.pid
            try:
                stdout, _ = proc.communicate(timeout=25)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise Rejected("role_worker_timeout") from None
            demand(proc.returncode == 0, "role_worker_failed")
            demand(0 < len(stdout) <= 10000,"role_worker_response")
            ack = json.loads(stdout)
            extra = {"combined_sha256"} if attachments else set()
            demand(set(ack) == {"status","binding","pid","parent_pid","packet_sha256","role_sha256","output_sha256"}|extra
                   and all(ack[k]==expected[k] for k in extra)
                   and ack["status"] == "ready" and isinstance(ack["binding"], dict) and set(ack["binding"]) == FIELDS
                   and ack["pid"] == proc.pid and ack["parent_pid"] == os.getpid() and ack["pid"] != os.getpid(),
                   "role_worker_identity")
            actual = ack["binding"]
            demand(actual["parent_task_id"] == scope["parent_task_id"], "role_worker_wrong_parent")
            demand(type(actual["version"]) is int and actual["version"] == scope["version"], "role_worker_wrong_version")
            demand(doc(actual) == doc(scope) and all(ack[key] == expected[key] for key in ("packet_sha256","role_sha256")),
                   "role_worker_wrong_binding")
        output = stage / "out" / scope["child_task_id"]
        demand(output.resolve(strict=True) == output and {p.name for p in output.iterdir()} == set(delivery.FILES.values()),
               "role_worker_outputs")
        files = {kind:allowed_file(output,name,LIMIT) for kind,name in delivery.FILES.items()}
        hashes = {kind:sha(data) for kind,data in files.items()}
        demand(ack["output_sha256"] == hashes, "role_worker_output_hash")
        result = json.loads(files["json"])
        sealed = {**task, "result":result, "local_files":delivery.FILES}
        delivery.verify_saved(sealed, files)
        source_result, expected_entries = linked[1]
        demand(all(result.get(key) == value for key,value in source_result.items())
               and result["job_id"] == parameters["job_id"] and result["state"] == "POSTER_SOURCE_BUNDLE_READY"
               and all(result[key] is False for key in ("execution_authorized","model_called","provider_called")),
               "role_worker_result_scope")
        with ZipFile(output / delivery.FILES["bundle"]) as package:
            items = package.infolist()
            demand(len(items) == len(expected_entries)
                   and set(item.filename for item in items) == set(expected_entries)
                   and all(item.file_size == len(expected_entries[item.filename]) and
                           package.read(item) == expected_entries[item.filename] for item in items),
                   "role_worker_bundle_sources")
        published = {kind:folder/name for kind,name in delivery.FILES.items()}
        demand(not any(path.exists() for path in published.values()), "role_worker_existing_output")
        for kind, target in published.items():
            (output/delivery.FILES[kind]).rename(target)
        accepted = {**expected,"state":"verified","worker_pid":ack["pid"],"workbench_pid":os.getpid(),
                    "output_sha256":hashes,"no_network_or_model":True,"native_agent_delegation_verified":False}
        write_state(stage,accepted)
        return result,published,{"binding":scope,"receipt_sha256":sha(doc(accepted)),
                                 "worker_pid":ack["pid"],"workbench_pid":os.getpid()}
    except (Rejected, delivery.WorkflowError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, Rejected) else "role_worker_rejected"
        write_state(stage,{"state":"failed", **expected,"reason":reason,"published":False,
                           "worker_pid":worker_pid,"workbench_pid":os.getpid()})
        raise Rejected(reason) from None


def write_state(stage, value):
    temp = stage/"execution.part"
    temp.write_bytes(doc(value))
    temp.replace(stage/"execution.json")


def verify_cold(folder, task, files):
    proof = task["agent_execution"]
    stage = folder / "agent"
    raw = allowed_file(stage,"execution.json",64000)
    pin = json.loads(raw)
    scope = proof["binding"]
    demand(pin["state"] == "verified" and sha(raw) == proof["receipt_sha256"]
           and checked_binding(scope) and doc(pin["binding"]) == doc(scope)
           and (scope["parent_task_id"],scope["version"],scope["parent_session_id"],scope["child_task_id"])
           == (task["result"]["poster_task_id"],task["result"]["version"], task["source"]["agent_session_id"], task["id"])
           and pin["output_sha256"] == {kind:sha(data) for kind,data in files.items()}
           and pin.get("combined_sha256") == (sha(doc(task["source"]["combined_visual"]))
                                              if task["source"].get("combined_visual") else None),
           "role_worker_cold_receipt")


def disable_external(event, _):
    if event.startswith("socket.") or event in {"subprocess.Popen","os.system","os.startfile"}:
        raise PermissionError("No external capability in the mall role process.")


if __name__ == "__main__":
    sys.addaudithook(disable_external)
    try:
        if len(sys.argv) != 2:
            raise Rejected("role_assignment_format")
        stage = Path(sys.argv[1]).resolve(strict=True)
        print(doc(execute(stage)).decode("ascii"),flush=True)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        print('{"status":"failed","code":"role_execution_failed"}',flush=True)
        sys.exit(1)
