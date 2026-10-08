"""Builds the lufira-packages repository (its own build.py, mirroring
lufira-tests) and reads the resulting index.json to find every default
package's .lpg — these used to be packed right here from sources living
inside LufiraOS/userspace/, but that source has moved to its own
repository so the kernel repo doesn't carry package sources/binaries.

index.json's "lpg" field is now a REAL download URL (raw.githubusercontent.
com/.../release/<name>.lpg, see lufira-packages/build_index.py) — meant for
dlpg's own "sync"/"upgrade" subcommands to fetch over the network from
INSIDE a running LufiraOS instance, not for this script to resolve as a
local filesystem path at image-build time (joining a repo root with a full
URL doesn't produce a valid path — this used to silently "work" only
because the field was a local path before dlpg gained networking).
build.py (run below) always leaves the exact same bytes locally in
build/<name>.lpg regardless of what index.json's URL says, so stage straight
from there instead.
"""

import json
import subprocess
import sys
from pathlib import Path


def _run(cmd, cwd) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True)


def fetch_default_packages(lufira_packages_repo: Path, lufira_repo: Path) -> list:
    """Builds every package from source in lufira_packages_repo, returns
    the list of resulting local .lpg paths (by name, via its index.json)."""
    build_py = lufira_packages_repo / "build.py"
    # cwd matters: build.py's own --out-dir/index.json paths are relative
    # to wherever it runs, not to this file.
    _run([sys.executable, str(build_py), "--lufira-repo", str(lufira_repo)], cwd=lufira_packages_repo)

    index = json.loads((lufira_packages_repo / "index.json").read_text())
    build_dir = lufira_packages_repo / "build"
    return [build_dir / f"{pkg['name']}.lpg" for pkg in index["packages"]]
