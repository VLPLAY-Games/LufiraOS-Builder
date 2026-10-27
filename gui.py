#!/usr/bin/env python3
"""LufiraOS-Builder GUI — thin Tkinter front-end over build.py's
build/run/debug/monitor/iso/flash/doctor subcommands. No build logic lives
here: every destructive/building action runs `python3 build.py <subcommand>`
as a subprocess and streams its output, so the GUI can't drift from the
CLI's behavior — the one exception is read-only status (dependency check,
removable-device list), queried directly from lufira_builder for instant
display with no subprocess round-trip.

v1.0.0 план, этап 3+редизайн: было одной плотной вертикальной колонкой
LabelFrame'ов — теперь вкладки (ttk.Notebook), лог всегда виден снизу
независимо от активной вкладки. Вся функциональность старого GUI сохранена
(toolbar, boot firmware, emulated devices, userspace drivers, package
tree) — просто перегруппирована + добавлены профили установки (этап 1),
проверка зависимостей (этап 2), installer ISO + запись на флешку (этап 3).
"""

import json
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

# QEMU -serial stdio feeds the guest's raw VT100 stream straight into the
# log; tk.Text doesn't interpret ESC sequences, so without parsing they'd
# show up as literal garbage ("[2J[01;01H...").
#
# A full VT100 emulator is overkill — self.log is an append-only log, not
# a terminal grid. Pragmatic middle ground: parse SGR (ESC[...m, color)
# into tk.Text tags; silently drop every other CSI (cursor moves, screen
# clear, DEC modes) since they're meaningless for an append-only view.
_ANSI_CSI_RE = re.compile(r'\x1b\[([0-9;=?]*)([A-Za-z])')

_SGR_FG = {
    30: '#1a1a1a', 31: '#e06c75', 32: '#98c379', 33: '#e5c07b',
    34: '#61afef', 35: '#c678dd', 36: '#56b6c2', 37: '#d0d0d0',
    90: '#5c6370', 91: '#ff6b6b', 92: '#b5e890', 93: '#f0d58c',
    94: '#82b8f0', 95: '#e0a0f0', 96: '#7fd4e0', 97: '#ffffff',
}

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lufira_builder import config, flash, tools

# При запуске из однофайловой PyInstaller-сборки __file__ не указывает на
# реальный каталог на диске — ориентируемся на каталог самого бинарника,
# как если бы он и был этим репозиторием (build.py/lufira_builder/ внутри
# него уже вшиты, а не лежат рядом отдельными файлами — см. _build_py_cmd()).
if getattr(sys, "frozen", False):
    REPO_ROOT = Path(sys.executable).resolve().parent
else:
    REPO_ROOT = Path(__file__).resolve().parent
BUILD_PY = REPO_ROOT / "build.py"
OUT_DIR = REPO_ROOT / "build"  # build.py's --out-dir default (relative to cwd=REPO_ROOT)
LUFIRA_PACKAGES_REPO = REPO_ROOT.parent / config.LUFIRA_PACKAGES_REPO


def _build_py_cmd(*args):
    """Базовая команда для запуска build.py с аргументами args.

    Из исходников — отдельный интерпретатор на BUILD_PY, как раньше. В
    однофайловой сборке BUILD_PY физически не существует (вшит в архив, не
    лежит рядом с бинарником), а sys.executable — сам этот бинарник, не
    python; вместо него зовём себя же с теми же args — единая точка входа
    (app.py) распознаёт их как подкоманду build.py и уходит в build.main(),
    не открывая GUI повторно.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, str(BUILD_PY), *args]

# Package CATALOG (names/versions/categories) comes straight from
# lufira-packages' build.py PACKAGES dict when a sibling checkout exists —
# unlike index.json (packages.py) this needs no prior build, so checkboxes
# exist before the first Build click. Safe to import (everything there sits
# behind `if __name__`). Loaded by explicit file path, not `import build`,
# to dodge a name clash with this repo's own top-level build.py.
#
# A fresh clone of just LufiraOS-Builder has no sibling lufira-packages
# checkout, so that path finds nothing — fall back to the same published
# index.json fetch_default_packages_remote() uses (packages.py), read here
# only for name/version/category, not the .lpg bytes themselves.
import importlib.util


def _load_package_catalog_local():
    build_py = LUFIRA_PACKAGES_REPO / "build.py"
    if not build_py.is_file():
        return None
    # build.py does its own `import build_index` (sibling module) — needs
    # its directory on sys.path for that lookup to resolve.
    repo_str = str(LUFIRA_PACKAGES_REPO)
    added = repo_str not in sys.path
    if added:
        sys.path.insert(0, repo_str)
    try:
        spec = importlib.util.spec_from_file_location("lufira_packages_build", build_py)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        catalog = dict(module.PACKAGES)
        # doom.lpg/netd.lpg aren't in PACKAGES (each has its own
        # build_doom_package()/build_netd_package(), not the generic
        # per-package loop) — add them here so they still show up as
        # checkboxes, under their own categories ("games"/"daemons").
        catalog["doom"] = (module.DOOM_PACKAGE_VERSION, module.DOOM_PACKAGE_CATEGORY, [])
        catalog["netd"] = (module.NETD_PACKAGE_VERSION, module.NETD_PACKAGE_CATEGORY, [])
        return catalog
    finally:
        if added:
            sys.path.remove(repo_str)


def _load_package_catalog_remote():
    with urllib.request.urlopen(config.LUFIRA_PACKAGES_INDEX_URL, timeout=10) as resp:
        index = json.loads(resp.read())
    return {
        pkg["name"]: (pkg["version"], pkg["category"], [])
        for pkg in index["packages"]
    }


def _load_package_catalog():
    try:
        local = _load_package_catalog_local()
        if local:
            return local
    except Exception:
        pass
    try:
        return _load_package_catalog_remote()
    except Exception:
        return {}


PACKAGE_CATALOG = _load_package_catalog()

PROFILE_LABELS = {
    "custom": "Custom (pick packages below)",
    "minimal": "Minimal — base only",
    "minimal_wm": "Minimal + WM — base + wm + settings + terminal",
    "full": "Full — everything except doom",
}


class BuilderGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LufiraOS-Builder")
        self.geometry("1040x740")
        self.minsize(860, 560)

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self._proc = None
        self._out_queue = queue.Queue()
        self._buttons = []

        self._build_toolbar()

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        paned = ttk.PanedWindow(body, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(paned)
        paned.add(self.notebook, weight=3)

        self._build_image_tab()
        self._build_installer_tab()
        self._build_dependencies_tab()

        log_frame = self._build_log_frame(paned)
        paned.add(log_frame, weight=2)

        self.after(80, self._drain_output_queue)
        self.after(200, self._refresh_dependencies)

    # ------------------------------------------------------------------
    # Toolbar — всегда видимые основные действия (было, не изменилось).
    # ------------------------------------------------------------------
    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=8)
        bar.pack(side=tk.TOP, fill=tk.X)

        for label, cmd in (
            ("Build", "build"),
            ("Run", "run"),
            ("Debug", "debug"),
            ("Monitor", "monitor"),
        ):
            b = ttk.Button(bar, text=label, command=lambda c=cmd: self._run_subcommand(c))
            b.pack(side=tk.LEFT, padx=4)
            self._buttons.append(b)

        # Separate from the others: runs build.py's `clear` subcommand
        # (wipes LufiraOS's make-clean output + --out-dir) — confirmed
        # first since it deletes build output, even if regenerable.
        clear_btn = ttk.Button(bar, text="Clear", command=self._run_clear)
        clear_btn.pack(side=tk.LEFT, padx=(12, 4))
        self._buttons.append(clear_btn)

        self.no_build_kernel = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="skip kernel rebuild", variable=self.no_build_kernel).pack(
            side=tk.LEFT, padx=(16, 4))

        # Default is "download prebuilt .lpg from lufira-packages' published
        # index.json" (do_build() in build.py) — this flips to building a
        # local lufira-packages checkout instead, for package development.
        self.build_from_source = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="packages from source", variable=self.build_from_source).pack(
            side=tk.LEFT, padx=(4, 4))

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(bar, textvariable=self.status_var).pack(side=tk.RIGHT, padx=4)

    # ------------------------------------------------------------------
    # Вкладка "Image" — профиль установки, пакеты, драйверы, загрузка,
    # эмулированные устройства. Всё, что определяет disk.img.
    # ------------------------------------------------------------------
    def _build_image_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Image")

        self._build_profile_section(tab)

        columns = ttk.Frame(tab)
        columns.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        left = ttk.Frame(columns)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        self._build_boot_section(left)
        self._build_devices_section(left)
        self._build_drivers_section(left)

        right = ttk.Frame(columns)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_packages_section(right)

    def _build_profile_section(self, parent):
        # Профили установки (v1.0.0 план, этап 1 — config.INSTALL_PROFILE_
        # CHOICES/resolve_profile_packages()): "custom" сохраняет СТАРОЕ
        # поведение этого GUI (ручной выбор из дерева пакетов справа,
        # которое тогда становится активным); любой другой профиль шлёт
        # --profile и прячет дерево (оно с --profile несовместимо, см.
        # do_build() в build.py).
        frame = ttk.LabelFrame(parent, text="Install profile", padding=8)
        frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))

        self.profile = tk.StringVar(value="custom")
        for value, label in PROFILE_LABELS.items():
            ttk.Radiobutton(frame, text=label, value=value, variable=self.profile,
                             command=self._on_profile_change).pack(anchor="w")

    def _on_profile_change(self):
        custom = self.profile.get() == "custom"
        state = tk.NORMAL if custom else tk.DISABLED
        for child in self.pkg_controls_frame.winfo_children():
            self._set_widget_state(child, state)
        self.pkg_tree.configure(selectmode=tk.EXTENDED if custom else tk.NONE)

    @staticmethod
    def _set_widget_state(widget, state):
        try:
            widget.configure(state=state)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            BuilderGUI._set_widget_state(child, state)

    def _build_boot_section(self, parent):
        # --legacy-bios: embeds the legacy BIOS stage1/stage2/legacy_main
        # chain into disk.img ALONGSIDE the UEFI path (one image, bootable
        # both ways) — see boot_legacy/ in LufiraOS. Still experimental
        # (v0.9, not fully finished) — flagged here so the GUI doesn't
        # quietly imply it's as battle-tested as the UEFI path.
        frame = ttk.LabelFrame(parent, text="Boot firmware", padding=8)
        frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))

        self.legacy_bios = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="Embed Legacy BIOS boot chain (alongside UEFI)",
            variable=self.legacy_bios, command=self._on_legacy_bios_toggle,
        ).pack(anchor="w")
        ttk.Label(frame, text="experimental, not fully finished yet",
                  foreground="#a06000").pack(anchor="w", padx=(20, 0))

        # --boot-mode: which firmware run/debug/monitor actually launch
        # QEMU with. "bios" only works on an image built with
        # --legacy-bios, so the radio button stays disabled (and the
        # selection snaps back to "uefi") until that checkbox is on —
        # the invalid combination simply isn't reachable through the GUI.
        mode_row = ttk.Frame(frame)
        mode_row.pack(anchor="w", pady=(4, 0))
        ttk.Label(mode_row, text="Launch with:").pack(side=tk.LEFT, padx=(0, 4))
        self.boot_mode = tk.StringVar(value="uefi")
        self.boot_mode_uefi_rb = ttk.Radiobutton(
            mode_row, text="UEFI", value="uefi", variable=self.boot_mode)
        self.boot_mode_bios_rb = ttk.Radiobutton(
            mode_row, text="Legacy BIOS", value="bios", variable=self.boot_mode,
            state=tk.DISABLED)
        self.boot_mode_uefi_rb.pack(side=tk.LEFT)
        self.boot_mode_bios_rb.pack(side=tk.LEFT, padx=(4, 0))

    def _on_legacy_bios_toggle(self):
        if self.legacy_bios.get():
            self.boot_mode_bios_rb.configure(state=tk.NORMAL)
        else:
            self.boot_mode.set("uefi")
            self.boot_mode_bios_rb.configure(state=tk.DISABLED)

    def _build_devices_section(self, parent):
        # Which QEMU device backs each driver subsystem for run/debug/
        # monitor (--audio/--net-device/--disk-mode in build.py) — every
        # option here is a real, independently-probed driver in LufiraOS
        # (see drivers/sound, drivers/net, drivers/disk), so this picks
        # which code path actually gets exercised rather than just cosmetics.
        frame = ttk.LabelFrame(parent, text="Emulated devices (run / debug / monitor)", padding=8)
        frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))

        self.audio_device = tk.StringVar(value="ac97")
        self.net_device = tk.StringVar(value="rtl8139")
        self.disk_mode = tk.StringVar(value="ide")

        for label, var, choices in (
            ("Audio:", self.audio_device, config.AUDIO_CHOICES),
            ("Network:", self.net_device, config.NET_CHOICES),
            ("Disk:", self.disk_mode, config.DISK_CHOICES),
        ):
            row = ttk.Frame(frame)
            row.pack(anchor="w", pady=1)
            ttk.Label(row, text=label, width=9).pack(side=tk.LEFT)
            combo = ttk.Combobox(row, textvariable=var, values=list(choices),
                                  state="readonly", width=9)
            combo.pack(side=tk.LEFT)

    def _build_drivers_section(self, parent):
        # netd/soundd — не обычные опциональные пакеты (ls/cp/...), а
        # root-демоны, без которых у образа вообще нет сети/звука (0.9.5:
        # вынесены из ядра в userspace, см. config.DRIVER_PACKAGES) —
        # своя labeled-секция, не затерянная строка в общем дереве пакетов,
        # и независимая от профиля (см. module docstring у config.py).
        frame = ttk.LabelFrame(parent, text="Userspace drivers", padding=8)
        frame.pack(side=tk.TOP, fill=tk.X)

        self.driver_vars = {}
        names = [n for n in config.DRIVER_PACKAGES if n in PACKAGE_CATALOG]
        if not names:
            ttk.Label(frame, text="(no driver packages found in the catalog)").pack(anchor="w")
            return
        for name in names:
            version, _category, _extra_includes = PACKAGE_CATALOG[name]
            var = tk.BooleanVar(value=True)
            self.driver_vars[name] = var
            ttk.Checkbutton(frame, text=f"{name} ({version})", variable=var).pack(anchor="w")

    def _build_packages_section(self, parent):
        self.pkg_controls_frame = ttk.Frame(parent)
        self.pkg_controls_frame.pack(fill=tk.BOTH, expand=True)
        pkg_frame = self.pkg_controls_frame

        ttk.Label(pkg_frame, text="Packages to install (only used by the \"Custom\" profile — "
                                   "click a category row to toggle all its packages)").pack(anchor="w")

        btn_row = ttk.Frame(pkg_frame)
        btn_row.pack(anchor="w", pady=(2, 4))
        ttk.Button(btn_row, text="All", command=lambda: self._set_all_packages(True)).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="None", command=lambda: self._set_all_packages(False)).pack(
            side=tk.LEFT, padx=(4, 0))

        # ttk.Treeview has no native checkbox column — "sel" shows a glyph
        # that _on_package_click() toggles by hand. self._pkg_selected maps
        # name -> bool, seeded all True (install-everything default).
        # show="tree headings" turns on the #0 tree column (expand arrows +
        # label) so packages can nest under a category row — the package
        # name lives in #0 instead of its own data column.
        self.pkg_tree = ttk.Treeview(pkg_frame, columns=("sel", "version"),
                                      show="tree headings", height=18)
        self.pkg_tree.heading("#0", text="package / category")
        self.pkg_tree.column("#0", width=190, anchor="w")
        self.pkg_tree.heading("sel", text="")
        self.pkg_tree.column("sel", width=28, anchor="center", stretch=False)
        self.pkg_tree.heading("version", text="version")
        self.pkg_tree.column("version", width=60, anchor="w")
        self.pkg_tree.pack(fill=tk.BOTH, expand=True)
        self.pkg_tree.bind("<Button-1>", self._on_package_click)

        # Драйверы (netd/soundd) выбираются в своей секции
        # (_build_drivers_section()), не здесь — см. комментарий там.
        self._pkg_selected = {name: True for name in PACKAGE_CATALOG
                               if name not in config.DRIVER_PACKAGES}
        self._populate_packages()

    def _category_mark(self, names):
        """☑ all selected, ☐ none selected, ◐ a mix — same glyph set as
        individual packages, just summarizing the whole category at a glance."""
        selected = [self._pkg_selected.get(n, True) for n in names]
        if all(selected):
            return "☑"
        if not any(selected):
            return "☐"
        return "◐"

    def _populate_packages(self):
        self.pkg_tree.delete(*self.pkg_tree.get_children())
        if not PACKAGE_CATALOG:
            self.pkg_tree.insert("", tk.END, text="(no package catalog — no local lufira-packages "
                                                    "checkout and index.json fetch failed, check network)")
            return

        by_category = {}
        for name, (version, category, _extra_includes) in PACKAGE_CATALOG.items():
            if name in config.DRIVER_PACKAGES:
                continue  # своя секция, см. _build_drivers_section()
            by_category.setdefault(category, []).append((name, version))

        for category in sorted(by_category):
            items = sorted(by_category[category])
            names = [name for name, _version in items]
            cat_iid = f"cat:{category}"
            self.pkg_tree.insert(
                "", tk.END, iid=cat_iid, open=True,
                text=f"{category} ({len(items)})",
                values=(self._category_mark(names), ""))
            for name, version in items:
                mark = "☑" if self._pkg_selected.get(name, True) else "☐"
                self.pkg_tree.insert(cat_iid, tk.END, iid=name, text=name, values=(mark, version))

    def _on_package_click(self, event):
        if self.profile.get() != "custom":
            return
        if self.pkg_tree.identify_region(event.x, event.y) != "cell":
            return
        if self.pkg_tree.identify_column(event.x) != "#1":  # "sel" column
            return
        iid = self.pkg_tree.identify_row(event.y)
        if not iid:
            return

        if iid.startswith("cat:"):
            category = iid[len("cat:"):]
            names = [name for name, (_v, c, _e) in PACKAGE_CATALOG.items() if c == category]
            turn_on = not all(self._pkg_selected.get(n, True) for n in names)
            for n in names:
                self._pkg_selected[n] = turn_on
        elif iid in self._pkg_selected:
            self._pkg_selected[iid] = not self._pkg_selected[iid]
        else:
            return

        self._populate_packages()

    def _set_all_packages(self, selected):
        for name in self._pkg_selected:
            self._pkg_selected[name] = selected
        self._populate_packages()

    def _selected_package_names(self):
        return [name for name, sel in self._pkg_selected.items() if sel]

    # ------------------------------------------------------------------
    # Вкладка "Installer / USB" — ISO (этап 3) + запись на флешку. Главная
    # идея v1.0.0: с одной флешки можно либо поставить систему (installer
    # ISO/LiveUSB с установщиком), либо сразу загрузиться с готового
    # образа — обе дорожки через один и тот же Write to USB.
    # ------------------------------------------------------------------
    def _build_installer_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Installer / USB")

        iso_frame = ttk.LabelFrame(tab, text="Installer ISO", padding=8)
        iso_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))

        warn = ("Builds build/lufiraos-installer.iso from the current Image settings. "
                "KNOWN ISSUE: neither UEFI nor legacy BIOS boot from this ISO has been confirmed "
                "to actually work yet (fails loading the FAT image under QEMU+OVMF) — needs the "
                "bootloader rework away from the current \"superfloppy\"/whole-image-read design "
                "first. Treat the ISO as a build-pipeline preview, not a working installer medium.")
        ttk.Label(iso_frame, text=warn, wraplength=820, foreground="#a06000",
                  justify=tk.LEFT).pack(anchor="w", pady=(0, 6))

        iso_btn = ttk.Button(iso_frame, text="Build installer ISO",
                              command=lambda: self._run_subcommand("iso"))
        iso_btn.pack(anchor="w")
        self._buttons.append(iso_btn)

        flash_frame = ttk.LabelFrame(tab, text="Write image to USB flash drive", padding=8)
        flash_frame.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(flash_frame, text="This OVERWRITES THE ENTIRE DEVICE — double-check it's the "
                                     "right one before writing.", foreground="#b00020").pack(anchor="w")

        img_row = ttk.Frame(flash_frame)
        img_row.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(img_row, text="Image:", width=9).pack(side=tk.LEFT)
        self.flash_image = tk.StringVar(value=str(OUT_DIR / "disk.img"))
        ttk.Entry(img_row, textvariable=self.flash_image).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(img_row, text="Browse…", command=self._browse_flash_image).pack(side=tk.LEFT, padx=(4, 0))

        dev_row = ttk.Frame(flash_frame)
        dev_row.pack(fill=tk.X, pady=(2, 6))
        ttk.Label(dev_row, text="Device:", width=9).pack(side=tk.LEFT)
        self.flash_device = tk.StringVar(value="")
        self.flash_device_combo = ttk.Combobox(dev_row, textvariable=self.flash_device,
                                                state="readonly", width=50)
        self.flash_device_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dev_row, text="Refresh", command=self._refresh_flash_devices).pack(
            side=tk.LEFT, padx=(4, 0))

        write_btn = ttk.Button(flash_frame, text="Write to USB…", command=self._confirm_and_flash)
        write_btn.pack(anchor="w")
        self._buttons.append(write_btn)

        self._refresh_flash_devices()

    def _browse_flash_image(self):
        path = filedialog.askopenfilename(
            initialdir=str(OUT_DIR), title="Select disk.img or installer .iso",
            filetypes=[("Disk/ISO image", "*.img *.iso"), ("All files", "*.*")])
        if path:
            self.flash_image.set(path)

    def _refresh_flash_devices(self):
        devices = flash.list_removable_devices()
        labels = [f"{d['path']}  —  {d['size_human']}  {d['model']}".strip() for d in devices]
        self._flash_device_paths = [d["path"] for d in devices]
        self.flash_device_combo.configure(values=labels or ["(no removable devices found)"])
        if labels:
            self.flash_device_combo.current(0)
        else:
            self.flash_device.set("(no removable devices found)")

    def _confirm_and_flash(self):
        if self._proc is not None:
            return
        idx = self.flash_device_combo.current()
        if idx < 0 or idx >= len(getattr(self, "_flash_device_paths", [])):
            messagebox.showerror("Write to USB", "No removable device selected — click Refresh "
                                                   "with the USB stick plugged in.")
            return
        device = self._flash_device_paths[idx]
        image_path = self.flash_image.get().strip()
        if not image_path or not Path(image_path).exists():
            messagebox.showerror("Write to USB", f"Image not found: {image_path}")
            return

        if not messagebox.askyesno(
                "Write to USB — irreversible",
                f"This will OVERWRITE EVERY BYTE on {device} with {image_path}.\n\n"
                f"All data currently on {device} will be permanently lost. Continue?"):
            return
        typed = simpledialog.askstring(
            "Confirm device", f"Type the device path again to confirm ({device}):", parent=self)
        if typed != device:
            messagebox.showinfo("Write to USB", "Input did not match — aborted, nothing was written.")
            return

        self._launch(_build_py_cmd("flash", "--image", image_path, "--device", device, "--yes"),
                      "flash")

    # ------------------------------------------------------------------
    # Вкладка "Dependencies" — preflight (этап 2): tools.check_tools()
    # читается напрямую (быстро, без побочных эффектов), установка
    # недостающего идёт через subprocess (`doctor --yes`), чтобы sudo-
    # вывод остался в общем логе, как у любого другого действия.
    # ------------------------------------------------------------------
    def _build_dependencies_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Dependencies")

        top = ttk.Frame(tab)
        top.pack(fill=tk.X)
        ttk.Button(top, text="Re-check", command=self._refresh_dependencies).pack(side=tk.LEFT)
        self.install_missing_btn = ttk.Button(
            top, text="Install missing (sudo apt-get)…", command=self._install_missing_deps,
            state=tk.DISABLED)
        self.install_missing_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._buttons.append(self.install_missing_btn)

        self.deps_tree = ttk.Treeview(tab, columns=("status", "reason"), show="tree headings",
                                       height=16)
        self.deps_tree.heading("#0", text="tool")
        self.deps_tree.column("#0", width=160, anchor="w")
        self.deps_tree.heading("status", text="status")
        self.deps_tree.column("status", width=90, anchor="center")
        self.deps_tree.heading("reason", text="needed for")
        self.deps_tree.column("reason", width=560, anchor="w")
        self.deps_tree.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.deps_tree.tag_configure("missing_required", foreground="#b00020")
        self.deps_tree.tag_configure("missing_optional", foreground="#a06000")
        self.deps_tree.tag_configure("found", foreground="#2e7d32")

    def _refresh_dependencies(self):
        self.deps_tree.delete(*self.deps_tree.get_children())
        rows = tools.check_tools()
        missing = tools.missing_tools(rows)
        for r in rows:
            if r["found"]:
                status, tag = "OK", "found"
            elif r["required"]:
                status, tag = "MISSING", "missing_required"
            else:
                status, tag = "missing (optional)", "missing_optional"
            self.deps_tree.insert("", tk.END, text=r["name"], values=(status, r["reason"]), tags=(tag,))
        self.install_missing_btn.configure(state=tk.NORMAL if missing else tk.DISABLED)

    def _install_missing_deps(self):
        if self._proc is not None:
            return
        missing = tools.missing_tools()
        pkgs = sorted({r["apt_pkg"] for r in missing})
        if not pkgs:
            return
        if not messagebox.askyesno(
                "Install missing dependencies",
                f"Run `sudo apt-get install -y {' '.join(pkgs)}`? You may be prompted for your "
                f"password in a terminal/log output below."):
            return
        self._launch(_build_py_cmd("doctor", "--yes"), "doctor")

    # ------------------------------------------------------------------
    # Лог — общий для всех вкладок (видим всегда, не только на "своей").
    # ------------------------------------------------------------------
    def _build_log_frame(self, parent):
        log_frame = ttk.Frame(parent)
        self.log = tk.Text(log_frame, wrap="none", bg="#101010", fg="#d0d0d0",
                            insertbackground="#d0d0d0", font=("Courier New", 10))
        yscroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=yscroll.set, state=tk.DISABLED)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        for code, color in _SGR_FG.items():
            self.log.tag_configure(f"fg{code}", foreground=color)
        self._log_fg_tag = None  # текущий активный SGR-тег (переживает между чанками)
        return log_frame

    def _append_log(self, text):
        self.log.configure(state=tk.NORMAL)

        # Parse ESC[...LETTER in place: "m" (SGR/color) updates
        # self._log_fg_tag for later inserts (must survive across chunk
        # boundaries — color and text often arrive in separate queue
        # items); any other letter (cursor/clear/DEC mode) is dropped.
        pos = 0
        for m in _ANSI_CSI_RE.finditer(text):
            if m.start() > pos:
                self._insert_tagged(text[pos:m.start()])
            params, final = m.group(1), m.group(2)
            if final == 'm':
                codes = [int(p) for p in params.split(';') if p.isdigit()] or [0]
                for code in codes:
                    if code == 0:
                        self._log_fg_tag = None
                    elif code in _SGR_FG:
                        self._log_fg_tag = f"fg{code}"
            pos = m.end()
        if pos < len(text):
            self._insert_tagged(text[pos:])

        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _insert_tagged(self, text):
        if not text:
            return
        if self._log_fg_tag:
            self.log.insert(tk.END, text, self._log_fg_tag)
        else:
            self.log.insert(tk.END, text)

    def _drain_output_queue(self):
        try:
            while True:
                line = self._out_queue.get_nowait()
                if line is None:
                    self._on_subcommand_done()
                else:
                    self._append_log(line)
        except queue.Empty:
            pass
        self.after(80, self._drain_output_queue)

    # ------------------------------------------------------------------
    # Запуск подкоманд (build/run/debug/monitor/clear/iso/flash/doctor) —
    # общий механизм, не изменился по сути от старого GUI.
    # ------------------------------------------------------------------
    def _set_buttons_enabled(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        for b in self._buttons:
            b.configure(state=state)
        if enabled:
            # install_missing_btn остаётся выключенной, если нечего ставить.
            self._refresh_dependencies()

    def _common_build_flags(self):
        cmd = []
        if self.no_build_kernel.get():
            cmd.append("--no-build-kernel")
        if self.build_from_source.get():
            cmd.append("--build-packages-from-source")

        if self.legacy_bios.get():
            cmd.append("--legacy-bios")
        if self.boot_mode.get() != "uefi":
            cmd += ["--boot-mode", self.boot_mode.get()]

        if self.audio_device.get() != "ac97":
            cmd += ["--audio", self.audio_device.get()]
        if self.net_device.get() != "rtl8139":
            cmd += ["--net-device", self.net_device.get()]
        if self.disk_mode.get() != "ide":
            cmd += ["--disk-mode", self.disk_mode.get()]

        if self.profile.get() != "custom":
            cmd += ["--profile", self.profile.get()]
        elif self._pkg_selected:
            # --only-package replaces the old all-or-nothing
            # --no-default-packages; only passed when selection is an actual
            # subset. NAJDENNYJ BUG (сохранено из старого GUI): выбор нуля
            # пакетов раньше проваливался в пустой --only-package цикл, т.е.
            # НИ ОДИН флаг не доходил до build.py — тот по умолчанию ставил
            # ВСЕ пакеты, ровно противоположное тому, что "ничего не
            # выбрано" должно значить в GUI. --no-default-packages — вот
            # настоящий флаг "ничего из опциональных".
            selected = self._selected_package_names()
            if not selected:
                cmd.append("--no-default-packages")
            elif len(selected) < len(self._pkg_selected):
                for name in selected:
                    cmd += ["--only-package", name]

        # Та же логика, что у пакетов выше (включая NAJDENNYJ BUG) — пустой
        # --driver-цикл без --no-drivers означало бы "ядро само решит",
        # то есть "поставить все", обратное тому, что значит "ничего не
        # выбрано" в GUI.
        if self.driver_vars:
            selected_drivers = [name for name, var in self.driver_vars.items() if var.get()]
            if not selected_drivers:
                cmd.append("--no-drivers")
            elif len(selected_drivers) < len(self.driver_vars):
                for name in selected_drivers:
                    cmd += ["--driver", name]
        return cmd

    def _run_subcommand(self, subcommand):
        if self._proc is not None:
            return  # a build/run/debug/monitor/clear/iso is already active
        cmd = _build_py_cmd(subcommand) + self._common_build_flags()
        self._launch(cmd, subcommand)

    def _run_clear(self):
        if self._proc is not None:
            return
        if not messagebox.askyesno(
                "Clear build output",
                "This removes LufiraOS's build/ (make clean) and this repository's "
                "own build output (disk.img etc.) — everything is regenerated by the "
                "next Build, but it will take a while. Continue?"):
            return
        self._launch(_build_py_cmd("clear"), "clear")

    def _launch(self, cmd, subcommand):
        self._append_log(f"\n$ {' '.join(cmd)}\n")
        self.status_var.set(f"Running: {subcommand}")
        self._set_buttons_enabled(False)

        self._proc = subprocess.Popen(
            cmd, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        threading.Thread(target=self._stream_output, daemon=True).start()

    def _stream_output(self):
        proc = self._proc
        for line in proc.stdout:
            self._out_queue.put(line)
        proc.wait()
        self._out_queue.put(f"\n[exit code {proc.returncode}]\n")
        self._out_queue.put(None)

    def _on_subcommand_done(self):
        self._proc = None
        self.status_var.set("Idle")
        self._set_buttons_enabled(True)
        self._refresh_flash_devices()


if __name__ == "__main__":
    BuilderGUI().mainloop()
