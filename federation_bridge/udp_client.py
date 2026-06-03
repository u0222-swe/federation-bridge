# SPDX-License-Identifier: AGPL-3.0-or-later
"""UDP client sending raw CoT XML datagrams to a configured destination."""

import asyncio
import logging

logger = logging.getLogger("federation-bridge.udp-client")


class CotUdpClient:
    """Sends CoT XML to a configured destination via UDP datagrams."""

    def __init__(self, host: str, port: int, bridge_name: str = "bridge"):
        self.host = host
        self.port = port
        self.bridge_name = bridge_name
        self._transport = None

    async def start(self, message_queue: asyncio.Queue):
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=(self.host, self.port),
        )
        logger.info(
            f"[{self.bridge_name}] UDP client sending to {self.host}:{self.port}"
        )
        try:
            while True:
                try:
                    message = await asyncio.wait_for(message_queue.get(), timeout=10)
                    payload = message.encode("utf-8") if isinstance(message, str) else message
                    self._transport.sendto(payload)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            await self.stop()
            raise

    async def stop(self):
        if self._transport:
            self._transport.close()
            self._transport = None
            logger.info(f"[{self.bridge_name}] UDP client stopped")
