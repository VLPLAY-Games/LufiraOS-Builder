"""LufiraOS image build settings.

All the sizes below used to live in LufiraOS/Makefile — after cutting the
Makefile down to "just kernel.bin + the bootloader" (v0.7 plan, stage 3,
step 3.0), the whole disk.img build moved here.
"""

from dataclasses import dataclass, field
from pathlib import Path

# Keep ESP_SIZE in sync with LUFIRAFS_ESP_SIZE in
# kernel/fs/lufirafs/lufirafs_format.h inside the LufiraOS repository —
# a mismatch means we'd format a different disk region than the one the
# kernel later reads (the same risk that used to be flagged directly in
# the Makefile).
DISK_TOTAL_SIZE = 16 * 1024 * 1024
ESP_SIZE = 4 * 1024 * 1024
REGION_SIZE = DISK_TOTAL_SIZE - ESP_SIZE

# Directories created in the LufiraFS region on every image build.
DEFAULT_DIRS = ["/system", "/logs", "/etc", "/bin", "/lib"]

# Seed files with fixed content/permissions — the same set the Makefile
# used to lay down directly.
DEFAULT_README_CONTENT = "Hello from LufiraOS!\n"
DEFAULT_README_DEST = "/readme.txt"
DEFAULT_SEED_PASSWD = "tools/seed/passwd"  # relative to the LufiraOS root
DEFAULT_SEED_GROUP = "tools/seed/group"

# Kernel loads this file directly on every boot/shell respawn
# (spawn_shell_process(), kernel.c) — not a dlpg package (see
# populate_lufirafs() in image.py for why). Source lives in
# lufira-packages/shell/, built straight to build/shell.elf (no .lpg
# wrapper). Path is relative to LUFIRA_PACKAGES_REPO's own --out-dir.
SHELL_ELF_PATH = "build/shell.elf"

# Та же логика, что у SHELL_ELF_PATH: единый .so, который ядро сам грузит
# и кэширует по фиксированному пути /lib/libc.so (dynlink_load_libc_cache(),
# dynlink.c) — не .lpg-пакет, он нужен всем пакетам сразу как часть платформы.
LIBC_SO_PATH = "build/libc.so"

BIOS_PATH = "/usr/share/ovmf/OVMF.fd"

# Package sources (cp/mv/ls/.../dlpg) moved out of LufiraOS/userspace/ into
# their own lufira-packages repo (own build.py, mirrors lufira-tests) so
# the kernel repo carries no package source/binaries — see packages.py.
LUFIRA_PACKAGES_REPO = "lufira-packages"  # sibling of this repository, like lufira_repo

# Автоклонирование недостающих sibling-репозиториев ("сборщик сам всё
# подтянет") — tools.ensure_repo() клонирует сюда, если каталога ещё нет.
LUFIRA_OS_GIT_URL = "https://github.com/VLPLAY-Games/LufiraOS"
LUFIRA_PACKAGES_GIT_URL = "https://github.com/VLPLAY-Games/lufira-packages"

# Тот же URL, что REMOTE_INDEX_URL в lufira-packages/base/dlpg.c — дефолтный
# путь получения пакетов (см. fetch_default_packages_remote() в packages.py);
# локальная сборка из исходников доступна через --build-packages-from-source.
LUFIRA_PACKAGES_INDEX_URL = (
    "https://raw.githubusercontent.com/VLPLAY-Games/lufira-packages/refs/heads/main/index.json"
)


@dataclass
class BuildConfig:
    lufira_repo: Path
    lufira_packages_repo: Path
    out_dir: Path
    disk_total_size: int = DISK_TOTAL_SIZE
    esp_size: int = ESP_SIZE
    packages: list = field(default_factory=list)  # list of paths to .lpg files
    build_kernel: bool = True
    install_default_packages: bool = True
    # None = install every default package. A set = install only the
    # named ones (--only-package in build.py / GUI checkboxes).
    only_packages: set = None

    @property
    def region_size(self) -> int:
        return self.disk_total_size - self.esp_size

    @property
    def disk_img(self) -> Path:
        return self.out_dir / "disk.img"

    @property
    def usbstick_img(self) -> Path:
        return self.out_dir / "usbstick.img"
