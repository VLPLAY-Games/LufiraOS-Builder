"""Fetches the lufira-tests release archive and unpacks its .elf test
binaries. Tests used to live in LufiraOS/test and get glob'd directly by
qemu_debug(); now they're built/released by the separate lufira-tests repo.

`source` is a local path or http(s) URL to a .tar.gz of .elf files. No
release server yet, so build.py's default points at a sibling checkout's
dist/ output — swapping in a real URL later needs no change here.
"""

import tarfile
import tempfile
import urllib.request
from pathlib import Path


def fetch_tests_archive(source) -> Path:
    """Returns a temp directory containing the extracted .elf files, or
    None if `source` doesn't exist (local path) — callers should treat
    that as "no tests available" rather than a hard failure."""
    source_str = str(source)
    tmp_dir = Path(tempfile.mkdtemp(prefix="lufira-tests-"))

    if source_str.startswith("http://") or source_str.startswith("https://"):
        archive_path = tmp_dir / "archive.tar.gz"
        urllib.request.urlretrieve(source_str, archive_path)
    else:
        archive_path = Path(source)
        if not archive_path.is_file():
            return None

    extract_dir = tmp_dir / "elf"
    extract_dir.mkdir()
    with tarfile.open(archive_path) as tf:
        tf.extractall(extract_dir)
    return extract_dir
