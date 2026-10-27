"""Assembles disk.img: the ESP (FAT12, bootloader+kernel.bin) + the
LufiraFS region (seed files, userspace programs, .lpg packages).

All of this logic used to live in LufiraOS/Makefile — v0.7 plan, stage 3,
step 3.0 moves it here entirely, cutting the original Makefile down to
"just kernel.bin + the bootloader".
"""

import re
import struct
import subprocess
import tempfile
from pathlib import Path

from . import config, legacy_boot, lpg

_VERSION_H_RE = re.compile(r"#define\s+LUFIRA_VERSION_(MAJOR|MINOR|PATCH)\s+(\d+)")


def _read_kernel_version(lufira_repo: Path) -> tuple:
    """kernel/version.h, same plain-regex read as lufira-packages'
    build.py — the one place a package's min_kernel_version (lpg.py) gets
    compared against the kernel this IMAGE is actually being built with,
    catching a mismatch at assembly time instead of at first boot."""
    text = (lufira_repo / "kernel" / "version.h").read_text()
    parts = dict(_VERSION_H_RE.findall(text))
    return (int(parts["MAJOR"]), int(parts["MINOR"]), int(parts["PATCH"]))


def _run(cmd) -> None:
    subprocess.run(cmd, check=True)


def mkfs(mkfs_bin: Path, cmd: str, image: Path, cfg: config.BuildConfig, *args) -> None:
    # offset — байт, где НАЧИНАЕТСЯ партиция 2 (LufiraFS), не 0: v1.0.0
    # переход на настоящий MBR (см. module docstring, _write_mbr() ниже) —
    # LufiraFS больше не сразу после ESP с нулевого байта диска, а в своей
    # партиции, сдвинутой на esp_partition_start/lufirafs_partition_start.
    _run([str(mkfs_bin), cmd, str(image), str(cfg.lufirafs_partition_start), str(cfg.region_size),
          *[str(a) for a in args]])


def _lba_to_chs(lba: int) -> bytes:
    """Классическая C/H/S-кодировка партиции в MBR (63 секторов/трек, 255
    головок/цилиндр) — свёртывается до максимума (1023,254,63), если LBA
    не влезает в 3 байта CHS. Ни BIOS (у нас только LBA-пути, INT13
    extensions), ни UEFI (игнорирует CHS, см. UEFI spec, "legacy MBR" ESP-
    discovery по LBA-полям) это реально не читают — пишем корректно просто
    чтобы диск не выглядел странно для сторонних инструментов (fdisk и т.п.)."""
    heads, sectors = 255, 63
    cyl = lba // (heads * sectors)
    rem = lba % (heads * sectors)
    head = rem // sectors
    sector = (rem % sectors) + 1
    if cyl > 1023:
        cyl, head, sector = 1023, 254, 63
    b0 = head & 0xFF
    b1 = ((cyl >> 2) & 0xC0) | (sector & 0x3F)
    b2 = cyl & 0xFF
    return bytes([b0, b1, b2])


def _partition_entry(bootable: bool, type_byte: int, start_lba: int, num_sectors: int) -> bytes:
    boot = 0x80 if bootable else 0x00
    chs_start = _lba_to_chs(start_lba)
    chs_end = _lba_to_chs(start_lba + num_sectors - 1)
    return struct.pack("<B3sB3sII", boot, chs_start, type_byte, chs_end, start_lba, num_sectors)


def _write_mbr(cfg: config.BuildConfig) -> None:
    """LBA0: 446 байт boot-кода (нули здесь — legacy_boot.patch_mbr_gap()
    допишет stage1 поверх, read-modify-write, если cfg.legacy_bios) +
    таблица из 2 партиций (ESP 0xEF, LufiraFS 0xDA, см. config.py) +
    0x55AA. disk_img уже должен существовать и быть нужного размера (см.
    build_esp() — создаётся dd'ом ДО вызова этой функции)."""
    sector = 512
    entry1 = _partition_entry(True, config.MBR_PARTITION_TYPE_ESP,
                               cfg.esp_partition_start // sector, cfg.esp_size // sector)
    entry2 = _partition_entry(False, config.MBR_PARTITION_TYPE_LUFIRAFS,
                               cfg.lufirafs_partition_start // sector, cfg.region_size // sector)

    mbr = bytearray(sector)
    mbr[0x1BE:0x1BE + 16] = entry1
    mbr[0x1CE:0x1CE + 16] = entry2
    mbr[0x1FE:0x200] = bytes([0x55, 0xAA])

    with cfg.disk_img.open("r+b") as f:
        f.seek(0)
        f.write(mbr)


def build_esp(cfg: config.BuildConfig, bootx64_efi: Path, kernel_bin: Path,
              legacy_boot_bins: dict | None = None) -> None:
    """legacy_boot_bins, when given (cfg.legacy_bios build), is
    {"stage1": Path, "stage2": Path, "legacy_main": Path} — see
    legacy_boot.py for how they get patched into the MBR gap, now that
    disk.img has a real partition table (v1.0.0 план — переход с
    суперфлоппи на MBR: ESP + LufiraFS как настоящие партиции)."""
    image = cfg.disk_img
    esp_img = cfg.out_dir / "esp.img"

    _run(["dd", "if=/dev/zero", f"of={image}", "bs=1024",
          f"count={cfg.disk_total_size // 1024}", "status=none"])
    _write_mbr(cfg)

    _run(["dd", "if=/dev/zero", f"of={esp_img}", "bs=1024",
          f"count={cfg.esp_size // 1024}", "status=none"])
    # Больше НЕ нужен "-R reserved sectors" трюк — у legacy-цепочки теперь
    # честное место в MBR-зазоре ДО этой партиции (см. legacy_boot.py),
    # а не внутри самой FAT12.
    _run(["mkfs.fat", "-F", "12", "-S", "512", str(esp_img)])
    _run(["mmd", "-i", str(esp_img), "::/EFI"])
    _run(["mmd", "-i", str(esp_img), "::/EFI/BOOT"])
    _run(["mcopy", "-i", str(esp_img), str(bootx64_efi), "::/EFI/BOOT/BOOTX64.EFI"])
    _run(["mcopy", "-i", str(esp_img), str(kernel_bin), "::/kernel.bin"])

    with image.open("r+b") as disk_f, esp_img.open("rb") as esp_f:
        disk_f.seek(cfg.esp_partition_start)
        disk_f.write(esp_f.read())
    esp_img.unlink()

    if legacy_boot_bins is not None:
        legacy_boot.patch_mbr_gap(image, legacy_boot_bins["stage1"], legacy_boot_bins["stage2"],
                                   legacy_boot_bins["legacy_main"], kernel_bin)


def populate_lufirafs(cfg: config.BuildConfig, mkfs_bin: Path) -> None:
    """Directories + seed files + /bin/shell.elf. Every other program goes
    through install_packages() below.
    shell.elf is the deliberate exception: not a dlpg package (dlpg never
    sees it in /etc/packages/installed, can't "remove" it — fatal, since
    the kernel loads it directly on every boot/respawn via
    spawn_shell_process() in kernel.c). Source: lufira-packages/shell/,
    built by that repo's build.py — see config.SHELL_ELF_PATH.
    """
    image = cfg.disk_img
    mkfs(mkfs_bin, "format", image, cfg)

    for d in config.DEFAULT_DIRS:
        mkfs(mkfs_bin, "mkdir", image, cfg, d)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        tf.write(config.DEFAULT_README_CONTENT)
        readme_path = tf.name
    mkfs(mkfs_bin, "put", image, cfg, readme_path, config.DEFAULT_README_DEST)
    Path(readme_path).unlink()

    mkfs(mkfs_bin, "put", image, cfg,
         cfg.lufira_repo / config.DEFAULT_SEED_PASSWD, "/etc/passwd")
    mkfs(mkfs_bin, "put", image, cfg,
         cfg.lufira_repo / config.DEFAULT_SEED_GROUP, "/etc/group")

    mkfs(mkfs_bin, "put", image, cfg,
         cfg.lufira_packages_repo / config.SHELL_ELF_PATH, "/bin/shell.elf", "755")

    mkfs(mkfs_bin, "put", image, cfg,
         cfg.lufira_packages_repo / config.LIBC_SO_PATH, "/lib/libc.so", "644")


def install_packages(cfg: config.BuildConfig, mkfs_bin: Path) -> None:
    """Installs cfg.packages into the image being built, using the same
    logic as dlpg install (see lpg.plan_install) — but writes files
    directly via mkfs_lufirafs put, with no running OS involved.
    """
    if not cfg.packages:
        return

    image = cfg.disk_img
    mkfs(mkfs_bin, "mkdir", image, cfg, "/etc/packages")

    kernel_version = _read_kernel_version(cfg.lufira_repo)

    installed: list = []
    for pkg_path in cfg.packages:
        pkg = lpg.parse_lpg(pkg_path)
        plan = lpg.plan_install(pkg, installed, allow_existing=True, kernel_version=kernel_version)

        for dest_path, data, mode in plan.files:
            with tempfile.NamedTemporaryFile("wb", delete=False) as tf:
                tf.write(data)
                tmp_path = tf.name
            mkfs(mkfs_bin, "put", image, cfg, tmp_path, dest_path, oct(mode)[2:])
            Path(tmp_path).unlink()
            print(f"  installed {dest_path} ({len(data)} bytes) from {Path(pkg_path).name}")

        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write(plan.receipt_text)
            receipt_tmp = tf.name
        mkfs(mkfs_bin, "put", image, cfg, receipt_tmp, f"/etc/packages/{plan.name}.files")
        Path(receipt_tmp).unlink()

        installed = plan.installed_after
        print(f"  package '{plan.name}' v{lpg.version_str(plan.version)} installed")

    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(lpg.render_installed_db(installed))
        db_tmp = tf.name
    mkfs(mkfs_bin, "put", image, cfg, db_tmp, "/etc/packages/installed")
    Path(db_tmp).unlink()


def assemble(cfg: config.BuildConfig, mkfs_bin: Path, bootx64_efi: Path, kernel_bin: Path,
             legacy_boot_bins: dict | None = None) -> Path:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    build_esp(cfg, bootx64_efi, kernel_bin, legacy_boot_bins)
    populate_lufirafs(cfg, mkfs_bin)
    install_packages(cfg, mkfs_bin)
    _run(["sync"])
    return cfg.disk_img
