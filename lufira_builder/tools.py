"""Host-side tools: building kernel.bin/BOOTX64.EFI and the helper
mkfs_lufirafs program from the LufiraOS repository tree.

lpg_pack isn't built here anymore — packing .lpg from source now happens
entirely inside lufira-packages' own build.py (which compiles its own
lpg_pack from LufiraOS/tools/lpg_pack.c); this builder only ever installs
already-built .lpg files (see packages.py/image.py), which needs no
packer at all.

The mkfs_lufirafs source itself (tools/mkfs_lufirafs.c) stays in
LufiraOS — it pulls in kernel/fs/lufirafs/lufirafs_format.h via a relative
include, and duplicating that header into LufiraOS-Builder would be a
needless risk of drift. The builder just compiles it from the given path.
"""

import subprocess
from pathlib import Path


def ensure_repo(path: Path, git_url: str, label: str) -> None:
    """Clones git_url into path if it doesn't exist yet — the point (user's
    request: "a user should be able to just download the builder and the
    builder pulls everything itself") is that a bare checkout of THIS
    repository alone is enough to run `python3 build.py run`: no sibling
    LufiraOS checkout has to be prepared by hand first. Does nothing if
    path already exists (whatever's there — a real checkout, a symlink, a
    deliberately different version — is left alone; this only fills in a
    MISSING directory, never touches or updates an existing one)."""
    if path.exists():
        return
    print(f"  {label} not found at {path} — cloning {git_url}")
    subprocess.run(["git", "clone", git_url, str(path)], check=True)


def build_kernel_and_bootloader(lufira_repo: Path) -> None:
    """Runs `make kernel bootloader` in LufiraOS's trimmed-down Makefile."""
    subprocess.run(
        ["make", "kernel", "bootloader"],
        cwd=lufira_repo,
        check=True,
    )


def compile_host_tools(lufira_repo: Path, out_dir: Path) -> dict:
    """Compiles mkfs_lufirafs, returns its path."""
    out_dir.mkdir(parents=True, exist_ok=True)

    mkfs_bin = out_dir / "mkfs_lufirafs"
    subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-o", str(mkfs_bin),
         str(lufira_repo / "tools" / "mkfs_lufirafs.c")],
        check=True,
    )

    return {"mkfs_lufirafs": mkfs_bin}
