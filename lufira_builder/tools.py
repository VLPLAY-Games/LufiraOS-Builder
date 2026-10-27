"""Host-side tools: building kernel.bin/BOOTX64.EFI and the helper
mkfs_lufirafs program from the LufiraOS repository tree.

lpg_pack isn't built here anymore — packing .lpg from source happens
inside lufira-packages' own build.py; this builder only installs
already-built .lpg files (see packages.py/image.py).

mkfs_lufirafs.c itself stays in LufiraOS (it includes
kernel/fs/lufirafs/lufirafs_format.h via a relative path — duplicating
that header here would risk drift); the builder just compiles it in place.
"""

import platform
import shutil
import subprocess
from pathlib import Path

from . import config

# Внешние программы, которые реально зовутся где-то в этом пайплайне (grep
# подтверждён: Makefile LufiraOS — gcc/ld/objcopy/nasm; этот репозиторий —
# git/dd/mkfs.fat/mmd/mcopy/qemu-system-x86_64/xorriso). "required=False" —
# нужно только для опциональных путей (--legacy-bios, сборка ISO), их
# отсутствие не блокирует обычный build/run.
# apt — потому что это единственный пакетный менеджер, под который этот
# список реально проверялся (Ubuntu/Debian); на других дистрибутивах
# doctor просто покажет имя бинаря без готовой команды установки.
REQUIRED_TOOLS = [
    ("git", "git", "клонирование sibling-репозиториев (LufiraOS/lufira-packages)"),
    ("make", "make", "сборка ядра/загрузчика (LufiraOS Makefile)"),
    ("gcc", "gcc", "сборка ядра и host-тула mkfs_lufirafs"),
    ("ld", "binutils", "линковка ядра"),
    ("objcopy", "binutils", "превращение ELF ядра в kernel.bin"),
    ("mkfs.fat", "dosfstools", "форматирование ESP (FAT12) внутри disk.img"),
    ("mmd", "mtools", "запись EFI/BOOT/BOOTX64.EFI и kernel.bin в ESP"),
    ("mcopy", "mtools", "то же самое, см. mmd"),
    ("dd", "coreutils", "создание/патчинг disk.img, esp.img, usbstick.img"),
    ("qemu-system-x86_64", "qemu-system-x86", "run/debug/monitor"),
]
OPTIONAL_TOOLS = [
    ("nasm", "nasm", "--legacy-bios (boot_legacy/ в LufiraOS, make bootloader-bios)"),
    ("xorriso", "xorriso", "сборка installer-ISO (build iso)"),
]


def _apt_available() -> bool:
    return shutil.which("apt-get") is not None


def check_tools() -> list:
    """Возвращает список {name, apt_pkg, reason, required, found} — found
    через shutil.which(name), плюс отдельная псевдо-запись "ovmf" (не
    бинарь, а файл прошивки — см. config.BIOS_PATH/_OVMF_CANDIDATES),
    нужная только для run/debug/monitor с --boot-mode uefi (umолчание)."""
    rows = []
    for name, apt_pkg, reason in REQUIRED_TOOLS:
        rows.append({"name": name, "apt_pkg": apt_pkg, "reason": reason,
                     "required": True, "found": shutil.which(name) is not None})
    for name, apt_pkg, reason in OPTIONAL_TOOLS:
        rows.append({"name": name, "apt_pkg": apt_pkg, "reason": reason,
                     "required": False, "found": shutil.which(name) is not None})
    rows.append({
        "name": "OVMF firmware", "apt_pkg": "ovmf", "reason": "UEFI-прошивка для QEMU (--boot-mode uefi)",
        "required": False, "found": any(Path(p).exists() for p in config._OVMF_CANDIDATES),
    })
    return rows


def missing_tools(rows: list = None) -> list:
    rows = rows if rows is not None else check_tools()
    return [r for r in rows if not r["found"]]


_ANSI_GREEN = "\033[32m"
_ANSI_RED = "\033[31m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RESET = "\033[0m"


def format_tools_report(rows: list) -> str:
    """Цветной (ANSI SGR — зелёный/красный/жёлтый) отчёт по rows
    (check_tools()), по одной строке на инструмент. Коды совпадают с тем,
    что уже парсит gui.py's _ANSI_CSI_RE/_SGR_FG — тот же текст одинаково
    читается и в реальном терминале, и в логе GUI."""
    width = max((len(r["name"]) for r in rows), default=4)
    lines = []
    for r in rows:
        if r["found"]:
            color, mark = _ANSI_GREEN, "OK"
        elif r["required"]:
            color, mark = _ANSI_RED, "MISSING"
        else:
            color, mark = _ANSI_YELLOW, "missing (optional)"
        lines.append(f"  {color}{r['name']:<{width}}  {mark:<20}{_ANSI_RESET}  {r['reason']}")
    return "\n".join(lines)


def preflight_check(also_required: tuple = ()) -> None:
    """Зовётся в начале do_build()/cmd_iso() в build.py (v1.0.0 план,
    этап 2, запуск "сам себя" — не отдельная ручная команда doctor,
    которую можно забыть вызвать). also_required — имена из OPTIONAL_TOOLS
    (напр. "nasm" при --legacy-bios, "xorriso" для `iso`), которые для
    ЭТОГО конкретного запуска считаются обязательными, даже хотя в целом
    опциональны.

    Тихо возвращается, если всё нужное для этого запуска на месте — вывод
    только при реальной проблеме, чтобы не засорять нормальный лог сборки.
    Останавливает сборку (sys.exit), если не хватает чего-то обязательного
    (включая also_required); просто предупреждает (не останавливает) про
    остальные опциональные инструменты."""
    rows = check_tools()
    for r in rows:
        if r["name"] in also_required:
            r["required"] = True

    missing = missing_tools(rows)
    if not missing:
        return

    print("=== Dependency check ===")
    print(format_tools_report(rows))

    required_missing = [r for r in missing if r["required"]]
    if required_missing:
        pkgs = sorted({r["apt_pkg"] for r in required_missing})
        import sys
        sys.exit(f"\nerror: missing required tool(s): {', '.join(r['name'] for r in required_missing)} "
                 f"— install with `sudo apt-get install -y {' '.join(pkgs)}` (or run "
                 f"`python3 build.py doctor`) and try again")

    print()  # только опциональное недостающее — предупреждение, сборка продолжается


def install_missing(rows: list) -> bool:
    """Ставит apt_pkg всех строк в rows через `sudo apt-get install -y`.
    Вызывающий отвечает за то, что пользователь уже подтвердил это (см.
    `doctor` в build.py — отдельный явный y/N перед sudo, не отсюда).
    Возвращает False без изменений, если apt-get недоступен (другой
    дистрибутив) — тогда вызывающий сам печатает имена пакетов."""
    if not _apt_available():
        return False
    pkgs = sorted({r["apt_pkg"] for r in rows})
    subprocess.run(["sudo", "apt-get", "update"], check=True)
    subprocess.run(["sudo", "apt-get", "install", "-y", *pkgs], check=True)
    return True


def _checkout_latest_release_tag(path: Path, label: str) -> None:
    """Pins a fresh clone to its latest SemVer release tag (vX.Y.Z,
    highest first) instead of leaving it on the default branch's HEAD — a
    from-scratch `python3 build.py run` (no prior sibling checkout) should
    build the latest RELEASED version, not whatever happens to be
    mid-development on main. No-op if the repo has no tags at all — e.g.
    lufira-packages, which ships releases as prebuilt .lpg via its own
    index.json instead (see packages.py), not as git tags."""
    result = subprocess.run(
        ["git", "-C", str(path), "tag", "--list", "v*", "--sort=-v:refname"],
        check=True, capture_output=True, text=True,
    )
    tags = [t for t in result.stdout.splitlines() if t.strip()]
    if not tags:
        return
    latest = tags[0]
    print(f"  {label}: pinning fresh clone to latest release {latest}")
    subprocess.run(["git", "-C", str(path), "checkout", latest], check=True)


def ensure_repo(path: Path, git_url: str, label: str) -> None:
    """Clones git_url into path if it doesn't exist yet, so a bare
    checkout of just this repository is enough to run `python3 build.py
    run` — no sibling LufiraOS checkout needed ahead of time. Leaves an
    existing path alone (checkout, symlink, whatever) — only fills in a
    missing directory, never updates one. A fresh clone is then pinned to
    the latest release tag, if the repo has any (see
    _checkout_latest_release_tag())."""
    if path.exists():
        return
    print(f"  {label} not found at {path} — cloning {git_url}")
    subprocess.run(["git", "clone", git_url, str(path)], check=True)
    _checkout_latest_release_tag(path, label)


def build_kernel_and_bootloader(lufira_repo: Path, legacy_bios: bool = False) -> None:
    """Runs `make kernel bootloader` in LufiraOS's trimmed-down Makefile,
    plus `make bootloader-bios` (stage1/stage2/legacy_main — see
    boot_legacy/) when legacy_bios is requested. That target needs nasm;
    left out of the default build so it stays an opt-in dependency."""
    subprocess.run(
        ["make", "kernel", "bootloader"],
        cwd=lufira_repo,
        check=True,
    )
    if legacy_bios:
        subprocess.run(
            ["make", "bootloader-bios"],
            cwd=lufira_repo,
            check=True,
        )


def compile_host_tools(lufira_repo: Path, out_dir: Path) -> dict:
    """Compiles mkfs_lufirafs, returns its path."""
    out_dir.mkdir(parents=True, exist_ok=True)

    mkfs_bin = out_dir / "mkfs_lufirafs"
    subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-o", str(mkfs_bin),
         str(lufira_repo / "tools" / "mkfs_lufirafs.c")],
        check=True,
    )

    return {"mkfs_lufirafs": mkfs_bin}
