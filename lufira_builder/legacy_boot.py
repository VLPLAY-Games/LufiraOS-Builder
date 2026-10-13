"""Legacy BIOS boot support — patches a stage1/stage2/legacy_main chain
into the very same ESP FAT12 "superfloppy" image that UEFI already boots
from directly (build_esp() in image.py just dd's it into disk.img at
offset 0, no partition table at all).

A FAT12 boot sector has the exact same 512-byte / 0x55AA shape as a
classic MBR: bytes 0x00-0x3D are the BPB (must stay exactly what mkfs.fat
wrote — EB 3C 90 jump included, it already points straight at 0x3E) and
bytes 0x3E-0x1FD (448 bytes) are free bootstrap-code space. We patch our
own stage1 in there. stage1 then loads stage2 (and stage2 in turn loads
legacy_main + kernel.bin + the whole disk) from sectors reserved via
`mkfs.fat -R` — sectors that are guaranteed to never be touched by the
FAT filesystem itself (not part of the FAT tables, root dir, or data
area), so this coexists safely with the normal FAT12 content (BOOTX64.EFI,
/kernel.bin) used by the UEFI path.

All the LBA/size constants below MUST stay in sync with LufiraOS/
boot_legacy/layout.inc — see the detailed comment there. Two sources of
truth instead of one is the weak point of this whole scheme; there's no
clean way to share a single header between NASM and Python.
"""

import struct
from pathlib import Path

STAGE2_LBA_START = 1
STAGE2_MAX_SECTORS = 64

LEGACY_MAIN_LBA = STAGE2_LBA_START + STAGE2_MAX_SECTORS           # 65
LEGACY_MAIN_MAX_SECTORS = 32

KERNEL_HEADER_LBA = LEGACY_MAIN_LBA + LEGACY_MAIN_MAX_SECTORS      # 97
KERNEL_MAGIC = 0x524B464C  # 'LFKR', little-endian in memory
KERNEL_MAX_SECTORS = 4094

RESERVED_SECTORS = KERNEL_HEADER_LBA + 1 + KERNEL_MAX_SECTORS      # 4192

SECTOR_SIZE = 512
BOOT_CODE_OFFSET = 0x3E
BOOT_CODE_MAX_LEN = SECTOR_SIZE - BOOT_CODE_OFFSET - 2  # 448, up to the 0x55AA signature


def _write_at_lba(data: bytearray, lba: int, blob: bytes, max_sectors: int, label: str) -> None:
    max_bytes = max_sectors * SECTOR_SIZE
    if len(blob) > max_bytes:
        raise ValueError(
            f"{label} is {len(blob)} bytes, exceeds its reserved budget of {max_bytes} bytes — "
            f"bump the matching *_MAX_SECTORS in both boot_legacy/layout.inc and this file"
        )
    offset = lba * SECTOR_SIZE
    if offset + len(blob) > len(data):
        raise ValueError(f"{label} would write past the end of the ESP image — esp_size too small")
    data[offset:offset + len(blob)] = blob


def patch_esp(esp_img: Path, stage1_bin: Path, stage2_bin: Path,
              legacy_main_bin: Path, kernel_bin: Path) -> None:
    """Patches an already-formatted (mkfs.fat -R >= RESERVED_SECTORS) ESP
    image in place: embeds stage1 into the boot sector's bootstrap-code
    region, and stage2/legacy_main/kernel.bin into the reserved sectors
    right after it. Call this AFTER mcopy has written BOOTX64.EFI/
    kernel.bin into the FAT12 filesystem (order doesn't actually matter —
    reserved sectors are outside the FAT area — but matching build_esp()'s
    existing step order keeps things easy to follow)."""
    data = bytearray(esp_img.read_bytes())

    stage1 = stage1_bin.read_bytes()
    if len(stage1) > BOOT_CODE_MAX_LEN:
        raise ValueError(f"stage1.bin is {len(stage1)} bytes, must fit in {BOOT_CODE_MAX_LEN} "
                          f"(the FAT12 boot sector's bootstrap-code region)")
    padded = stage1 + bytes(BOOT_CODE_MAX_LEN - len(stage1))
    data[BOOT_CODE_OFFSET:BOOT_CODE_OFFSET + BOOT_CODE_MAX_LEN] = padded
    # Bytes 0x00-0x3D (BPB) and 0x1FE-0x1FF (0x55AA signature) are left
    # exactly as mkfs.fat wrote them.

    _write_at_lba(data, STAGE2_LBA_START, stage2_bin.read_bytes(), STAGE2_MAX_SECTORS, "stage2.bin")
    _write_at_lba(data, LEGACY_MAIN_LBA, legacy_main_bin.read_bytes(),
                  LEGACY_MAIN_MAX_SECTORS, "legacy_main.bin")

    kernel_bytes = kernel_bin.read_bytes()
    # Заголовок дополняется нулями до ЦЕЛОГО сектора — stage2.asm читает
    # его как ровно один сектор (LBA=KERNEL_HEADER_LBA) и начинает данные
    # ядра СТРОГО со следующего сектора (KERNEL_HEADER_LBA+1), без разбора
    # байтовых смещений внутри сектора. Без этого паддинга kernel.bin
    # сдвигался бы на 8 байт и stage2 читал бы не настоящий _start с начала
    # файла, а байт 504 — ровно это и произошло при живой отладке.
    header = struct.pack("<II", KERNEL_MAGIC, len(kernel_bytes))
    header = header + bytes(SECTOR_SIZE - len(header))
    _write_at_lba(data, KERNEL_HEADER_LBA, header + kernel_bytes,
                  KERNEL_MAX_SECTORS + 1, "kernel.bin (+ header sector)")

    esp_img.write_bytes(data)
