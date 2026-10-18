"""Build a Linux/Python 3.12 Lambda bundle independently of host Python."""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "build" / "lambda"


def main():
    shutil.rmtree(TARGET, ignore_errors=True)
    TARGET.mkdir(parents=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--target",
            str(TARGET),
            "--platform",
            "manylinux2014_x86_64",
            "--python-version",
            "3.12",
            "--only-binary=:all:",
            "-r",
            str(ROOT / "requirements-lambda.txt"),
        ],
        check=True,
    )
    shutil.copytree(
        ROOT / "services" / "pipeline",
        TARGET / "pipeline",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    archive = ROOT / "build" / "lambda.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(TARGET.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                output.write(path, path.relative_to(TARGET))
    print(f"Built {archive.relative_to(ROOT)} ({archive.stat().st_size / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    main()
