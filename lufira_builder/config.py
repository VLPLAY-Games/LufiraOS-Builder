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
DEFAULT_DIRS = ["/system", "/logs", "/etc", "/bin"]

# Seed files with fixed content/permissions — the same set the Makefile
# used to lay down directly.
DEFAULT_README_CONTENT = "Hello from LufiraOS!\n"
DEFAULT_README_DEST = "/readme.txt"
DEFAULT_SEED_PASSWD = "tools/seed/passwd"  # relative to the LufiraOS root
DEFAULT_SEED_GROUP = "tools/seed/group"

# v0.7 plan, stage 5, sub-stage 6: the kernel loads exactly this file
# directly on every boot/shell respawn (spawn_shell_process(), kernel.c) —
# NOT a package, direct-stage forever (see the comment on
# populate_lufirafs() in image.py). shell.c itself has moved out of
# LufiraOS into lufira-packages (its shell/ folder, not base/ or user/ —
# still not a dlpg package, just source that lives there too so the
# kernel repo carries no userspace program source at all); its build.py
# compiles it straight to build/shell.elf, with no .lpg wrapper. Path here
# is relative to LUFIRA_PACKAGES_REPO's own --out-dir (default "build").
SHELL_ELF_PATH = "build/shell.elf"

BIOS_PATH = "/usr/share/ovmf/OVMF.fd"

# Package sources (cp/mv/ls/mkdir/rm/kill/ps/dlpg/du/df/free/cpuload) used
# to live inside LufiraOS/userspace/ and get packed into .lpg right here.
# They've moved out to their own lufira-packages repository (with its own
# build.py, mirroring lufira-tests) so the kernel repo doesn't carry
# package sources/binaries at all — see packages.py's
# fetch_default_packages(), which runs that build and reads its index.json.
LUFIRA_PACKAGES_REPO = "lufira-packages"  # sibling of this repository, like lufira_repo


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

    @property
    def region_size(self) -> int:
        return self.disk_total_size - self.esp_size

    @property
    def disk_img(self) -> Path:
        return self.out_dir / "disk.img"

    @property
    def usbstick_img(self) -> Path:
        return self.out_dir / "usbstick.img"
