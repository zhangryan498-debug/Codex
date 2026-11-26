"""Durable, local workbench provider-operation receipts. No HTTP or credentials."""

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat


_HASH = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_KEY_LIKE = re.compile(r"(?:sk-[\\w-]{8,}|akia[0-9a-z]{16}|eyJ[A-Za-z0-9_-]+\\.)", re.I)
_STATUS_CODE = re.compile(r"[a-z][a-z0-9_]{0,47}")
_PURPOSE_MEDIA = {
    "language": {"application/json", "text/plain"},
    "image": {"image/png", "image/jpeg", "image/webp"},
    "video": {"video/mp4", "video/webm"},
}
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    spec_json TEXT NOT NULL,
    task_id TEXT NOT NULL,
    provider_account TEXT NOT NULL,
    state TEXT NOT NULL,
    job_id TEXT,
    reason_code TEXT,
    media_type TEXT,
    output_sha256 TEXT,
    output_bytes INTEGER,
    output BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS operation_task ON operations(task_id, created_at, operation_id);
CREATE UNIQUE INDEX IF NOT EXISTS provider_job
    ON operations(provider_account, job_id) WHERE job_id IS NOT NULL;
"""


class OperationStateError(RuntimeError):
    """A local state transition failed; never use it as a signal to submit again."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _ident(value):
    return (isinstance(value, str) and _ID.fullmatch(value)
            and "://" not in value and not _KEY_LIKE.search(value))


@dataclass(frozen=True)
class OperationSpec:
    task_id: str
    task_version: str
    task_revision: str
    purpose: str
    provider_account: str
    model: str
    prompt_sha256: str
    source_sha256: str

    def __post_init__(self):
        for key in ("task_id", "task_version", "provider_account", "model"):
            value = getattr(self, key)
            if key == "task_version" and type(value) is int and value >= 0:
                value = str(value)
                object.__setattr__(self, key, value)
            if not _ident(value):
                raise ValueError(f"invalid operation {key}")
        if self.purpose not in _PURPOSE_MEDIA:
            raise ValueError("invalid operation purpose")
        for key in ("task_revision", "prompt_sha256", "source_sha256"):
            value = getattr(self, key)
            if not isinstance(value, str) or not _HASH.fullmatch(value):
                raise ValueError(f"invalid operation {key}")

    @property
    def operation_id(self):
        return sha256(self._json().encode("utf-8"))

    def _json(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


class OperationStore:
    """Use an explicitly supplied local data directory, separate from task files.

    Only the winner of start() may send an initial provider request. A receipt
    still marked 'submitting' after an interruption is uncertain, never retryable.
    Video recovery uses the existing job ID with pending(), finish(), or failed().
    Each output BLOB and its receipt commit in one SQLite transaction.
    """

    def __init__(self, root):
        root = Path(root).absolute()
        self._reject_links(root)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "provider_operations.sqlite3"
        with closing(self._connect()) as db:
            db.executescript(_SCHEMA)

    @staticmethod
    def _reject_links(path):
        try:
            entry = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(entry.st_mode) or (
                getattr(entry, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise OperationStateError("operation_storage_is_link")

    def _connect(self):
        for path in (self.path.parent, self.path, *(self.path.with_name(self.path.name + suffix)
                                                     for suffix in ("-journal", "-wal", "-shm"))):
            self._reject_links(path)
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def _write(self):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.rollback()
                raise

    def _record(self, db, spec):
        row = db.execute(
            """SELECT operation_id, spec_json, state, job_id, reason_code,
                      media_type, output_sha256, output_bytes, created_at, updated_at
                 FROM operations WHERE operation_id = ?""",
            (spec.operation_id,),
        ).fetchone()
        if row is None:
            return None
        stored = dict(row)
        if stored.pop("spec_json") != spec._json():
            raise OperationStateError("operation_binding_mismatch")
        stored["binding"] = asdict(spec)
        return stored

    def get(self, spec):
        with closing(self._connect()) as db:
            return self._record(db, spec)

    def _stored_spec(self, row, task_id):
        try:
            spec = OperationSpec(**json.loads(row["spec_json"]))
            if spec.task_id == task_id and spec.operation_id == row["operation_id"]:
                return spec
        except (ValueError, TypeError):
            pass
        raise OperationStateError("operation_binding_mismatch")

    def lookup(self, task_id, operation_id):
        """Recover an operation only through the owning workbench task."""
        if not _ident(task_id) or not isinstance(operation_id, str) or not _HASH.fullmatch(operation_id):
            return None
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT operation_id, spec_json FROM operations WHERE task_id = ? AND operation_id = ?",
                (task_id, operation_id),
            ).fetchone()
            return self._record(db, self._stored_spec(row, task_id)) if row else None

    def operations_by_task(self, task_id):
        if not _ident(task_id):
            raise ValueError("invalid operation task_id")
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT operation_id, spec_json FROM operations WHERE task_id = ?
                   ORDER BY created_at DESC, operation_id""",
                (task_id,),
            ).fetchall()
            return [self._record(db, self._stored_spec(row, task_id)) for row in rows]

    def start(self, spec):
        """Durably reserve a single initial request before accessing the provider."""
        with self._write() as db:
            now = _timestamp()
            added = db.execute(
                """INSERT INTO operations
                       (operation_id, spec_json, task_id, provider_account, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'submitting', ?, ?)
                   ON CONFLICT(operation_id) DO NOTHING""",
                (spec.operation_id, spec._json(), spec.task_id, spec.provider_account, now, now),
            ).rowcount == 1
            return self._record(db, spec), added

    def submitted(self, spec, job_id):
        return self._transition(spec, {"submitting"}, "submitted", job_id=job_id)

    def pending(self, spec, job_id):
        return self._transition(spec, {"submitted"}, "pending", job_id=job_id)

    def failed(self, spec, reason_code, *, job_id=None):
        return self._transition(
            spec, {"submitting", "submitted", "pending"}, "failed", job_id=job_id,
            reason_code=reason_code,
        )

    def unknown(self, spec, reason_code):
        return self._transition(spec, {"submitting"}, "unknown", reason_code=reason_code)

    def finish(self, spec, data, *, media_type, job_id=None):
        if (not isinstance(data, bytes) or not 0 < len(data) <= MAX_OUTPUT_BYTES
                or not isinstance(media_type, str) or media_type not in _PURPOSE_MEDIA[spec.purpose]):
            raise ValueError("invalid operation output")
        allowed = {"submitted", "pending"} if spec.purpose == "video" else {"submitting"}
        return self._transition(
            spec, allowed, "done", job_id=job_id, media_type=media_type, output=data,
        )

    def read_output(self, spec):
        """Return the fully committed output with verified operation binding and digest."""
        with closing(self._connect()) as db:
            record = self._record(db, spec)
            if record is None or record["state"] != "done":
                raise OperationStateError("operation_output_unavailable")
            size = record["output_bytes"]
            if type(size) is not int or not 0 < size <= MAX_OUTPUT_BYTES:
                raise OperationStateError("operation_output_invalid")
            row = db.execute(
                """SELECT output FROM operations
                   WHERE operation_id = ? AND length(output) = ?""",
                (spec.operation_id, size),
            ).fetchone()
            if row is None or sha256(row["output"]) != record["output_sha256"]:
                raise OperationStateError("operation_output_invalid")
            if record["media_type"] not in _PURPOSE_MEDIA[spec.purpose]:
                raise OperationStateError("operation_output_invalid")
            return row["output"], record["media_type"]

    def _transition(self, spec, allowed, state, *, job_id=None, reason_code=None,
                    media_type=None, output=None):
        if spec.purpose == "video" and state in {"submitted", "pending", "done"} and not job_id:
            raise ValueError("video operation requires a job ID")
        if job_id is not None and (spec.purpose != "video" or not _ident(job_id)):
            raise ValueError("invalid operation job ID")
        if state in {"failed", "unknown"} and (
                not isinstance(reason_code, str) or not _STATUS_CODE.fullmatch(reason_code)
                or _KEY_LIKE.search(reason_code)):
            raise ValueError("invalid operation reason code")
        digest = sha256(output) if output is not None else None
        size = len(output) if output is not None else None
        with self._write() as db:
            record = self._record(db, spec)
            if record is None:
                raise OperationStateError("operation_not_started")
            if record["job_id"] is not None and record["job_id"] != job_id:
                raise OperationStateError("operation_job_mismatch")
            if record["state"] == state:
                if all(record[key] == value for key, value in (
                    ("job_id", job_id), ("reason_code", reason_code),
                    ("media_type", media_type), ("output_sha256", digest),
                )):
                    return record
                raise OperationStateError("operation_update_conflict")
            if record["state"] not in allowed:
                raise OperationStateError("operation_transition_not_allowed")
            try:
                db.execute(
                    """UPDATE operations SET state = ?, job_id = ?, reason_code = ?,
                              media_type = ?, output_sha256 = ?, output_bytes = ?,
                              output = ?, updated_at = ? WHERE operation_id = ?""",
                    (state, job_id, reason_code, media_type, digest, size, output,
                     _timestamp(), spec.operation_id),
                )
            except sqlite3.IntegrityError:
                raise OperationStateError("provider_job_already_bound") from None
            return self._record(db, spec)
