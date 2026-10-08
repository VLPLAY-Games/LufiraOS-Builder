"""Host-side tools: building kernel.bin/BOOTX64.EFI and the helper
mkfs_lufirafs program from the LufiraOS repository tree.

lpg_pack isn't built here anymore — packing .lpg from source happens
inside lufira-packages' own build.py; this builder only installs
already-built .lpg files (see packages.py/image.py).

mkfs_lufirafs.c itself stays in LufiraOS (it includes
kernel/fs/lufirafs/lufirafs_format.h via a relative path — duplicating
that header here would risk drift); the builder just compiles it in place.
"""

import subprocess
from pathlib import Path


def ensure_repo(path: Path, git_url: str, label: str) -> None:
    """Clones git_url into path if it doesn't exist yet, so a bare
    checkout of just this repository is enough to run `python3 build.py
    run` — no sibling LufiraOS checkout needed ahead of time. Leaves an
    existing path alone (checkout, symlink, whatever) — only fills in a
    missing directory, never updates one."""
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
