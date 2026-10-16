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


def _checkout_latest_release_tag(path: Path, label: str) -> None:
    """Pins a fresh clone to its latest SemVer release tag (vX.Y.Z,
    highest first) instead of leaving it on the default branch's HEAD — a
    from-scratch `python3 build.py run` (no prior sibling checkout) should
    build the latest RELEASED version, not whatever happens to be
    mid-development on main. No-op if the repo has no tags at all — e.g.
    lufira-packages, which ships releases as prebuilt .lpg via its own
    index.json instead (see packages.py), not as git tags."""
    result = subprocess.run(
        ["git", "-C", str(path), "tag", "--list", "v*", "--sort=-v:refname"],
        check=True, capture_output=True, text=True,
    )
    tags = [t for t in result.stdout.splitlines() if t.strip()]
    if not tags:
        return
    latest = tags[0]
    print(f"  {label}: pinning fresh clone to latest release {latest}")
    subprocess.run(["git", "-C", str(path), "checkout", latest], check=True)


def ensure_repo(path: Path, git_url: str, label: str) -> None:
    """Clones git_url into path if it doesn't exist yet, so a bare
    checkout of just this repository is enough to run `python3 build.py
    run` — no sibling LufiraOS checkout needed ahead of time. Leaves an
    existing path alone (checkout, symlink, whatever) — only fills in a
    missing directory, never updates one. A fresh clone is then pinned to
    the latest release tag, if the repo has any (see
    _checkout_latest_release_tag())."""
    if path.exists():
        return
    print(f"  {label} not found at {path} — cloning {git_url}")
    subprocess.run(["git", "clone", git_url, str(path)], check=True)
    _checkout_latest_release_tag(path, label)


def build_kernel_and_bootloader(lufira_repo: Path, legacy_bios: bool = False) -> None:
    """Runs `make kernel bootloader` in LufiraOS's trimmed-down Makefile,
    plus `make bootloader-bios` (stage1/stage2/legacy_main — see
    boot_legacy/) when legacy_bios is requested. That target needs nasm;
    left out of the default build so it stays an opt-in dependency."""
    subprocess.run(
        ["make", "kernel", "bootloader"],
        cwd=lufira_repo,
        check=True,
    )
    if legacy_bios:
        subprocess.run(
            ["make", "bootloader-bios"],
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
