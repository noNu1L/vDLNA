#!/usr/bin/env python
"""vDLNA — Virtual sound card DLNA streaming tool."""

import sys


def _set_dpi_awareness() -> None:
    """Enable Windows DPI awareness so the GUI renders crisp at >100% scaling."""
    if sys.platform != "win32":
        return
    import ctypes
    for _awareness in (2, 1, 0):  # PerMonitorV2 → PerMonitor → System
        try:
            result = ctypes.windll.shcore.SetProcessDpiAwareness(_awareness)
            if result == 0:
                return
        except Exception:
            continue
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


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


def main() -> None:
    from vdlna.gui import run_gui
    run_gui()


if __name__ == "__main__":
    _set_dpi_awareness()
    _ensure_single_instance()
    try:
        main()
    except KeyboardInterrupt:
        pass
