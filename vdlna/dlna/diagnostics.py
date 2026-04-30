"""Network diagnostics for SSDP/DLNA discovery issues."""

import socket
import subprocess
import sys


def run_diagnostics() -> list[str]:
    """Run network diagnostics and return a list of diagnostic messages."""
    lines: list[str] = []

    lines.append("=== vDLNA Network Diagnostics ===")

    # 1. Check SSDP Discovery Windows service
    if sys.platform == "win32":
        lines.append("")
        lines.append("--- Windows SSDP Discovery Service ---")
        try:
            result = subprocess.run(
                ["sc", "query", "SSDPSRV"],
                capture_output=True, text=True, timeout=5,
            )
            lines.append(result.stdout.strip())
            if "RUNNING" not in result.stdout:
                lines.append("[FIX] SSDP Discovery service is NOT running.")
                lines.append("      Run: sc start SSDPSRV")
                lines.append("      Or:  services.msc -> SSDP Discovery -> Start")
        except Exception as e:
            lines.append(f"Could not query SSDP service: {e}")

        lines.append("")
        lines.append("--- UPnP Device Host Service ---")
        try:
            result = subprocess.run(
                ["sc", "query", "upnphost"],
                capture_output=True, text=True, timeout=5,
            )
            lines.append(result.stdout.strip())
            if "RUNNING" not in result.stdout:
                lines.append("[FIX] UPnP Device Host service is NOT running.")
                lines.append("      Run: sc start upnphost")
        except Exception as e:
            lines.append(f"Could not query UPnP service: {e}")

    # 2. Check network interfaces
    lines.append("")
    lines.append("--- Network Interfaces ---")
    hostname = socket.gethostname()
    lines.append(f"Hostname: {hostname}")
    try:
        ip = socket.gethostbyname(hostname)
        lines.append(f"Primary IP: {ip}")
    except Exception:
        lines.append("Primary IP: (could not resolve)")

    try:
        # Get all interfaces
        import array
        import ctypes
        import ctypes.wintypes

        AF_INET = 2
        ERROR_BUFFER_OVERFLOW = 111

        # GetAdaptersInfo approach
        result = subprocess.run(
            ["ipconfig"], capture_output=True, text=True, timeout=5,
        )
        # Filter just the IPv4 lines
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if "IPv4" in stripped or "IP Address" in stripped or "Subnet Mask" in stripped:
                lines.append(f"  {stripped}")
    except Exception:
        pass

    # 3. Test raw M-SEARCH
    lines.append("")
    lines.append("--- Raw M-SEARCH Test ---")
    msearch = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 3\r\n"
        "ST: ssdp:all\r\n"
        "\r\n"
    )

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(3)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Try binding to specific interface
        sock.bind(("0.0.0.0", 0))
        local_port = sock.getsockname()[1]
        lines.append(f"Bound to local port {local_port}")

        sock.sendto(msearch.encode(), ("239.255.255.250", 1900))
        lines.append("M-SEARCH sent to 239.255.255.250:1900")

        responses = []
        try:
            while True:
                data, addr = sock.recvfrom(8192)
                text = data.decode(errors="replace")
                # Sanitize: remove \r\n for display
                first_line = text.split("\r\n")[0] if text else ""
                responses.append(f"From {addr}: {first_line[:120]}")
        except socket.timeout:
            pass

        sock.close()

        if responses:
            lines.append(f"Received {len(responses)} response(s):")
            for r in responses:
                lines.append(f"  {r}")
        else:
            lines.append("No responses received in 3 seconds.")
            lines.append("[POSSIBLE CAUSE] Windows Firewall is blocking SSDP traffic.")
            lines.append("")
            lines.append("To fix, add a firewall rule:")
            lines.append('  netsh advfirewall firewall add rule name="SSDP" ')
            lines.append('    dir=in protocol=UDP localport=1900 action=allow')
            lines.append("Or temporarily disable firewall for testing.")

    except PermissionError:
        lines.append("[ERROR] Permission denied binding UDP socket.")
    except Exception as e:
        lines.append(f"[ERROR] Raw M-SEARCH failed: {e}")

    lines.append("")
    lines.append("=== Diagnostics Complete ===")
    return lines


def run_raw_msearch(timeout: float = 3.0, log_cb=None) -> list[dict]:
    """Run a raw Python-socket M-SEARCH for maximum visibility.

    Returns list of dicts with raw response data.
    """
    def _log(msg: str) -> None:
        if log_cb:
            log_cb(msg)

    msearch = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {int(timeout)}\r\n"
        "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
        "\r\n"
    )

    results: list[dict] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 0))
    _log(f"Raw M-SEARCH bound to 0.0.0.0:{sock.getsockname()[1]}")

    sock.sendto(msearch.encode(), ("239.255.255.250", 1900))
    _log(f"Sent M-SEARCH to 239.255.255.250:1900")

    try:
        while True:
            data, addr = sock.recvfrom(8192)
            text = data.decode(errors="replace")
            # Parse response headers
            headers = {}
            lines_list = text.split("\r\n")
            if lines_list:
                first = lines_list[0]
                headers["_response_line"] = first
                for line in lines_list[1:]:
                    if ":" in line:
                        key, _, val = line.partition(":")
                        headers[key.strip().upper()] = val.strip()
            headers["_remote_addr"] = f"{addr[0]}:{addr[1]}"
            results.append(headers)
            _log(f"Response from {addr}: {headers.get('SERVER', headers.get('USN', first))}")
    except socket.timeout:
        pass
    finally:
        sock.close()

    _log(f"Raw M-SEARCH complete: {len(results)} response(s)")
    return results
