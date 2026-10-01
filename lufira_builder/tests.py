"""Fetches the lufira-tests release archive and unpacks its .elf test
binaries — v0.7 plan, stage 7. The tests used to live inside LufiraOS/test
and get glob'd directly by qemu_debug(); now they're built and released by
a separate lufira-tests repository with its own build (see build.py
there), and this module is the only place that knows how to go get the
result.

`source` is a local path OR an http(s) URL to a .tar.gz of .elf files.
There's no release server yet, so the default in build.py just points at
lufira-tests' own dist/ output (a sibling checkout) — same stub-today,
real-URL-later pattern as the "lpg" field in lufira-packages/index.json:
swapping a real GitHub Releases URL in later needs no change here.
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
