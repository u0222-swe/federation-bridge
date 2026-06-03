# SPDX-License-Identifier: AGPL-3.0-or-later
"""TCP client sending raw CoT XML to a configured destination."""

import asyncio
import logging

logger = logging.getLogger("federation-bridge.tcp-client")


class CotTcpClient:
    """Sends CoT XML to a configured destination via TCP."""

    def __init__(self, host: str, port: int, bridge_name: str = "bridge"):
        self.host = host
        self.port = port
        self.bridge_name = bridge_name
        self._writer = None

    async def start(self, message_queue: asyncio.Queue):
        """Connect and send queued CoT XML messages."""
        backoff = 1
        while True:
            try:
                reader, self._writer = await asyncio.open_connection(self.host, self.port)
                logger.info(f"[{self.bridge_name}] TCP client connected to {self.host}:{self.port}")
                backoff = 1

                while True:
                    try:
                        message = await asyncio.wait_for(message_queue.get(), timeout=10)
                        self._writer.write(message.encode("utf-8") if isinstance(message, str) else message)
                        await self._writer.drain()
                    except asyncio.TimeoutError:
                        continue

            except (ConnectionRefusedError, OSError) as e:
                logger.warning(
                    f"[{self.bridge_name}] TCP client cannot connect to "
                    f"{self.host}:{self.port}: {e}. Retrying in {backoff}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except asyncio.CancelledError:
                break

    async def stop(self):
        if self._writer:
            self._writer.close()
            logger.info(f"[{self.bridge_name}] TCP client disconnected")
