"""aiohttp HTTP server serving a FLAC stream at /stream.flac."""

import asyncio
import socket

from aiohttp import web

from vdlna.audio.encoder import FlacBroadcastEncoder


class StreamServer:
    """HTTP server that exposes the FLAC broadcast stream.

    Each GET /stream.flac registers a new client queue with the encoder
    and streams FLAC frames via chunked transfer encoding.
    """

    def __init__(self, encoder: FlacBroadcastEncoder, port: int = 9876):
        self._encoder = encoder
        self._port = port
        self._app = web.Application()
        self._app.router.add_get("/stream.flac", self._handle_stream)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await self._site.start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    @property
    def url(self) -> str:
        host = self._get_local_ip()
        return f"http://{host}:{self._port}/stream.flac"

    @property
    def port(self) -> int:
        return self._port

    @staticmethod
    def _get_local_ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        q = self._encoder.add_client()
        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "audio/flac",
                "Connection": "close",
                "Cache-Control": "no-cache",
            },
        )
        await resp.prepare(request)

        try:
            while True:
                data = await q.get()
                await resp.write(data)
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self._encoder.remove_client(q)
        return resp
