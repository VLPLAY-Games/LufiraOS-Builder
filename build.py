#!/usr/bin/env python3
"""LufiraOS-Builder — сборщик disk.img из готового kernel.bin/BOOTX64.EFI
(LufiraOS/Makefile теперь делает только их, v0.7 план, этап 3, шаг 3.0) и
набора seed-файлов/.lpg-пакетов. Наследует роль прежних run/debug/monitor
таргетов Makefile'а — запускает QEMU над собранным им же образом.

Консольный CLI — первый проход (см. план); GUI, если понадобится, ляжет
поверх той же lufira_builder/ логики отдельным слоем.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lufira_builder import config, image, qemu, tools


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--lufira-repo", type=Path,
                   default=Path(__file__).resolve().parent.parent / "LufiraOS",
                   help="путь к репозиторию LufiraOS (по умолчанию: сосед этого репозитория)")
    p.add_argument("--out-dir", type=Path, default=Path("build"),
                   help="каталог для disk.img и промежуточных файлов")
    p.add_argument("--package", action="append", dest="packages", default=[],
                   metavar="PATH.lpg",
                   help="установить .lpg-пакет во время сборки образа (можно несколько раз)")
    p.add_argument("--no-build-kernel", action="store_false", dest="build_kernel",
                   help="не запускать `make kernel bootloader` в LufiraOS, взять уже собранные файлы")


def make_config(args) -> config.BuildConfig:
    return config.BuildConfig(
        lufira_repo=args.lufira_repo,
        out_dir=args.out_dir,
        packages=args.packages,
        build_kernel=args.build_kernel,
    )


def do_build(args) -> config.BuildConfig:
    cfg = make_config(args)

    if cfg.build_kernel:
        print("=== Building kernel + bootloader (LufiraOS Makefile) ===")
        tools.build_kernel_and_bootloader(cfg.lufira_repo)

    bootx64_efi = cfg.lufira_repo / "build" / "BOOTX64.EFI"
    kernel_bin = cfg.lufira_repo / "build" / "kernel.bin"
    if not bootx64_efi.exists() or not kernel_bin.exists():
        sys.exit(f"error: {bootx64_efi} / {kernel_bin} not found — run `make kernel bootloader` in "
                  f"{cfg.lufira_repo} first, or drop --no-build-kernel")

    print("=== Compiling host tools (mkfs_lufirafs, lpg_pack) ===")
    host_tools = tools.compile_host_tools(cfg.lufira_repo, cfg.out_dir)

    userspace_elf_files = sorted(str(p) for p in (cfg.lufira_repo / "userspace").glob("**/*.elf"))

    print("=== Assembling disk image ===")
    disk_img = image.assemble(cfg, host_tools["mkfs_lufirafs"], bootx64_efi, kernel_bin, userspace_elf_files)
    print(f"disk image created: {disk_img}")
    return cfg


def cmd_build(args) -> None:
    do_build(args)


def cmd_run(args) -> None:
    cfg = do_build(args)
    qemu.qemu_run(cfg)


def cmd_debug(args) -> None:
    cfg = do_build(args)
    mkfs_bin = cfg.out_dir / "mkfs_lufirafs"
    tests_dir = args.tests_dir or (cfg.lufira_repo / "test")
    qemu.qemu_debug(cfg, mkfs_bin, tests_dir)


def cmd_monitor(args) -> None:
    cfg = do_build(args)
    qemu.qemu_monitor(cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="собрать disk.img")
    add_common_args(p_build)
    p_build.set_defaults(func=cmd_build)

    p_run = sub.add_parser("run", help="собрать disk.img и запустить QEMU (как старый `make run`)")
    add_common_args(p_run)
    p_run.set_defaults(func=cmd_run)

    p_debug = sub.add_parser("debug", help="собрать, добавить /tests + USB-флешку, запустить QEMU (как `make debug`)")
    add_common_args(p_debug)
    p_debug.add_argument("--tests-dir", type=Path, default=None,
                          help="каталог с тестовыми .elf (по умолчанию: <lufira-repo>/test)")
    p_debug.set_defaults(func=cmd_debug)

    p_monitor = sub.add_parser("monitor", help="собрать и запустить QEMU с HMP-монитором (как `make monitor`)")
    add_common_args(p_monitor)
    p_monitor.set_defaults(func=cmd_monitor)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
