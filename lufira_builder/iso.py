"""installer-ISO сборка (v1.0.0 план, этап 3) — гибридный UEFI+BIOS ISO
поверх уже готового disk.img (build_iso() ничего не меняет в самой сборке
диска, только оборачивает её в El Torito).

СТАТУС (проверено вживую, QEMU+OVMF, screendump через HMP-монитор — ПЕРВЫЙ
прогон ошибочно казался рабочим из-за того, что QEMU дописывает NVRAM
обратно в общий /usr/share/qemu/OVMF.fd между прогонами; повтор с
одноразовой копией прошивки — единственный достоверный результат):

  - Ни UEFI, ни legacy BIOS boot С ЭТОГО ISO СЕЙЧАС НЕ РАБОТАЮТ.
    UEFI-путь (El Torito "-eltorito-alt-boot -e ... -no-emul-boot" с ESP-
    или даже целиком disk.img в качестве EFI-boot-image) стабильно падает
    на "[ERROR] FAT: read error or allocation failed" — тот же самый
    LoadFATImage() в fat_loader.c, что при обычной загрузке с диска
    читает ВЕСЬ disk.img одним куском через Block I/O Protocol, здесь
    натыкается на блочное устройство, которое El Torito EFI-сущность
    представляет иначе (другой размер/геометрия), чем настоящий диск —
    та же корневая причина, что и у "суперфлоппи" (см. README/план: один
    большой read() всего образа вместо нормальной работы с разделами).
  - Legacy BIOS-путь (El Torito hard-disk-emulation, "-hard-disk-boot" на
    всём disk.img) падает раньше и по другой причине: stage1.asm
    (boot_legacy/stage1.asm) сознательно без CHS-фоллбэка — только
    INT13h extensions (LBA), а SeaBIOS не объявляет extensions для CD,
    эмулированного как hard disk.

  Вывод: ISO не починить одними флагами xorriso — нужна переделка
  загрузчика (уход от "суперфлоппи"/одного большого read() к нормальному
  партиционированному чтению, см. план). build_iso() ниже оставлен как
  рабочая обвязка (собирает синтаксически верный гибридный ISO), но пока
  НЕ рекламируется как загружаемый — вызывающий код обязан явно
  предупреждать об этом (см. cmd_iso() в build.py).
"""

import subprocess
from pathlib import Path

from . import config


def _run(cmd) -> None:
    subprocess.run(cmd, check=True)


def build_iso(cfg: config.BuildConfig, out_path: Path) -> Path:
    """disk.img уже должен быть собран (cfg.disk_img существует — см.
    image.assemble()), включая legacy BIOS boot chain, если cfg.legacy_bios
    (patch_esp() в legacy_boot.py уже встроила её в сам disk.img на этапе
    do_build()) — иначе дополнительных файлов для ISO не нужно. Если
    cfg.legacy_bios, в ISO кладётся ЕЩЁ и El Torito hdd-emulation запись
    (см. предупреждение в module docstring — не подтверждена под SeaBIOS)."""
    if not cfg.disk_img.exists():
        raise FileNotFoundError(f"{cfg.disk_img} not found — assemble the disk image first")

    iso_root = cfg.out_dir / "iso_root"
    iso_root.mkdir(parents=True, exist_ok=True)
    (iso_root / "EFI" / "BOOT").mkdir(parents=True, exist_ok=True)

    esp_image = iso_root / "EFI" / "BOOT" / "ESP.IMG"
    with cfg.disk_img.open("rb") as src:
        esp_image.write_bytes(src.read(cfg.esp_size))

    cmd = ["xorriso", "-as", "mkisofs", "-V", "LUFIRAOS", "-o", str(out_path),
           "-eltorito-alt-boot", "-e", "EFI/BOOT/ESP.IMG", "-no-emul-boot"]

    if cfg.legacy_bios:
        # См. предупреждение выше — кладём, но НЕ рекламируем как рабочий
        # путь, пока stage1 не научится CHS-фоллбэку.
        hdd_image = iso_root / "BOOT.IMG"
        hdd_image.write_bytes(cfg.disk_img.read_bytes())
        cmd = ["xorriso", "-as", "mkisofs", "-V", "LUFIRAOS", "-o", str(out_path),
               "-b", "BOOT.IMG", "-hard-disk-boot", "-boot-info-table",
               "-eltorito-alt-boot", "-e", "EFI/BOOT/ESP.IMG", "-no-emul-boot"]

    cmd.append(str(iso_root))
    _run(cmd)
    return out_path
