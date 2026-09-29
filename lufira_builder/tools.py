"""Хостовые инструменты: сборка kernel.bin/BOOTX64.EFI и вспомогательных
C-программ (mkfs_lufirafs, lpg_pack) из дерева репозитория LufiraOS.

Сами исходники (tools/mkfs_lufirafs.c, tools/lpg_pack.c) остаются жить в
LufiraOS — они подключают kernel/fs/lufirafs/lufirafs_format.h относительным
include'ом, и дублировать этот заголовок в LufiraOS-Builder было бы лишним
риском рассинхронизации. Builder просто компилирует их из указанного пути.
"""

import subprocess
from pathlib import Path


def build_kernel_and_bootloader(lufira_repo: Path) -> None:
    """Запускает `make kernel bootloader` в урезанном Makefile LufiraOS."""
    subprocess.run(
        ["make", "kernel", "bootloader"],
        cwd=lufira_repo,
        check=True,
    )


def compile_host_tools(lufira_repo: Path, out_dir: Path) -> dict:
    """Компилирует mkfs_lufirafs и lpg_pack, возвращает пути к бинарникам."""
    out_dir.mkdir(parents=True, exist_ok=True)

    mkfs_bin = out_dir / "mkfs_lufirafs"
    subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-o", str(mkfs_bin),
         str(lufira_repo / "tools" / "mkfs_lufirafs.c")],
        check=True,
    )

    lpg_pack_bin = out_dir / "lpg_pack"
    subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-o", str(lpg_pack_bin),
         str(lufira_repo / "tools" / "lpg_pack.c")],
        check=True,
    )

    return {"mkfs_lufirafs": mkfs_bin, "lpg_pack": lpg_pack_bin}
