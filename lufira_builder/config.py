"""LufiraOS image build settings.

All the sizes below used to live in LufiraOS/Makefile — after cutting the
Makefile down to "just kernel.bin + the bootloader" (v0.7 plan, stage 3,
step 3.0), the whole disk.img build moved here.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Keep ESP_SIZE in sync with LUFIRAFS_ESP_SIZE in
# kernel/fs/lufirafs/lufirafs_format.h inside the LufiraOS repository —
# a mismatch means we'd format a different disk region than the one the
# kernel later reads (the same risk that used to be flagged directly in
# the Makefile).
#
# Back to 16 MiB (12 MiB LufiraFS region) — briefly tried 32/64 MiB while
# chasing "dlpg install doom" failures, but disk space was never the real
# issue (confirmed at 64 MiB/60 MiB-free-region, which still failed the
# same way). The two actual bugs were (1) LUFIRAFS_MAX_FILE_SIZE being
# smaller than doom.lpg — fixed via LUFIRAFS_BLOCK_SIZE in
# lufirafs_format.h — and (2) dlpg install never creating a file's parent
# directory (/usr/share didn't exist on an image that never had doom
# baked in at build time, where mkfs_lufirafs's ensure_dir_path() would
# have created it for free) — fixed via ensure_parent_dirs() in
# lufira-packages/base/dlpg.c.
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

# OVMF (UEFI firmware for QEMU) lives at a different path on every distro —
# no two package managers agree. Pick the first one that actually exists on
# this machine; fall back to the Debian/Ubuntu path (the historical
# default here) so a missing-firmware error still points at a familiar,
# googleable path instead of an empty string.
_OVMF_CANDIDATES = [
    "/usr/share/ovmf/OVMF.fd",                   # Debian/Ubuntu (ovmf package)
    "/usr/share/edk2/x64/OVMF.4m.fd",             # Arch (edk2-ovmf package)
    "/usr/share/edk2-ovmf/x64/OVMF.fd",           # some Arch-derivatives
    "/usr/share/OVMF/OVMF_CODE.fd",               # Fedora/RHEL/CentOS (edk2-ovmf)
    "/usr/share/edk2/ovmf/OVMF_CODE.fd",          # older Fedora layout
    "/usr/share/qemu/ovmf-x86_64-code.bin",       # openSUSE
]
BIOS_PATH = next((p for p in _OVMF_CANDIDATES if os.path.exists(p)), _OVMF_CANDIDATES[0])

# Emulated-device choices for run/debug/monitor — which QEMU device backs
# each LufiraOS driver subsystem (drivers/sound, drivers/net, drivers/disk
# all probe for more than one controller and fall back gracefully, so any
# of these combinations boots; this just controls which real driver code
# path actually gets exercised). Single source of truth for both build.py's
# argparse choices and gui.py's dropdowns — see qemu.py for the actual
# -device/-netdev/-drive strings each choice expands to.
AUDIO_CHOICES = ("ac97", "hda", "none")
NET_CHOICES = ("rtl8139", "e1000", "e1000e", "none")
DISK_CHOICES = ("ide", "ahci", "nvme")

# Userspace-драйверы (0.9.5: сеть/звук вынесены из ядра в отдельные
# root-демоны — netd/soundd, lufira-packages/daemons/) — обычные .lpg
# пакеты категории "daemons" в lufira-packages/build.py, но в Builder
# выделены в собственный раздел (CLI --driver/--no-drivers, GUI "Userspace
# drivers") вместо общего списка пакетов: без них образ грузится, но без
# сети/звука вообще, это не опциональная утилита вроде ls/cp. Единый
# источник истины для build.py и gui.py — как AUDIO_CHOICES/NET_CHOICES выше.
DRIVER_PACKAGES = ("netd", "soundd")

# Профили установки (v1.0.0, установщик + Builder, этап 1) — ПРАВИЛО над
# категориями/именами пакетов из реального index.json lufira-packages
# (categories-аргумент fetch_default_packages()/fetch_default_packages_remote()
# в packages.py), а не отдельный захардкоженный список пакетов — новый
# пакет категории "base"/"apps" подхватится сам, без правок этого файла.
#
# Драйверы (netd/soundd, DRIVER_PACKAGES выше) ни в один профиль НЕ входят
# — они остаются уже существующим отдельным выбором (install_drivers/
# only_drivers), независимым от профиля: тот же набор, что ставит обычная
# полная сборка уже сегодня. "full" ниже — это "все пакеты, кроме
# драйверов и категории games"; при драйверах по умолчанию включёнными
# (install_drivers=True) результат совпадает с "все пакеты кроме дума", но
# снятая галочка с конкретного драйвера работает как и раньше — профиль её
# не отменяет и не включает обратно.
INSTALL_PROFILE_CHOICES = ("minimal", "minimal_wm", "full")


def resolve_profile_packages(profile: str, pkg_categories: dict) -> set:
    """pkg_categories: {package_name: category}, т.е. ровно то, что
    возвращают fetch_default_packages()/fetch_default_packages_remote() в
    packages.py. Возвращает множество ИМЁН пакетов (без драйверов, без
    games), которое профиль ставит — используется build.py как
    cfg.only_packages."""
    if profile == "minimal":
        return {name for name, cat in pkg_categories.items() if cat == "base"}
    if profile == "minimal_wm":
        base = {name for name, cat in pkg_categories.items() if cat == "base"}
        extra = {"wm", "settings", "terminal"} & set(pkg_categories)
        return base | extra
    if profile == "full":
        driver_names = set(DRIVER_PACKAGES)
        return {name for name, cat in pkg_categories.items()
                if cat != "games" and name not in driver_names}
    raise ValueError(f"unknown install profile: {profile!r}")

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
    legacy_bios: bool = False  # embed the legacy BIOS stage1/2 + legacy_main chain too
    boot_mode: str = "uefi"    # "uefi" or "bios" — which firmware qemu.py boots with
    install_default_packages: bool = True
    # None = install every default package. A set = install only the
    # named ones (--only-package in build.py / GUI checkboxes).
    only_packages: set = None
    # None = no profile (--only-package/--no-default-packages decide as
    # before). One of INSTALL_PROFILE_CHOICES resolves to only_packages via
    # resolve_profile_packages() in do_build() — see build.py, mutually
    # exclusive with --only-package/--no-default-packages (do_build()
    # errors out if both are given, instead of picking one silently).
    profile: str = None
    install_drivers: bool = True
    # None = install every driver in DRIVER_PACKAGES. A set = install only
    # the named ones (--driver in build.py / GUI "Userspace drivers" checkboxes).
    only_drivers: set = None
    bios_path: str = None  # override for BIOS_PATH; None = use the auto-detected default
    # Which QEMU device backs each driver subsystem for run/debug/monitor —
    # see AUDIO_CHOICES/NET_CHOICES/DISK_CHOICES above and qemu.py.
    audio: str = "ac97"
    net_device: str = "rtl8139"
    disk_mode: str = "ide"

    @property
    def region_size(self) -> int:
        return self.disk_total_size - self.esp_size

    @property
    def effective_bios_path(self) -> str:
        return self.bios_path or BIOS_PATH

    @property
    def disk_img(self) -> Path:
        return self.out_dir / "disk.img"

    @property
    def usbstick_img(self) -> Path:
        return self.out_dir / "usbstick.img"
