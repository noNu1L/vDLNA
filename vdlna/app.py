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
        self._stream_started = False

    def configure_audio(self, device_index: int, sample_rate: int, channels: int) -> None:
        channels = min(channels, 2)
        self._capture._device_index = device_index
        self._capture._sample_rate = sample_rate
        self._capture._channels = channels
        self._encoder._sample_rate = sample_rate
        self._encoder._channels = channels

    async def start_stream(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._stream_started:
            return
        self._encoder.set_event_loop(loop)
        self._encoder.start()
        self._capture.set_pcm_callback(self._encoder.feed_pcm)
        await self._server.start()
        self._capture.start()
        self._stream_started = True

    async def stop_stream(self) -> None:
        if not self._stream_started:
            return
        self._capture.stop()
        self._encoder.stop()
        await self._server.stop()
        self._stream_started = False

    @property
    def stream_url(self) -> str:
        return self._server.url

    @property
    def latency(self) -> float:
        return self._capture.latency

    @property
    def client_count(self) -> int:
        return self._encoder.client_count

    def feed_silence(self, frames: int = 1024) -> None:
        import numpy as np
        silence = np.zeros((frames, self._capture._channels), dtype=np.float32)
        self._encoder.feed_pcm(silence)

    async def run(self, dlna_device: dict | None = None) -> None:
        """Start all subsystems and run until Ctrl+C."""
        loop = asyncio.get_running_loop()

        # Find virtual audio device
        dev_idx = VirtualAudioDevice.find_device_index()
        if dev_idx is None:
            print("[WARN] Virtual audio device not found. Available devices:")
            for d in VirtualAudioDevice.list_devices():
                print(f"  [{d['index']}] {d['name']}")
            print("[INFO] Install it with: python main.py device install")
            print("[INFO] Will attempt capture from default input device instead.")
        else:
            for d in VirtualAudioDevice.list_devices():
                if d["index"] == dev_idx:
                    self.configure_audio(d["index"], d["sample_rate"], d["channels"])
                    break
            print(f"[INFO] Capturing from device [{dev_idx}]: virtual audio driver")

        await self.start_stream(loop)
        print(f"[INFO] FLAC stream available at: {self._server.url}")
        print("[INFO] Press Ctrl+C to stop.")

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

        if self._renderer:
            try:
                await self._renderer.stop()
            except Exception:
                pass
            finally:
                await self._renderer.close()

        await self.stop_stream()
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
