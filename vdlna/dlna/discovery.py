"""SSDP-based DLNA device discovery using async_upnp_client."""

import asyncio
import logging
import socket
import sys

import aiohttp
from async_upnp_client.search import SsdpSearchListener
from xml.etree import ElementTree as ET

logger = logging.getLogger("vdlna.discovery")


def _get_local_interfaces() -> list[str]:
    """Return list of non-loopback IPv4 addresses on this machine."""
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        # Get all IPs for this hostname
        info = socket.getaddrinfo(hostname, None, socket.AF_INET,
                                  socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        for item in info:
            ip = item[4][0]
            if ip != "127.0.0.1" and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    # Also try connecting to a public IP to discover the preferred interface
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip not in ips:
            ips.insert(0, ip)
    except Exception:
        pass

    return ips


async def _fetch_device_identity(location: str, log_cb=None) -> tuple[str | None, str | None]:
    """Fetch UPnP device description XML and extract friendlyName/manufacturer."""
    if not location:
        return None, None

    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(location) as resp:
                if resp.status != 200:
                    return None, None
                xml_text = await resp.text()
    except Exception as e:
        if log_cb:
            log_cb(f"device description fetch failed for {location}: {e}")
        return None, None

    try:
        root = ET.fromstring(xml_text)
        friendly_name = None
        manufacturer = None

        # UPnP device description typically uses this namespace
        ns = {"u": "urn:schemas-upnp-org:device-1-0"}
        device = root.find("u:device", ns)
        if device is None:
            device = root.find("device")
        if device is None:
            return None, None

        fn = device.find("u:friendlyName", ns)
        if fn is None:
            fn = device.find("friendlyName")
        mf = device.find("u:manufacturer", ns)
        if mf is None:
            mf = device.find("manufacturer")
        if fn is not None and fn.text:
            friendly_name = fn.text.strip()
        if mf is not None and mf.text:
            manufacturer = mf.text.strip()
        return friendly_name, manufacturer
    except Exception as e:
        if log_cb:
            log_cb(f"device description parse failed for {location}: {e}")
        return None, None


async def scan_dlna_renderers(
    timeout: float = 3.0,
    log_cb=None,
    device_cb=None,
) -> list[dict]:
    """Scan LAN for DLNA media renderers via SSDP and return device info list.

    Each dict contains: udn, friendly_name, manufacturer, location, host.

    Args:
        timeout: Seconds to wait for SSDP responses.
        log_cb: Optional callback(str) for per-event logging.
        device_cb: Optional callback(dict) called immediately when a renderer
                   is discovered, before the scan completes.
    """
    renderers: list[dict] = []
    all_responses: list[dict] = []
    seen_udns: set[str] = set()

    def _log(msg: str) -> None:
        logger.debug(msg)
        if log_cb:
            log_cb(msg)

    async def _on_response(headers: dict) -> None:
        # Extract key fields (headers is CaseInsensitiveDict from async_upnp_client)
        usn = headers.get("USN", headers.get("usn", ""))
        location = headers.get("LOCATION", headers.get("location", ""))
        server = headers.get("SERVER", headers.get("server", ""))
        st = headers.get("ST", headers.get("st", ""))
        nt = headers.get("NT", headers.get("nt", ""))
        remote = headers.get("_remote_addr", headers.get("_REMOTE_ADDR", ""))

        _log(f"SSDP response from {remote}: USN={usn}, ST={st}, "
             f"SERVER={server[:60] if server else 'none'}")

        all_responses.append({
            "usn": usn, "location": location, "server": server,
            "st": st, "remote": str(remote),
        })

        # Only include MediaRenderer devices
        if "MediaRenderer" not in usn and "MediaRenderer" not in location:
            _log(f"  -> skipped (not a MediaRenderer)")
            return

        # Avoid duplicates by UDN — claim the slot before any await
        udn = usn.split("::")[0] if "::" in usn else usn
        if udn in seen_udns:
            _log(f"  -> skipped (duplicate UDN: {udn})")
            return
        seen_udns.add(udn)

        host = ""
        device_id = ""
        if location and "://" in location:
            parts = location.split("/")
            host = parts[2]
            # /device/{device_id}/device.xml → extract device_id
            if len(parts) >= 5 and parts[3] == "device":
                device_id = parts[4]

        friendly_name, manufacturer = await _fetch_device_identity(location, _log)
        display_name = friendly_name or server or usn

        _log(f"  -> ACCEPTED: udn={udn}, host={host}, name={display_name}")

        device = {
            "udn": udn,
            "friendly_name": display_name,
            "manufacturer": manufacturer or "",
            "location": location,
            "host": host,
            "server": server,
            "device_id": device_id,
        }
        renderers.append(device)

        if device_cb:
            try:
                device_cb(device)
            except Exception:
                pass

    # Try each local interface
    interfaces = _get_local_interfaces()
    _log(f"Local interfaces: {interfaces}")

    for src_ip in interfaces:
        if renderers:
            break
        _log(f"Trying source IP: {src_ip}")
        await _scan_on_interface(src_ip, timeout, _on_response, _log)

    if not all_responses:
        _log("NO SSDP responses received at all. Check:")
        _log("  1. Windows Firewall may be blocking UDP 1900")
        _log("     netsh advfirewall firewall add rule name=\"SSDP\" ")
        _log("       dir=in protocol=UDP localport=1900 action=allow")
        _log("  2. Try: python main.py dlna diag")
    elif not renderers:
        _log(f"Received {len(all_responses)} SSDP response(s) but none were "
             f"MediaRenderer.")
        _log("All responses:")
        for r in all_responses:
            _log(f"  USN={r['usn']}, ST={r['st']}")

    return renderers


async def _scan_on_interface(
    src_ip: str, timeout: float, response_cb, log_cb,
) -> None:
    """Run SSDP scan on a specific source IP."""

    async def _on_connect() -> None:
        log_cb(f"Listener connected on {src_ip}, sending M-SEARCH...")
        listener.async_search()

    listener = SsdpSearchListener(
        async_callback=response_cb,
        timeout=int(timeout),
        search_target="urn:schemas-upnp-org:device:MediaRenderer:1",
        async_connect_callback=_on_connect,
        source=(src_ip, 0),
    )

    try:
        await listener.async_start()
        log_cb(f"Listening on {src_ip} for {timeout+1}s...")
        await asyncio.sleep(timeout + 1)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log_cb(f"SSDP error on {src_ip}: {e}")

    listener.async_stop()


async def scan_all_upnp(timeout: float = 5.0, log_cb=None) -> list[dict]:
    """Broader scan: search for ALL UPnP devices (ssdp:all), return raw info.

    Useful for debugging — shows everything on the network, not just renderers.
    """
    devices: list[dict] = []

    def _log(msg: str) -> None:
        logger.debug(msg)
        if log_cb:
            log_cb(msg)

    async def _on_response(headers: dict) -> None:
        usn = headers.get("USN", headers.get("usn", ""))
        location = headers.get("LOCATION", headers.get("location", ""))
        server = headers.get("SERVER", headers.get("server", ""))
        st = headers.get("ST", headers.get("st", ""))
        nt = headers.get("NT", headers.get("nt", ""))
        remote = headers.get("_remote_addr", "")

        _log(f"UPnP device: {server[:80] if server else usn[:80]}")
        _log(f"  USN={usn}, ST={st}, NT={nt}, location={location[:80]}")

        devices.append({
            "usn": usn, "location": location, "server": server,
            "st": st, "nt": nt, "remote": str(remote),
        })

    # Try each local interface
    interfaces = _get_local_interfaces()
    _log(f"Starting broad SSDP scan on interfaces: {interfaces}")

    for src_ip in interfaces:
        if devices:
            break
        _log(f"Trying source IP: {src_ip}")

        async def _on_connect() -> None:
            _log("SSDP listener connected (ssdp:all), sending M-SEARCH...")
            listener_.async_search()

        listener_ = SsdpSearchListener(
            async_callback=_on_response,
            timeout=int(timeout),
            search_target="ssdp:all",
            async_connect_callback=_on_connect,
            source=(src_ip, 0),
        )

        try:
            await listener_.async_start()
            _log(f"Listening on {src_ip} for {timeout+1}s...")
            await asyncio.sleep(timeout + 1)
        except Exception as e:
            _log(f"SSDP error on {src_ip}: {e}")
        listener_.async_stop()

        if devices:
            _log(f"Found {len(devices)} UPnP device(s) on interface {src_ip}")
            break

    if not devices:
        _log("NO UPnP devices found. Check:")
        _log("  1. Are you connected to a network?")
        _log("  2. Is Windows Firewall blocking UDP 1900?")
        _log("  3. Is the SSDP Discovery service running? "
             "(services.msc -> SSDP Discovery)")

    return devices
