"""Упаковка "базовых" user-пакетов (v0.7 план, этап 4) в .lpg через
lpg_pack (этап 2) — манифест генерируется на лету, потому что путь до
исходного .elf зависит от --lufira-repo, а lpg_pack принимает только
готовый текстовый манифест с уже подставленными путями.
"""

import subprocess
import tempfile
from pathlib import Path

from . import config


def _run(cmd) -> None:
    subprocess.run(cmd, check=True)


def build_packages(cfg: config.BuildConfig, lpg_pack_bin: Path, package_specs: list) -> list:
    """Пакует список описаний (см. config.DEFAULT_USER_PACKAGES/
    DEFAULT_BASE_PACKAGES) в .lpg, возвращает пути к собранным файлам."""
    pkg_dir = cfg.out_dir / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    lpg_paths = []
    for pkg in package_specs:
        elf_src = cfg.lufira_repo / pkg["elf"]
        dest = f"/bin/{Path(pkg['elf']).name}"
        manifest = (
            f"name={pkg['name']}\n"
            f"version={pkg['version']}\n"
            f"category={pkg['category']}\n"
            "depends=\n"
            "[files]\n"
            f"{elf_src} {dest} 755\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".manifest", delete=False) as tf:
            tf.write(manifest)
            manifest_path = tf.name

        lpg_path = pkg_dir / f"{pkg['name']}.lpg"
        _run([str(lpg_pack_bin), manifest_path, str(lpg_path)])
        Path(manifest_path).unlink()
        lpg_paths.append(lpg_path)

    return lpg_paths
