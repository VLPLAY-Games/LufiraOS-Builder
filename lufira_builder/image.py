"""Сборка disk.img: ESP (FAT12, bootloader+kernel.bin) + LufiraFS-регион
(seed-файлы, userspace-программы, .lpg-пакеты).

Раньше вся эта логика жила в LufiraOS/Makefile — v0.7 план, этап 3, шаг 3.0
переносит её сюда целиком, разрезая исходный Makefile на "только
kernel.bin + загрузчик".
"""

import subprocess
import tempfile
from pathlib import Path

from . import config, lpg


def _run(cmd) -> None:
    subprocess.run(cmd, check=True)


def mkfs(mkfs_bin: Path, cmd: str, image: Path, cfg: config.BuildConfig, *args) -> None:
    _run([str(mkfs_bin), cmd, str(image), str(cfg.esp_size), str(cfg.region_size), *[str(a) for a in args]])


def build_esp(cfg: config.BuildConfig, bootx64_efi: Path, kernel_bin: Path) -> None:
    image = cfg.disk_img
    esp_img = cfg.out_dir / "esp.img"

    _run(["dd", "if=/dev/zero", f"of={image}", "bs=1024",
          f"count={cfg.disk_total_size // 1024}", "status=none"])

    _run(["dd", "if=/dev/zero", f"of={esp_img}", "bs=1024",
          f"count={cfg.esp_size // 1024}", "status=none"])
    _run(["mkfs.fat", "-F", "12", "-S", "512", str(esp_img)])
    _run(["mmd", "-i", str(esp_img), "::/EFI"])
    _run(["mmd", "-i", str(esp_img), "::/EFI/BOOT"])
    _run(["mcopy", "-i", str(esp_img), str(bootx64_efi), "::/EFI/BOOT/BOOTX64.EFI"])
    _run(["mcopy", "-i", str(esp_img), str(kernel_bin), "::/kernel.bin"])
    _run(["dd", f"if={esp_img}", f"of={image}", "conv=notrunc", "status=none"])
    esp_img.unlink()


def populate_lufirafs(cfg: config.BuildConfig, mkfs_bin: Path, direct_stage_elf_files: list) -> None:
    """direct_stage_elf_files — программы, кладущиеся в /bin напрямую, минуя
    .lpg (сейчас только userspace/base/dlpg.elf — сам себя пакетом не
    ставит). Всё остальное (v0.7 план, этап 4: du/df/free/cpuload) идёт
    через install_packages() ниже.
    """
    image = cfg.disk_img
    mkfs(mkfs_bin, "format", image, cfg)

    for d in config.DEFAULT_DIRS:
        mkfs(mkfs_bin, "mkdir", image, cfg, d)

    for f in direct_stage_elf_files:
        mkfs(mkfs_bin, "put", image, cfg, f, f"/bin/{Path(f).name}", "755")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        tf.write(config.DEFAULT_README_CONTENT)
        readme_path = tf.name
    mkfs(mkfs_bin, "put", image, cfg, readme_path, config.DEFAULT_README_DEST)
    Path(readme_path).unlink()

    mkfs(mkfs_bin, "put", image, cfg,
         cfg.lufira_repo / config.DEFAULT_SEED_PASSWD, "/etc/passwd")
    mkfs(mkfs_bin, "put", image, cfg,
         cfg.lufira_repo / config.DEFAULT_SEED_GROUP, "/etc/group")


def install_packages(cfg: config.BuildConfig, mkfs_bin: Path) -> None:
    """Устанавливает cfg.packages в собираемый образ той же логикой, что и
    dlpg install (см. lpg.plan_install) — но пишет файлы напрямую через
    mkfs_lufirafs put, без работающей ОС.
    """
    if not cfg.packages:
        return

    image = cfg.disk_img
    mkfs(mkfs_bin, "mkdir", image, cfg, "/etc/packages")

    installed: list = []
    for pkg_path in cfg.packages:
        pkg = lpg.parse_lpg(pkg_path)
        plan = lpg.plan_install(pkg, installed, allow_existing=True)

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
             direct_stage_elf_files: list) -> Path:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    build_esp(cfg, bootx64_efi, kernel_bin)
    populate_lufirafs(cfg, mkfs_bin, direct_stage_elf_files)
    install_packages(cfg, mkfs_bin)
    _run(["sync"])
    return cfg.disk_img
