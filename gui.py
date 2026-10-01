#!/usr/bin/env python3
"""LufiraOS-Builder GUI — a thin Tkinter front-end over build.py's own
subcommands (build/run/debug/monitor). Pt.6 of the user's list: build.py's
docstring only ever said "a GUI, if one is ever needed, would sit on top
of the same lufira_builder/ logic as a separate layer" — this is that
layer. No new build logic lives here: every button just runs
`python3 build.py <subcommand>` as a subprocess and streams its output,
so the GUI can never drift from the CLI's actual behavior.
"""

import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lufira_builder import config

REPO_ROOT = Path(__file__).resolve().parent
BUILD_PY = REPO_ROOT / "build.py"
# Package sources/builds now live in lufira-packages (see packages.py) —
# its index.json (present once that repo's build.py has run at least once)
# is the only place package metadata exists anymore.
LUFIRA_PACKAGES_INDEX = REPO_ROOT.parent / config.LUFIRA_PACKAGES_REPO / "index.json"


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

        self.no_build_kernel = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="--no-build-kernel", variable=self.no_build_kernel).pack(
            side=tk.LEFT, padx=(16, 4))

        self.no_default_packages = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="--no-default-packages", variable=self.no_default_packages).pack(
            side=tk.LEFT, padx=4)

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

        pkg_frame = ttk.Frame(paned)
        ttk.Label(pkg_frame, text="Default packages").pack(anchor="w")
        cols = ("name", "version", "category")
        self.pkg_tree = ttk.Treeview(pkg_frame, columns=cols, show="headings", height=20)
        for c, w in zip(cols, (90, 70, 60)):
            self.pkg_tree.heading(c, text=c)
            self.pkg_tree.column(c, width=w, anchor="w")
        self.pkg_tree.pack(fill=tk.BOTH, expand=True)
        paned.add(pkg_frame, weight=1)

        self._populate_packages()

    def _populate_packages(self):
        if not LUFIRA_PACKAGES_INDEX.is_file():
            self.pkg_tree.insert("", tk.END, values=("(run Build once)", "", ""))
            return
        index = json.loads(LUFIRA_PACKAGES_INDEX.read_text())
        for pkg in index["packages"]:
            self.pkg_tree.insert("", tk.END, values=(pkg["name"], pkg["version"], pkg["category"]))

    def _append_log(self, text):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

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
            return  # a build/run/debug/monitor is already active

        cmd = [sys.executable, str(BUILD_PY), subcommand]
        if self.no_build_kernel.get():
            cmd.append("--no-build-kernel")
        if self.no_default_packages.get():
            cmd.append("--no-default-packages")

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
        for item in self.pkg_tree.get_children():
            self.pkg_tree.delete(item)
        self._populate_packages()


if __name__ == "__main__":
    BuilderGUI().mainloop()
