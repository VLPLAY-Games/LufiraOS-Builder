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

# v0.7 план, этап 4: du/df/free/cpuload (уже отдельные ELF с этапа 1)
# ставятся в образ через .lpg + install_packages(), а не прямым put'ом —
# первая сквозная проверка пайплайна "упаковка -> установка во время сборки
# образа". Категория user — необязательные утилиты, не часть базовой
# системы. Сама разбивка на пакеты осталась внутри LufiraOS-Builder
# (lufira-packages с base/user появится только на этапе 6, пока не создаём
# его прежде времени).
DEFAULT_USER_PACKAGES = [
    {"name": "du", "version": "1.0.1", "category": "user", "elf": "userspace/user/du.elf"},  # 1.0.1: cwd-резолвинг (этап 5, под-этап 1)
    {"name": "df", "version": "1.0.0", "category": "user", "elf": "userspace/user/df.elf"},
    {"name": "free", "version": "1.0.0", "category": "user", "elf": "userspace/user/free.elf"},
    {"name": "cpuload", "version": "1.1.0", "category": "user", "elf": "userspace/user/cpuload.elf"},  # 1.1.0: per-process разбивка (этап 5, под-этап 4)
]

# v0.7 план, этап 5, под-этап 1: cp/mv/ls/mkdir/rm — первая (самая
# низкорисковая) группа Base-команд, переносимых из кернел-нативного шелла
# в пакеты. Категория base — часть основной системы, не опциональные
# утилиты (в отличие от DEFAULT_USER_PACKAGES выше). kernel-native
# command_cp/mv/ls/mkdir/rm пока НЕ удалены — решение снести builtin'ы
# насовсем принимается вместе с под-этапом 6 (переписанный shell), не
# раньше.
#
# Под-этап 2: kill (без wait — тот архитектурно не может быть отдельной
# программой в этой модели процессов, см. комментарий в userspace/user/
# kill.c; остаётся kernel-native builtin'ом, как и в любом настоящем
# Unix-шелле).
#
# Под-этап 3: run/runbg остались kernel-native builtin'ами (фикс cwd —
# прямо в kernel/system/elf/elf.c, без отдельных .elf), сюда добавлять
# нечего.
#
# Под-этап 4: ps — единственная команда всего этапа 5, которой
# понадобился новый syscall (SYS_PSLIST=30, kernel/system/syscall/
# syscall.h). cpuload.elf (out.4) переиздан следующей версией: та же
# программа, но теперь с per-process разбивкой поверх SYS_PSLIST.
#
# Под-этап 5: dlpg — сам себя переносить было не нужно (уже userspace ELF
# с этапа 2), но раньше он был ЕДИНСТВЕННЫМ исключением из общего
# .lpg-пайплайна — стелился в /bin напрямую (см. старый параметр
# direct_stage_elf_files в image.py), потому что не мог установить сам
# себя через ещё не существующий на диске dlpg.elf. Но install_packages()
# ниже — чистый Python в LufiraOS-Builder, а не запуск настоящего dlpg на
# госте, так что никакой курицы-и-яйца на самом деле нет: билдер пакует и
# "устанавливает" dlpg.lpg той же логикой, что и всё остальное. Теперь
# direct-stage не нужен вообще ни для чего.
DEFAULT_BASE_PACKAGES = [
    {"name": "cp", "version": "1.0.0", "category": "base", "elf": "userspace/user/cp.elf"},
    {"name": "mv", "version": "1.0.0", "category": "base", "elf": "userspace/user/mv.elf"},
    {"name": "ls", "version": "1.0.0", "category": "base", "elf": "userspace/user/ls.elf"},
    {"name": "mkdir", "version": "1.0.0", "category": "base", "elf": "userspace/user/mkdir.elf"},
    {"name": "rm", "version": "1.0.0", "category": "base", "elf": "userspace/user/rm.elf"},
    {"name": "kill", "version": "1.0.0", "category": "base", "elf": "userspace/user/kill.elf"},
    {"name": "ps", "version": "1.0.0", "category": "base", "elf": "userspace/user/ps.elf"},
    {"name": "dlpg", "version": "1.0.0", "category": "base", "elf": "userspace/base/dlpg.elf"},
]


@dataclass
class BuildConfig:
    lufira_repo: Path
    out_dir: Path
    disk_total_size: int = DISK_TOTAL_SIZE
    esp_size: int = ESP_SIZE
    packages: list = field(default_factory=list)  # список путей к .lpg
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
