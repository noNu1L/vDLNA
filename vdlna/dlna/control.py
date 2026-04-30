"""DLNA MediaRenderer control via SOAP/AVTransport service."""

import re
from xml.etree import ElementTree as ET

import aiohttp

AV_TRANSPORT_URN = "urn:schemas-upnp-org:service:AVTransport:1"
RC_URN = "urn:schemas-upnp-org:service:RenderingControl:1"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"


class DlnaRenderer:
    """Controls a DLNA MediaRenderer: set stream URI, play, stop, volume."""

    def __init__(self, location: str):
        self._location = location
        self._av_url: str | None = None
        self._rc_url: str | None = None
        self._session: aiohttp.ClientSession | None = None

    async def connect(self, stream_url: str) -> None:
        """Fetch device description, resolve AVTransport + RenderingControl URLs."""
        self._session = aiohttp.ClientSession()

        async with self._session.get(self._location) as resp:
            desc_xml = await resp.text()

        self._av_url = _extract_control_url(desc_xml, AV_TRANSPORT_URN)
        if not self._av_url:
            raise RuntimeError("Device does not expose AVTransport controlURL")

        base = "/".join(self._location.split("/")[:3])
        if self._av_url.startswith("/"):
            self._av_url = base + self._av_url

        self._rc_url = _extract_control_url(desc_xml, RC_URN)
        if self._rc_url and self._rc_url.startswith("/"):
            self._rc_url = base + self._rc_url

        await self._soap(SET_URI_ENVELOPE, stream_url)

    async def play(self) -> None:
        await self._soap(PLAY_ENVELOPE)

    async def stop(self) -> None:
        await self._soap(STOP_ENVELOPE)

    async def set_volume(self, volume: int) -> None:
        """Set renderer volume (0-100)."""
        if self._rc_url is None:
            return
        await self._soap_rc(SET_VOLUME_ENVELOPE, volume)

    async def get_volume(self) -> int | None:
        """Get current renderer volume (0-100), or None if unavailable."""
        if self._rc_url is None:
            return None
        text = await self._soap_rc(GET_VOLUME_ENVELOPE)
        if text is None:
            return None
        v = _extract_current_volume(text)
        return int(v) if v is not None else None

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _soap(self, envelope_fn, *args) -> None:
        if self._session is None or self._av_url is None:
            raise RuntimeError("Not connected — call connect() first")

        body, action = envelope_fn(*args)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{AV_TRANSPORT_URN}#{action}"',
        }
        async with self._session.post(
            self._av_url, data=body.encode("utf-8"), headers=headers
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    f"SOAP {action} failed HTTP {resp.status}: {text[:300]}"
                )

    async def _soap_rc(self, envelope_fn, *args) -> str | None:
        if self._session is None or self._rc_url is None:
            return None

        body, action = envelope_fn(*args)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{RC_URN}#{action}"',
        }
        async with self._session.post(
            self._rc_url, data=body.encode("utf-8"), headers=headers
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    f"SOAP {action} failed HTTP {resp.status}: {text[:300]}"
                )
            return text


# ── SOAP envelope builders ────────────────────────────────────────────────────

def SET_URI_ENVELOPE(stream_url: str) -> tuple[str, str]:
    action = "SetAVTransportURI"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{AV_TRANSPORT_URN}">'
        "<InstanceID>0</InstanceID>"
        f"<CurrentURI>{_escape(stream_url)}</CurrentURI>"
        "<CurrentURIMetaData></CurrentURIMetaData>"
        f"</u:{action}>"
        "</s:Body>"
        "</s:Envelope>"
    )
    return xml, action


def PLAY_ENVELOPE() -> tuple[str, str]:
    action = "Play"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{AV_TRANSPORT_URN}">'
        "<InstanceID>0</InstanceID>"
        "<Speed>1</Speed>"
        f"</u:{action}>"
        "</s:Body>"
        "</s:Envelope>"
    )
    return xml, action


def STOP_ENVELOPE() -> tuple[str, str]:
    action = "Stop"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{AV_TRANSPORT_URN}">'
        "<InstanceID>0</InstanceID>"
        f"</u:{action}>"
        "</s:Body>"
        "</s:Envelope>"
    )
    return xml, action


def SET_VOLUME_ENVELOPE(volume: int) -> tuple[str, str]:
    action = "SetVolume"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{RC_URN}">'
        "<InstanceID>0</InstanceID>"
        "<Channel>Master</Channel>"
        f"<DesiredVolume>{volume}</DesiredVolume>"
        f"</u:{action}>"
        "</s:Body>"
        "</s:Envelope>"
    )
    return xml, action


def GET_VOLUME_ENVELOPE() -> tuple[str, str]:
    action = "GetVolume"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{RC_URN}">'
        "<InstanceID>0</InstanceID>"
        "<Channel>Master</Channel>"
        f"</u:{action}>"
        "</s:Body>"
        "</s:Envelope>"
    )
    return xml, action


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_control_url(desc_xml: str, service_urn: str) -> str | None:
    """Parse device description XML and return AVTransport controlURL."""
    try:
        root = ET.fromstring(desc_xml)
        ns_match = re.search(r'xmlns="([^"]+)"', desc_xml)
        ns = {"d": ns_match.group(1)} if ns_match else {}

        def find(el, tag):
            result = el.find(f"d:{tag}", ns) if ns else el.find(tag)
            if result is None:
                result = el.find(tag)
            return result

        def findall(el, tag):
            result = el.findall(f"d:{tag}", ns) if ns else el.findall(tag)
            if not result:
                result = el.findall(tag)
            return result

        device = find(root, "device")
        if device is None:
            return None

        service_list = find(device, "serviceList")
        if service_list is None:
            return None

        for svc in findall(service_list, "service"):
            stype = find(svc, "serviceType")
            if stype is not None and stype.text and stype.text.strip() == service_urn:
                ctrl = find(svc, "controlURL")
                if ctrl is not None and ctrl.text:
                    return ctrl.text.strip()
    except Exception:
        pass

    # Regex fallback for non-standard XML
    pattern = re.compile(
        r"<serviceType[^>]*>\s*" + re.escape(service_urn) +
        r"\s*</serviceType>.*?<controlURL[^>]*>(.*?)</controlURL>",
        re.DOTALL,
    )
    m = pattern.search(desc_xml)
    return m.group(1).strip() if m else None


def _extract_current_volume(xml_text: str) -> str | None:
    """Extract CurrentVolume value from GetVolume SOAP response."""
    v = re.search(r"<CurrentVolume>(\d+)</CurrentVolume>", xml_text)
    return v.group(1) if v else None


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))
