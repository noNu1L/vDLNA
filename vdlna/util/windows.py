"""Windows-specific helpers — startup registry and system tray."""

import sys
import threading
import winreg
from pathlib import Path

STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_NAME = "vDLNA"


def add_startup_entry(exe_path: str) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')


def remove_startup_entry() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, STARTUP_NAME)
    except FileNotFoundError:
        pass


def has_startup_entry() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, STARTUP_NAME)
        return True
    except FileNotFoundError:
        return False


def get_exe_path() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return sys.argv[0]


def tray_icon_image():
    from PIL import Image
    ico = Path(__file__).resolve().parents[2] / "assets" / "icon_32.png"
    if ico.exists():
        return Image.open(ico).convert("RGBA")
    # fallback
    from PIL import ImageDraw
    img = Image.new("RGBA", (32, 32), (50, 140, 80, 255))
    ImageDraw.Draw(img).text((9, 6), "D", fill="white")
    return img


def apply_window_icon(root) -> None:
    """设置窗口左上角及任务栏图标。"""
    ico = Path(__file__).resolve().parents[2] / "assets" / "icon.ico"
    if ico.exists():
        root.iconbitmap(default=str(ico))


def start_tray_icon(show_cb, exit_cb):
    import pystray
    menu = pystray.Menu(
        pystray.MenuItem("打开主界面", show_cb, default=True),
        pystray.MenuItem("退出", exit_cb),
    )
    icon = pystray.Icon("vDLNA", tray_icon_image(), "vDLNA", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon
