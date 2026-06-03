# SPDX-License-Identifier: AGPL-3.0-or-later
"""TCP server accepting raw CoT XML connections."""

import asyncio
import logging

logger = logging.getLogger("federation-bridge.tcp-server")

COT_END_TAG = b"</event>"


class CotTcpServer:
    """Accepts TCP connections delivering raw CoT XML."""

    def __init__(self, port: int, bridge_name: str = "bridge"):
        self.port = port
        self.bridge_name = bridge_name
        self._server = None

    async def start(self, message_queue: asyncio.Queue):
        """Start TCP server and put received CoT XML strings on the queue."""

        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            addr = writer.get_extra_info("peername")
            logger.info(f"[{self.bridge_name}] TCP client connected: {addr}")
            buf = b""
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    buf += data
                    while COT_END_TAG in buf:
                        idx = buf.index(COT_END_TAG) + len(COT_END_TAG)
                        message = buf[:idx]
                        buf = buf[idx:]
                        try:
                            await message_queue.put(message.decode("utf-8", errors="replace"))
                        except Exception as e:
                            logger.error(f"[{self.bridge_name}] Error queueing message: {e}")
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            finally:
                writer.close()
                logger.info(f"[{self.bridge_name}] TCP client disconnected: {addr}")

        self._server = await asyncio.start_server(handle_client, "0.0.0.0", self.port)
        logger.info(f"[{self.bridge_name}] TCP server listening on port {self.port}")
        await self._server.serve_forever()

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info(f"[{self.bridge_name}] TCP server stopped")
