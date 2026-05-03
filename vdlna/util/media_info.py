"""Detect currently playing media info from window titles.

Most media players (NetEase, QQ Music, Spotify, etc.) put the current
track info in their window title, typically as "Title - Artist".
This module enumerates visible windows belonging to known media player
processes and extracts the track info.
"""

import ctypes
from ctypes import wintypes

# Known media player process names (lowercase)
_PLAYER_EXES = {
    "cloudmusic.exe",   # NetEase Cloud Music
    "qqmusic.exe",      # QQ Music
    "spotify.exe",      # Spotify
    "wmplayer.exe",     # Windows Media Player
    "music.ui.exe",     # Apple Music (Windows)
    "foobar2000.exe",   # foobar2000
    "kgmusic.exe",      # 酷狗音乐
}

# Window class names that are likely media player main windows
_PLAYER_CLASSES = {
    "spotify",          # Spotify
    "qobuz",            # Qobuz
    "tidal",            # Tidal
}


def _get_visible_windows() -> list[tuple[int, str, str]]:
    """Return list of (pid, title, class_name) for all visible windows."""
    results = []
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length < 3 or length > 200:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True

        # Get class name
        cls_buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls_buf, 64)

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        results.append((pid.value, title, cls_buf.value.lower()))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return results


def _get_process_name(pid: int) -> str:
    """Get the executable name for a PID."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)  # QUERY | READ
    if not handle:
        return ""
    buf = ctypes.create_unicode_buffer(260)
    size = wintypes.DWORD(260)
    if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        path = buf.value
        kernel32.CloseHandle(handle)
        return path.rsplit("\\", 1)[-1].lower()
    kernel32.CloseHandle(handle)
    return ""


def get_media_info_sync() -> dict | None:
    """Return current track info, or None."""
    windows = _get_visible_windows()
    for pid, title, cls_name in windows:
        # Check class name first (fast path)
        if any(kw in cls_name for kw in _PLAYER_CLASSES):
            pass  # matched by class
        else:
            proc = _get_process_name(pid)
            if proc not in _PLAYER_EXES:
                continue

        # Skip non-music titles (file explorer, etc.)
        skips = ("文件资源管理器", "File Explorer", "Program Manager",
                 "Windows", "Microsoft", "Settings", "设置")
        if any(s in title for s in skips):
            continue

        # Try "Title - Artist" | "Artist - Title" | just "Title"
        title = title.strip()
        if " - " in title:
            parts = title.rsplit(" - ", 1)
            song = parts[0].strip()
            artist = parts[1].strip()
            # Heuristic: artist part is usually shorter
            if len(artist) > len(song):
                song, artist = artist, song
            return {"title": song, "artist": artist}
        elif len(title) > 2:
            return {"title": title, "artist": ""}

    return None
