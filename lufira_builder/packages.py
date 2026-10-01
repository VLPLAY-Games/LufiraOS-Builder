"""Builds the lufira-packages repository (its own build.py, mirroring
lufira-tests) and reads the resulting index.json to find every default
package's .lpg — these used to be packed right here from sources living
inside LufiraOS/userspace/, but that source has moved to its own
repository so the kernel repo doesn't carry package sources/binaries.

Mirrors the tests.py "sibling repo with its own build + release metadata"
pattern: index.json's "lpg" field is a local path today (no release
server yet) and will become a real download URL later with no change to
how fetch_default_packages() is called.
"""

import json
import subprocess
import sys
from pathlib import Path


def _run(cmd, cwd) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True)


def fetch_default_packages(lufira_packages_repo: Path, lufira_repo: Path) -> list:
    """Builds every package from source in lufira_packages_repo, returns
    the list of resulting .lpg paths (resolved from its index.json)."""
    build_py = lufira_packages_repo / "build.py"
    # cwd matters: build.py's own --out-dir/index.json paths are relative
    # to wherever it runs, not to this file.
    _run([sys.executable, str(build_py), "--lufira-repo", str(lufira_repo)], cwd=lufira_packages_repo)

    index = json.loads((lufira_packages_repo / "index.json").read_text())
    return [lufira_packages_repo / pkg["lpg"] for pkg in index["packages"]]
