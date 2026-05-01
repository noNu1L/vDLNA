"""Application orchestrator — wires audio capture, FLAC encoding, HTTP server, and DLNA."""

import asyncio
import signal
import sys
import time

from vdlna.audio.capture import AudioCapture
from vdlna.audio.encoder import FlacBroadcastEncoder
from vdlna.audio.virtual_device import VirtualAudioDevice
from vdlna.dlna.control import DlnaRenderer
from vdlna.streaming.http_server import StreamServer


class App:
    """Top-level application orchestrator.

    Usage:
        app = App(port=9876)
        await app.run(dlna_device=None)  # stream only
        await app.run(dlna_device="Living Room Speaker")  # auto-connect to DLNA
    """

    def __init__(self, port: int = 9876):
        self._port = port
        self._encoder = FlacBroadcastEncoder()
        self._capture = AudioCapture()
        self._server = StreamServer(self._encoder, port=port)

        self._renderer: DlnaRenderer | None = None
        self._shutdown_event = asyncio.Event()

    async def run(self, dlna_device: dict | None = None) -> None:
        """Start all subsystems and run until Ctrl+C."""
        loop = asyncio.get_running_loop()
        self._encoder.set_event_loop(loop)

        # Find virtual audio device
        dev_idx = VirtualAudioDevice.find_device_index()
        if dev_idx is None:
            print("[WARN] Virtual audio device not found. Available devices:")
            for d in VirtualAudioDevice.list_devices():
                print(f"  [{d['index']}] {d['name']}")
            print("[INFO] Install it with: python main.py device install")
            print("[INFO] Will attempt capture from default input device instead.")
        else:
            self._capture._device_index = dev_idx
            for d in VirtualAudioDevice.list_devices():
                if d["index"] == dev_idx:
                    self._capture._sample_rate = d["sample_rate"]
                    self._capture._channels = min(d["channels"], 2)
                    self._encoder._sample_rate = d["sample_rate"]
                    self._encoder._channels = min(d["channels"], 2)
                    break
            print(f"[INFO] Capturing from device [{dev_idx}]: virtual audio driver")

        self._encoder.start()
        self._capture.set_pcm_callback(self._encoder.feed_pcm)

        # Start HTTP server
        await self._server.start()
        print(f"[INFO] FLAC stream available at: {self._server.url}")
        print("[INFO] Press Ctrl+C to stop.")

        # Start audio capture
        self._capture.start()

        # DLNA auto-connect
        if dlna_device:
            print(f"[INFO] Connecting to DLNA renderer: {dlna_device.get('friendly_name', 'Unknown')}")
            self._renderer = DlnaRenderer(dlna_device["location"])
            try:
                await self._renderer.connect(self._server.url)
                await self._renderer.play()
                print(f"[OK] Streaming to {dlna_device['friendly_name']}")
            except Exception as e:
                print(f"[ERROR] DLNA connection failed: {e}")

        # Main loop — print status every 2 seconds
        try:
            while not self._shutdown_event.is_set():
                client_count = self._encoder.client_count
                latency = self._capture.latency
                print(f"\r[STATUS] Clients: {client_count} | Latency: {latency*1000:.1f}ms    ",
                      end="", flush=True)
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        print("\n[INFO] Shutting down...")
        self._capture.stop()
        self._encoder.stop()

        if self._renderer:
            try:
                await self._renderer.stop()
            except Exception:
                pass
            finally:
                await self._renderer.close()

        await self._server.stop()
        print("[INFO] Stopped.")


def setup_signal_handlers(app: App) -> None:
    """Register Ctrl+C handler for graceful shutdown."""
    loop = asyncio.get_running_loop()

    def _on_signal():
        app._shutdown_event.set()

    try:
        loop.add_signal_handler(signal.SIGINT, _on_signal)
    except NotImplementedError:
        # Windows — signal handlers are set but work differently
        pass
    try:
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
    except NotImplementedError:
        pass
