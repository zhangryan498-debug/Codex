"""Load pinned NVIDIA wheels for this Python process without changing PATH.

Call configure_gpu_runtime() before importing faster_whisper/ctranslate2.
Keep this module loaded for the lifetime of GPU inference.
"""
from importlib import metadata
from pathlib import Path
import ctypes
import os
import sys

_VERSIONS = {
    "nvidia-cuda-runtime-cu12": "12.3.101",
    "nvidia-cublas-cu12": "12.3.4.1",
    "nvidia-cudnn-cu12": "9.10.2.21",
}
_DIRECTORY_HANDLES = []
_LIBRARY_HANDLES = []
_CONFIGURED = None


def configure_gpu_runtime():
    """Register this venv's DLL directories and preload CUDA/cuBLAS/cuDNN.

    Returns a JSON-serializable manifest. Idempotent within a process.
    Does not install packages or alter environment/registry PATH.
    """
    global _CONFIGURED
    if _CONFIGURED is not None:
        return _CONFIGURED
    if sys.platform != "win32":
        raise RuntimeError("video_gpu_runtime targets the project's Windows x86-64 runtime")
    original_path = os.environ.get("PATH", "")
    prefix = Path(sys.prefix).resolve()
    dll_files = {}
    directories = set()
    versions = {}
    for package, expected_version in _VERSIONS.items():
        try:
            distribution = metadata.distribution(package)
        except metadata.PackageNotFoundError as error:
            raise RuntimeError(
                f"Missing {package}; install interface/video-gpu-requirements.lock.txt in this venv"
            ) from error
        if distribution.version != expected_version:
            raise RuntimeError(f"{package}: expected {expected_version}, found {distribution.version}")
        versions[package] = distribution.version
        for relative in distribution.files or []:
            if relative.suffix.lower() != ".dll":
                continue
            path = Path(distribution.locate_file(relative)).resolve()
            if not path.is_relative_to(prefix) or not path.is_file():
                raise RuntimeError(f"NVIDIA DLL is outside this venv or missing: {path}")
            dll_files[path.name.lower()] = path
            directories.add(path.parent)
    if not directories:
        raise RuntimeError("Pinned NVIDIA wheels contain no DLL directories")
    # Directory handles must stay alive; closing them removes the process search paths.
    for directory in sorted(directories):
        _DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
    preloaded = {}
    # cuDNN loads split libraries by basename; preload them so it does not need PATH.
    preload_names = (
        "cudart64_12.dll", "cublasLt64_12.dll", "cublas64_12.dll",
        "cudnn_graph64_9.dll", "cudnn_ops64_9.dll", "cudnn_adv64_9.dll",
        "cudnn_cnn64_9.dll", "cudnn_heuristic64_9.dll",
        "cudnn_engines_precompiled64_9.dll", "cudnn_engines_runtime_compiled64_9.dll",
        "cudnn64_9.dll",
    )
    for filename in preload_names:
        path = dll_files.get(filename.lower())
        if path is None:
            raise RuntimeError(f"Pinned NVIDIA wheels do not provide {filename}")
        try:
            _LIBRARY_HANDLES.append(ctypes.WinDLL(str(path)))
        except OSError as error:
            raise RuntimeError(f"Cannot load {filename} from {path}: {error}") from error
        preloaded[filename] = str(path)
    if os.environ.get("PATH", "") != original_path:
        raise RuntimeError("Process PATH changed unexpectedly during DLL registration")
    _CONFIGURED = {
        "configured": True,
        "python_prefix": str(prefix),
        "versions": versions,
        "dll_directories": [str(path) for path in sorted(directories)],
        "preloaded": preloaded,
        "process_path_changed": False,
        "system_path_changed": False,
    }
    return _CONFIGURED
