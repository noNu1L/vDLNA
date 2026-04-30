"""Virtual audio device detection for vDLNA.

Detects installed virtual audio devices (VB-CABLE, Virtual-Audio-Driver, etc.).
Does NOT manage driver installation — users install drivers themselves.
"""

import webbrowser

import sounddevice as sd

VB_CABLE_URL = "https://vb-audio.com/Cable/index.htm"


class VirtualAudioDevice:
    """Detect and query virtual audio devices."""

    @staticmethod
    def find_device_index() -> int | None:
        """Return the sounddevice device index for a virtual audio input, or None."""
        devices = sd.query_devices()
        for idx, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                name_lower = dev["name"].lower()
                if any(kw in name_lower for kw in (
                    "vb-audio", "cable", "vbaudio",
                    "virtual", "vad", "streaming add-on",
                )):
                    return idx
        return None

    @staticmethod
    def is_installed() -> bool:
        """Check whether any supported virtual audio driver is present."""
        return VirtualAudioDevice.find_device_index() is not None

    @staticmethod
    def open_download_page() -> None:
        """Open the VB-CABLE download page in the default browser."""
        webbrowser.open(VB_CABLE_URL)

    @staticmethod
    def list_devices() -> list[dict]:
        """List all input-capable audio devices."""
        result = []
        devices = sd.query_devices()
        for idx, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                result.append({
                    "index": idx,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "sample_rate": int(dev["default_samplerate"]),
                })
        return result
