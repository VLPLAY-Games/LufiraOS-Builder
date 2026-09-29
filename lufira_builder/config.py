"""Настройки сборки образа LufiraOS.

Раньше все размеры ниже жили в LufiraOS/Makefile — после разреза
Makefile'а на "только kernel.bin + загрузчик" (v0.7 план, этап 3, шаг 3.0)
вся сборка disk.img переехала сюда.
"""

from dataclasses import dataclass, field
from pathlib import Path

# Держите ESP_SIZE в синхроне с LUFIRAFS_ESP_SIZE в
# kernel/fs/lufirafs/lufirafs_format.h внутри репозитория LufiraOS —
# расхождение означает, что мы отформатируем не тот регион диска, который
# потом читает ядро (тот же риск, что раньше был отмечен прямо в Makefile).
DISK_TOTAL_SIZE = 16 * 1024 * 1024
ESP_SIZE = 4 * 1024 * 1024
REGION_SIZE = DISK_TOTAL_SIZE - ESP_SIZE

# Каталоги, создаваемые в LufiraFS-регионе на каждой сборке образа.
DEFAULT_DIRS = ["/system", "/logs", "/etc", "/bin"]

# Сид-файлы с фиксированным содержимым/правами — тот же набор, что раньше
# клался Makefile'ом напрямую.
DEFAULT_README_CONTENT = "Hello from LufiraOS!\n"
DEFAULT_README_DEST = "/readme.txt"
DEFAULT_SEED_PASSWD = "tools/seed/passwd"  # относительно корня LufiraOS
DEFAULT_SEED_GROUP = "tools/seed/group"

BIOS_PATH = "/usr/share/ovmf/OVMF.fd"


@dataclass
class BuildConfig:
    lufira_repo: Path
    out_dir: Path
    disk_total_size: int = DISK_TOTAL_SIZE
    esp_size: int = ESP_SIZE
    packages: list = field(default_factory=list)  # список путей к .lpg
    build_kernel: bool = True

    @property
    def region_size(self) -> int:
        return self.disk_total_size - self.esp_size

    @property
    def disk_img(self) -> Path:
        return self.out_dir / "disk.img"

    @property
    def usbstick_img(self) -> Path:
        return self.out_dir / "usbstick.img"
