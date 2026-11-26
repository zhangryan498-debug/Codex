"""Check the installed local workbench before opening the browser."""

import argparse
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile


# Exercise the same export/read-back contract as normal tasks on a synthetic 16 px image.
# The renderer's dependencies resolve through its own production entry point.
PSD_CHECK = """
const layout = {
  schema: 'dt-layout-v1', id: 'STARTUP-CHECK', width: 16, height: 16,
  elements: [{id: 'CHK-E001', type: 'rect', role: 'background',
              x: 0, y: 0, width: 16, height: 16, fill: '#ffffff'}],
};
Promise.resolve().then(() => require(process.argv[1]).render(layout, process.argv[2], process.argv[3])).then(result => {
  if (result.psd.status === 'DEPENDENCY_UNAVAILABLE') {
    process.exitCode = 2;
  } else if (result.psd.status !== 'EXPORTED_AND_READ_BACK') {
    throw new Error(result.psd.reason || 'PSD export/readback unavailable');
  }
}).catch(error => { console.error(error.message); process.exitCode = error.code === 'DT_PSD_DEPENDENCY' ? 2 : 1; });
"""


def fail(code, message):
    print(f"[{code}] {message}", file=sys.stderr, flush=True)
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--node", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--foreground-token-plan", action="store_true")
    parser.add_argument("--customer-model-workspace", action="store_true")
    parser.add_argument("--customer-protocol-fixture")
    parser.add_argument("--account-catalog-fixture")
    options = parser.parse_args()
    if not 0 <= options.port <= 65535:
        return fail("port", "端口应为 1–65535。只做本机测试时可指定 0，由系统分配空闲端口。")
    if not options.project.is_dir():
        return fail("project", "D&T 项目目录不存在。请恢复完整项目后再启动。")
    try:
        from PIL import Image, ImageOps  # noqa: F401
    except (ImportError, OSError):
        return fail("pillow", "当前项目 Python 无法加载 Pillow 图像依赖。请使用配套的 D&T Python 环境。")

    if not options.engine.is_file():
        return fail("engine", "找不到 D&T PSD 渲染器。请恢复配套 engine.cjs 或检查 DT_POSTER_ENGINE。")
    if not options.node.is_file():
        return fail("node", "找不到本机 Node.js。请恢复配套 Node.js 或检查 DT_POSTER_NODE。")
    node = str(options.node.resolve())
    engine = options.engine.resolve()
    # Match the task renderer's credential-free environment.
    allowed = {
        "ALLUSERSPROFILE", "APPDATA", "DT_PSD_DEPENDENCIES", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH",
        "PATHEXT", "PROGRAMDATA", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "WINDIR",
    }
    renderer_env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    try:
        subprocess.run([node, "--version"], env=renderer_env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return fail("node", "Node.js 无法运行。请检查 DT_POSTER_NODE 指向可用的本机 node.exe。")

    try:
        with tempfile.TemporaryDirectory(prefix="dt-poster-startup-") as scratch:
            checked = subprocess.run(
                [node, "-e", PSD_CHECK, str(engine), scratch, str(Path(scratch) / "export")],
                cwd=engine.parent, env=renderer_env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=15, check=False,
            )
    except subprocess.TimeoutExpired:
        return fail("engine_timeout", "PSD 渲染自检超时。请检查配套引擎及文件权限后重试。")
    except OSError:
        return fail("engine_io", "无法使用本机临时目录完成 PSD 渲染自检。请检查目录权限或可用空间。")
    if checked.returncode == 2:
        return fail("psd_dependency", "缺少可用的 ag-psd。请恢复配套 PSD 引擎依赖后重试。")
    if checked.returncode:
        return fail("engine_module", "PSD 引擎或依赖加载失败。请恢复配套引擎目录及 Node 模块。")

    try:
        import server
        from profile_paths import ProfilePathError
    except (ImportError, OSError):
        return fail("app_module", "无法加载工作台的 Python 模块。请恢复完整工作台和配套环境。")

    try:
        server.main()
    except ProfilePathError as error:
        return fail("profile_path", str(error))
    except (FileExistsError, NotADirectoryError):
        return fail("profile_path", "客户资料路径无法作为目录使用。请选用另一个本机目录。")
    except OSError as error:
        windows_code = getattr(error, "winerror", None)
        if windows_code == 206 or error.errno == errno.ENAMETOOLONG:
            return fail("profile_path", "资料目录路径过长。请选择层级更少、名称更短的本机目录。已有文件未迁移。")
        if windows_code in {10013, 10048} or error.errno == errno.EADDRINUSE:
            port = "自动分配端口" if options.port == 0 else f"端口 {options.port}"
            return fail("port_unavailable", f"本机{port}不可用。请关闭占用端口的程序或指定另一端口。")
        if error.errno in {errno.EACCES, errno.EAGAIN} and error.filename is None:
            return fail("profile_in_use", "此客户资料目录可能已在另一工作台中开启。请关闭该实例或改用其他目录。")
        if error.errno in {errno.EACCES, errno.ENOENT, errno.ENOSPC, errno.EROFS}:
            return fail("profile_access", "无法访问客户资料目录。请检查目录权限及空间，或选用其他本机目录。")
        return fail("startup_io", f"启动时发生本机文件或网络错误（代码 {windows_code or error.errno}）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
