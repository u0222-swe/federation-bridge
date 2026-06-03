# SPDX-License-Identifier: AGPL-3.0-or-later
"""UDP server accepting raw CoT XML datagrams.

Each datagram is treated as one or more complete CoT events, delimited
by </event> (matching tcp_server.py behavior). UDP is commonly used for
multicast/broadcast CoT distribution by sensor systems.
"""

import asyncio
import logging

logger = logging.getLogger("federation-bridge.udp-server")

COT_END_TAG = b"</event>"


class CotUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, message_queue: asyncio.Queue, bridge_name: str):
        self.message_queue = message_queue
        self.bridge_name = bridge_name
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        # A datagram may contain one or more </event>-delimited events.
        # If no terminator is present, treat the whole datagram as one event.
        try:
            if COT_END_TAG not in data:
                self.message_queue.put_nowait(data.decode("utf-8", errors="replace"))
                return
            buf = data
            while COT_END_TAG in buf:
                idx = buf.index(COT_END_TAG) + len(COT_END_TAG)
                message = buf[:idx]
                buf = buf[idx:]
                self.message_queue.put_nowait(message.decode("utf-8", errors="replace"))
        except asyncio.QueueFull:
            logger.warning(f"[{self.bridge_name}] UDP queue full, dropping datagram from {addr}")
        except Exception as e:
            logger.error(f"[{self.bridge_name}] UDP datagram error from {addr}: {e}")

    def error_received(self, exc):
        logger.warning(f"[{self.bridge_name}] UDP error: {exc}")


class CotUdpServer:
    """Listens for raw CoT XML on a UDP port."""

    def __init__(self, port: int, bridge_name: str = "bridge"):
        self.port = port
        self.bridge_name = bridge_name
        self._transport = None

    async def start(self, message_queue: asyncio.Queue):
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: CotUdpProtocol(message_queue, self.bridge_name),
            local_addr=("0.0.0.0", self.port),
        )
        logger.info(f"[{self.bridge_name}] UDP server listening on port {self.port}")
        try:
            # Keep coroutine alive while transport is open
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await self.stop()
            raise

    async def stop(self):
        if self._transport:
            self._transport.close()
            self._transport = None
            logger.info(f"[{self.bridge_name}] UDP server stopped")
