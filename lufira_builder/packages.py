"""Gets every default package's .lpg (plus shell.elf/libc.so) ready for
image.py to stage — two ways:

- fetch_default_packages_remote() (default): downloads index.json from
  lufira-packages, then each package's .lpg via its "lpg" URL, plus
  shell.elf/libc.so from the "shell_elf"/"libc_so" entries (not .lpg
  packages — direct-staged runtime files, see lufira-packages/build_index.py).
  Caches by sha256 so a repeat build only re-downloads what changed.
  No local lufira-packages checkout or toolchain needed.

- fetch_default_packages() (opt-in via --build-packages-from-source):
  builds lufira-packages from source via its own build.py.
"""

import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import config


def _run(cmd, cwd) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=30) as resp:
        dest.write_bytes(resp.read())


def _fetch_verified(url: str, dest: Path, expected_sha256: str, label: str) -> None:
    """Skips the download if dest already matches expected_sha256 (the
    common repeat-build case). Raises on mismatch instead of silently
    staging bytes that don't match what index.json claimed."""
    if dest.exists() and _sha256_file(dest) == expected_sha256:
        return
    print(f"  downloading {label} ({url})")
    _download(url, dest)
    got = _sha256_file(dest)
    if got != expected_sha256:
        raise RuntimeError(
            f"{label}: downloaded sha256 {got} does not match index.json's {expected_sha256} "
            f"(corrupted download, or index.json/release/ are out of sync)"
        )


def fetch_default_packages_remote(cache_dir: Path) -> tuple:
    """Returns (list_of_lpg_paths, runtime_root). runtime_root mirrors a
    lufira-packages checkout's build/ layout (build/shell.elf, build/libc.so)
    so it can be passed straight through as cfg.lufira_packages_repo."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    index_path = cache_dir / "index.json"
    print(f"  fetching {config.LUFIRA_PACKAGES_INDEX_URL}")
    _download(config.LUFIRA_PACKAGES_INDEX_URL, index_path)
    index = json.loads(index_path.read_text())

    build_dir = cache_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    lpgs = []
    for pkg in index["packages"]:
        dest = build_dir / f"{pkg['name']}.lpg"
        _fetch_verified(pkg["lpg"], dest, pkg["sha256"], pkg["name"])
        lpgs.append(dest)

    for key, filename in (("shell_elf", "shell.elf"), ("libc_so", "libc.so")):
        entry = index.get(key)
        if not entry:
            raise RuntimeError(
                f"lufira-packages index.json has no '{key}' entry — "
                f"it needs regenerating with a build_index.py that publishes it"
            )
        _fetch_verified(entry["url"], build_dir / filename, entry["sha256"], filename)

    return lpgs, cache_dir


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
