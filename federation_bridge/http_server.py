# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP(S) server accepting CoT XML via POST, optionally with mTLS.

Mirrors the post2udp.rb sidecar: receives CoT events POSTed by a peer bridge or
a CDS/guard and puts them on the bridge's inbound CoT queue. Each request body
may contain one or more </event>-delimited events (matching tcp_server.py /
udp_server.py). Only POST is accepted; other methods get 405. Because HTTP is
connection-oriented the handler applies natural backpressure — it awaits a free
slot on the bounded inbound queue rather than dropping like the lossy UDP path.

TLS is optional: supply a server cert/key for one-way TLS, and additionally a CA
cert to require (and verify) a client certificate — i.e. mutual TLS."""

import asyncio
import logging
import os
import ssl

from aiohttp import web

logger = logging.getLogger("federation-bridge.http-server")

COT_END_TAG = "</event>"
MAX_BODY_SIZE = 1_000_000  # matches post2udp.rb MAX_BODY_SIZE


class CotHttpServer:
    """Listens for CoT XML POSTed over HTTP(S) on a dedicated port."""

    def __init__(self, port: int, server_cert: str = "", server_key: str = "",
                 ca_cert: str = "", bridge_name: str = "bridge"):
        self.port = port
        self.server_cert = server_cert
        self.server_key = server_key
        self.ca_cert = ca_cert
        self.bridge_name = bridge_name
        self._queue: asyncio.Queue | None = None
        self._runner: web.AppRunner | None = None

    def _build_ssl(self):
        if not (self.server_cert and self.server_key):
            return None
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(self.server_cert, self.server_key)
        if self.ca_cert and os.path.exists(self.ca_cert):
            ctx.load_verify_locations(self.ca_cert)
            ctx.verify_mode = ssl.CERT_REQUIRED  # require client cert -> mTLS
            logger.info(f"[{self.bridge_name}] HTTP server requiring client cert (mTLS)")
        return ctx

    async def start(self, message_queue: asyncio.Queue):
        """Bind the HTTP server and feed received CoT onto the inbound queue."""
        self._queue = message_queue
        app = web.Application(client_max_size=MAX_BODY_SIZE)
        app.router.add_post("/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        try:
            ssl_ctx = self._build_ssl()
        except (OSError, ssl.SSLError) as e:
            # e.g. a missing/unreadable server cert or key. Log clearly instead
            # of letting the task die silently with no listener bound.
            logger.error(
                f"[{self.bridge_name}] HTTP server TLS setup failed ({e}); "
                f"not listening on port {self.port}"
            )
            await self._runner.cleanup()
            self._runner = None
            return
        site = web.TCPSite(self._runner, "0.0.0.0", self.port, ssl_context=ssl_ctx)
        await site.start()
        scheme = "https" if ssl_ctx else "http"
        logger.info(
            f"[{self.bridge_name}] HTTP server listening for CoT POST on "
            f"port {self.port} ({scheme})"
        )
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await self.stop()
            raise

    async def _handle(self, request: web.Request) -> web.Response:
        raw = await request.read()
        body = raw.decode("utf-8", errors="replace")
        count = 0
        if COT_END_TAG not in body:
            await self._queue.put(body)
            count = 1
        else:
            buf = body
            while COT_END_TAG in buf:
                idx = buf.index(COT_END_TAG) + len(COT_END_TAG)
                await self._queue.put(buf[:idx])
                buf = buf[idx:]
                count += 1
        logger.debug(f"[{self.bridge_name}] HTTP server received {count} event(s)")
        return web.Response(text="OK\n")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            logger.info(f"[{self.bridge_name}] HTTP server stopped")
