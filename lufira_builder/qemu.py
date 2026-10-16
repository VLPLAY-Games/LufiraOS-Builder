"""Launches QEMU on top of the assembled disk.img — previously the run/debug/
monitor targets in LufiraOS/Makefile, now entirely here (v0.7 plan, stage 3).
"""

import subprocess
import tempfile
from pathlib import Path

from . import config, image as image_mod


def _run(cmd) -> None:
    subprocess.run(cmd, check=True)


def _disk_args(cfg: config.BuildConfig) -> list:
    """Main disk.img, attached through whichever controller cfg.disk_mode
    picks — drivers/disk in LufiraOS probes AHCI, then NVMe, then falls
    back to legacy ATA PIO, so any of these boots; this just decides which
    of those driver paths actually gets exercised in QEMU. id="maindisk"
    stays clear of the usbstick drive (qemu_debug/qemu_monitor use their
    own "usbstick" id) and of the AHCI controller's own "ahci0" id."""
    img = f"file={cfg.disk_img},format=raw"
    if cfg.disk_mode == "ahci":
        return [
            "-device", "ahci,id=ahci0",
            "-drive", f"if=none,id=maindisk,{img}",
            "-device", "ide-hd,bus=ahci0.0,drive=maindisk",
        ]
    if cfg.disk_mode == "nvme":
        return [
            "-drive", f"if=none,id=maindisk,{img}",
            "-device", "nvme,drive=maindisk,serial=lufiraos0",
        ]
    # "ide" (default) — legacy ATA, matches this tool's historical behavior.
    return ["-drive", f"{img},if=ide,index=0"]


def _net_args(cfg: config.BuildConfig) -> list:
    if cfg.net_device == "none":
        return []
    return ["-netdev", "user,id=net0", "-device", f"{cfg.net_device},netdev=net0"]


def _audio_args(cfg: config.BuildConfig) -> list:
    """AC97 and Intel HDA both go through one shared ALSA sink
    (id="audio0") — "alsa" is the one backend that works headless without
    a running PulseAudio/PipeWire session, matching how this tool already
    ran AC97 before --audio existed."""
    if cfg.audio == "none":
        return []
    if cfg.audio == "hda":
        return [
            "-device", "intel-hda",
            "-device", "hda-duplex,audiodev=audio0",
            "-audiodev", "driver=alsa,id=audio0",
        ]
    # "ac97" (default)
    return [
        "-machine", "pcspk-audiodev=audio0",
        "-audiodev", "driver=alsa,id=audio0",
        "-device", "AC97,audiodev=audio0",
    ]


def _common_prefix(cfg: config.BuildConfig) -> list:
    cmd = [
        "qemu-system-x86_64",
        # accel=kvm:tcg: tries HW accel (/dev/kvm), falls back to TCG if
        # unavailable — never a launch error, just faster when accel exists.
        # Matters for TLS/crypto (SYS_NET_FETCH, dlpg sync/upgrade): RSA on
        # pure TCG is slow enough to look like a hang.
        # Must go through -machine's accel= property, not a bare -accel
        # flag ("invalid accelerator kvm:tcg" on this QEMU version). "pc" is
        # QEMU's own default machine type — this doesn't change the chipset.
        "-machine", "pc,accel=kvm:tcg",
    ]
    # boot_mode="bios" omits -bios entirely so QEMU falls back to its
    # built-in SeaBIOS (legacy/CSM boot) instead of OVMF (UEFI) — the same
    # disk.img boots through either path when built with --legacy-bios,
    # see boot_legacy/ in LufiraOS.
    if cfg.boot_mode != "bios":
        cmd += ["-bios", cfg.effective_bios_path]
    cmd += _disk_args(cfg)
    return cmd


def qemu_run(cfg: config.BuildConfig) -> None:
    cmd = _common_prefix(cfg) + [
        "-m", "128M",
        *_net_args(cfg),
        *_audio_args(cfg),
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
        *_net_args(cfg),
        *_audio_args(cfg),
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
        *_net_args(cfg),
        *_audio_args(cfg),
        "-serial", "stdio",
        "-device", "qemu-xhci,id=xhci", "-device", "usb-kbd", "-device", "usb-mouse",
        "-drive", f"if=none,id=usbstick,file={cfg.usbstick_img},format=raw",
        "-device", "usb-storage,bus=xhci.0,drive=usbstick",
        "-monitor", "telnet:127.0.0.1:4444,server,nowait",
        "-no-reboot", "-no-shutdown",
    ]
    _run(cmd)
