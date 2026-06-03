# SPDX-License-Identifier: AGPL-3.0-or-later
"""gRPC federation client connecting to FedHub via JWT auth.

Implements the TAK Server federation client sequence as observed in
TakFigClient.java and HubFigClient.java:

1. Open serverFederateGroupsStream (subscribe to FedHub's groups)
2. Wait for SERVING status from FedHub
3. Open ServerEventStream (send events) and send empty FederatedEvent first
4. HealthCheck every 3 seconds
5. ClientEventStream (receive events from FedHub)
"""

import asyncio
import glob
import logging
import os
import subprocess
import uuid

import grpc

from .proto_gen import fig_pb2, fig_pb2_grpc

# FedHub CA certificate for TLS verification
FEDHUB_CA_CERT = os.getenv(
    "FEDHUB_CA_CERT",
    "/opt/tak/federation-hub/certs/files/ca.pem"
)
# TLS hostname override (cert CN/SAN may differ from connection address)
FEDHUB_TLS_NAME = os.getenv("FEDHUB_TLS_NAME", "")

logger = logging.getLogger("federation-bridge.grpc")


class FederationGrpcClient:
    """Connects to FedHub as a federated node via gRPC bidirectional streaming."""

    def __init__(self, address: str, port: int, jwt_token: str, bridge_name: str = "bridge"):
        self.address = address
        self.port = port
        self.jwt_token = jwt_token
        self.bridge_name = bridge_name
        self.bridge_uid = str(uuid.uuid4())
        self.server_id = str(uuid.uuid4()).replace("-", "")[:32]
        self._channel = None
        self._stub = None
        self._connected = False
        # Set when we receive SERVING status from serverFederateGroupsStream
        self._serving_event = asyncio.Event()
        # Set when ServerEventStream is ready to accept events
        self._send_ready = asyncio.Event()

    async def connect(self):
        """Establish gRPC channel with TLS + JWT auth metadata."""
        target = f"{self.address}:{self.port}"
        logger.info(f"[{self.bridge_name}] Connecting to FedHub at {target}")

        # FedHub CA cert for TLS verification
        root_cert = None
        if os.path.exists(FEDHUB_CA_CERT):
            with open(FEDHUB_CA_CERT, "rb") as f:
                root_cert = f.read()
            logger.info(f"[{self.bridge_name}] Using FedHub CA cert: {FEDHUB_CA_CERT}")
        credentials = grpc.ssl_channel_credentials(root_certificates=root_cert)

        options = [
            ("grpc.keepalive_time_ms", 10000),
            ("grpc.keepalive_timeout_ms", 5000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.max_pings_without_data", 0),
            ("grpc.http2.min_time_between_pings_ms", 10000),
            ("grpc.http2.min_ping_interval_without_data_ms", 5000),
        ]
        tls_name = FEDHUB_TLS_NAME or self._detect_tls_name()
        if tls_name:
            options.append(("grpc.ssl_target_name_override", tls_name))
            logger.info(f"[{self.bridge_name}] TLS name override: {tls_name}")

        self._channel = grpc.aio.secure_channel(target, credentials, options=options)
        self._stub = fig_pb2_grpc.FederatedChannelStub(self._channel)
        self._connected = True
        logger.info(f"[{self.bridge_name}] Connected to FedHub (TLS)")

    async def disconnect(self):
        self._connected = False
        self._serving_event.set()
        self._send_ready.set()
        if self._channel:
            await self._channel.close()
            logger.info(f"[{self.bridge_name}] Disconnected from FedHub")

    def _metadata(self):
        return (("authorization", f"Bearer {self.jwt_token}"),)

    def _identity(self):
        """Identity matches what TakFigClient.java sends."""
        return fig_pb2.Identity(
            name=self.bridge_name,
            serverId=self.server_id,
            type=fig_pb2.Identity.FEDERATION_TAK_CLIENT,
            uid=self.bridge_uid,
        )

    def _subscription(self):
        return fig_pb2.Subscription(filter="", identity=self._identity())

    async def receive_groups(self):
        """Subscribe to FedHub's federate groups stream.

        This is THE critical first step. When FedHub sends SERVING status,
        we know we're registered and can start sending events.

        Mirrors TakFigClient.java:serverFederateGroups()
        """
        backoff = 1
        while self._connected:
            try:
                stream = self._stub.ServerFederateGroupsStream(
                    self._subscription(), metadata=self._metadata(), timeout=None
                )
                logger.info(f"[{self.bridge_name}] Subscribed to FedHub groups stream")
                backoff = 1
                async for fed_groups in stream:
                    if (fed_groups.HasField("streamUpdate")
                            and fed_groups.streamUpdate.status == fig_pb2.ServerHealth.SERVING):
                        logger.info(
                            f"[{self.bridge_name}] FedHub groups stream is SERVING — "
                            f"ready to send events"
                        )
                        self._serving_event.set()
                    if fed_groups.federateGroups:
                        logger.debug(
                            f"[{self.bridge_name}] FedHub remote groups: "
                            f"{list(fed_groups.federateGroups)}"
                        )
            except grpc.aio.AioRpcError as e:
                if not self._connected:
                    break
                if e.code() == grpc.StatusCode.UNIMPLEMENTED:
                    # Server doesn't support this - skip and proceed to setup event sender
                    logger.info(
                        f"[{self.bridge_name}] Groups stream UNIMPLEMENTED, "
                        f"proceeding directly to event sender"
                    )
                    self._serving_event.set()
                    return
                logger.warning(
                    f"[{self.bridge_name}] Groups stream error: {e.code()}. "
                    f"Reconnecting in {backoff}s"
                )
                # Reset serving state - need re-handshake
                self._serving_event.clear()
                self._send_ready.clear()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except asyncio.CancelledError:
                break

    async def receive_events(self, outbound_queue: asyncio.Queue):
        """Open ClientEventStream and put received events on the queue.

        Mirrors TakFigClient.java:clientEventStream pattern.
        """
        backoff = 1
        received = 0
        while self._connected:
            try:
                stream = self._stub.ClientEventStream(
                    self._subscription(), metadata=self._metadata(), timeout=None
                )
                backoff = 1
                async for event in stream:
                    received += 1
                    if received <= 3 or received % 100 == 0:
                        if event.HasField("event"):
                            descr = f"uid={event.event.uid}"
                        elif event.HasField("contact"):
                            descr = (
                                f"contact uid={event.contact.uid} "
                                f"callsign={event.contact.callsign} "
                                f"op={event.contact.operation}"
                            )
                        else:
                            descr = "empty"
                        logger.info(
                            f"[{self.bridge_name}] Received event #{received} "
                            f"({descr}, groups={list(event.federateGroups)})"
                        )
                    await outbound_queue.put(event)
            except grpc.aio.AioRpcError as e:
                if not self._connected:
                    break
                logger.warning(
                    f"[{self.bridge_name}] ClientEventStream error: {e.code()} - {e.details()}. "
                    f"Reconnecting in {backoff}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except asyncio.CancelledError:
                break

    async def send_events(self, inbound_queue: asyncio.Queue):
        """Send events via ServerEventStream — but ONLY after groups stream is SERVING.

        Mirrors HubFigClient.java:setupEventStreamSender():
        1. Wait for SERVING status from groups stream
        2. Send empty FederatedEvent first (initial registration handshake)
        3. Then send queued events
        """
        # Wait until FedHub groups stream is SERVING
        logger.info(f"[{self.bridge_name}] Waiting for SERVING status before sending events")
        await self._serving_event.wait()
        if not self._connected:
            return
        logger.info(f"[{self.bridge_name}] SERVING received, opening ServerEventStream")

        sent_count = 0
        empty_sent = False

        async def event_generator():
            nonlocal sent_count, empty_sent
            # CRITICAL: send empty FederatedEvent first (matches HubFigClient.java line 640)
            if not empty_sent:
                yield fig_pb2.FederatedEvent()
                empty_sent = True
                logger.info(f"[{self.bridge_name}] Sent initial empty FederatedEvent")

            while self._connected:
                try:
                    event = await asyncio.wait_for(inbound_queue.get(), timeout=5)
                    sent_count += 1
                    if sent_count <= 3 or sent_count % 20 == 0:
                        if event.HasField("contact"):
                            descr = (
                                f"contact uid={event.contact.uid} "
                                f"callsign={event.contact.callsign} "
                                f"op={event.contact.operation}"
                            )
                        else:
                            descr = f"uid={event.event.uid}"
                        logger.info(
                            f"[{self.bridge_name}] Sending event #{sent_count} "
                            f"({descr}, groups={list(event.federateGroups)})"
                        )
                    yield event
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

        backoff = 1
        while self._connected:
            try:
                # ServerEventStream returns a Subscription when the stream completes
                response = await self._stub.ServerEventStream(
                    event_generator(), metadata=self._metadata(), timeout=None
                )
                logger.info(
                    f"[{self.bridge_name}] ServerEventStream returned subscription: "
                    f"name={response.identity.name}, "
                    f"type={fig_pb2.Identity.ConnectionType.Name(response.identity.type)}"
                )
                backoff = 1
            except grpc.aio.AioRpcError as e:
                if not self._connected:
                    break
                logger.warning(
                    f"[{self.bridge_name}] ServerEventStream error: {e.code()} - {e.details()}. "
                    f"Reconnecting in {backoff}s"
                )
                # Reset for re-handshake
                empty_sent = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except asyncio.CancelledError:
                break

    async def health_check_loop(self):
        """Periodic HealthCheck (every 3s, matches TakFigClient.java)."""
        await asyncio.sleep(2)
        while self._connected:
            try:
                health = fig_pb2.ClientHealth(status=fig_pb2.ClientHealth.SERVING)
                await self._stub.HealthCheck(health, metadata=self._metadata(), timeout=10)
                await asyncio.sleep(3)
            except grpc.aio.AioRpcError as e:
                logger.debug(f"[{self.bridge_name}] HealthCheck error: {e.code()}")
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                break

    def _detect_tls_name(self) -> str:
        """Try to read CN from FedHub server certificate for TLS override."""
        cert_dir = os.path.dirname(FEDHUB_CA_CERT)
        for cert_file in glob.glob(os.path.join(cert_dir, "*server*.pem")):
            try:
                result = subprocess.run(
                    ["openssl", "x509", "-in", cert_file, "-noout", "-subject"],
                    capture_output=True, text=True, timeout=5
                )
                for part in result.stdout.split(","):
                    if "CN=" in part or "CN =" in part:
                        cn = part.split("CN")[-1].strip().lstrip("=").strip()
                        logger.debug(f"Detected TLS name from {cert_file}: {cn}")
                        return cn
            except Exception:
                pass
        return ""

    @property
    def connected(self) -> bool:
        return self._connected
