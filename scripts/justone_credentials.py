"""Windows Credential Manager adapter; no network, clipboard, files, or key CLI args.

CLI actions are a harmless UUID-scoped self-test and a strictly private anonymous
pipe enrollment entry. The pipe parent must be an already authorized private
transport; this module does not create a user-facing remote input channel.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from datetime import datetime, timezone
import hmac
import json
import msvcrt
import sys
import uuid

SERVICE_NAME = "D&T/JustOne/OpenAPI"
ADAPTER_REVISION = 4
SELFTEST_PREFIX = SERVICE_NAME + "/selftest/"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
MAX_BLOB_BYTES = 2560


class CredentialError(RuntimeError):
    def __init__(self, operation: str, code: int):
        self.operation = operation
        self.winerror = code
        super().__init__(f"Windows Credential Manager {operation} failed (code {code})")


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def wipe(value: bytearray | None) -> None:
    if value:
        ctypes.memset(ctypes.addressof((ctypes.c_ubyte * len(value)).from_buffer(value)), 0, len(value))


class CredentialStore:
    """Current Windows logon user's generic credentials, without enumeration."""

    def __init__(self):
        if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RuntimeError("This reviewed adapter requires Windows x64")
        expected = {"TargetName": 8, "Comment": 16, "LastWritten": 24,
                    "CredentialBlobSize": 32, "CredentialBlob": 40, "Persist": 48,
                    "AttributeCount": 52, "Attributes": 56, "TargetAlias": 64, "UserName": 72}
        if ctypes.sizeof(wintypes.DWORD) != 4 or ctypes.sizeof(CREDENTIALW) != 80 or ctypes.alignment(CREDENTIALW) != 8:
            raise RuntimeError("Unsupported CREDENTIALW ABI")
        if any(getattr(CREDENTIALW, field).offset != offset for field, offset in expected.items()):
            raise RuntimeError("Unexpected CREDENTIALW field offset")
        self._api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._pointer = ctypes.POINTER(CREDENTIALW)
        self._api.CredWriteW.argtypes = [self._pointer, wintypes.DWORD]
        self._api.CredWriteW.restype = wintypes.BOOL
        self._api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(self._pointer)]
        self._api.CredReadW.restype = wintypes.BOOL
        self._api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._api.CredDeleteW.restype = wintypes.BOOL
        self._api.CredFree.argtypes = [ctypes.c_void_p]
        self._api.CredFree.restype = None
        self._api.CredGetSessionTypes.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self._api.CredGetSessionTypes.restype = wintypes.BOOL
        self.counts = {"CredReadW": 0, "CredWriteW": 0, "CredDeleteW": 0, "CredFree": 0,
                       "CredGetSessionTypes": 0}

    def maximum_persistence(self) -> int:
        levels = (wintypes.DWORD * 7)()
        self.counts["CredGetSessionTypes"] += 1
        if not self._api.CredGetSessionTypes(7, levels):
            raise CredentialError("session_capability", ctypes.get_last_error())
        return int(levels[CRED_TYPE_GENERIC])

    @staticmethod
    def _check_target(target: str) -> None:
        if target == SERVICE_NAME:
            return
        if target.startswith(SELFTEST_PREFIX):
            suffix = target[len(SELFTEST_PREFIX):]
            if str(uuid.UUID(suffix)) == suffix:
                return
        raise ValueError("Target is outside the fixed D&T credential namespace")

    def _read_pointer(self, target: str):
        self._check_target(target)
        pointer = self._pointer()
        self.counts["CredReadW"] += 1
        ok = self._api.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer))
        error = ctypes.get_last_error() if not ok else 0
        if not ok:
            if error == ERROR_NOT_FOUND:
                return None
            raise CredentialError("read", error)
        return pointer

    def _free(self, pointer) -> None:
        if pointer:
            # This zeroes only the OS-returned copy, not the persisted credential.
            try:
                size = pointer.contents.CredentialBlobSize
                if 0 < size <= MAX_BLOB_BYTES and pointer.contents.CredentialBlob:
                    ctypes.memset(pointer.contents.CredentialBlob, 0, size)
            finally:
                self._api.CredFree(pointer)
                self.counts["CredFree"] += 1

    def exists(self, target: str) -> bool:
        pointer = self._read_pointer(target)
        try:
            return pointer is not None
        finally:
            self._free(pointer)

    def read_blob(self, target: str) -> bytearray:
        pointer = self._read_pointer(target)
        if pointer is None:
            raise CredentialError("read", ERROR_NOT_FOUND)
        try:
            size = pointer.contents.CredentialBlobSize
            if not 0 < size <= MAX_BLOB_BYTES or not pointer.contents.CredentialBlob:
                raise RuntimeError("Credential blob length is invalid")
            result = bytearray(size)
            ctypes.memmove((ctypes.c_ubyte * size).from_buffer(result), pointer.contents.CredentialBlob, size)
            return result
        finally:
            self._free(pointer)

    def write_new(self, target: str, blob: bytearray) -> None:
        """Serialize cooperating enrollments in the current interactive session.

        WinCred has no atomic create-only. External writers or another Windows
        session are outside this adapter's guarantees; this is not app isolation.
        """
        self._check_target(target)
        if not isinstance(blob, bytearray) or not 0 < len(blob) <= MAX_BLOB_BYTES:
            raise ValueError("A nonempty mutable bytearray of at most 2560 bytes is required")
        if self.maximum_persistence() < CRED_PERSIST_LOCAL_MACHINE:
            raise RuntimeError("Current logon session does not support required durable credential storage")
        with _enrollment_lock():
            if self.exists(target):
                raise RuntimeError("Credential target already exists; overwrite is refused")
            credential = CREDENTIALW()
            credential.Type = CRED_TYPE_GENERIC
            credential.TargetName = target
            credential.Comment = "D&T JustOne protected local credential"
            credential.CredentialBlobSize = len(blob)
            credential.CredentialBlob = ctypes.cast((wintypes.BYTE * len(blob)).from_buffer(blob), ctypes.POINTER(wintypes.BYTE))
            credential.Persist = CRED_PERSIST_LOCAL_MACHINE
            credential.UserName = "D&T JustOne OpenAPI"
            self.counts["CredWriteW"] += 1
            if not self._api.CredWriteW(ctypes.byref(credential), 0):
                raise CredentialError("write", ctypes.get_last_error())

    def delete(self, target: str) -> bool:
        self._check_target(target)
        self.counts["CredDeleteW"] += 1
        if not self._api.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
            code = ctypes.get_last_error()
            if code != ERROR_NOT_FOUND:
                raise CredentialError("delete", code)
            return False
        return True


@contextmanager
def _enrollment_lock():
    api = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.ReleaseMutex.argtypes = [wintypes.HANDLE]
    api.ReleaseMutex.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.CreateMutexW(None, False, "Local\\DAndT.JustOne.OpenAPI.Enrollment.v1")
    if not handle:
        raise CredentialError("enrollment_lock_create", ctypes.get_last_error())
    acquired = False
    try:
        result = api.WaitForSingleObject(handle, 5000)
        acquired = result in (0, 0x80)
        if not acquired:
            raise CredentialError("enrollment_lock_wait", int(result))
        yield
    finally:
        try:
            if acquired:
                api.ReleaseMutex(handle)
        finally:
            api.CloseHandle(handle)


def store_for_authorized_client(blob: bytearray) -> None:
    """Future private, in-process enrollment hook; never called by the self-test."""
    try:
        CredentialStore().write_new(SERVICE_NAME, blob)
    finally:
        wipe(blob)


@contextmanager
def secret_for_authorized_client():
    """Future in-process read hook. No environment/CLI/file fallback, no logging.

    Caller must not log, stringify, persist or upload the value. Python/client
    libraries can create copies, so complete process-memory erasure is not claimed.
    """
    blob = CredentialStore().read_blob(SERVICE_NAME)
    try:
        yield blob
    finally:
        wipe(blob)


def _stdin_is_pipe() -> bool:
    if sys.stdin.isatty():
        return False
    api = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    api.GetFileType.argtypes = [wintypes.HANDLE]
    api.GetFileType.restype = wintypes.DWORD
    return api.GetFileType(msvcrt.get_osfhandle(sys.stdin.fileno())) == 3


def credential_status() -> dict:
    """Return existence/capability only; never emit the credential blob."""
    store = CredentialStore()
    maximum = store.maximum_persistence()
    return {"service_name": SERVICE_NAME, "maximum_persistence": maximum,
            "credential_present": store.exists(SERVICE_NAME) if maximum >= CRED_PERSIST_LOCAL_MACHINE else None,
            "credential_value_printed": False, "network_requests": 0}


def store_from_private_stdin() -> dict:
    """Read raw ASCII API key from an anonymous pipe, never argv/TTY/file.

    First run self-test in this exact execution context. Successful enrollment
    does not authorize or send a provider request. No existing key is overwritten.
    The parent must close the pipe at EOF. Do not use shell interpolation, echo,
    chat, a clipboard-reading agent, or a plaintext file to feed the pipe.
    """
    record = {"status": "BLOCKED", "service_name": SERVICE_NAME,
              "adapter_revision": ADAPTER_REVISION, "stdin_read": False,
              "protected_write": False, "readback_verified": False,
              "credential_printed_or_persisted_as_plaintext": False,
              "network_requests": 0}
    store = CredentialStore()
    maximum = store.maximum_persistence()
    record["current_context_maximum_persistence"] = maximum
    if maximum < CRED_PERSIST_LOCAL_MACHINE:
        record["reason"] = "durable_storage_not_supported_in_this_context"
        return record
    if not _stdin_is_pipe():
        record["reason"] = "private_anonymous_pipe_required_no_tty_or_file"
        return record
    probe = self_test()
    record["same_context_dummy_selftest"] = {
        key: probe.get(key) for key in ["pass", "write_succeeded", "readback_matches", "delete_succeeded", "absent_after_delete"]
    }
    if not probe["pass"]:
        record["reason"] = "same_context_dummy_selftest_not_passed_input_not_read"
        record["dummy_selftest_target"] = probe["target"]
        if "failure" in probe:
            record["dummy_selftest_failure"] = probe["failure"]
        if "cleanup_failure" in probe:
            record["dummy_cleanup_failure"] = probe["cleanup_failure"]
        return record
    buffer = bytearray(MAX_BLOB_BYTES + 3)
    returned = None
    try:
        used = 0
        while used < len(buffer):
            view = memoryview(buffer)[used:]
            try:
                count = sys.stdin.buffer.readinto(view)
            finally:
                view.release()
            record["stdin_read"] = True
            if not count:
                break
            used += count
        del buffer[used:]
        # Permit one conventional line terminator, never multiple keys/lines.
        if buffer.endswith(b"\n"):
            buffer.pop()
            if buffer.endswith(b"\r"):
                buffer.pop()
        if not 0 < len(buffer) <= MAX_BLOB_BYTES or any(x < 33 or x > 126 for x in buffer):
            record["reason"] = "invalid_input_format_value_not_logged"
            return record
        with _enrollment_lock():
            store.write_new(SERVICE_NAME, buffer)
            record["protected_write"] = True
            record["status"] = "PROTECTED_WRITE_UNVERIFIED"
            returned = store.read_blob(SERVICE_NAME)
            record["readback_verified"] = hmac.compare_digest(buffer, returned)
            record["status"] = "STORED_AND_READBACK_VERIFIED" if record["readback_verified"] else "PROTECTED_WRITE_UNVERIFIED"
            if not record["readback_verified"]:
                record["reason"] = "readback_mismatch_operator_review_required_no_auto_delete"
        return record
    except CredentialError as exc:
        record["failure"] = {"operation": exc.operation, "win32_code": exc.winerror}
        return record
    except Exception as exc:
        record["failure"] = {"type": type(exc).__name__, "message_not_logged": True}
        return record
    finally:
        wipe(returned)
        wipe(buffer)


def self_test() -> dict:
    target = SELFTEST_PREFIX + str(uuid.uuid4())
    record = {"at_utc": datetime.now(timezone.utc).isoformat(), "mode": "HARMLESS_DUMMY_ONLY",
              "adapter_revision": ADAPTER_REVISION,
              "target": target, "real_service_touched": False, "real_token_read": False,
              "network_requests": 0, "settings_changed": False, "dependencies_installed": False,
              "abi_x64_verified": False, "write_succeeded": False, "readback_matches": False,
              "delete_succeeded": False, "absent_after_delete": False, "pass": False}
    store = None
    dummy = bytearray(b"D&T HARMLESS SELFTEST - NOT AN API KEY")
    returned = None
    checked_absent = False
    created_by_this_test = False
    try:
        store = CredentialStore()
        record["abi_x64_verified"] = True
        # A random self-test namespace is the only target read during this test.
        if store.exists(target):
            raise RuntimeError("Self-test target collision; refusing to continue")
        checked_absent = True
        store.write_new(target, dummy)
        created_by_this_test = True
        record["write_succeeded"] = True
        returned = store.read_blob(target)
        record["readback_matches"] = hmac.compare_digest(dummy, returned)
    except CredentialError as exc:
        record["failure"] = {"operation": exc.operation, "win32_code": exc.winerror}
    except Exception as exc:
        record["failure"] = {"type": type(exc).__name__, "message_not_logged": True}
    finally:
        for value in (returned, dummy):
            try:
                wipe(value)
            except Exception:
                record["buffer_wipe_failure"] = True
        if created_by_this_test and store is not None:
            try:
                record["delete_succeeded"] = store.delete(target)
                record["absent_after_delete"] = not store.exists(target)
            except CredentialError as exc:
                record["cleanup_failure"] = {"operation": exc.operation, "win32_code": exc.winerror}
        elif checked_absent and store is not None:
            # Pre-check absence is not ownership: never delete after a failed
            # write or a collision discovered by write_new's second pre-check.
            record["delete_not_attempted_without_confirmed_creation"] = True
            try:
                record["absent_after_failed_write"] = not store.exists(target)
                if not record["absent_after_failed_write"]:
                    record["unowned_target_present_not_deleted"] = True
            except CredentialError as exc:
                record["post_failure_presence_check"] = {"operation": exc.operation, "win32_code": exc.winerror}
        if store is not None:
            record["wincred_call_counts"] = store.counts
    record["pass"] = (all(record[x] for x in ["abi_x64_verified", "write_succeeded", "readback_matches", "delete_succeeded", "absent_after_delete"])
                      and not record.get("buffer_wipe_failure", False))
    return record


if __name__ == "__main__":
    command = sys.argv[1:]
    if command == ["self-test"]:
        result = self_test()
    elif command == ["store-stdin"]:
        try:
            result = store_from_private_stdin()
        except CredentialError as exc:
            result = {"status": "BLOCKED_BEFORE_INPUT", "stdin_read": False,
                      "failure": {"operation": exc.operation, "win32_code": exc.winerror}, "network_requests": 0}
    elif command == ["store-status"]:
        try:
            result = credential_status()
        except CredentialError as exc:
            result = {"credential_present": None, "failure": {"operation": exc.operation, "win32_code": exc.winerror}, "network_requests": 0}
    elif command == ["pipe-self-test"]:
        if not _stdin_is_pipe():
            result = {"pass": False, "reason": "pipe_required", "network_requests": 0}
        else:
            value = bytearray(64)
            count = sys.stdin.buffer.readinto(value)
            del value[count:]
            arrived = hmac.compare_digest(value, bytearray(b"DT-HARMLESS-PIPE-PROBE"))
            wipe(value)
            result = self_test() if arrived else {"pass": False, "reason": "harmless_probe_value_not_matched", "network_requests": 0}
            result["harmless_pipe_input_matches"] = arrived
    else:
        raise SystemExit("Supported actions: self-test, pipe-self-test, store-stdin or store-status. Never pass a credential as an argument.")
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result.get("pass") or result.get("status") == "STORED_AND_READBACK_VERIFIED" or command == ["store-status"] and "failure" not in result else 1)
