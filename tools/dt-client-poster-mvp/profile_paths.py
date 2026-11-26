"""Profile path handling shared by the Windows launcher and poster worker."""

from contextlib import closing
import ntpath
import os
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from urllib.parse import quote

_EXTENDED = "\\\\?\\"
_DEVICE = "\\\\.\\"
_MAX_PATH = 260
# Reserve room for task IDs, an export attempt, and its longest failure artifact.
_EXPORT_SUFFIX = "\\tasks\\" + "0" * 32 + "\\artwork\\attempt-" + "0" * 32 + "\\incomplete-output\\psd-layers-recomposite.png"
_INVALID_PART = re.compile(r'[<>:"|?*]')
_DEVICE_NAME = re.compile(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³]", re.IGNORECASE)


class ProfilePathError(ValueError):
    """The launcher can explain which local profile input needs to change."""


def _utf16_length(value):
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def _windows_path(raw):
    if raw.startswith(_DEVICE):
        raise ProfilePathError("请选择普通本机磁盘上的资料目录。设备路径无法用作资料目录。")
    if raw.startswith(_EXTENDED):
        raw = raw[4:]
        if raw[:4].lower() == "unc\\":
            raw = "\\\\" + raw[4:]
        elif not re.match(r"^[A-Za-z]:\\", raw):
            raise ProfilePathError("请选择带盘符的本机资料目录。此扩展设备路径不受支持。")
    absolute = ntpath.abspath(raw)
    drive, tail = ntpath.splitdrive(absolute)
    if not drive or not tail.startswith("\\"):
        raise ProfilePathError("请选择带盘符的本机资料目录，例如 C:\\DT\\Poster。")

    for part in tail.split("\\"):
        if _utf16_length(part) > 255:
            raise ProfilePathError("资料路径中有文件夹名称过长。请缩短名称，或选择层级更少的本机目录。")
        stem = part.split(".", 1)[0].rstrip()
        if (_INVALID_PART.search(part) or _DEVICE_NAME.fullmatch(stem)
                or any(ord(char) < 32 for char in part) or part.endswith((".", " "))):
            raise ProfilePathError("资料路径包含 Windows 不支持的文件夹名称。请选择另一个本机目录。")

    if _utf16_length(absolute + _EXPORT_SUFFIX) >= _MAX_PATH:
        if absolute.startswith("\\\\"):
            absolute = _EXTENDED + "UNC\\" + absolute[2:]
        else:
            absolute = _EXTENDED + absolute
    return Path(absolute)


def resolve_profile(path):
    if os.name != "nt":
        return path.resolve()
    # Use the extended form for the first filesystem access. Resolve aliases
    # before binding the profile lock and all task-relative paths.
    candidate = _windows_path(str(path))
    return _windows_path(str(candidate.resolve()))


def open_readonly_database(path):
    # A conventional file URI treats an extended Windows prefix as an authority.
    uri = "file:" + quote(str(path), safe="") if str(path).startswith(_EXTENDED) else path.as_uri()
    return sqlite3.connect(uri + "?mode=ro", uri=True)


def verify_profile_path(profile):
    if not str(profile).startswith(_EXTENDED):
        return
    # Exercise SQLite's actual path limit before starting the UI or any model
    # request. The probe uses the same private spool names as the worker.
    try:
        with TemporaryDirectory(dir=profile, prefix=".copy-") as work:
            state = Path(work) / "rotation.sqlite"
            with closing(sqlite3.connect(state)) as database:
                database.execute("CREATE TABLE path_probe(value INTEGER NOT NULL)")
                database.execute("INSERT INTO path_probe VALUES (1)")
                database.commit()
            with closing(open_readonly_database(state)) as database:
                database.execute("SELECT value FROM path_probe").fetchone()
    except (OSError, sqlite3.Error) as error:
        raise ProfilePathError(
            "当前资料路径无法读写本机任务数据库。请选择更短、层级更少且可写的本机目录。已有文件未迁移。"
        ) from error
