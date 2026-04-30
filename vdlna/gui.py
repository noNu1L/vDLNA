"""Tkinter GUI for vDLNA — device management, DLNA scan, stream control."""

import asyncio
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from vdlna.app import App
from vdlna.audio.virtual_device import VirtualAudioDevice
from vdlna.dlna.control import DlnaRenderer
from vdlna.dlna.discovery import scan_dlna_renderers


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
        """Schedule a coroutine on the background loop and return its result (blocking)."""
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
        self._root = tk.Tk()
        self._root.title("vDLNA — Virtual DLNA Streamer")
        self._root.geometry("720x600")
        self._root.resizable(False, False)

        self._app: App | None = None
        self._streaming = False
        self._dlna_devices: list[dict] = []
        self._bind_status_by_udn: dict[str, str] = {}
        self._renderer_by_udn: dict[str, DlnaRenderer] = {}
        self._scan_running = False
        self._stream_ready_event = threading.Event()
        self._stream_done_event = threading.Event()
        self._stream_done_event.set()
        self._stream_error: str | None = None
        self._log_queue = queue.Queue()

        self._async = AsyncLoop()
        self._async.start()

        self._build_ui()
        self._poll_log()
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._log("vDLNA GUI started.")

    # ── UI construction ──────────────────────────────────────

    def _build_ui(self) -> None:
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(4, weight=1)  # log area expands

        r = 0
        self._build_device_section(r); r += 1
        ttk.Separator(self._root, orient="horizontal").grid(
            row=r, column=0, sticky="ew", pady=4); r += 1
        self._build_playback_section(r); r += 1
        ttk.Separator(self._root, orient="horizontal").grid(
            row=r, column=0, sticky="ew", pady=4); r += 1
        self._build_log_section(r)

    def _section(self, row: int, title: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(self._root, text=title, padding=6)
        frame.grid(row=row, column=0, sticky="ew", padx=8, pady=2)
        frame.columnconfigure(0, weight=1)
        return frame

    def _build_device_section(self, row: int) -> None:
        f = self._section(row, "Virtual Audio Device")
        self._dev_status_var = tk.StringVar(value="Checking...")
        ttk.Label(f, textvariable=self._dev_status_var).grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        self._download_btn = ttk.Button(
            f, text="Download VB-CABLE", command=self._open_vbcable)
        self._download_btn.grid(row=0, column=1, padx=(0, 4))
        ttk.Button(f, text="Refresh Status", command=self._refresh_device_status).grid(
            row=0, column=2, padx=4)
        self._refresh_device_status()

    def _build_playback_section(self, row: int) -> None:
        f = self._section(row, "Playback")

        # Device list
        cols = ("name", "host", "udn", "bind")
        self._dlna_tree = ttk.Treeview(f, columns=cols, show="headings",
                                        height=6, selectmode="browse")
        self._dlna_tree.heading("name", text="Name")
        self._dlna_tree.heading("host", text="Host")
        self._dlna_tree.heading("udn", text="UDN")
        self._dlna_tree.heading("bind", text="Bind Status")
        self._dlna_tree.column("name", width=180)
        self._dlna_tree.column("host", width=120)
        self._dlna_tree.column("udn", width=260)
        self._dlna_tree.column("bind", width=100, anchor="center")
        self._dlna_tree.grid(row=0, column=0, columnspan=4, sticky="ew", pady=2)

        # Stream controls + actions
        ttk.Label(f, text="Port:").grid(row=1, column=0, sticky="w")
        self._port_var = tk.StringVar(value="9876")
        self._port_entry = ttk.Entry(f, textvariable=self._port_var, width=8)
        self._port_entry.grid(row=1, column=1, sticky="w", padx=4)

        self._url_var = tk.StringVar(value="http://---/stream.flac")
        ttk.Label(f, textvariable=self._url_var, foreground="gray").grid(
            row=1, column=2, padx=8, sticky="w")

        self._stream_status_var = tk.StringVar(value="Stopped")
        ttk.Label(f, textvariable=self._stream_status_var).grid(
            row=1, column=3, padx=8, sticky="e")

        btn_frame = ttk.Frame(f)
        btn_frame.grid(row=3, column=0, columnspan=4, sticky="w", pady=4)

        self._scan_btn = ttk.Button(btn_frame, text="Scan Network", command=self._scan_dlna)
        self._scan_btn.grid(row=0, column=0, padx=(0, 8))

        self._bind_btn = ttk.Button(btn_frame, text="Bind", command=self._bind_selected)
        self._bind_btn.grid(row=0, column=1, padx=4)

        self._stop_btn = ttk.Button(btn_frame, text="Stop Stream",
                                     command=self._stop_stream, state="disabled")
        self._stop_btn.grid(row=0, column=2, padx=4)

        ttk.Label(btn_frame, text="  Volume:").grid(row=0, column=3, padx=(16, 2))
        self._vol_var = tk.IntVar(value=100)
        self._vol_scale = ttk.Scale(btn_frame, from_=0, to=100, variable=self._vol_var,
                                     orient="horizontal", length=120, command=self._on_volume,
                                     state="disabled")
        self._vol_scale.grid(row=0, column=4, padx=2)
        self._vol_scale.bind("<MouseWheel>", self._on_vol_wheel)
        self._vol_label = ttk.Label(btn_frame, text="100%", width=5)
        self._vol_label.grid(row=0, column=5, padx=(2, 0))

        f.columnconfigure(3, weight=1)

    def _build_log_section(self, row: int) -> None:
        f = self._section(row, "Log")
        f.rowconfigure(0, weight=1)
        self._log_text = tk.Text(f, height=10, state="disabled", wrap="word",
                                  font=("Consolas", 9))
        self._log_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(f, orient="vertical", command=self._log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._log_text.configure(yscrollcommand=scrollbar.set)

    # ── Device actions ───────────────────────────────────────

    def _open_vbcable(self) -> None:
        self._log("Opening VB-CABLE download page...")
        VirtualAudioDevice.open_download_page()

    def _refresh_device_status(self) -> None:
        if VirtualAudioDevice.is_installed():
            idx = VirtualAudioDevice.find_device_index()
            self._dev_status_var.set(f"Installed (sounddevice index: {idx})")
            self._download_btn.grid_remove()
        else:
            self._dev_status_var.set(
                "Not installed — click 'Download VB-CABLE' to get the driver")
            self._download_btn.grid()

    # ── DLNA actions ─────────────────────────────────────────

    def _scan_dlna(self) -> None:
        if self._scan_running:
            return
        self._scan_running = True
        self._scan_btn.configure(state="disabled", text="Scanning...")
        self._log("Scanning for DLNA renderers...")
        self._dlna_tree.delete(*self._dlna_tree.get_children())
        self._dlna_devices = []
        self._bind_status_by_udn = {}

        def _on_device(device: dict) -> None:
            # Called from asyncio thread — schedule UI update on tk thread
            def _update_ui() -> None:
                self._dlna_devices.append(device)
                self._bind_status_by_udn[device["udn"]] = "Unbound"
                self._dlna_tree.insert("", "end", values=(
                    device["friendly_name"], device["host"], device["udn"], "Unbound"))

            self._root.after(0, _update_ui)

        def _scan_worker() -> None:
            log_entries = []

            def _on_log(msg: str) -> None:
                log_entries.append(msg)

            try:
                devices = self._async.run_coro(
                    scan_dlna_renderers(timeout=2.0, log_cb=_on_log, device_cb=_on_device))
                self._root.after(0, lambda: self._on_scan_complete(devices, log_entries, None))
            except Exception as e:
                self._root.after(0, lambda: self._on_scan_complete([], log_entries, str(e)))

        threading.Thread(target=_scan_worker, daemon=True).start()

    def _on_scan_complete(self, devices: list[dict], log_entries: list[str], error: str | None) -> None:
        self._scan_running = False
        self._scan_btn.configure(state="normal", text="Scan Network")

        for entry in log_entries:
            self._log(f"  DEBUG: {entry}")

        if error:
            self._log(f"Scan error: {error}")
            return

        if not devices:
            self._log("No DLNA renderers found.")
            return

        self._log(f"Found {len(devices)} DLNA renderer(s).")

    def _refresh_bind_status_column(self) -> None:
        for item_id in self._dlna_tree.get_children():
            vals = list(self._dlna_tree.item(item_id, "values"))
            if len(vals) < 4:
                continue
            udn = vals[2]
            vals[3] = self._bind_status_by_udn.get(udn, "Unbound")
            self._dlna_tree.item(item_id, values=vals)

    def _set_bind_status(self, udn: str, status: str) -> None:
        self._bind_status_by_udn[udn] = status
        self._refresh_bind_status_column()

    def _bind_selected(self) -> None:
        sel = self._dlna_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select a DLNA device first.")
            return

        values = self._dlna_tree.item(sel[0], "values")
        name, host, udn, *_ = values

        # Toggle behavior: Bound -> unbind
        current = self._bind_status_by_udn.get(udn, "Unbound")
        if current == "Bound":
            self._unbind_device(udn, name)
            return

        self._set_bind_status(udn, "Binding")
        self._bind_btn.configure(state="disabled")

        def _bind_worker() -> None:
            try:
                if not self._streaming:
                    self._start_stream(background=True)
                    ready = self._stream_ready_event.wait(timeout=10)
                    if not ready:
                        raise RuntimeError("Stream startup timeout")
                    if self._stream_error:
                        raise RuntimeError(self._stream_error)

                if not self._app or not self._streaming:
                    raise RuntimeError("Stream is not running")

                device = next((d for d in self._dlna_devices if d["udn"] == udn), None)
                if not device:
                    raise RuntimeError("Selected device not found")

                stream_url = self._app._server.url
                renderer = DlnaRenderer(device["location"])
                self._async.run_coro(renderer.connect(stream_url))
                self._async.run_coro(renderer.play())
                self._renderer_by_udn[udn] = renderer

                self._root.after(0, lambda: self._on_bind_success(udn, name, stream_url))
            except Exception as e:
                err = str(e)
                self._root.after(0, lambda u=udn, n=name, er=err: self._on_bind_error(u, n, er))

        threading.Thread(target=_bind_worker, daemon=True).start()

    def _on_bind_success(self, udn: str, name: str, url: str) -> None:
        self._set_bind_status(udn, "Bound")
        self._bind_btn.configure(state="normal")
        self._vol_scale.configure(state="normal")
        self._log(f"Bound: {name} → {url}")
        self._sync_volume_from_device(udn)

    def _on_bind_error(self, udn: str, name: str, err: str) -> None:
        self._set_bind_status(udn, "Error")
        self._bind_btn.configure(state="normal")
        self._log(f"Bind failed for {name}: {err}")

    def _unbind_device(self, udn: str, name: str) -> None:
        renderer = self._renderer_by_udn.get(udn)
        if not renderer:
            self._set_bind_status(udn, "Unbound")
            return

        self._set_bind_status(udn, "Binding")
        self._bind_btn.configure(state="disabled")

        def _unbind_worker() -> None:
            try:
                self._async.run_coro(renderer.stop())
                self._async.run_coro(renderer.close())
            except Exception:
                pass
            finally:
                self._renderer_by_udn.pop(udn, None)
                self._root.after(0, lambda: self._on_unbind_done(udn, name))

        threading.Thread(target=_unbind_worker, daemon=True).start()

    def _on_unbind_done(self, udn: str, name: str) -> None:
        self._set_bind_status(udn, "Unbound")
        self._bind_btn.configure(state="normal")
        if not self._renderer_by_udn:
            self._vol_scale.configure(state="disabled")
        self._log(f"Unbound: {name}")

    # ── Volume ────────────────────────────────────────────────

    def _on_vol_wheel(self, event) -> None:
        delta = 5 if event.delta > 0 else -5
        new_val = max(0, min(100, self._vol_var.get() + delta))
        self._vol_var.set(new_val)
        self._on_volume()

    def _on_volume(self, *_args) -> None:
        val = self._vol_var.get()
        self._vol_label.configure(text=f"{val}%")
        renderers = list(self._renderer_by_udn.values())
        if not renderers:
            return

        def _send_volume() -> None:
            for r in renderers:
                try:
                    self._async.run_coro(r.set_volume(val))
                except Exception:
                    pass

        threading.Thread(target=_send_volume, daemon=True).start()

    def _sync_volume_from_device(self, udn: str) -> None:
        """Fetch current volume from the DLNA device and update the slider."""

        def _fetch() -> None:
            renderer = self._renderer_by_udn.get(udn)
            if renderer is None:
                return
            try:
                vol = self._async.run_coro(renderer.get_volume())
                if vol is not None:
                    self._root.after(0, lambda: self._vol_var.set(vol))
                    self._root.after(0, lambda: self._vol_label.configure(text=f"{vol}%"))
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    # ── Stream actions ───────────────────────────────────────

    def _start_stream(self, background: bool = False) -> None:
        if self._streaming:
            return
        try:
            port = int(self._port_var.get())
        except ValueError:
            if not background:
                messagebox.showerror("Invalid Port", "Port must be a number.")
            return

        self._stream_ready_event.clear()
        self._stream_done_event.clear()
        self._stream_error = None
        self._log(f"Starting stream server on port {port}...")
        self._stream_status_var.set("Starting...")

        self._app = App(port=port)

        async def _run():
            try:
                self._app._encoder.set_event_loop(self._async._loop)
                self._app._encoder.start()
                self._app._capture.set_pcm_callback(self._app._encoder.feed_pcm)

                dev_idx = VirtualAudioDevice.find_device_index()
                if dev_idx is not None:
                    import sounddevice as sd
                    dev_info = sd.query_devices(dev_idx)
                    actual_rate = int(dev_info["default_samplerate"])
                    actual_ch = min(int(dev_info["max_input_channels"]), 2)
                    self._app._capture._device_index = dev_idx
                    self._app._capture._sample_rate = actual_rate
                    self._app._capture._channels = actual_ch
                    self._app._encoder._sample_rate = actual_rate
                    self._app._encoder._channels = actual_ch
                    self._log(f"Capture: device={dev_info['name'].strip()}, "
                              f"{actual_rate}Hz, {actual_ch}ch")

                await self._app._server.start()
                url = self._app._server.url
                self._app._capture.start()

                # Prime the encoder with one silent frame so FLAC STREAMINFO
                # is written immediately — DLNA devices need it before audio starts.
                import numpy as np
                silence = np.zeros((1024, actual_ch if dev_idx is not None else 2),
                                   dtype=np.float32)
                self._app._encoder.feed_pcm(silence)

                self._streaming = True
                self._stream_ready_event.set()
                self._root.after(0, lambda: self._on_stream_started(url))

                while self._streaming:
                    clients = self._app._encoder.client_count
                    latency = self._app._capture.latency
                    self._root.after(0, lambda c=clients, l=latency: (
                        self._stream_status_var.set(
                            f"Running — Clients: {c} | Latency: {l*1000:.1f}ms")
                    ))
                    await asyncio.sleep(1)

                self._app._capture.stop()
                self._app._encoder.stop()
                await self._app._server.stop()
                self._root.after(0, self._on_stream_stopped)
            except Exception as e:
                err = str(e)
                self._stream_error = err
                self._stream_ready_event.set()
                self._root.after(0, lambda er=err: self._log(f"Stream error: {er}"))
                self._root.after(0, self._on_stream_stopped)

        asyncio.run_coroutine_threadsafe(_run(), self._async._loop)

    def _on_stream_started(self, url: str) -> None:
        self._url_var.set(url)
        self._stream_status_var.set("Running — Clients: 0")
        self._stop_btn.configure(state="normal")
        self._port_entry.configure(state="disabled")
        self._log(f"Stream available at: {url}")

    def _stop_stream(self) -> None:
        self._log("Stopping stream...")
        self._streaming = False
        self._stop_btn.configure(state="disabled")

    def _on_stream_stopped(self) -> None:
        self._stream_status_var.set("Stopped")
        self._url_var.set("http://---/stream.flac")
        self._port_entry.configure(state="normal")
        self._app = None
        self._log("Stream stopped.")
        self._stream_done_event.set()

    # ── Log ───────────────────────────────────────────────────

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

    # ── Lifecycle ────────────────────────────────────────────

    def _on_close(self) -> None:
        if self._streaming:
            if not messagebox.askyesno("Stream Running",
                                        "Stream is still running. Stop and exit?"):
                return
            self._stop_stream()
            self._stream_done_event.wait(timeout=5)
        self._async.stop()
        self._root.destroy()

    def run(self) -> None:
        self._root.mainloop()


def run_gui() -> None:
    AppGui().run()
