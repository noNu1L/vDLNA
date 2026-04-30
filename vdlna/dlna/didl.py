"""DIDL-Lite XML builder for DLNA SetAVTransportURI payloads."""

from xml.etree.ElementTree import Element, SubElement, tostring


def build_didl_audio_item(title: str, stream_url: str, mime_type: str = "audio/flac") -> str:
    """Build a DIDL-Lite XML string describing an audio stream item.

    Returns a UTF-8 XML string suitable for SetAVTransportURI SOAP action.
    """
    didl = Element("DIDL-Lite",
                   xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
                   **{"xmlns:dc": "http://purl.org/dc/elements/1.1/",
                      "xmlns:upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/"})

    item = SubElement(didl, "item", id="0", parentID="-1", restricted="1")

    dc_title = SubElement(item, "dc:title")
    dc_title.text = title

    upnp_class = SubElement(item, "upnp:class")
    upnp_class.text = "object.item.audioItem.musicTrack"

    res = SubElement(item, "res", protocolInfo=f"http-get:*:{mime_type}:*")
    res.text = stream_url

    return tostring(didl, encoding="unicode", xml_declaration=True)
