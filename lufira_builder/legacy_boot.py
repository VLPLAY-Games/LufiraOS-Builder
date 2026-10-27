"""Legacy BIOS boot support — v1.0.0 план: раньше stage1 жил в FAT12-
boot-секторе на LBA0 (суперфлоппи, никакой таблицы разделов не было), а
stage2/legacy_main/kernel.bin — в секторах, зарезервированных внутри самой
FAT12 через `mkfs.fat -R` (чтобы FAT их не трогала). Теперь LBA0 — настоящий
MBR (см. image.py:_write_mbr(), партиции 0xEF/0xDA), а stage1 патчится
ТОЛЬКО в boot-код область MBR (0x000-0x1BD, 446 байт — partition table и
0x55AA НЕ трогаем, их уже записал _write_mbr()); stage2/legacy_main/
kernel.bin лежат в "зазоре" ДО первой партиции (LBA 1..GAP_SECTORS-1,
config.ESP_GAP_SIZE) — та же идея, что у GRUB ("MBR gap"), просто сырые
абсолютные LBA внутри disk.img, без какой-либо файловой системы.

Все LBA/размерные константы ниже ОБЯЗАНЫ совпадать с LufiraOS/
boot_legacy/layout.inc — см. подробный комментарий там. Два источника
истины вместо одного — слабое место этой схемы, общий с остальным этим
файлом приём (нет чистого способа шарить один заголовок между NASM и
Python).
"""

import struct
from pathlib import Path

STAGE2_LBA_START = 1
STAGE2_MAX_SECTORS = 64

LEGACY_MAIN_LBA = STAGE2_LBA_START + STAGE2_MAX_SECTORS           # 65
LEGACY_MAIN_MAX_SECTORS = 32

KERNEL_HEADER_LBA = LEGACY_MAIN_LBA + LEGACY_MAIN_MAX_SECTORS      # 97
KERNEL_MAGIC = 0x524B464C  # 'LFKR', little-endian in memory
KERNEL_MAX_SECTORS = 3998

SECTOR_SIZE = 512
BOOT_CODE_OFFSET = 0x000
BOOT_CODE_MAX_LEN = 0x1BE  # 446 байт — до начала таблицы разделов (0x1BE)


def _write_at_lba(f, lba: int, blob: bytes, max_sectors: int, label: str) -> None:
    max_bytes = max_sectors * SECTOR_SIZE
    if len(blob) > max_bytes:
        raise ValueError(
            f"{label} is {len(blob)} bytes, exceeds its reserved budget of {max_bytes} bytes — "
            f"bump the matching *_MAX_SECTORS in both boot_legacy/layout.inc and this file"
        )
    f.seek(lba * SECTOR_SIZE)
    f.write(blob)


def patch_mbr_gap(disk_img: Path, stage1_bin: Path, stage2_bin: Path,
                   legacy_main_bin: Path, kernel_bin: Path) -> None:
    """disk_img уже содержит правильный MBR (_write_mbr() в image.py уже
    отработала — partition table и 0x55AA на месте) и правильный общий
    размер (достаточно большой, чтобы вместить зазор + обе партиции).
    Патчит ТОЛЬКО boot-код MBR + сырые LBA зазора, ничего больше в файле
    не трогает."""
    stage1 = stage1_bin.read_bytes()
    if len(stage1) > BOOT_CODE_MAX_LEN:
        raise ValueError(f"stage1.bin is {len(stage1)} bytes, must fit in {BOOT_CODE_MAX_LEN} "
                          f"(MBR boot-code area, before the partition table at 0x1BE)")
    padded_stage1 = stage1 + bytes(BOOT_CODE_MAX_LEN - len(stage1))

    with disk_img.open("r+b") as f:
        # read-modify-write: меняем только 0x000-0x1BD, partition table
        # (0x1BE-0x1FD) и сигнатуру (0x1FE-0x1FF), которые уже на месте,
        # НЕ трогаем.
        f.seek(BOOT_CODE_OFFSET)
        f.write(padded_stage1)

        _write_at_lba(f, STAGE2_LBA_START, stage2_bin.read_bytes(), STAGE2_MAX_SECTORS, "stage2.bin")
        _write_at_lba(f, LEGACY_MAIN_LBA, legacy_main_bin.read_bytes(),
                      LEGACY_MAIN_MAX_SECTORS, "legacy_main.bin")

        kernel_bytes = kernel_bin.read_bytes()
        # Заголовок дополняется нулями до ЦЕЛОГО сектора — stage2.asm читает
        # его как ровно один сектор (LBA=KERNEL_HEADER_LBA) и начинает данные
        # ядра СТРОГО со следующего сектора (KERNEL_HEADER_LBA+1), без разбора
        # байтовых смещений внутри сектора.
        header = struct.pack("<II", KERNEL_MAGIC, len(kernel_bytes))
        header = header + bytes(SECTOR_SIZE - len(header))
        _write_at_lba(f, KERNEL_HEADER_LBA, header + kernel_bytes,
                      KERNEL_MAX_SECTORS + 1, "kernel.bin (+ header sector)")
