"""Assembles disk.img: the ESP (FAT12, bootloader+kernel.bin) + the
LufiraFS region (seed files, userspace programs, .lpg packages).

All of this logic used to live in LufiraOS/Makefile — v0.7 plan, stage 3,
step 3.0 moves it here entirely, cutting the original Makefile down to
"just kernel.bin + the bootloader".
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


def assemble(cfg: config.BuildConfig, mkfs_bin: Path, bootx64_efi: Path, kernel_bin: Path) -> Path:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    build_esp(cfg, bootx64_efi, kernel_bin)
    populate_lufirafs(cfg, mkfs_bin)
    install_packages(cfg, mkfs_bin)
    _run(["sync"])
    return cfg.disk_img
