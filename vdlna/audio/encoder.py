"""FLAC broadcast encoder using pyFLAC StreamEncoder with multi-client queue fanout."""

import asyncio
import threading
from collections import deque

import numpy as np
import pyflac


class FlacBroadcastEncoder:
    """Encodes PCM frames in real time and fans out FLAC frames to multiple async queues.

    One encoder, N queues — each client receives the same FLAC frames.
    """

    def __init__(self, sample_rate: int = 44100, channels: int = 2,
                 bits_per_sample: int = 16, compression_level: int = 5):
        self._sample_rate = sample_rate
        self._channels = channels
        self._bits_per_sample = bits_per_sample
        self._compression_level = compression_level

        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: list[asyncio.Queue[bytes]] = []
        self._lock = threading.Lock()
        self._encoder: pyflac.StreamEncoder | None = None

        self._header_blocks: list[bytes] = []
        self._header_complete = False
        self._audio_ring: deque[bytes] = deque(maxlen=64)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def add_client(self) -> asyncio.Queue[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        with self._lock:
            # Pre-fill with cached FLAC header so every client gets a valid stream
            for block in self._header_blocks:
                try:
                    q.put_nowait(block)
                except asyncio.QueueFull:
                    break
            # Pre-fill with recent audio frames so new clients have immediate data
            for block in self._audio_ring:
                try:
                    q.put_nowait(block)
                except asyncio.QueueFull:
                    break
            self._queues.append(q)
        return q

    def remove_client(self, q: asyncio.Queue[bytes]) -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    @property
    def client_count(self) -> int:
        return len(self._queues)

    def start(self) -> None:
        if self._encoder is not None:
            return
        self._encoder = pyflac.StreamEncoder(
            sample_rate=self._sample_rate,
            write_callback=self._on_flac_frame,
            compression_level=self._compression_level,
            streamable_subset=True,
        )

    def feed_pcm(self, samples: np.ndarray) -> None:
        """Called from sounddevice callback thread to feed PCM data.

        Args:
            samples: float32/float64 numpy array of shape (frames, channels),
                     values in [-1.0, 1.0].
        """
        if self._encoder is None:
            return
        # pyflac infers bits_per_sample from dtype.itemsize * 8.
        # Use int16 for standard 16-bit FLAC — universally supported by DLNA devices.
        int_samples = (samples * 32767).astype(np.int16)
        self._encoder.process(int_samples)

    def _on_flac_frame(self, buffer: bytes, samples: int,
                       current_frame: int, total_samples: int) -> None:
        """Called by pyflac when FLAC data is ready.

        pyflac emits:
          1) fLaC stream marker
          2) metadata blocks (STREAMINFO, etc.)
          3) audio frames (start with 0xFFF8 / 0xFFF9)

        We cache the marker + metadata once so late-connecting clients still
        receive a valid FLAC stream header.
        """
        if self._loop is None:
            return

        # Cache the FLAC header (everything before the first audio frame)
        if not self._header_complete:
            if buffer[:4] == b"fLaC":
                self._header_blocks.append(buffer)
            elif len(buffer) >= 2 and buffer[0] == 0xFF and buffer[1] in (0xF8, 0xF9):
                self._header_complete = True
                self._audio_ring.append(buffer)
            else:
                self._header_blocks.append(buffer)
        else:
            self._audio_ring.append(buffer)

        with self._lock:
            queues = list(self._queues)
        for q in queues:
            try:
                self._loop.call_soon_threadsafe(self._put_nowait, q, buffer)
            except Exception:
                pass

    @staticmethod
    def _put_nowait(q: asyncio.Queue[bytes], data: bytes) -> None:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(data)
            except asyncio.QueueEmpty:
                pass

    def stop(self) -> None:
        if self._encoder is None:
            return
        self._encoder.finish()
        self._encoder = None
