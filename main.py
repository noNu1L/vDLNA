#!/usr/bin/env python
"""vDLNA — Virtual sound card DLNA streaming tool.

GUI mode (default):  python main.py
CLI interactive:      python main.py --cli
One-shot:             python main.py device|dlna|stream ...
"""

import argparse
import asyncio
import cmd
import shlex
import sys


def _ensure_single_instance() -> None:
    """Windows 命名互斥体，防止程序重复启动。"""
    if sys.platform != "win32":
        return
    import ctypes
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\vDLNA_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning("vDLNA", "程序已在运行中。")
        root.destroy()
        sys.exit(0)

from vdlna.app import App, setup_signal_handlers
from vdlna.audio.virtual_device import VirtualAudioDevice
from vdlna.dlna.diagnostics import run_diagnostics, run_raw_msearch
from vdlna.dlna.discovery import scan_all_upnp, scan_dlna_renderers


class VdlnaShell(cmd.Cmd):
    """Interactive shell for vDLNA."""

    intro = (
        "\n"
        "  ╔══════════════════════════════════════╗\n"
        "  ║       vDLNA — Virtual DLNA Streamer  ║\n"
        "  ╚══════════════════════════════════════╝\n"
        "\n"
        "  Type 'help' or '?' to list commands.\n"
    )
    prompt = "\nvDLNA> "

    def __init__(self):
        super().__init__()
        self._last_devices: list[dict] = []
        self._stream_port: int = 9876

    # ── device ──────────────────────────────────────────────

    def do_device(self, arg: str) -> None:
        """Show virtual audio device status.

Usage: device status
If no virtual device is installed, opens the VB-CABLE download page.
"""
        args = shlex.split(arg)
        action = args[0] if args else "status"
        if action == "status":
            self._device_status()
        else:
            self._print_usage("device status")

    def _device_status(self) -> None:
        if VirtualAudioDevice.is_installed():
            idx = VirtualAudioDevice.find_device_index()
            print(f"[OK] Virtual audio device is installed (index: {idx}).")
        else:
            print("[INFO] Virtual audio device is NOT installed.")
            print()
            print("      vDLNA recommends VB-CABLE (free, signed):")
            print("      https://vb-audio.com/Cable/index.htm")
            print()
            print("      Opening download page in browser...")
            VirtualAudioDevice.open_download_page()
            print()
            print("Available input devices:")
            for d in VirtualAudioDevice.list_devices():
                print(f"  [{d['index']}] {d['name']} "
                      f"({d['channels']}ch, {d['sample_rate']}Hz)")

    # ── dlna ─────────────────────────────────────────────────

    def do_dlna(self, arg: str) -> None:
        """Scan the LAN for DLNA media renderers or run diagnostics.

Usage: dlna scan | dlna diag
  scan — SSDP scan for DLNA MediaRenderer devices
  diag — run network diagnostics for SSDP/DLNA issues
Stores found devices so you can reference them in 'stream'.
"""
        args = shlex.split(arg)
        if not args or args[0] not in ("scan", "diag"):
            self._print_usage("dlna scan | dlna diag")
            return

        if args[0] == "diag":
            for line in run_diagnostics():
                print(line)
            return

        # scan
        print("Scanning for DLNA media renderers (5s timeout)...")

        async def _scan():
            return await scan_dlna_renderers(timeout=5.0, log_cb=print)

        devices = asyncio.run(_scan())
        self._last_devices = devices

        if not devices:
            print("[INFO] No DLNA renderers found.")
            print("Trying broad UPnP scan...")
            all_devs = asyncio.run(scan_all_upnp(timeout=5.0, log_cb=print))
            if all_devs:
                print(f"\nFound {len(all_devs)} UPnP device(s), but none are MediaRenderer.")
            else:
                print("Nothing found. Run 'dlna diag' for diagnostics.")
            return

        print(f"\nFound {len(devices)} renderer(s):\n")
        for i, d in enumerate(devices):
            print(f"  [{i}] {d['friendly_name']}")
            print(f"      UDN:  {d['udn']}")
            print(f"      Host: {d['host']}")
            if d['manufacturer']:
                print(f"      By:   {d['manufacturer']}")
            print()

    # ── stream ───────────────────────────────────────────────

    def do_stream(self, arg: str) -> None:
        """Start audio capture and HTTP FLAC streaming.

Usage: stream [--dlna NAME|INDEX] [--port PORT]
  --dlna NAME   — auto-connect to a DLNA renderer by name or UDN
  --dlna INDEX  — auto-connect using last 'dlna scan' result index
  --port PORT   — HTTP server port (default: 9876)

Ctrl+C to stop streaming.
"""
        parser = argparse.ArgumentParser(prog="stream", add_help=False)
        parser.add_argument("--dlna", type=str, default=None)
        parser.add_argument("--port", type=int, default=self._stream_port)
        try:
            ns = parser.parse_args(shlex.split(arg))
        except SystemExit:
            return

        self._stream_port = ns.port
        app = App(port=ns.port)

        dlna_device = None
        if ns.dlna:
            dlna_device = self._resolve_dlna(ns.dlna)
            if dlna_device is None:
                return

        print(f"[INFO] Starting stream server on port {ns.port}...")

        async def _run():
            setup_signal_handlers(app)
            try:
                await app.run(dlna_device=dlna_device)
            except KeyboardInterrupt:
                pass

        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            pass

        print("[INFO] Stream stopped.")

    def _resolve_dlna(self, query: str) -> dict | None:
        # Try index into last scan results first
        try:
            idx = int(query)
            if 0 <= idx < len(self._last_devices):
                d = self._last_devices[idx]
                print(f"[INFO] Selected: {d['friendly_name']}")
                return d
            print(f"[ERROR] Index {idx} out of range (0-{len(self._last_devices)-1})")
            return None
        except ValueError:
            pass

        # Search by name/UDN — re-scan
        print(f"Searching for DLNA device matching: '{query}'...")

        async def _scan():
            return await scan_dlna_renderers(timeout=5.0)

        devices = asyncio.run(_scan())
        self._last_devices = devices

        match = None
        for d in devices:
            name = d["friendly_name"].lower()
            udn = d["udn"].lower()
            if query.lower() in name or query.lower() in udn:
                match = d
                break

        if match:
            print(f"[INFO] Found: {match['friendly_name']}")
            return match

        print(f"[ERROR] No DLNA renderer matching '{query}' found.")
        print("Available devices:")
        for i, d in enumerate(devices):
            print(f"  [{i}] {d['friendly_name']} ({d['udn']})")
        print("Run 'dlna scan' first, or use an index number.")
        return None

    # ── meta ─────────────────────────────────────────────────

    def do_quit(self, _arg: str) -> bool:
        """Exit the shell."""
        print("Bye.")
        return True

    do_exit = do_quit
    do_q = do_quit

    def do_EOF(self, _arg: str) -> bool:
        print("")
        return True

    def emptyline(self) -> None:
        pass  # don't repeat last command on empty Enter

    @staticmethod
    def _print_usage(usage: str) -> None:
        print(f"Usage: {usage}")


# ── one-shot (non-interactive) mode ──────────────────────────

def cmd_device(args: argparse.Namespace) -> None:
    shell = VdlnaShell()
    if args.action == "status":
        shell._device_status()


def cmd_dlna(args: argparse.Namespace) -> None:
    if args.action == "scan":
        print("Scanning for DLNA media renderers...")

        async def _scan():
            return await scan_dlna_renderers(timeout=5.0, log_cb=print)

        devices = asyncio.run(_scan())
        if not devices:
            print("No DLNA renderers found.")
            print("\nTrying broad scan for all UPnP devices...")
            all_devs = asyncio.run(scan_all_upnp(timeout=5.0, log_cb=print))
            if all_devs:
                print(f"\nFound {len(all_devs)} UPnP device(s) total:")
                for d in all_devs:
                    print(f"  {d.get('server', d.get('usn', '?'))[:100]}")
                    print(f"    ST={d.get('st', '?')}, USN={d.get('usn', '?')[:80]}")
            return
        print(f"\nFound {len(devices)} renderer(s):\n")
        for d in devices:
            print(f"  Name:    {d['friendly_name']}")
            print(f"  UDN:     {d['udn']}")
            print(f"  Host:    {d['host']}")
            if d['manufacturer']:
                print(f"  Vendor:  {d['manufacturer']}")
            print()
    elif args.action == "diag":
        for line in run_diagnostics():
            print(line)


def cmd_stream(args: argparse.Namespace) -> None:
    app = App(port=args.port)

    async def _run():
        setup_signal_handlers(app)
        dlna_device = None
        if args.dlna:
            print(f"Searching for DLNA device matching: '{args.dlna}'")
            devices = await scan_dlna_renderers(timeout=5.0)
            match = None
            for d in devices:
                name = d["friendly_name"].lower()
                udn = d["udn"].lower()
                if args.dlna.lower() in name or args.dlna.lower() in udn:
                    match = d
                    break
            if match:
                dlna_device = match
                print(f"Found: {match['friendly_name']}")
            else:
                print(f"[ERROR] No DLNA renderer matching '{args.dlna}' found.")
                print("Available devices:")
                for d in devices:
                    print(f"  - {d['friendly_name']} ({d['udn']})")
                return

        try:
            await app.run(dlna_device=dlna_device)
        except KeyboardInterrupt:
            pass

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="vDLNA — Virtual sound card DLNA streaming tool",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--cli", action="store_true",
                        help="Launch interactive CLI shell instead of GUI")
    sub = parser.add_subparsers(dest="command")

    dev = sub.add_parser("device", help="Show virtual audio device status")
    dev.add_argument("action", choices=["status"])

    dlna = sub.add_parser("dlna", help="DLNA device discovery")
    dlna.add_argument("action", choices=["scan", "diag"])

    stream = sub.add_parser("stream", help="Start audio capture and HTTP stream")
    stream.add_argument("--dlna", type=str, default=None, metavar="NAME")
    stream.add_argument("--port", type=int, default=9876)

    # Parse known args; ignore unknown to allow interactive mode passthrough
    args, unknown = parser.parse_known_args()

    if args.help and args.command is None:
        parser.print_help()
        print("\nGUI mode:     python main.py")
        print("CLI mode:     python main.py --cli")
        return

    if args.command == "device":
        cmd_device(args)
    elif args.command == "dlna":
        cmd_dlna(args)
    elif args.command == "stream":
        cmd_stream(args)
    elif args.command is None and args.cli:
        VdlnaShell().cmdloop()
    elif args.command is None and not unknown:
        from vdlna.gui import run_gui
        run_gui()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _ensure_single_instance()
    main()
