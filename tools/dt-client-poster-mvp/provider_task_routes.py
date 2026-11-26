"""Provider operations scoped to a saved local workbench task."""
from hashlib import sha256
import json
import re
from threading import Thread

from copy_constraints import copy_schema, model_width_guidance, normalize_copy
from provider_inference_wire import InferenceWireError
from provider_model_workspace import KEY_LIKE, PURPOSES
from provider_task_state import OperationSpec, OperationStateError, OperationStore

PATH = re.compile(r"/api/tasks/([0-9a-f]{32})/provider-operations(?:/([0-9a-f]{64})/(poll|output))?")
DIGEST = re.compile(r"[0-9a-f]{64}")
LANGUAGE_TOOL = "dt_propose_selling_points"
BUSY_STATES = {"submitting", "submitted", "pending", "unknown"}
REJECTIONS = {"provider_permission_denied", "provider_rate_limited", "invalid_payload", "unsupported_route"}
EXTENSIONS = {"language": "json", "image": "png", "video": "mp4"}


class TaskModelError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code = status, code
        super().__init__(message)


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def task_binding(task):
    source = task["source"]
    version = max((v["version"] for v in task.get("versions", ())), default=1)
    details = {field: task.get(field) for field in (
        "title", "facts", "site", "confirmed_copy", "foreground_detail", "versions"
    )}
    details.update(source=source, version=version)
    return version, sha256(packed(details)).hexdigest()


def model_input(task, purpose, prompt):
    if purpose != "language":
        limit = 2100 if purpose == "image" else 5000
        if not 0 < len(prompt) <= limit:
            raise TaskModelError(400, "prompt_required", f"请填写 1 至 {limit} 字的生成说明。")
        return {"prompt": prompt}
    if task.get("confirmed_copy"):
        raise TaskModelError(409, "copy_already_confirmed", "本任务已有确认文案，请沿用。")
    if len(prompt) > 500:
        raise TaskModelError(400, "prompt_too_long", "语言补充说明最多 500 字。")
    saved = {"product": task["title"], "facts": task["facts"]}
    if prompt:
        saved["instruction"] = prompt
    return {
        "messages": [
            {"role": "system", "content": (
                "You are preparing a draft selling point for human review. Use only the confirmed product facts "
                "in the user message. Do not invent product claims. Call the supplied function to propose the draft. "
                + model_width_guidance()
            )},
            {"role": "user", "content": packed(saved).decode("utf-8")},
        ],
        "tools": [{
            "type": "function",
            "function": {"name": LANGUAGE_TOOL, "description": "Propose a title and subtitle for human review.",
                         "parameters": copy_schema()},
        }],
    }


def language_candidate(result):
    try:
        call, = result["message"]["tool_calls"]
        if call["function"]["name"] != LANGUAGE_TOOL:
            raise ValueError
        data = json.loads(call["function"]["arguments"])
        if not isinstance(data, dict) or set(data) != {"title", "subtitle"}:
            raise ValueError
        candidate = {field: normalize_copy(data[field], field) for field in ("title", "subtitle")}
        if any(KEY_LIKE.search(text) for text in candidate.values()):
            raise ValueError
        return packed(candidate)
    except (ValueError, KeyError, TypeError, RecursionError):
        raise InferenceWireError("language_tool_missing_or_invalid") from None


class TaskModelOperations:
    def __init__(self, root, models, task_lookup, gate):
        self.db = OperationStore(root / "provider-task-operations")
        self.models = models
        self.task_lookup = task_lookup
        self.gate = gate
        self.active = set()
        self.poll_errors = {}

    def summary(self, task_id):
        with self.gate:
            task = self.task_lookup(task_id)
            version, revision = task_binding(task)
            records = [self.public(row) for row in self.db.operations_by_task(task_id)]
            return {"task_id": task_id, "version": version, "revision": revision, "operations": records}

    def public(self, record):
        spec = OperationSpec(**record["binding"])
        state = record["state"]
        if state == "submitting" and spec.operation_id not in self.active:
            record = self.db.unknown(spec, "operation_interrupted")
            state = "unknown"
        result = {
            "id": spec.operation_id, "purpose": spec.purpose,
            "state": "succeeded" if state == "done" else state, "account_id": spec.provider_account,
            "model_id": spec.model, "task_version": int(spec.task_version), "revision": spec.task_revision,
            "job_id": record["job_id"], "error": record["reason_code"] or self.poll_errors.get(spec.operation_id),
            "fixture": bool(self.models.fixture),
        }
        if state == "done":
            result["sha256"] = record["output_sha256"]
            result["output_href"] = f"/api/tasks/{spec.task_id}/provider-operations/{spec.operation_id}/output"
            if spec.purpose == "language":
                data, _ = self.db.read_output(spec)
                candidate = json.loads(data)
                result["preview"] = candidate["title"] + "\n" + candidate["subtitle"]
        return result

    def start(self, task_id, request):
        if (set(request) != {"approved", "purpose", "account_id", "model_id", "prompt", "task_version", "revision"}
                or request["approved"] is not True or type(request["task_version"]) is not int
                or any(not isinstance(request[k], str) for k in ("purpose", "account_id", "model_id", "prompt", "revision"))
                or request["purpose"] not in PURPOSES):
            raise TaskModelError(400, "approval_required", "请在当前任务确认调用的账户、模型和版本。")
        purpose = request["purpose"]
        with self.gate:
            task = self.task_lookup(task_id)
            source = task["source"]
            asset = source.get("sha256", "")
            if (source.get("kind") != "local_upload" or not DIGEST.fullmatch(asset)
                    or task.get("site") not in {"CN", "PH"} or task.get("kind")):
                raise TaskModelError(400, "task_scope", "请先保存带商品原图的本机商品任务。")
            version, revision = task_binding(task)
            if (request["task_version"], request["revision"]) != (version, revision):
                raise TaskModelError(409, "task_changed", "任务内容已有更新，请刷新并确认新版本。")
            payload = model_input(task, purpose, request["prompt"].strip())
            if KEY_LIKE.search(packed(payload).decode("utf-8")):
                raise TaskModelError(400, "protected_text", "提交内容疑似含密钥，请移除后再确认。")
            try:
                spec = OperationSpec(task_id, version, revision, purpose, request["account_id"], request["model_id"],
                                     sha256(packed(payload)).hexdigest(), asset)
            except ValueError:
                raise TaskModelError(400, "invalid_selection", "请重新选择用途模型。") from None
            existing = self.db.get(spec)
            if existing:
                return self.public(existing)
            if purpose == "video":
                for record in self.db.operations_by_task(task_id):
                    if record["binding"]["purpose"] == "video" and record["state"] in BUSY_STATES:
                        raise TaskModelError(409, "existing_video", "该任务已有视频提交或提交结果不明，请先核对现有回执。")
            child = self.models.operation_child(purpose, spec.provider_account, spec.model)
            try:
                record, created = self.db.start(spec)
            except Exception:
                child.operation_gate.release()
                raise
            if created:
                self.launch(spec, child, "video_submit" if purpose == "video" else purpose, payload)
            else:
                child.operation_gate.release()
            return self.public(record)

    def find(self, task_id, ident):
        self.task_lookup(task_id)
        row = self.db.lookup(task_id, ident)
        if row is None:
            raise TaskModelError(404, "missing_operation", "此任务没有这条回执。")
        return row, OperationSpec(**row["binding"])

    def poll(self, task_id, ident, payload):
        if payload != {"approved": True} or payload.get("approved") is not True:
            raise TaskModelError(400, "approval_required", "请在本机任务中确认查询。")
        with self.gate:
            row, spec = self.find(task_id, ident)
            if spec.purpose != "video" or not row["job_id"] or row["state"] not in {"submitted", "pending"}:
                raise TaskModelError(409, "no_pollable_video", "当前没有可查询的视频任务号。")
            if ident not in self.active:
                child = self.models.operation_child("video", spec.provider_account, spec.model, continuation=True)
                self.launch(spec, child, "video_status", {"task_id": row["job_id"]})
            return self.public(row)

    def launch(self, spec, child, operation, payload):
        ident = spec.operation_id
        self.active.add(ident)
        try:
            Thread(target=self.execute, args=(spec, child, operation, payload), daemon=True).start()
        except RuntimeError:
            self.active.discard(ident)
            child.operation_gate.release()
            if operation != "video_status":
                self.db.failed(spec, "worker_start_failed")
            raise

    def execute(self, spec, child, operation, payload):
        poll_error = None
        try:
            result = child.operate(operation, spec.model, payload)
            if operation == "language":
                self.db.finish(spec, language_candidate(result), media_type="application/json")
            elif operation == "image":
                self.db.finish(spec, result["media"], media_type=result["mime_type"])
            elif operation == "video_submit":
                self.db.submitted(spec, result["task_id"])
                self.update_video(spec, result)
            else:
                self.update_video(spec, result)
                if result["status"] == "unknown":
                    poll_error = "video_status_unknown"
        except (InferenceWireError, OperationStateError, OSError, ValueError, TypeError, KeyError) as error:
            code = error.code if isinstance(error, InferenceWireError) else "operation_interrupted"
            if operation == "video_submit":
                row = self.db.get(spec)
                if row["state"] == "submitting":
                    if code in REJECTIONS:
                        self.db.failed(spec, code)
                    else:
                        self.db.unknown(spec, "submission_uncertain")
            elif operation != "video_status":
                self.db.failed(spec, code)
            else:
                poll_error = code
        finally:
            with self.gate:
                if operation == "video_status":
                    if poll_error:
                        self.poll_errors[spec.operation_id] = poll_error
                    else:
                        self.poll_errors.pop(spec.operation_id, None)
                self.active.discard(spec.operation_id)

    def update_video(self, spec, result):
        job_id, status = result["task_id"], result["status"]
        if status in {"pending", "running"} and self.db.get(spec)["state"] == "submitted":
            self.db.pending(spec, job_id)
        elif status == "succeeded":
            self.db.finish(spec, result["media"], media_type=result["mime_type"], job_id=job_id)
        elif status in {"failed", "canceled"}:
            self.db.failed(spec, "video_" + status, job_id=job_id)

    def output(self, task_id, ident):
        with self.gate:
            row, spec = self.find(task_id, ident)
            if row["state"] != "done":
                raise TaskModelError(409, "output_unavailable", "任务尚无可取回的结果。")
            data, mime = self.db.read_output(spec)
            return data, mime, f"{spec.purpose}-v{spec.task_version}.{EXTENSIONS[spec.purpose]}"
