#!/usr/bin/env python3
"""LufiraOS-Builder — assembles disk.img from the already-built
kernel.bin/BOOTX64.EFI (LufiraOS/Makefile now only produces those) plus
seed files/.lpg packages, then takes over the old run/debug/monitor
Makefile targets to run QEMU on the image.

Console CLI; gui.py is a Tkinter front-end over this same module.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lufira_builder import config, flash, image, iso, packages, qemu, tests, tools


def _app_root() -> Path:
    # __file__ не указывает на реальный каталог на диске при запуске из
    # однофайловой PyInstaller-сборки — используем каталог самого бинарника.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--lufira-repo", type=Path,
                   default=_app_root().parent / "LufiraOS",
                   help="path to the LufiraOS repository (default: sibling of this repository)")
    p.add_argument("--out-dir", type=Path, default=Path("build"),
                   help="directory for disk.img and intermediate files")
    p.add_argument("--package", action="append", dest="packages", default=[],
                   metavar="PATH.lpg",
                   help="install an extra .lpg package during image build (may be repeated)")
    p.add_argument("--no-build-kernel", action="store_false", dest="build_kernel",
                   help="don't run `make kernel bootloader` in LufiraOS, use already-built files")
    p.add_argument("--legacy-bios", action="store_true",
                   help="also embed the legacy BIOS boot chain (boot_legacy/ in LufiraOS) into "
                        "disk.img, alongside the UEFI path — one image, bootable both ways")
    p.add_argument("--boot-mode", choices=("uefi", "bios"), default="uefi",
                   help="which firmware `run`/`debug`/`monitor` launch QEMU with (default: uefi). "
                        "\"bios\" only works on an image built with --legacy-bios")
    p.add_argument("--no-default-packages", action="store_false", dest="install_default_packages",
                   help="don't install du/df/free/cp/mv/ls/.../dlpg (shell.elf is still built/staged — not optional)")
    p.add_argument("--only-package", action="append", dest="only_packages", default=None,
                   metavar="NAME",
                   help="install only this default package (by name, may be repeated) instead of "
                        "all of them; overrides --no-default-packages")
    p.add_argument("--profile", choices=config.INSTALL_PROFILE_CHOICES, default=None,
                   help="install one of the named profiles instead of picking packages by hand: "
                        "minimal (base only), minimal_wm (base + wm + settings + terminal), "
                        "full (every package except doom) — drivers (netd/soundd) are unaffected, "
                        "see --no-drivers/--driver. Mutually exclusive with --only-package/"
                        "--no-default-packages.")
    p.add_argument("--no-drivers", action="store_false", dest="install_drivers",
                   help="don't install any userspace driver daemon (netd/soundd, see "
                        "config.DRIVER_PACKAGES) — the image boots with no network/sound support")
    p.add_argument("--driver", action="append", dest="only_drivers", default=None,
                   metavar="NAME", choices=config.DRIVER_PACKAGES,
                   help="install only this userspace driver daemon (by name, may be repeated) "
                        "instead of all of them; overrides --no-drivers")
    p.add_argument("--lufira-packages-repo", type=Path,
                   default=_app_root().parent / config.LUFIRA_PACKAGES_REPO,
                   help="path to the lufira-packages repository (only used with "
                        "--build-packages-from-source; default: sibling of this repository)")
    p.add_argument("--build-packages-from-source", action="store_true",
                   help="build lufira-packages from a local source checkout (--lufira-packages-repo) "
                        "instead of the default of downloading prebuilt .lpg files straight from its "
                        "published index.json — use this when actively developing packages")
    p.add_argument("--bios-path", default=None,
                   help="override the auto-detected OVMF firmware path used for UEFI boot "
                        f"(auto-detected default on this machine: {config.BIOS_PATH})")
    p.add_argument("--audio", choices=config.AUDIO_CHOICES, default="ac97",
                   help="which audio controller run/debug/monitor attach (default: ac97) — "
                        "exercises drivers/sound/ac97.c or hda.c in the guest; \"none\" omits "
                        "an audio device entirely")
    p.add_argument("--net-device", choices=config.NET_CHOICES, default="rtl8139",
                   help="which NIC run/debug/monitor attach (default: rtl8139) — exercises "
                        "drivers/net/rtl8139.c, e1000.c or e1000e.c in the guest; \"none\" omits "
                        "a NIC entirely")
    p.add_argument("--disk-mode", choices=config.DISK_CHOICES, default="ide",
                   help="which controller run/debug/monitor attach disk.img through (default: "
                        "ide, legacy ATA) — \"ahci\"/\"nvme\" exercise drivers/disk/ahci.c or "
                        "nvme.c in the guest instead")


def make_config(args) -> config.BuildConfig:
    return config.BuildConfig(
        lufira_repo=args.lufira_repo,
        lufira_packages_repo=args.lufira_packages_repo,
        out_dir=args.out_dir,
        packages=list(args.packages),
        build_kernel=args.build_kernel,
        legacy_bios=args.legacy_bios,
        boot_mode=args.boot_mode,
        install_default_packages=args.install_default_packages,
        only_packages=set(args.only_packages) if args.only_packages else None,
        profile=args.profile,
        install_drivers=args.install_drivers,
        only_drivers=set(args.only_drivers) if args.only_drivers else None,
        bios_path=args.bios_path,
        audio=args.audio,
        net_device=args.net_device,
        disk_mode=args.disk_mode,
    )


def do_build(args) -> config.BuildConfig:
    cfg = make_config(args)

    if cfg.profile is not None and (cfg.only_packages is not None or not cfg.install_default_packages):
        sys.exit("error: --profile is mutually exclusive with --only-package/--no-default-packages "
                 "— a profile already decides which default packages to install")

    # Пользователь: "сборщик сам всё подтянет" — LufiraOS нужен ВСЕГДА
    # (ядро собирается только локально, готового релиза ядра нет и не
    # планируется), независимо от режима получения пакетов ниже.
    print("=== Checking sibling repositories ===")
    tools.ensure_repo(cfg.lufira_repo, config.LUFIRA_OS_GIT_URL, "LufiraOS")

    if cfg.build_kernel:
        print("=== Building kernel + bootloader (LufiraOS Makefile) ===")
        tools.build_kernel_and_bootloader(cfg.lufira_repo, legacy_bios=cfg.legacy_bios)

    bootx64_efi = cfg.lufira_repo / "build" / "BOOTX64.EFI"
    kernel_bin = cfg.lufira_repo / "build" / "kernel.bin"
    if not bootx64_efi.exists() or not kernel_bin.exists():
        sys.exit(f"error: {bootx64_efi} / {kernel_bin} not found — run `make kernel bootloader` in "
                  f"{cfg.lufira_repo} first, or drop --no-build-kernel")

    legacy_boot_bins = None
    if cfg.legacy_bios:
        legacy_dir = cfg.lufira_repo / "build" / "legacy_boot"
        legacy_boot_bins = {
            "stage1": legacy_dir / "stage1.bin",
            "stage2": legacy_dir / "stage2.bin",
            "legacy_main": legacy_dir / "legacy_main.bin",
        }
        missing = [str(p) for p in legacy_boot_bins.values() if not p.exists()]
        if missing:
            sys.exit(f"error: --legacy-bios given but missing {missing} — run `make bootloader-bios` "
                      f"in {cfg.lufira_repo} first, or drop --no-build-kernel")

    print("=== Compiling host tools (mkfs_lufirafs) ===")
    host_tools = tools.compile_host_tools(cfg.lufira_repo, cfg.out_dir)

    # lufira-packages also provides shell.elf (config.SHELL_ELF_PATH) — not
    # optional, populate_lufirafs() needs it regardless of
    # --no-default-packages. Default: download prebuilt packages from
    # lufira-packages' index.json (see packages.py); --build-packages-from-
    # source builds a local checkout instead, for package development.
    if args.build_packages_from_source:
        tools.ensure_repo(args.lufira_packages_repo, config.LUFIRA_PACKAGES_GIT_URL, "lufira-packages")
        print(f"=== Building lufira-packages from source ({args.lufira_packages_repo}) ===")
        default_lpgs, pkg_categories = packages.fetch_default_packages(args.lufira_packages_repo, cfg.lufira_repo)
    else:
        print("=== Fetching lufira-packages release (prebuilt .lpg via index.json) ===")
        cache_dir = cfg.out_dir / "packages_cache"
        default_lpgs, runtime_root, pkg_categories = packages.fetch_default_packages_remote(cache_dir)
        # fetch_default_packages_remote() lays cache_dir out in the same
        # shape image.py expects (build/shell.elf, build/libc.so), so
        # pointing cfg.lufira_packages_repo there needs no change to image.py.
        cfg.lufira_packages_repo = runtime_root

    if cfg.profile is not None:
        cfg.only_packages = config.resolve_profile_packages(cfg.profile, pkg_categories)
        print(f"=== Profile '{cfg.profile}' selects: {', '.join(sorted(cfg.only_packages))} ===")

    # Драйверы (netd/soundd) отобраны отдельным разделом --driver/--no-drivers
    # (аналогично --only-package/--no-default-packages, но независимо от
    # них — драйвер не "обычный" пакет, см. config.DRIVER_PACKAGES) — делим
    # default_lpgs на две группы и фильтруем каждую своей парой флагов.
    driver_names = set(config.DRIVER_PACKAGES)
    driver_lpgs = [p for p in default_lpgs if p.stem in driver_names]
    other_lpgs = [p for p in default_lpgs if p.stem not in driver_names]

    if cfg.only_packages is not None:
        other_lpgs = [p for p in other_lpgs if p.stem in cfg.only_packages]
    elif not cfg.install_default_packages:
        other_lpgs = []

    if cfg.only_drivers is not None:
        driver_lpgs = [p for p in driver_lpgs if p.stem in cfg.only_drivers]
    elif not cfg.install_drivers:
        driver_lpgs = []

    cfg.packages = [str(p) for p in other_lpgs + driver_lpgs] + cfg.packages

    print("=== Assembling disk image ===")
    disk_img = image.assemble(cfg, host_tools["mkfs_lufirafs"], bootx64_efi, kernel_bin, legacy_boot_bins)
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


def cmd_iso(args) -> None:
    cfg = do_build(args)
    out_path = args.out_iso or (cfg.out_dir / "lufiraos-installer.iso")
    print("=== Building installer ISO ===")
    result = iso.build_iso(cfg, Path(out_path))
    print(f"ISO created: {result}")
    print("WARNING: neither the UEFI nor the legacy BIOS boot path has been confirmed to actually "
          "boot from this ISO yet (fails with a FAT read error under QEMU+OVMF) — see "
          "lufira_builder/iso.py's module docstring. Needs the bootloader rework (see README/plan, "
          "\"superfloppy\"/whole-image-read) before this is a real installer medium.")


def cmd_flash_list(args) -> None:
    devices = flash.list_removable_devices()
    if not devices:
        print("No removable devices found (lsblk missing, or none plugged in).")
        return
    for d in devices:
        mp = f" mounted at {', '.join(d['mountpoints'])}" if d["mountpoints"] else ""
        print(f"  {d['path']:<14} {d['size_human']:>8}  {d['model']}{mp}")


def cmd_flash_write(args) -> None:
    image_path = Path(args.image)
    if not image_path.exists():
        sys.exit(f"error: {image_path} not found")

    devices = {d["path"]: d for d in flash.list_removable_devices()}
    info = devices.get(args.device)
    if info is None and not args.force:
        sys.exit(f"error: {args.device} is not a recognized removable device (lsblk RM=1) — "
                  f"pass --force only if you are certain this is the right drive")

    size_line = f" ({info['size_human']}, {info['model']})" if info else " (--force, unverified device)"
    print(f"About to overwrite EVERYTHING on {args.device}{size_line} with {image_path}.")
    if not args.yes:
        answer = input("Type the device path again to confirm: ").strip()
        if answer != args.device:
            print("Aborted — input did not match.")
            return

    def _progress(line):
        print(line, end="", flush=True)

    flash.write_image(image_path, args.device, force=args.force, progress_cb=_progress)
    print(f"\ndone: {image_path} -> {args.device}")


def cmd_doctor(args) -> None:
    """Preflight: проверяет, что все внешние тулы на месте (нужно
    особенно на свежей системе — v1.0.0 план, этап 2). --yes ставит
    недостающее через `sudo apt-get install` без вопроса (для
    неинтерактивных вызовов, напр. из будущего CI); без --yes спрашивает
    один раз перед sudo, ровно как обычный `apt install` попросил бы."""
    rows = tools.check_tools()
    missing = tools.missing_tools(rows)

    width = max(len(r["name"]) for r in rows)
    for r in rows:
        mark = "OK  " if r["found"] else ("MISSING (optional)" if not r["required"] else "MISSING")
        print(f"  {r['name']:<{width}}  {mark:<20} {r['reason']}")

    if not missing:
        print("\ndoctor: все найдено, можно собирать.")
        return

    required_missing = [r for r in missing if r["required"]]
    print(f"\ndoctor: не найдено {len(missing)} из {len(rows)}"
          + (f", включая {len(required_missing)} обязательных" if required_missing else " (все опциональные)"))

    if missing[0]["apt_pkg"] == "ovmf" and len(missing) == 1:
        pass  # ovmf отдельно обрабатывается ниже тем же путём, просто пояснение не нужно
    pkgs = sorted({r["apt_pkg"] for r in missing})
    print(f"Пакеты (apt): {' '.join(pkgs)}")

    if not args.yes:
        answer = input("Поставить через `sudo apt-get install -y ...` сейчас? [y/N] ").strip().lower()
        if answer not in ("y", "yes", "д", "да"):
            print("Пропущено — поставь вручную и перезапусти doctor.")
            return
    if not tools.install_missing(missing):
        print("apt-get не найден (не Ubuntu/Debian) — поставь вручную через свой пакетный менеджер: "
              + " ".join(pkgs))
        return
    print("\ndoctor: повторная проверка после установки —")
    for r in tools.check_tools():
        if r["apt_pkg"] in pkgs:
            print(f"  {r['name']:<{width}}  {'OK' if r['found'] else 'ещё MISSING'}")


def cmd_clear(args) -> None:
    """Guaranteed from-scratch rebuild: `make clean` in LufiraOS (removes
    kernel.bin/BOOTX64.EFI/*.o) plus this repo's --out-dir. lufira-packages
    needs no help — its own build.py already rmtree's its out_dir on every run.
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

    p_iso = sub.add_parser("iso", help="assemble disk.img and wrap it as a hybrid UEFI+BIOS installer ISO "
                                        "(UEFI boot confirmed working; BIOS boot from the ISO is experimental)")
    add_common_args(p_iso)
    p_iso.add_argument("--out-iso", type=Path, default=None,
                        help="output .iso path (default: <out-dir>/lufiraos-installer.iso)")
    p_iso.set_defaults(func=cmd_iso)

    p_flash_list = sub.add_parser("flash-list", help="list removable (USB) devices disk.img/the ISO could be written to")
    p_flash_list.set_defaults(func=cmd_flash_list)

    p_flash = sub.add_parser("flash", help="write a disk.img/ISO straight to a USB device (DESTRUCTIVE — overwrites the whole device)")
    p_flash.add_argument("--image", required=True, type=Path, help="path to disk.img or an installer .iso")
    p_flash.add_argument("--device", required=True, help="target block device, e.g. /dev/sdb (see `flash-list`)")
    p_flash.add_argument("--force", action="store_true",
                          help="write even if the device isn't recognized as removable by lsblk — dangerous")
    p_flash.add_argument("--yes", action="store_true",
                          help="skip the interactive confirmation (type-the-device-path-back) prompt")
    p_flash.set_defaults(func=cmd_flash_write)

    p_doctor = sub.add_parser("doctor", help="check that all external tools (gcc/nasm/mkfs.fat/qemu/xorriso/...) are installed")
    p_doctor.add_argument("--yes", action="store_true",
                           help="install missing packages via `sudo apt-get install -y` without asking")
    p_doctor.set_defaults(func=cmd_doctor)

    p_clear = sub.add_parser("clear", help="remove all build output (LufiraOS `make clean` + --out-dir) for a from-scratch rebuild")
    p_clear.add_argument("--lufira-repo", type=Path,
                          default=_app_root().parent / "LufiraOS",
                          help="path to the LufiraOS repository (default: sibling of this repository)")
    p_clear.add_argument("--out-dir", type=Path, default=Path("build"),
                          help="directory for disk.img and intermediate files")
    p_clear.set_defaults(func=cmd_clear)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
