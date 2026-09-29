"""Разбор .lpg (tools/lpg_format.h из LufiraOS) и симуляция установки пакета
на этапе сборки образа — те же правила, что и у userspace/base/dlpg.c
(проверка зависимостей ДО распаковки, тот же текстовый формат БД
/etc/packages/installed и receipt-файлов /etc/packages/<name>.files),
только выполняются на хосте в Python, а не внутри работающей ОС.
"""

import struct
from dataclasses import dataclass, field

MAGIC = b"LPG1"

# '<' — без выравнивания, зеркалит __attribute__((packed)) структур в
# tools/lpg_format.h побайтово.
_HEADER_FMT = "<4s32sHHHBBII"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 52
_DEP_FMT = "<32sHHH"
_DEP_SIZE = struct.calcsize(_DEP_FMT)  # 38
_FILE_FMT = "<64sIII"
_FILE_SIZE = struct.calcsize(_FILE_FMT)  # 76

CATEGORY_BASE = 0
CATEGORY_USER = 1


class LpgError(Exception):
    pass


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii")


@dataclass
class LpgFile:
    path: str
    size: int
    mode: int
    data: bytes


@dataclass
class LpgPackage:
    name: str
    version: tuple  # (major, minor, patch)
    category: int
    deps: list  # [(name, min_version)]
    files: list  # [LpgFile]


def version_str(v: tuple) -> str:
    return f"{v[0]}.{v[1]}.{v[2]}"


def version_gte(a: tuple, b: tuple) -> bool:
    return a >= b


def parse_lpg(path) -> LpgPackage:
    data = open(path, "rb").read()
    if len(data) < _HEADER_SIZE:
        raise LpgError(f"{path}: too small to be a .lpg")

    magic, name_raw, maj, minr, pat, category, _reserved, dep_count, file_count = \
        struct.unpack_from(_HEADER_FMT, data, 0)
    if magic != MAGIC:
        raise LpgError(f"{path}: bad magic")
    if dep_count > 16 or file_count > 64 or file_count == 0:
        raise LpgError(f"{path}: invalid header (dep_count={dep_count}, file_count={file_count})")

    off = _HEADER_SIZE
    deps = []
    for _ in range(dep_count):
        dname, dmaj, dminr, dpat = struct.unpack_from(_DEP_FMT, data, off)
        deps.append((_cstr(dname), (dmaj, dminr, dpat)))
        off += _DEP_SIZE

    files = []
    for _ in range(file_count):
        fpath, fsize, fmode, foffset = struct.unpack_from(_FILE_FMT, data, off)
        files.append(LpgFile(_cstr(fpath), fsize, fmode, data[foffset:foffset + fsize]))
        off += _FILE_SIZE

    return LpgPackage(_cstr(name_raw), (maj, minr, pat), category, deps, files)


@dataclass
class InstalledEntry:
    name: str
    version: tuple
    category: int


def parse_installed_db(text: str) -> list:
    entries = []
    for line in text.splitlines():
        if not line:
            continue
        name, ver_str, cat_str = line.split(":", 2)
        parts = tuple(int(x) for x in ver_str.split("."))
        cat = CATEGORY_BASE if cat_str == "base" else CATEGORY_USER
        entries.append(InstalledEntry(name, parts, cat))
    return entries


def render_installed_db(entries: list) -> str:
    lines = []
    for e in entries:
        cat_str = "base" if e.category == CATEGORY_BASE else "user"
        lines.append(f"{e.name}:{version_str(e.version)}:{cat_str}")
    return "".join(line + "\n" for line in lines)


@dataclass
class InstallPlan:
    name: str
    version: tuple
    category: int
    files: list  # [(dest_path, data, mode)]
    receipt_text: str
    installed_after: list  # обновлённый список InstalledEntry


def plan_install(pkg: LpgPackage, installed: list, allow_existing: bool) -> InstallPlan:
    """Повторяет do_install() из dlpg.c: сначала зависимости, потом файлы."""
    by_name = {e.name: e for e in installed}

    existing = by_name.get(pkg.name)
    if existing is not None and not allow_existing:
        raise LpgError(f"package '{pkg.name}' is already installed (use update)")

    for dep_name, min_version in pkg.deps:
        dep = by_name.get(dep_name)
        if dep is None:
            raise LpgError(f"missing dependency '{dep_name}'")
        if not version_gte(dep.version, min_version):
            raise LpgError(
                f"dependency '{dep_name}' too old (need >= {version_str(min_version)}, "
                f"have {version_str(dep.version)})"
            )

    files = [(f.path, f.data, f.mode) for f in pkg.files]
    receipt_text = "".join(f.path + "\n" for f in pkg.files)

    updated = [e for e in installed if e.name != pkg.name]
    updated.append(InstalledEntry(pkg.name, pkg.version, pkg.category))

    return InstallPlan(pkg.name, pkg.version, pkg.category, files, receipt_text, updated)
