# LufiraOS-Builder

![License](https://img.shields.io/badge/license-GPL--3.0-green)
![Status](https://img.shields.io/badge/status-alpha-orange)

**LufiraOS-Builder** assembles the bootable `disk.img` for [LufiraOS](https://github.com/VLPLAY-Games/LufiraOS) and runs it in QEMU. It replaces the old `make run`/`make debug`/`make monitor` Makefile targets with a standalone Python CLI (plus an optional Tkinter GUI) that drives the build independently of the kernel's own Makefile.

It does not contain any OS code itself — it is pure tooling. A bare checkout of just this repository is enough to go straight to `python3 build.py run`: a missing [LufiraOS](https://github.com/VLPLAY-Games/LufiraOS) checkout is cloned automatically, and by default every package is downloaded prebuilt (sha256-verified) straight from [lufira-packages](https://github.com/VLPLAY-Games/lufira-packages)' published `index.json` — no local package-repo checkout or extra toolchain needed for that part. `--build-packages-from-source` switches to building `lufira-packages` from a local checkout instead (also auto-cloned if missing), for actively developing packages.

## What it does

- **`build`** — assembles `disk.img` (FAT12 ESP + LufiraFS region) from the kernel/bootloader binaries and the default (or a custom `--package`/`--only-package` selection of) `.lpg` packages, without launching anything.
- **`run`** — `build`, then launches QEMU (serial on stdio, KVM-accelerated when available).
- **`debug`** — `build` plus the `/tests` payload and an attached USB stick image, then launches QEMU.
- **`monitor`** — `build`, then launches QEMU with the HMP monitor exposed on `telnet:127.0.0.1:4444` (scripted input/screendumps for live testing).
- **`clear`** — removes all build output (`LufiraOS`'s own `make clean` plus this repository's `--out-dir`) for a clean rebuild.

A thin Tkinter GUI (`gui.py`) sits on top of the same CLI: every button just runs the corresponding `build.py` subcommand as a subprocess and streams its output, so the GUI can never drift from the CLI's actual behavior.

## Usage

```bash
# A bare checkout of this repository alone is enough — LufiraOS is cloned
# automatically if missing, and packages are fetched prebuilt by default.
python3 build.py run
```

```bash
python3 build.py --help
python3 build.py run --help      # per-subcommand options (--package, --only-package,
                                   # --no-build-kernel, --build-packages-from-source, ...)
```

```bash
python3 gui.py                   # optional Tkinter front-end
```

## Project Structure

```
LufiraOS-Builder/
├── build.py                 # CLI entry point (build/run/debug/monitor/clear)
├── gui.py                   # optional Tkinter front-end over build.py
└── lufira_builder/          # the actual assembly/QEMU logic, imported by build.py
    ├── config.py            # paths, defaults
    ├── image.py             # disk.img assembly (FAT12 ESP + LufiraFS staging)
    ├── packages.py          # .lpg package staging/selection
    ├── lpg.py                # .lpg format helpers
    ├── qemu.py               # QEMU invocation (run/debug/monitor)
    └── tools.py              # misc host-tool wrappers
```

## License

This project is licensed under the GPL-3.0 License. See the [LICENSE](LICENSE) file for details.
