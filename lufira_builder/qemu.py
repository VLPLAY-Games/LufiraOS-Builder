"""Launches QEMU on top of the assembled disk.img — previously the run/debug/
monitor targets in LufiraOS/Makefile, now entirely here (v0.7 plan, stage 3).
"""

import subprocess
import tempfile
from pathlib import Path

from . import config, image as image_mod


def _run(cmd) -> None:
    subprocess.run(cmd, check=True)


def _common_prefix(cfg: config.BuildConfig) -> list:
    return [
        "qemu-system-x86_64",
        # accel=kvm:tcg: tries HW accel (/dev/kvm), falls back to TCG if
        # unavailable — never a launch error, just faster when accel exists.
        # Matters for TLS/crypto (SYS_NET_FETCH, dlpg sync/upgrade): RSA on
        # pure TCG is slow enough to look like a hang.
        # Must go through -machine's accel= property, not a bare -accel
        # flag ("invalid accelerator kvm:tcg" on this QEMU version). "pc" is
        # QEMU's own default machine type — this doesn't change the chipset.
        "-machine", "pc,accel=kvm:tcg",
        "-bios", config.BIOS_PATH,
        "-drive", f"file={cfg.disk_img},format=raw,if=ide,index=0",
    ]


def qemu_run(cfg: config.BuildConfig) -> None:
    cmd = _common_prefix(cfg) + [
        "-m", "128M",
        "-netdev", "user,id=net0", "-device", "rtl8139,netdev=net0",
        "-machine", "pcspk-audiodev=audio",
        "-audiodev", "driver=alsa,id=audio",
        "-device", "AC97,audiodev=audio",
        "-device", "qemu-xhci",
        "-device", "usb-kbd",
        "-device", "usb-mouse",
        "-serial", "stdio",
    ]
    _run(cmd)


def _build_usbstick_plain(cfg: config.BuildConfig) -> None:
    if cfg.usbstick_img.exists():
        return
    _run(["dd", "if=/dev/zero", f"of={cfg.usbstick_img}", "bs=1024", "count=8192", "status=none"])


def _build_usbstick_with_test_file(cfg: config.BuildConfig) -> None:
    if cfg.usbstick_img.exists():
        cfg.usbstick_img.unlink()
    _run(["dd", "if=/dev/zero", f"of={cfg.usbstick_img}", "bs=1024", "count=8192", "status=none"])
    _run(["mkfs.fat", "-F", "12", str(cfg.usbstick_img)])
    _run(["mmd", "-i", str(cfg.usbstick_img), "::/TESTDIR"])
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        tf.write("Hello from batched USB MSD test\n")
        host_test = tf.name
    _run(["mcopy", "-i", str(cfg.usbstick_img), host_test, "::/HOSTTEST.TXT"])
    Path(host_test).unlink()


def qemu_debug(cfg: config.BuildConfig, mkfs_bin: Path, tests_elf_dir: Path) -> None:
    devmode_flag = cfg.out_dir / "devmode.flag"
    devmode_flag.write_text("1\n")
    image_mod.mkfs(mkfs_bin, "put", cfg.disk_img, cfg, devmode_flag, "/system/devmode.flag")
    devmode_flag.unlink()

    image_mod.mkfs(mkfs_bin, "mkdir", cfg.disk_img, cfg, "/tests")
    test_elf_files = sorted(tests_elf_dir.glob("**/*.elf")) if tests_elf_dir and tests_elf_dir.is_dir() else []
    if not test_elf_files:
        print("  (no test .elf files found — see --tests-archive)")
    for f in test_elf_files:
        image_mod.mkfs(mkfs_bin, "put", cfg.disk_img, cfg, f, f"/tests/{f.name}")

    _build_usbstick_with_test_file(cfg)

    debug_log = cfg.out_dir / "qemu_debug.log"
    cmd = _common_prefix(cfg) + [
        "-m", "256M",
        "-netdev", "user,id=net0", "-device", "rtl8139,netdev=net0",
        "-serial", "stdio", "-no-reboot", "-no-shutdown",
        "-device", "qemu-xhci,id=xhci", "-device", "usb-kbd", "-device", "usb-mouse",
        "-drive", f"if=none,id=usbstick,file={cfg.usbstick_img},format=raw",
        "-device", "usb-storage,bus=xhci.0,drive=usbstick",
        "-d", "int,cpu_reset,guest_errors", "-D", str(debug_log),
    ]
    _run(cmd)


def qemu_monitor(cfg: config.BuildConfig) -> None:
    _build_usbstick_plain(cfg)
    cmd = _common_prefix(cfg) + [
        "-m", "256M",
        "-netdev", "user,id=net0", "-device", "rtl8139,netdev=net0", "-serial", "stdio",
        "-device", "qemu-xhci,id=xhci", "-device", "usb-kbd", "-device", "usb-mouse",
        "-drive", f"if=none,id=usbstick,file={cfg.usbstick_img},format=raw",
        "-device", "usb-storage,bus=xhci.0,drive=usbstick",
        "-monitor", "telnet:127.0.0.1:4444,server,nowait",
        "-no-reboot", "-no-shutdown",
    ]
    _run(cmd)
