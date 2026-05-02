"""Real-time audio latency measurement via cross-correlation.

Measures the delay between a virtual audio device output and microphone
capture using numpy cross-correlation. No external dependencies beyond
numpy and sounddevice.
"""

import threading
from collections import deque

import numpy as np
import sounddevice as sd


class LatencyMeter:
    """Measure round-trip latency between virtual audio and microphone.

    Opens two separate sounddevice.InputStream instances at the same
    sample rate. Large ring buffers (~10 seconds) allow detecting
    multi-second DLNA delays via cross-correlation.
    """

    # At 48kHz with 1024-frame blocks: 48000 * 10 / 1024 ≈ 470 blocks
    RING_BLOCKS = 512
    CORR_WINDOW_SECS = 8.0
    MIN_DATA_SECS = 2.0

    def __init__(
        self,
        virtual_device_index: int,
        sample_rate: int,
        channels: int,
        mic_device_index: int | None = None,
    ):
        self._virt_index = virtual_device_index
        self._sample_rate = sample_rate
        self._channels = min(channels, 2)
        self._mic_index = mic_device_index

        self._lock = threading.Lock()
        self._virt_blocks: deque[np.ndarray] = deque(maxlen=self.RING_BLOCKS)
        self._mic_blocks: deque[np.ndarray] = deque(maxlen=self.RING_BLOCKS)
        self._virt_stream: sd.InputStream | None = None
        self._mic_stream: sd.InputStream | None = None
        self._running = False

    # -- InputStream callbacks -------------------------------------------------

    def _virt_callback(self, indata: np.ndarray, frames: int,
                       _time_info, status: sd.CallbackFlags) -> None:
        if status:
            return
        with self._lock:
            self._virt_blocks.append(indata.copy())

    def _mic_callback(self, indata: np.ndarray, frames: int,
                      _time_info, status: sd.CallbackFlags) -> None:
        if status:
            return
        with self._lock:
            self._mic_blocks.append(indata.copy())

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return

        self._virt_blocks.clear()
        self._mic_blocks.clear()

        self._virt_stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            device=self._virt_index,
            dtype=np.float32,
            callback=self._virt_callback,
        )
        self._mic_stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            device=self._mic_index,
            dtype=np.float32,
            callback=self._mic_callback,
        )
        self._virt_stream.start()
        self._mic_stream.start()
        self._running = True

    def stop(self) -> None:
        self._running = False
        for s in (self._virt_stream, self._mic_stream):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass
        self._virt_stream = None
        self._mic_stream = None
        with self._lock:
            self._virt_blocks.clear()
            self._mic_blocks.clear()

    # -- measurement ----------------------------------------------------------

    def compute_latency_ms(self) -> float | None:
        """Cross-correlate virtual vs mic audio, return estimated latency in ms.

        Returns None if either buffer has less than MIN_DATA_SECS of data.
        Searches up to CORR_WINDOW_SECS of lag in either direction.
        """
        block_frames = 1024
        min_blocks = max(1, int(self.MIN_DATA_SECS * self._sample_rate / block_frames))

        with self._lock:
            if len(self._virt_blocks) < min_blocks or len(self._mic_blocks) < min_blocks:
                return None
            virt = np.concatenate(list(self._virt_blocks))
            mic = np.concatenate(list(self._mic_blocks))

        # Downmix to mono
        if virt.ndim == 2 and virt.shape[1] > 1:
            virt_mono = virt.mean(axis=1)
        else:
            virt_mono = virt.ravel()
        mic_mono = mic.ravel()

        # Use the most recent N seconds from each for correlation.
        # With mode='full' this can detect lags up to CORR_WINDOW_SECS.
        corr_samples = int(self.CORR_WINDOW_SECS * self._sample_rate)
        if len(virt_mono) > corr_samples:
            virt_mono = virt_mono[-corr_samples:]
        if len(mic_mono) > corr_samples:
            mic_mono = mic_mono[-corr_samples:]

        # Remove DC, normalise to unit energy (mic level ≠ line level)
        virt_mono = virt_mono - virt_mono.mean()
        mic_mono = mic_mono - mic_mono.mean()
        v_std = virt_mono.std()
        m_std = mic_mono.std()
        if v_std < 1e-9 or m_std < 1e-9:
            return None

        virt_mono /= v_std
        mic_mono /= m_std

        # Cross-correlation
        corr = np.correlate(virt_mono, mic_mono, mode="full")
        peak_idx = int(np.argmax(np.abs(corr)))
        centre = len(corr) // 2
        offset_samples = peak_idx - centre
        latency_ms = (offset_samples / self._sample_rate) * 1000.0
        return latency_ms

    @property
    def is_running(self) -> bool:
        return self._running
