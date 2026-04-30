"""Audio capture from virtual sound card via sounddevice."""

import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd


class AudioCapture:
    """Captures PCM audio from a given sounddevice input device.

    Runs a sounddevice InputStream in a dedicated callback thread.
    """

    def __init__(self, sample_rate: int = 44100, channels: int = 2,
                 device_index: int | None = None):
        self._sample_rate = sample_rate
        self._channels = channels
        self._device_index = device_index
        self._stream: sd.InputStream | None = None
        self._callback: Callable[[np.ndarray], None] | None = None
        self._running = False

    def set_pcm_callback(self, cb: Callable[[np.ndarray], None]) -> None:
        """Set callback that receives float64 numpy array (frames, channels)."""
        self._callback = cb

    def start(self) -> None:
        if self._running:
            return

        def _audio_callback(indata: np.ndarray, frames: int,
                            time_info, status: sd.CallbackFlags) -> None:
            if status:
                pass  # xrun or underflow — skip noisy frame
            if self._callback is not None:
                self._callback(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            device=self._device_index,
            dtype=np.float32,
            callback=_audio_callback,
        )
        self._stream.start()
        self._running = True

    def stop(self) -> None:
        if not self._running or self._stream is None:
            return
        self._stream.stop()
        self._stream.close()
        self._stream = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def latency(self) -> float:
        if self._stream is not None:
            return self._stream.latency
        return 0.0
