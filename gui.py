#!/usr/bin/env python3
"""LufiraOS-Builder GUI — thin Tkinter front-end over build.py's
build/run/debug/monitor subcommands. No build logic lives here: every
button runs `python3 build.py <subcommand>` as a subprocess and streams
its output, so the GUI can't drift from the CLI's behavior.
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
from tkinter import messagebox, ttk

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
from lufira_builder import config

# При запуске из однофайловой PyInstaller-сборки __file__ не указывает на
# реальный каталог на диске — ориентируемся на каталог самого бинарника,
# как если бы он и был этим репозиторием (build.py/lufira_builder/ внутри
# него уже вшиты, а не лежат рядом отдельными файлами — см. _build_py_cmd()).
if getattr(sys, "frozen", False):
    REPO_ROOT = Path(sys.executable).resolve().parent
else:
    REPO_ROOT = Path(__file__).resolve().parent
BUILD_PY = REPO_ROOT / "build.py"
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


class BuilderGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LufiraOS-Builder")
        self.geometry("880x620")

        self._proc = None
        self._out_queue = queue.Queue()
        self._buttons = []

        self._build_toolbar()
        self._build_boot_section()
        self._build_devices_section()
        self._build_body()
        self.after(80, self._drain_output_queue)

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
        ttk.Checkbutton(bar, text="--no-build-kernel", variable=self.no_build_kernel).pack(
            side=tk.LEFT, padx=(16, 4))

        # Default is "download prebuilt .lpg from lufira-packages' published
        # index.json" (do_build() in build.py) — this flips to building a
        # local lufira-packages checkout instead, for package development.
        self.build_from_source = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="--build-packages-from-source", variable=self.build_from_source).pack(
            side=tk.LEFT, padx=(4, 4))

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(bar, textvariable=self.status_var).pack(side=tk.RIGHT, padx=4)

    def _build_boot_section(self):
        # Separate, clearly-labeled area for boot-firmware choices — kept
        # apart from the toolbar's build-mechanics toggles and from the
        # package list, same "separate concerns" split the user asked for.
        frame = ttk.LabelFrame(self, text="Boot firmware", padding=8)
        frame.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 8))

        # --legacy-bios: embeds the legacy BIOS stage1/stage2/legacy_main
        # chain into disk.img ALONGSIDE the UEFI path (one image, bootable
        # both ways) — see boot_legacy/ in LufiraOS. Still experimental
        # (v0.9, not fully finished) — flagged here so the GUI doesn't
        # quietly imply it's as battle-tested as the UEFI path.
        self.legacy_bios = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="Embed Legacy BIOS boot chain (alongside UEFI)",
            variable=self.legacy_bios, command=self._on_legacy_bios_toggle,
        ).pack(side=tk.LEFT)

        ttk.Label(frame, text="experimental, not fully finished yet",
                  foreground="#a06000").pack(side=tk.LEFT, padx=(8, 20))

        # --boot-mode: which firmware run/debug/monitor actually launch
        # QEMU with. "bios" only works on an image built with
        # --legacy-bios, so the radio button stays disabled (and the
        # selection snaps back to "uefi") until that checkbox is on —
        # the invalid combination simply isn't reachable through the GUI.
        ttk.Label(frame, text="Launch with:").pack(side=tk.LEFT, padx=(0, 4))
        self.boot_mode = tk.StringVar(value="uefi")
        self.boot_mode_uefi_rb = ttk.Radiobutton(
            frame, text="UEFI", value="uefi", variable=self.boot_mode)
        self.boot_mode_bios_rb = ttk.Radiobutton(
            frame, text="Legacy BIOS", value="bios", variable=self.boot_mode,
            state=tk.DISABLED)
        self.boot_mode_uefi_rb.pack(side=tk.LEFT)
        self.boot_mode_bios_rb.pack(side=tk.LEFT, padx=(4, 0))

    def _on_legacy_bios_toggle(self):
        if self.legacy_bios.get():
            self.boot_mode_bios_rb.configure(state=tk.NORMAL)
        else:
            self.boot_mode.set("uefi")
            self.boot_mode_bios_rb.configure(state=tk.DISABLED)

    def _build_devices_section(self):
        # Which QEMU device backs each driver subsystem for run/debug/
        # monitor (--audio/--net-device/--disk-mode in build.py) — every
        # option here is a real, independently-probed driver in LufiraOS
        # (see drivers/sound, drivers/net, drivers/disk), so this picks
        # which code path actually gets exercised rather than just cosmetics.
        frame = ttk.LabelFrame(self, text="Emulated devices (run / debug / monitor)", padding=8)
        frame.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 8))

        self.audio_device = tk.StringVar(value="ac97")
        self.net_device = tk.StringVar(value="rtl8139")
        self.disk_mode = tk.StringVar(value="ide")

        for label, var, choices in (
            ("Audio:", self.audio_device, config.AUDIO_CHOICES),
            ("Network:", self.net_device, config.NET_CHOICES),
            ("Disk:", self.disk_mode, config.DISK_CHOICES),
        ):
            ttk.Label(frame, text=label).pack(side=tk.LEFT, padx=(0, 4))
            combo = ttk.Combobox(frame, textvariable=var, values=list(choices),
                                  state="readonly", width=9)
            combo.pack(side=tk.LEFT, padx=(0, 16))

    def _build_body(self):
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        log_frame = ttk.Frame(paned)
        self.log = tk.Text(log_frame, wrap="none", bg="#101010", fg="#d0d0d0",
                            insertbackground="#d0d0d0", font=("Courier New", 10))
        yscroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=yscroll.set, state=tk.DISABLED)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        paned.add(log_frame, weight=3)
        for code, color in _SGR_FG.items():
            self.log.tag_configure(f"fg{code}", foreground=color)
        self._log_fg_tag = None  # текущий активный SGR-тег (переживает между чанками)

        pkg_frame = ttk.Frame(paned)
        ttk.Label(pkg_frame, text="Packages to install (grouped by category — click a "
                                   "category row to toggle all its packages)").pack(anchor="w")

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
                                      show="tree headings", height=22)
        self.pkg_tree.heading("#0", text="package / category")
        self.pkg_tree.column("#0", width=190, anchor="w")
        self.pkg_tree.heading("sel", text="")
        self.pkg_tree.column("sel", width=28, anchor="center", stretch=False)
        self.pkg_tree.heading("version", text="version")
        self.pkg_tree.column("version", width=60, anchor="w")
        self.pkg_tree.pack(fill=tk.BOTH, expand=True)
        self.pkg_tree.bind("<Button-1>", self._on_package_click)
        paned.add(pkg_frame, weight=1)

        self._pkg_selected = {name: True for name in PACKAGE_CATALOG}
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

    def _set_buttons_enabled(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        for b in self._buttons:
            b.configure(state=state)

    def _run_subcommand(self, subcommand):
        if self._proc is not None:
            return  # a build/run/debug/monitor/clear is already active

        cmd = _build_py_cmd(subcommand)
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

        # --only-package replaces the old all-or-nothing
        # --no-default-packages; only passed when selection is an actual
        # subset. NAJDENNYJ BUG: selecting zero packages used to fall
        # through this branch with an empty --only-package loop, i.e. NO
        # flag at all reached build.py — which then defaulted to "install
        # every package" (cfg.only_packages stays None), the exact opposite
        # of what "None" in the GUI is supposed to mean. --no-default-
        # packages is the real "install none of the optional ones" flag.
        if PACKAGE_CATALOG:
            selected = self._selected_package_names()
            if not selected:
                cmd.append("--no-default-packages")
            elif len(selected) < len(PACKAGE_CATALOG):
                for name in selected:
                    cmd += ["--only-package", name]

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


if __name__ == "__main__":
    BuilderGUI().mainloop()
