"""Install the pinned Windows renderer and media tools in project-local work/."""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "work" / "openmontage-runtime"
COMMIT = "fecb9a1e3cc23ef84de30b272059457a427f16f8"
FFMPEG_VERSION = "9.0.2"
ARCHIVE_NAME = f"ffmpeg-{FFMPEG_VERSION}-essentials_build.7z"
ARCHIVE_URL = f"https://www.gyan.dev/ffmpeg/builds/packages/{ARCHIVE_NAME}"
ARCHIVE_SHA256 = "4705843ccaaf54257c16ad90f3e952ece33c17df964ecf7bfdbb0f49c7171077"
EXTRACTOR_URL = "https://www.7-zip.org/a/7zr.exe"
EXTRACTOR_RELEASE_URL = "https://github.com/ip7z/7zip/releases/download/26.04/7zr.exe"
EXTRACTOR_SHA256 = "256feca8e274e5da655e2a284fabafd9f554365eb164862089dacd4e8276d282"
UPSTREAM = {
    "tools/base_tool.py": "3fe6ecfdf4e8f9a10a66db6f61a65a24637d77226f13a7d78d44b5f0cf006913",
    "tools/video/video_compose.py": "9a036258eed73ae043a06f82ea1057a00048decf165e60dc49d13f32243db4d4",
    "LICENSE": "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0",
}
MEDIA_BINARIES = {
    "ffmpeg.exe": "3256173f3f8bffd7df12227c68adf68025edb1832273a9530688a7bb1ed8edec",
    "ffprobe.exe": "f0d36ecbbdd3bcfac3efa078c96c7271c2e68b3810595552ac3b7f17e9a65c52",
}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked(path, expected):
    if not path.is_file() or sha256(path) != expected:
        raise ValueError(f"Missing or SHA-256 mismatch: {path}")


def verify_runtime(runtime=RUNTIME):
    for relative, expected in UPSTREAM.items():
        checked(runtime / "source" / relative, expected)
    for name, expected in MEDIA_BINARIES.items():
        checked(runtime / "bin" / name, expected)


def download(url, path):
    request = urllib.request.Request(url, headers={"User-Agent": "D-T-OpenMontage-Setup"})
    with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as output:
        shutil.copyfileobj(response, output)


def setup(archive):
    if sys.platform != "win32":
        raise ValueError("This installer targets Windows x64.")
    if RUNTIME.exists():
        verify_runtime()
        return
    RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".openmontage-install-", dir=RUNTIME.parent) as temp:
        stage = Path(temp) / "runtime"
        (stage / "bin").mkdir(parents=True)
        if archive is None:
            archive = Path(temp) / ARCHIVE_NAME
            download(ARCHIVE_URL, archive)
        checked(archive, ARCHIVE_SHA256)
        extractor = Path(temp) / "7zr.exe"
        try:
            download(EXTRACTOR_URL, extractor)
            checked(extractor, EXTRACTOR_SHA256)
        except (ValueError, OSError):
            download(EXTRACTOR_RELEASE_URL, extractor)
        checked(extractor, EXTRACTOR_SHA256)
        folder = ARCHIVE_NAME.removesuffix(".7z")
        subprocess.run(
            [str(extractor), "x", str(archive.resolve()), f"-o{temp}", "-y",
             f"{folder}/bin/ffmpeg.exe", f"{folder}/bin/ffprobe.exe", f"{folder}/LICENSE"],
            check=True, capture_output=True,
        )
        unpacked = Path(temp) / folder
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            (unpacked / "bin" / name).rename(stage / "bin" / name)
        (unpacked / "LICENSE").rename(stage / "FFmpeg-LICENSE")
        for relative, expected in UPSTREAM.items():
            path = stage / "source" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            download(f"https://raw.githubusercontent.com/OpenMontageInc/OpenMontage/{COMMIT}/{relative}", path)
            checked(path, expected)
        for folder in ("tools", "tools/video"):
            (stage / "source" / folder / "__init__.py").touch()
        verify_runtime(stage)
        # Windows may lock a newly launched executable during antivirus scanning.
        stage.rename(RUNTIME)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-archive", type=Path, help="Reuse the pinned official 7z archive after hash verification")
    args = parser.parse_args()
    try:
        setup(args.ffmpeg_archive)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")
    print(RUNTIME)
