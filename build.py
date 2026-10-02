#!/usr/bin/env python3
"""LufiraOS-Builder — assembles disk.img from the already-built
kernel.bin/BOOTX64.EFI (LufiraOS/Makefile now only produces those, v0.7
plan, stage 3, step 3.0) plus the set of seed files/.lpg packages.
Takes over the role of the old run/debug/monitor Makefile targets — runs
QEMU on top of the image it just assembled.

Console CLI for now (see the plan); a GUI, if one is ever needed, would sit
on top of the same lufira_builder/ logic as a separate layer.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lufira_builder import config, image, packages, qemu, tests, tools


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--lufira-repo", type=Path,
                   default=Path(__file__).resolve().parent.parent / "LufiraOS",
                   help="path to the LufiraOS repository (default: sibling of this repository)")
    p.add_argument("--out-dir", type=Path, default=Path("build"),
                   help="directory for disk.img and intermediate files")
    p.add_argument("--package", action="append", dest="packages", default=[],
                   metavar="PATH.lpg",
                   help="install an extra .lpg package during image build (may be repeated)")
    p.add_argument("--no-build-kernel", action="store_false", dest="build_kernel",
                   help="don't run `make kernel bootloader` in LufiraOS, use already-built files")
    p.add_argument("--no-default-packages", action="store_false", dest="install_default_packages",
                   help="don't install du/df/free/cp/mv/ls/.../dlpg (shell.elf is still built/staged — not optional)")
    p.add_argument("--only-package", action="append", dest="only_packages", default=None,
                   metavar="NAME",
                   help="install only this default package (by name, may be repeated) instead of "
                        "all of them; overrides --no-default-packages")
    p.add_argument("--lufira-packages-repo", type=Path,
                   default=Path(__file__).resolve().parent.parent / config.LUFIRA_PACKAGES_REPO,
                   help="path to the lufira-packages repository (default: sibling of this repository)")


def make_config(args) -> config.BuildConfig:
    return config.BuildConfig(
        lufira_repo=args.lufira_repo,
        lufira_packages_repo=args.lufira_packages_repo,
        out_dir=args.out_dir,
        packages=list(args.packages),
        build_kernel=args.build_kernel,
        install_default_packages=args.install_default_packages,
        only_packages=set(args.only_packages) if args.only_packages else None,
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

    print("=== Compiling host tools (mkfs_lufirafs) ===")
    host_tools = tools.compile_host_tools(cfg.lufira_repo, cfg.out_dir)

    # lufira-packages also builds shell.elf (see config.SHELL_ELF_PATH) —
    # NOT optional, populate_lufirafs() needs it regardless of
    # --no-default-packages, so this build always runs. Only whether the
    # resulting du/df/free/cp/mv/... .lpg files get INSTALLED is optional.
    print(f"=== Building lufira-packages ({args.lufira_packages_repo}) ===")
    default_lpgs = packages.fetch_default_packages(args.lufira_packages_repo, cfg.lufira_repo)
    if cfg.only_packages is not None:
        default_lpgs = [p for p in default_lpgs if p.stem in cfg.only_packages]
        cfg.packages = [str(p) for p in default_lpgs] + cfg.packages
    elif cfg.install_default_packages:
        cfg.packages = [str(p) for p in default_lpgs] + cfg.packages

    print("=== Assembling disk image ===")
    disk_img = image.assemble(cfg, host_tools["mkfs_lufirafs"], bootx64_efi, kernel_bin)
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

    tests_archive = args.tests_archive
    if tests_archive is None:
        tests_archive = (args.lufira_repo.parent / "lufira-tests" / "dist" /
                          "lufira-tests-latest.tar.gz")

    print(f"=== Fetching tests archive ({tests_archive}) ===")
    tests_elf_dir = tests.fetch_tests_archive(tests_archive)
    qemu.qemu_debug(cfg, mkfs_bin, tests_elf_dir)


def cmd_monitor(args) -> None:
    cfg = do_build(args)
    qemu.qemu_monitor(cfg)


def cmd_clear(args) -> None:
    """Wipes everything a `build` would otherwise reuse incrementally, for
    a guaranteed-from-scratch rebuild — `make clean` in LufiraOS (removes
    kernel.bin/BOOTX64.EFI/*.o, see its own Makefile `clean` target) plus
    this repository's own --out-dir (disk.img/mkfs_lufirafs/usbstick.img).
    lufira-packages needs no help here: its own build.py already does
    `shutil.rmtree(out_dir)` unconditionally on every run (see the PACKAGES
    loop there), so it never goes stale on its own.
    """
    print(f"=== make clean ({args.lufira_repo}) ===")
    subprocess.run(["make", "clean"], cwd=args.lufira_repo, check=True)

    print(f"=== Removing {args.out_dir} ===")
    shutil.rmtree(args.out_dir, ignore_errors=True)
    print("clear: done")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="assemble disk.img")
    add_common_args(p_build)
    p_build.set_defaults(func=cmd_build)

    p_run = sub.add_parser("run", help="assemble disk.img and launch QEMU (like the old `make run`)")
    add_common_args(p_run)
    p_run.set_defaults(func=cmd_run)

    p_debug = sub.add_parser("debug", help="assemble, add /tests + a USB stick, launch QEMU (like `make debug`)")
    add_common_args(p_debug)
    p_debug.add_argument(
        "--tests-archive", default=None,
        help="local path or http(s) URL to the lufira-tests release .tar.gz "
             "(default: dist/lufira-tests-latest.tar.gz in a sibling lufira-tests checkout — "
             "there's no release server yet, so this is a local stand-in for a future release URL)")
    p_debug.set_defaults(func=cmd_debug)

    p_monitor = sub.add_parser("monitor", help="assemble and launch QEMU with the HMP monitor (like `make monitor`)")
    add_common_args(p_monitor)
    p_monitor.set_defaults(func=cmd_monitor)

    p_clear = sub.add_parser("clear", help="remove all build output (LufiraOS `make clean` + --out-dir) for a from-scratch rebuild")
    p_clear.add_argument("--lufira-repo", type=Path,
                          default=Path(__file__).resolve().parent.parent / "LufiraOS",
                          help="path to the LufiraOS repository (default: sibling of this repository)")
    p_clear.add_argument("--out-dir", type=Path, default=Path("build"),
                          help="directory for disk.img and intermediate files")
    p_clear.set_defaults(func=cmd_clear)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
