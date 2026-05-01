"""Virtual audio device detection for vDLNA.

Detects installed virtual audio devices (VB-CABLE, Virtual-Audio-Driver, etc.).
Does NOT manage driver installation — users install drivers themselves.
"""

import re
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
    def list_virtual_devices() -> list[dict]:
        """List only virtual audio input devices (filtered by keyword, deduplicated by name).

        Windows exposes each device once per host API (MME / DirectSound / WASAPI).
        We keep only one entry per name, preferring WASAPI for lowest latency.
        """
        wasapi_idx = next(
            (i for i, api in enumerate(sd.query_hostapis())
             if "wasapi" in api["name"].lower()),
            None,
        )

        seen: dict[str, dict] = {}  # normalised_name -> best entry
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] <= 0:
                continue

            name: str = dev["name"].strip()
            name_lower = name.lower()

            entry = {
                "index": idx,
                "name": name,
                "channels": dev["max_input_channels"],
                "sample_rate": int(dev["default_samplerate"]),
                "hostapi": dev["hostapi"],
            }
            key = f"{name_lower.split('(')[0].strip()} {dev['max_input_channels']}"
            if key not in seen or dev["hostapi"] == wasapi_idx:
                seen[key] = entry

        return sorted(
            (
                {k: v for k, v in e.items() if k != "hostapi"}
                for e in seen.values()
                if "()" not in e["name"]          # 过滤空括号设备
            ),
            key=lambda d: d["name"].lower(),      # 按名称排序
        )
        # result = []
        # for entry in seen.values():
        #     try:
        #         sd.check_input_settings(
        #             device=entry["index"],
        #             channels=min(entry["channels"], 2),
        #             samplerate=entry["sample_rate"],
        #         )
        #         result.append({k: v for k, v in entry.items() if k != "hostapi"})
        #     except Exception:
        #         pass  # 设备不可用，跳过
        # return result

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
