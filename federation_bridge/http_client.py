# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP(S) client that POSTs CoT XML to a remote endpoint, optionally with mTLS.

Mirrors the udp2post.rb sidecar: drains the bridge's outbound CoT queue and
POSTs each event to a configured URL over a persistent aiohttp session, with a
single retry on transient connection errors. Unlike the UDP output this is a
reliable, connection-oriented path — suitable for a CDS/guard that ingests CoT
over HTTPS with mutual TLS.

mTLS is enabled by supplying a client cert/key; a CA cert (when present) is used
to verify the server. With an https:// URL and no CA the client warns and
disables peer verification (matching grpc_client.py's behaviour)."""

import asyncio
import logging
import os
import ssl

import aiohttp

logger = logging.getLogger("federation-bridge.http-client")

_HEADERS = {"Content-Type": "application/xml", "Accept": "application/xml"}


class CotHttpClient:
    """POSTs CoT XML messages to a configured URL, optionally with mTLS."""

    def __init__(self, url: str, client_cert: str = "", client_key: str = "",
                 ca_cert: str = "", bridge_name: str = "bridge"):
        self.url = url
        self.client_cert = client_cert
        self.client_key = client_key
        self.ca_cert = ca_cert
        self.bridge_name = bridge_name
        self._session: aiohttp.ClientSession | None = None
        self._ssl = None  # ssl.SSLContext, or None for plain http

    def _build_ssl(self):
        if not self.url.lower().startswith("https"):
            return None
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        if self.client_cert and self.client_key:
            ctx.load_cert_chain(self.client_cert, self.client_key)
            logger.info(f"[{self.bridge_name}] HTTP client using mTLS cert {self.client_cert}")
        if self.ca_cert and os.path.exists(self.ca_cert):
            ctx.load_verify_locations(self.ca_cert)
        else:
            if self.ca_cert:
                logger.warning(
                    f"[{self.bridge_name}] CA cert not found ({self.ca_cert}); "
                    f"disabling server verification"
                )
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def start(self, message_queue: asyncio.Queue):
        """Drain the queue and POST each CoT message to the remote URL."""
        try:
            self._ssl = self._build_ssl()
        except (OSError, ssl.SSLError) as e:
            # e.g. a missing/unreadable client cert or key. Without this guard
            # the task would die silently and the HTTP output would simply stop.
            logger.error(
                f"[{self.bridge_name}] HTTP client TLS setup failed ({e}); "
                f"output to {self.url} disabled"
            )
            return
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self._session = aiohttp.ClientSession(timeout=timeout)
        logger.info(f"[{self.bridge_name}] HTTP client posting CoT to {self.url}")
        try:
            while True:
                try:
                    message = await asyncio.wait_for(message_queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    continue
                await self._post_with_retry(message)
        except asyncio.CancelledError:
            await self.stop()
            raise

    async def _post_with_retry(self, message):
        payload = message.encode("utf-8") if isinstance(message, str) else message
        for attempt in (1, 2):
            try:
                async with self._session.post(
                    self.url, data=payload, headers=_HEADERS, ssl=self._ssl
                ) as resp:
                    if resp.status >= 400:
                        body = (await resp.text())[:200]
                        logger.warning(
                            f"[{self.bridge_name}] HTTP POST failed: "
                            f"{resp.status} {resp.reason} {body}"
                        )
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == 1:
                    logger.warning(
                        f"[{self.bridge_name}] HTTP POST error ({e}); retrying once"
                    )
                    continue
                logger.warning(
                    f"[{self.bridge_name}] HTTP POST dropped after retry: {e}"
                )

    async def stop(self):
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info(f"[{self.bridge_name}] HTTP client stopped")
