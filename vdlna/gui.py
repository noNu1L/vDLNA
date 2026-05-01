"""Tkinter GUI for vDLNA — device management, DLNA scan, stream control."""

import asyncio
import queue
import socket
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from vdlna.app import App
from vdlna.audio.virtual_device import VirtualAudioDevice
from vdlna.dlna.control import DlnaRenderer
from vdlna.dlna.discovery import scan_dlna_renderers
from vdlna.util.config import load_config, save_config

try:
    from vdlna.util.windows import (
        has_startup_entry, add_startup_entry, remove_startup_entry,
        get_exe_path, start_tray_icon,
    )
    _HAS_WINDOWS_UTILS = True
except Exception:
    _HAS_WINDOWS_UTILS = False


DLNA_UDN_KEY = "dlna_udn"
DLNA_LOCATION_KEY = "dlna_location"
DLNA_NAME_KEY = "dlna_friendly_name"
DLNA_HOST_KEY = "dlna_host"
AUDIO_DEVICE_NAME_KEY = "audio_device_name"
PORT_KEY = "port"


def _find_available_port(start: int, max_tries: int = 20) -> int:
    """Return the first available TCP port starting from `start`."""
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"端口 {start}–{start + max_tries - 1} 均不可用")


class AsyncLoop:
    """Background asyncio event loop running in a daemon thread."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def run_coro(self, coro):
        if self._loop is None:
            raise RuntimeError("AsyncLoop not started")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def stop(self) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=3)


class AppGui:
    """Main GUI window."""

    def __init__(self):
        self._app: App | None = None
        self._streaming = False
        self._dlna_devices: list[dict] = []
        self._renderer: DlnaRenderer | None = None
        self._bound_udn: str | None = None
        self._scan_running = False
        self._stream_ready_event = threading.Event()
        self._stream_done_event = threading.Event()
        self._stream_done_event.set()
        self._stream_error: str | None = None
        self._log_queue = queue.Queue()
        self._tray_icon = None
        self._pending_auto_bind_udn: str | None = None
        self._async = AsyncLoop()
        self._async.start()

        self._root = tk.Tk()
        self._root.title("vDLNA")
        self._root.geometry("580x400")
        self._root.resizable(False, False)

        self._port_var = tk.StringVar(value="9876")  # 内部使用，不显示

        self._build_ui()
        self._poll_log()
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._apply_saved_config()
        self._log("vDLNA 图形界面已启动。")

    def _run_bg(self, task, on_ok=None, on_err=None) -> None:
        """Run *task()* in a daemon thread; call on_ok() or on_err(err) on the main thread."""
        def _worker():
            try:
                result = task()
                if on_ok:
                    self._root.after(0, lambda: on_ok(result))
            except Exception as exc:
                err = str(exc)
                if on_err:
                    self._root.after(0, lambda e=err: on_err(e))
        threading.Thread(target=_worker, daemon=True).start()

    # ── UI ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(3, weight=1)  # 日志区扩展

        r = 0
        self._build_top_bar(r); r += 1
        self._build_device_section(r); r += 1
        self._build_stream_section(r); r += 1
        self._build_log_section(r)

    def _build_top_bar(self, row: int) -> None:
        bar = ttk.Frame(self._root, padding=(8, 4, 8, 0))
        bar.grid(row=row, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)

        self._startup_var = tk.BooleanVar(value=self._check_startup_entry())
        ttk.Checkbutton(bar, text="开机启动",
                        variable=self._startup_var,
                        command=self._on_startup_toggle).grid(row=0, column=0, sticky="w")

        self._download_btn = ttk.Button(bar, text="下载 VB-CABLE",
                                         command=self._open_vbcable)
        self._download_btn.grid(row=0, column=1, sticky="e")

    def _build_device_section(self, row: int) -> None:
        f = ttk.LabelFrame(self._root, text="设备选择", padding=(8, 4))
        f.grid(row=row, column=0, sticky="ew", padx=8, pady=(2, 0))
        f.columnconfigure(1, weight=1)
        f.columnconfigure(2, weight=0)

        ttk.Label(f, text="监听设备:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._audio_combo = ttk.Combobox(f, state="readonly")
        self._audio_combo.grid(row=0, column=1, columnspan=2, sticky="ew")
        self._refresh_audio_list()

        ttk.Label(f, text="DLNA设备:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        self._dlna_combo = ttk.Combobox(f, state="readonly", width=28)
        self._dlna_combo.grid(row=1, column=1, sticky="ew", pady=(4, 0))
        self._scan_btn = ttk.Button(f, text="搜索设备", command=self._scan_dlna_background)
        self._scan_btn.grid(row=1, column=2, padx=(6, 0), pady=(4, 0))

    def _build_stream_section(self, row: int) -> None:
        f = ttk.LabelFrame(self._root, text="推流", padding=(8, 4))
        f.grid(row=row, column=0, sticky="ew", padx=8, pady=(4, 0))
        f.columnconfigure(3, weight=1)

        self._bind_btn = ttk.Button(f, text="建立连接", command=self._toggle_bind)
        self._bind_btn.grid(row=0, column=0, padx=(0, 10))

        ttk.Label(f, text="音量:").grid(row=0, column=1, padx=(0, 2))
        self._vol_var = tk.IntVar(value=100)
        self._vol_scale = ttk.Scale(f, from_=0, to=100, variable=self._vol_var,
                                     orient="horizontal", length=100,
                                     command=self._on_volume, state="disabled")
        self._vol_scale.grid(row=0, column=2, padx=2)
        self._vol_scale.bind("<MouseWheel>", self._on_vol_wheel)
        self._vol_label = ttk.Label(f, text="100%", width=5)
        self._vol_label.grid(row=0, column=3, padx=(2, 0), sticky="w")

        self._stream_status_var = tk.StringVar(value="已停止")
        ttk.Label(f, textvariable=self._stream_status_var, foreground="gray").grid(
            row=0, column=4, padx=(8, 0), sticky="e")

    def _build_log_section(self, row: int) -> None:
        f = ttk.LabelFrame(self._root, text="日志", padding=(8, 4))
        f.grid(row=row, column=0, sticky="nsew", padx=8, pady=(4, 6))
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)

        self._log_text = tk.Text(f, height=8, state="disabled", wrap="word",
                                  font=("Consolas", 9))
        self._log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(f, orient="vertical", command=self._log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._log_text.configure(yscrollcommand=scrollbar.set)

    # ── 声卡列表 ─────────────────────────────────────────────

    def _refresh_audio_list(self) -> None:
        # 只列监听设备（关键词过滤）
        self._audio_devices = VirtualAudioDevice.list_virtual_devices()
        if self._audio_devices:
            self._audio_combo["values"] = [
                f"{d['name']}  {d['sample_rate']}Hz  {d['channels']}ch" for d in self._audio_devices
            ]
            self._audio_combo.current(0)
            self._download_btn.grid_remove()
            for d in self._audio_devices:
                self._log(f"声卡: [{d['index']}] {d['name']}  {d['sample_rate']}Hz  {d['channels']}ch")
        else:
            self._audio_combo["values"] = []
            self._audio_combo.set("未检测到监听设备")
            self._audio_combo.configure(state="disabled")
            self._download_btn.grid()
            self._log("未检测到监听设备。")

    def _open_vbcable(self) -> None:
        self._log("正在打开 VB-CABLE 下载页面...")
        VirtualAudioDevice.open_download_page()

    # ── DLNA 扫描 ────────────────────────────────────────────

    def _auto_scan(self) -> None:
        self._scan_dlna_background()

    def _scan_dlna_background(self) -> None:
        if self._scan_running:
            return
        self._scan_running = True
        self._scan_btn.configure(state="disabled", text="搜索中...")

        def _worker() -> None:
            try:
                devices = self._async.run_coro(scan_dlna_renderers(timeout=5.0))
                self._root.after(0, lambda: self._on_scan_complete(devices))
            except Exception:
                self._root.after(0, lambda: self._on_scan_complete([]))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_scan_complete(self, devices: list[dict]) -> None:
        self._scan_running = False
        self._scan_btn.configure(state="normal", text="搜索设备")

        # 以 UDN 合并：已有设备保留，新设备追加，扫描到的用真实数据更新
        existing_by_udn = {d["udn"]: d for d in self._dlna_devices}
        added = 0
        for d in devices:
            if d["udn"] not in existing_by_udn:
                existing_by_udn[d["udn"]] = d
                added += 1
            else:
                # 用扫描到的真实数据更新（location 可能变化）
                existing_by_udn[d["udn"]].update(d)

        self._dlna_devices = list(existing_by_udn.values())

        # 记住当前选中项
        cur = self._dlna_combo.current()
        cur_udn = self._dlna_devices[cur]["udn"] if 0 <= cur < len(self._dlna_devices) else None

        labels = [f"{d['friendly_name']}  ({d['host']})" for d in self._dlna_devices]
        self._dlna_combo["values"] = labels

        # 恢复选中项
        if cur_udn:
            for i, d in enumerate(self._dlna_devices):
                if d["udn"] == cur_udn:
                    self._dlna_combo.current(i)
                    break
        elif labels:
            self._dlna_combo.current(0)

        msg = f"扫描到 {len(devices)} 个 DLNA 设备"
        if added:
            msg += f"，新增 {added} 个"
        self._log(msg + "。")

        # 旧版配置退回路径
        if self._pending_auto_bind_udn and not self._bound_udn:
            udn = self._pending_auto_bind_udn
            self._pending_auto_bind_udn = None
            device = next((d for d in devices if d["udn"] == udn), None)
            if device:
                self._dlna_combo.current(self._dlna_devices.index(device))
                self._log(f"自动连接: {device['friendly_name']}")
                self._do_bind()

    # ── 建立连接 / 断开连接 ──────────────────────────────────────────

    def _toggle_bind(self) -> None:
        if self._bound_udn:
            self._do_unbind()
        else:
            self._do_bind()

    def _set_connecting_ui(self, text: str = "连接中...") -> None:
        self._bind_btn.configure(state="disabled", text=text)
        self._audio_combo.configure(state="disabled")
        self._dlna_combo.configure(state="disabled")
        self._scan_btn.configure(state="disabled")

    def _set_unbound_ui(self) -> None:
        self._bind_btn.configure(state="normal", text="建立连接")
        self._audio_combo.configure(state="readonly")
        self._dlna_combo.configure(state="readonly")
        self._scan_btn.configure(state="normal")
        self._vol_scale.configure(state="disabled")

    def _set_bound_ui(self) -> None:
        self._bind_btn.configure(state="normal", text="断开连接")
        self._vol_scale.configure(state="normal")

    def _start_bind(self, audio_dev: dict, target: dict, save_cfg: bool) -> None:
        self._set_connecting_ui()

        def _task():
            if not self._streaming:
                self._start_stream_sync(audio_dev)

            if not self._app or not self._streaming:
                raise RuntimeError("推流未运行")

            stream_url = self._app.stream_url
            renderer = DlnaRenderer(target["location"])
            self._async.run_coro(renderer.connect(stream_url))
            self._async.run_coro(renderer.play())
            self._renderer = renderer
            self._bound_udn = target["udn"]

            if save_cfg:
                save_config({
                    PORT_KEY: int(self._port_var.get()),
                    AUDIO_DEVICE_NAME_KEY: audio_dev["name"],
                    DLNA_UDN_KEY: target["udn"],
                    DLNA_LOCATION_KEY: target["location"],
                    DLNA_NAME_KEY: target["friendly_name"],
                    DLNA_HOST_KEY: target["host"],
                })
            return target["friendly_name"], stream_url

        self._run_bg(
            _task,
            on_ok=lambda r: self._on_bind_success(*r),
            on_err=self._on_bind_error,
        )

    def _do_bind(self) -> None:
        audio_idx = self._audio_combo.current()
        if audio_idx < 0 or audio_idx >= len(self._audio_devices):
            messagebox.showwarning("提示", "请先选择监听设备。")
            return

        dlna_idx = self._dlna_combo.current()
        if dlna_idx < 0 or dlna_idx >= len(self._dlna_devices):
            messagebox.showwarning("提示", "请先选择 DLNA 设备。")
            return

        audio_dev = self._audio_devices[audio_idx]
        dlna_dev = self._dlna_devices[dlna_idx]
        self._start_bind(audio_dev, dlna_dev, save_cfg=True)

    def _on_bind_success(self, name: str, url: str) -> None:
        self._set_bound_ui()
        self._log(f"已连接: {name} → {url}")
        self._sync_volume_from_device()

    def _on_bind_error(self, err: str) -> None:
        self._set_unbound_ui()
        self._log(f"连接失败: {err}")

    def _do_unbind(self) -> None:
        renderer = self._renderer
        if not renderer:
            self._reset_bind_state()
            return

        self._set_connecting_ui("断开中...")

        def _task():
            try:
                self._async.run_coro(renderer.stop())
                self._async.run_coro(renderer.close())
            except Exception:
                pass
            self._renderer = None
            self._bound_udn = None
            self._streaming = False
            cfg = load_config()
            cfg.pop(DLNA_UDN_KEY, None)
            cfg.pop(DLNA_LOCATION_KEY, None)
            cfg.pop(DLNA_NAME_KEY, None)
            cfg.pop(DLNA_HOST_KEY, None)
            save_config(cfg)

        self._run_bg(_task, on_ok=lambda _: self._reset_bind_state())

    def _reset_bind_state(self) -> None:
        self._set_unbound_ui()
        self._log("已断开连接。")

    # ── 音量 ─────────────────────────────────────────────────

    def _on_vol_wheel(self, event) -> None:
        self._vol_var.set(max(0, min(100, self._vol_var.get() + (5 if event.delta > 0 else -5))))
        self._on_volume()

    def _on_volume(self, *_args) -> None:
        val = self._vol_var.get()
        self._vol_label.configure(text=f"{val}%")
        if not self._renderer:
            return
        renderer = self._renderer
        self._run_bg(lambda: self._async.run_coro(renderer.set_volume(val)))

    def _sync_volume_from_device(self) -> None:
        def _fetch():
            if not self._renderer:
                return None
            return self._async.run_coro(self._renderer.get_volume())

        def _apply(vol):
            if vol is not None:
                self._vol_var.set(vol)
                self._vol_label.configure(text=f"{vol}%")

        self._run_bg(_fetch, on_ok=_apply)

    # ── 配置 ─────────────────────────────────────────────────

    def _apply_saved_config(self) -> None:
        cfg = load_config()
        if not cfg:
            return
        if PORT_KEY in cfg:
            self._port_var.set(str(cfg[PORT_KEY]))

        # 恢复监听设备选择
        audio_name = cfg.get(AUDIO_DEVICE_NAME_KEY, "")
        if audio_name:
            for i, d in enumerate(self._audio_devices):
                if d["name"] == audio_name:
                    self._audio_combo.current(i)
                    break

        # 从配置恢复 DLNA 设备到下拉框（无需扫描）
        friendly = cfg.get(DLNA_NAME_KEY, "")
        host = cfg.get(DLNA_HOST_KEY, "")
        udn = cfg.get(DLNA_UDN_KEY, "")
        location = cfg.get(DLNA_LOCATION_KEY, "")
        if friendly and udn and location:
            saved_device = {
                "udn": udn,
                "friendly_name": friendly,
                "host": host,
                "location": location,
                "manufacturer": "",
            }
            self._dlna_devices = [saved_device]
            label = f"{friendly}  ({host})"
            self._dlna_combo["values"] = [label]
            self._dlna_combo.current(0)
            self._log(f"已从配置恢复设备: {label}")
            # 直接连接，不等扫描
            self._root.after(800, lambda: self._direct_connect_from_config(cfg))
        elif udn:
            self._pending_auto_bind_udn = udn

    def _direct_connect_from_config(self, cfg: dict) -> None:
        """跳过扫描，直接用保存的 location 建立连接。"""
        if self._bound_udn:
            return
        audio_idx = self._audio_combo.current()
        if audio_idx < 0 or audio_idx >= len(self._audio_devices):
            return

        audio_dev = self._audio_devices[audio_idx]
        target = {
            "udn": cfg[DLNA_UDN_KEY],
            "friendly_name": cfg.get(DLNA_NAME_KEY, cfg[DLNA_UDN_KEY]),
            "host": cfg.get(DLNA_HOST_KEY, ""),
            "location": cfg[DLNA_LOCATION_KEY],
        }
        self._log(f"直接连接: {target['friendly_name']}  ({target['host']})")
        self._start_bind(audio_dev, target, save_cfg=False)

    # ── 开机启动 ─────────────────────────────────────────────

    @staticmethod
    def _check_startup_entry() -> bool:
        return _HAS_WINDOWS_UTILS and has_startup_entry()

    def _on_startup_toggle(self) -> None:
        if not _HAS_WINDOWS_UTILS:
            return
        try:
            if self._startup_var.get():
                add_startup_entry(get_exe_path())
                self._log("已添加到开机启动。")
            else:
                remove_startup_entry()
                self._log("已从开机启动移除。")
        except Exception as e:
            self._log(f"开机启动设置失败: {e}")

    # ── 推流 ─────────────────────────────────────────────────

    def _start_stream_sync(self, audio_dev: dict) -> None:
        try:
            port = int(self._port_var.get())
        except ValueError:
            raise RuntimeError("端口号无效")

        port = _find_available_port(port)
        self._log(f"使用端口: {port}")

        self._stream_ready_event.clear()
        self._stream_done_event.clear()
        self._stream_error = None
        self._root.after(0, lambda: self._stream_status_var.set("启动中..."))

        self._app = App(port=port)

        async def _run():
            try:
                dev_idx = audio_dev["index"]
                actual_rate = audio_dev["sample_rate"]
                actual_ch = min(audio_dev["channels"], 2)
                self._app.configure_audio(dev_idx, actual_rate, actual_ch)
                self._log(f"采集: {audio_dev['name']} {actual_rate}Hz {actual_ch}ch")

                await self._app.start_stream(self._async._loop)
                url = self._app.stream_url
                self._app.feed_silence()

                self._streaming = True
                self._stream_ready_event.set()
                self._root.after(0, lambda: self._on_stream_started(url))

                while self._streaming:
                    latency = self._app.latency
                    self._root.after(0, lambda l=latency:
                        self._stream_status_var.set(f"运行中 | 延迟: {l*1000:.1f}ms"))
                    await asyncio.sleep(1)

                await self._app.stop_stream()
                self._root.after(0, self._on_stream_stopped)
            except Exception as e:
                err = str(e)
                self._stream_error = err
                self._stream_ready_event.set()
                self._root.after(0, lambda er=err: self._log(f"推流错误: {er}"))
                self._root.after(0, self._on_stream_stopped)

        asyncio.run_coroutine_threadsafe(_run(), self._async._loop)

        if not self._stream_ready_event.wait(timeout=10):
            raise RuntimeError("推流启动超时")
        if self._stream_error:
            raise RuntimeError(self._stream_error)

    def _on_stream_started(self, url: str) -> None:
        self._stream_status_var.set("运行中 | 延迟: --")
        self._log(f"推流地址: {url}")

    def _on_stream_stopped(self) -> None:
        self._stream_status_var.set("已停止")
        self._app = None
        self._log("推流已停止。")
        self._stream_done_event.set()

    # ── 日志 ─────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_queue.put(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

    def _poll_log(self) -> None:
        while True:
            try:
                line = self._log_queue.get_nowait()
                self._log_text.configure(state="normal")
                self._log_text.insert("end", line)
                self._log_text.see("end")
                self._log_text.configure(state="disabled")
            except queue.Empty:
                break
        self._root.after(500, self._poll_log)

    # ── 生命周期 ─────────────────────────────────────────────

    def _on_close(self) -> None:
        if self._tray_icon is not None:
            self._root.withdraw()
        else:
            self._do_quit()

    def _show_window(self) -> None:
        self._root.deiconify()
        self._root.lift()

    def _do_quit(self) -> None:
        if self._renderer:
            try:
                self._async.run_coro(self._renderer.stop())
                self._async.run_coro(self._renderer.close())
            except Exception:
                pass
        if self._streaming:
            self._streaming = False
            self._stream_done_event.wait(timeout=5)
        if self._tray_icon is not None:
            self._tray_icon.stop()
        self._async.stop()
        self._root.destroy()

    def run(self) -> None:
        if _HAS_WINDOWS_UTILS:
            try:
                self._tray_icon = start_tray_icon(self._show_window, self._do_quit)
            except Exception as e:
                self._log(f"托盘图标启动失败: {e}")
        self._root.mainloop()


def run_gui() -> None:
    AppGui().run()
