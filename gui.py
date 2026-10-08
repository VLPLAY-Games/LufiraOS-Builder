#!/usr/bin/env python3
"""LufiraOS-Builder GUI — thin Tkinter front-end over build.py's
build/run/debug/monitor subcommands. No build logic lives here: every
button runs `python3 build.py <subcommand>` as a subprocess and streams
its output, so the GUI can't drift from the CLI's behavior.
"""

import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
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

REPO_ROOT = Path(__file__).resolve().parent
BUILD_PY = REPO_ROOT / "build.py"
LUFIRA_PACKAGES_REPO = REPO_ROOT.parent / config.LUFIRA_PACKAGES_REPO

# Package CATALOG (names/versions/categories) comes straight from
# lufira-packages' build.py PACKAGES dict — unlike index.json (packages.py)
# this needs no prior build, so checkboxes exist before the first Build
# click. Safe to import (everything there sits behind `if __name__`).
# Loaded by explicit file path, not `import build`, to dodge a name clash
# with this repo's own top-level build.py.
import importlib.util


def _load_package_catalog():
    build_py = LUFIRA_PACKAGES_REPO / "build.py"
    if not build_py.is_file():
        return {}
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
        return dict(module.PACKAGES)
    finally:
        if added:
            sys.path.remove(repo_str)


try:
    PACKAGE_CATALOG = _load_package_catalog()
except Exception:
    PACKAGE_CATALOG = {}


class BuilderGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LufiraOS-Builder")
        self.geometry("820x560")

        self._proc = None
        self._out_queue = queue.Queue()
        self._buttons = []

        self._build_toolbar()
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

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(bar, textvariable=self.status_var).pack(side=tk.RIGHT, padx=4)

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
        ttk.Label(pkg_frame, text="Packages to install").pack(anchor="w")

        btn_row = ttk.Frame(pkg_frame)
        btn_row.pack(anchor="w", pady=(2, 4))
        ttk.Button(btn_row, text="All", command=lambda: self._set_all_packages(True)).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="None", command=lambda: self._set_all_packages(False)).pack(
            side=tk.LEFT, padx=(4, 0))

        # ttk.Treeview has no native checkbox column — "sel" shows a glyph
        # that _on_package_click() toggles by hand. self._pkg_selected maps
        # name -> bool, seeded all True (install-everything default).
        cols = ("sel", "name", "version", "category")
        self.pkg_tree = ttk.Treeview(pkg_frame, columns=cols, show="headings", height=20)
        self.pkg_tree.heading("sel", text="")
        self.pkg_tree.column("sel", width=28, anchor="center", stretch=False)
        for c, w in zip(("name", "version", "category"), (90, 60, 60)):
            self.pkg_tree.heading(c, text=c)
            self.pkg_tree.column(c, width=w, anchor="w")
        self.pkg_tree.pack(fill=tk.BOTH, expand=True)
        self.pkg_tree.bind("<Button-1>", self._on_package_click)
        paned.add(pkg_frame, weight=1)

        self._pkg_selected = {name: True for name in PACKAGE_CATALOG}
        self._populate_packages()

    def _populate_packages(self):
        self.pkg_tree.delete(*self.pkg_tree.get_children())
        if not PACKAGE_CATALOG:
            self.pkg_tree.insert("", tk.END, values=("", "(lufira-packages not found)", "", ""))
            return
        for name, (version, category, _extra_includes) in sorted(PACKAGE_CATALOG.items()):
            mark = "☑" if self._pkg_selected.get(name, True) else "☐"
            self.pkg_tree.insert("", tk.END, iid=name, values=(mark, name, version, category))

    def _on_package_click(self, event):
        if self.pkg_tree.identify_region(event.x, event.y) != "cell":
            return
        if self.pkg_tree.identify_column(event.x) != "#1":  # "sel" column
            return
        name = self.pkg_tree.identify_row(event.y)
        if not name or name not in self._pkg_selected:
            return
        self._pkg_selected[name] = not self._pkg_selected[name]
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

        cmd = [sys.executable, str(BUILD_PY), subcommand]
        if self.no_build_kernel.get():
            cmd.append("--no-build-kernel")

        # --only-package replaces the old all-or-nothing
        # --no-default-packages; only passed when selection is an actual subset.
        if PACKAGE_CATALOG:
            selected = self._selected_package_names()
            if len(selected) < len(PACKAGE_CATALOG):
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
        self._launch([sys.executable, str(BUILD_PY), "clear"], "clear")

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
