"""Запись готового образа (disk.img или installer ISO) на физическую
флешку (v1.0.0 план, этап 3 — "главная идея": одна флешка, два сценария —
либо installer, либо сразу рабочий LiveUSB, через dd тот же механизм для
обоих). dd на сырое блочное устройство необратимо — list_removable_devices()
по умолчанию показывает только то, что ядро само считает съёмным
(udev "ID_BUS=usb"/RM=1), а write_image() требует явного совпадения с этим
списком, если force=False.
"""

import json
import subprocess
from pathlib import Path


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **kw)


def list_removable_devices() -> list:
    """Возвращает [{"path": "/dev/sdb", "size": "14.9G", "model": "...",
    "mountpoints": ["/media/x", ...]}] — только TYPE=disk и RM=1
    (съёмное), через lsblk -J. Пустой список, если lsblk недоступен или
    съёмных устройств нет — вызывающий сам решает, что показать
    пользователю (например, предложить вписать путь вручную)."""
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,MODEL,TRAN,RM,TYPE,MOUNTPOINTS"],
            check=True, capture_output=True, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []

    data = json.loads(result.stdout)
    devices = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk" or str(dev.get("rm")) not in ("1", "True", "true"):
            continue
        size_bytes = int(dev.get("size") or 0)
        mountpoints = [m for m in _collect_mountpoints(dev) if m]
        devices.append({
            "path": dev.get("path") or f"/dev/{dev['name']}",
            "size_bytes": size_bytes,
            "size_human": _human_size(size_bytes),
            "model": (dev.get("model") or "").strip(),
            "mountpoints": mountpoints,
        })
    return devices


def _collect_mountpoints(dev: dict) -> list:
    mps = list(dev.get("mountpoints") or [])
    for child in dev.get("children", []) or []:
        mps += _collect_mountpoints(child)
    return mps


def _human_size(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def write_image(image_path: Path, device: str, *, force: bool = False,
                 progress_cb=None) -> None:
    """Пишет image_path на device через dd. По умолчанию (force=False)
    отказывается, если device не входит в list_removable_devices() —
    единственная защита от случайной записи на системный диск, которую
    можно сделать программно, не спрашивая пользователя дополнительно;
    финальное "да, это точно та флешка" — ответственность вызывающего UI
    (CLI/GUI), не этой функции. progress_cb(str), если задан, получает
    каждую строку stderr от `dd status=progress` (прогресс пишется именно
    туда, не в stdout)."""
    if not Path(image_path).exists():
        raise FileNotFoundError(str(image_path))

    if not force:
        known = {d["path"] for d in list_removable_devices()}
        if device not in known:
            raise ValueError(
                f"{device} не найден среди съёмных устройств (lsblk RM=1) — "
                f"похоже на системный диск. Передай force=True, только если ты "
                f"точно уверен(а), что это нужная флешка."
            )

    cmd = ["dd", f"if={image_path}", f"of={device}", "bs=4M", "conv=fsync", "status=progress"]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
    for line in proc.stderr:
        if progress_cb:
            progress_cb(line)
    proc.wait()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    _run(["sync"], check=True)
