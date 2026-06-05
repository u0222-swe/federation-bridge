# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single federation bridge instance with gRPC + TCP components."""

import asyncio
import logging
import time
from enum import Enum
from .grpc_client import FederationGrpcClient
from .tcp_server import CotTcpServer
from .tcp_client import CotTcpClient
from .udp_server import CotUdpServer
from .udp_client import CotUdpClient
from .http_server import CotHttpServer
from .http_client import CotHttpClient
from .cot_converter import (
    cot_xml_to_federated_event,
    cot_xml_to_contact_event,
    extract_contact_info,
    extract_federate_groups,
    federated_event_to_cot_xml,
    parse_group_translation,
    translate_groups,
)
from .cot_simulator import CotSimulator
from .cot_transform import CotTransformer
from . import virtual_chat
from .wire_log import log_to_fedhub, log_from_fedhub

logger = logging.getLogger("federation-bridge.bridge")

# Re-announce a known contact every N seconds so peer TAKservers that
# restarted (or that connected to FedHub after our initial CREATE) still
# build the clientUid → federate-subscription mapping required for chat.
CONTACT_REANNOUNCE_INTERVAL_SEC = 300

# How often the Virtual Chat User re-sends its presence PLI. Kept well below
# the PLI stale window so the contact never ages out of ATAK's contact list.
VIRTUAL_CHAT_PLI_INTERVAL_SEC = 60


class BridgeStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


class Bridge:
    """A single federation bridge with gRPC client, TCP server, and TCP client."""

    def __init__(self, bridge_id: str, name: str, fedhub_address: str,
                 fedhub_port: int, jwt_token: str,
                 cot_input_port: int = 0,
                 cot_input_udp_port: int = 0,
                 cot_input_http_port: int = 0,
                 cot_output_host: str = "", cot_output_port: int = 0,
                 cot_output_protocol: str = "tcp",
                 cot_output_http_url: str = "",
                 http_client_cert: str = "", http_client_key: str = "",
                 http_ca_cert: str = "",
                 http_server_cert: str = "", http_server_key: str = "",
                 http_server_ca: str = "",
                 group_override: str = "",
                 embed_federate_groups: bool = False,
                 group_translation: str = "",
                 simulator_enabled: bool = False,
                 simulator_interval: int = 5,
                 simulator_tracks: int = 5,
                 simulator_direction: str = "fedhub",
                 simulator_groups: str = "",
                 virtual_chat_enabled: bool = False,
                 virtual_chat_callsign: str = "",
                 callsign_rewrite_out: str = "", callsign_rewrite_in: str = "",
                 redact_out: str = "", redact_in: str = "",
                 classify_access_out: str = "", classify_access_in: str = "",
                 classify_remarks_out: str = "", classify_remarks_in: str = ""):
        self.id = bridge_id
        self.name = name
        self.fedhub_address = fedhub_address
        self.fedhub_port = fedhub_port
        self.jwt_token = jwt_token
        self.cot_input_port = cot_input_port
        self.cot_input_udp_port = cot_input_udp_port
        self.cot_input_http_port = cot_input_http_port
        self.cot_output_host = cot_output_host
        self.cot_output_port = cot_output_port
        self.cot_output_protocol = (cot_output_protocol or "tcp").lower()
        self.cot_output_http_url = cot_output_http_url
        self.http_client_cert = http_client_cert
        self.http_client_key = http_client_key
        self.http_ca_cert = http_ca_cert
        self.http_server_cert = http_server_cert
        self.http_server_key = http_server_key
        self.http_server_ca = http_server_ca
        self.group_override = group_override
        self.embed_federate_groups = embed_federate_groups
        self.group_translation = group_translation
        # Translation tables, applied only when group carrying is on.
        # _cot_to_fedhub: incoming CoT group -> FedHub group (CoT -> protobuf).
        # _fedhub_to_cot: the reverse, for embedding outgoing groups in CoT.
        self._cot_to_fedhub = parse_group_translation(group_translation)
        self._fedhub_to_cot = {v: k for k, v in self._cot_to_fedhub.items()}
        self.simulator_enabled = simulator_enabled
        self.simulator_interval = simulator_interval
        self.simulator_tracks = simulator_tracks
        self.simulator_direction = (simulator_direction or "fedhub").lower()
        self.simulator_groups = [
            g.strip() for g in (simulator_groups or "").split(",") if g.strip()
        ]
        self.virtual_chat_enabled = virtual_chat_enabled
        # Empty callsign => default of fedhub-broadcast-chat-<name>
        self.virtual_chat_callsign = (
            virtual_chat_callsign.strip()
            or virtual_chat.default_callsign(name)
        )
        self.virtual_chat_uid = virtual_chat.virtual_uid(bridge_id)
        # Direction-scoped CoT transforms. _transform_out rewrites CoT heading
        # out the CoT output (applied in _convert_inbound); _transform_in
        # rewrites CoT arriving on the CoT input (applied in _convert_outbound).
        self._transform_out = CotTransformer(
            callsign_rewrite=callsign_rewrite_out, redact=redact_out,
            classify_access=classify_access_out, classify_remarks=classify_remarks_out,
        )
        self._transform_in = CotTransformer(
            callsign_rewrite=callsign_rewrite_in, redact=redact_in,
            classify_access=classify_access_in, classify_remarks=classify_remarks_in,
        )
        self.status = BridgeStatus.STOPPED
        self._tasks: list[asyncio.Task] = []

        # Internal queues
        self._grpc_inbound = asyncio.Queue(maxsize=1000)   # FedHub -> CoT XML out
        self._grpc_outbound = asyncio.Queue(maxsize=1000)  # CoT XML in -> FedHub
        self._cot_inbound = asyncio.Queue(maxsize=1000)    # TCP server -> converter
        self._cot_outbound = asyncio.Queue(maxsize=1000)   # converter -> TCP client

        # uid -> {"callsign": str, "last_announced": float}. Drives the
        # ContactListEntry CREATE refresh loop in _convert_outbound.
        self._announced_contacts: dict[str, dict] = {}

    async def start(self):
        """Start all bridge components as async tasks."""
        self.status = BridgeStatus.STARTING
        logger.info(f"[{self.name}] Starting bridge")

        grpc_client = FederationGrpcClient(
            self.fedhub_address, self.fedhub_port,
            self.jwt_token, self.name
        )
        await grpc_client.connect()

        # CRITICAL ORDER (matches TakFigClient.java sequence):
        # 1. Subscribe to FedHub's groups stream — wait for SERVING status
        # 2. Open ClientEventStream (receive)
        # 3. Open ServerEventStream (send) — only after SERVING
        # 4. HealthCheck loop every 3s

        # 1. Receive FedHub's groups stream (signals SERVING when ready)
        self._tasks.append(asyncio.create_task(
            grpc_client.receive_groups(),
            name=f"{self.name}-grpc-groups"
        ))

        # 2. Receive events from FedHub
        self._tasks.append(asyncio.create_task(
            grpc_client.receive_events(self._grpc_inbound),
            name=f"{self.name}-grpc-recv"
        ))

        # 3. Send events to FedHub (waits for SERVING internally)
        self._tasks.append(asyncio.create_task(
            grpc_client.send_events(self._grpc_outbound),
            name=f"{self.name}-grpc-send"
        ))

        # 4. HealthCheck loop
        self._tasks.append(asyncio.create_task(
            grpc_client.health_check_loop(),
            name=f"{self.name}-grpc-health"
        ))

        # Converter: protobuf -> CoT XML (inbound from FedHub)
        self._tasks.append(asyncio.create_task(
            self._convert_inbound(),
            name=f"{self.name}-conv-in"
        ))

        # Converter: CoT XML -> protobuf (outbound to FedHub)
        self._tasks.append(asyncio.create_task(
            self._convert_outbound(),
            name=f"{self.name}-conv-out"
        ))

        # TCP server (CoT XML input)
        if self.cot_input_port:
            tcp_srv = CotTcpServer(self.cot_input_port, self.name)
            self._tasks.append(asyncio.create_task(
                tcp_srv.start(self._cot_inbound),
                name=f"{self.name}-tcp-srv"
            ))

        # UDP server (CoT XML input via datagrams)
        if self.cot_input_udp_port:
            udp_srv = CotUdpServer(self.cot_input_udp_port, self.name)
            self._tasks.append(asyncio.create_task(
                udp_srv.start(self._cot_inbound),
                name=f"{self.name}-udp-srv"
            ))

        # HTTP server (CoT XML input via POST, optionally mTLS)
        if self.cot_input_http_port:
            http_srv = CotHttpServer(
                self.cot_input_http_port,
                server_cert=self.http_server_cert,
                server_key=self.http_server_key,
                ca_cert=self.http_server_ca,
                bridge_name=self.name,
            )
            self._tasks.append(asyncio.create_task(
                http_srv.start(self._cot_inbound),
                name=f"{self.name}-http-srv"
            ))

        # Simulator (generates random CoT events). Direction selects where the
        # synthetic events go: toward FedHub (the CoT->protobuf path), out the
        # CoT output (the protobuf->CoT path), or both.
        if self.simulator_enabled:
            sim_targets = []
            if self.simulator_direction in ("fedhub", "both"):
                # Toward FedHub the group is always carried (then restored to
                # federateGroups on conversion), so simulated traffic always
                # belongs to its configured group regardless of the embed toggle.
                sim_targets.append({"queue": self._cot_inbound, "embed_groups": True})
            if self.simulator_direction in ("cot_output", "both"):
                # Only useful if a CoT output is configured to drain the queue;
                # otherwise the events would pile up with no consumer.
                if self.cot_output_host and self.cot_output_port:
                    # On the CoT wire the non-standard <__fedhubgroups> element
                    # is only added when group embedding is enabled.
                    sim_targets.append({
                        "queue": self._cot_outbound,
                        "embed_groups": self.embed_federate_groups,
                    })
                else:
                    logger.warning(
                        f"[{self.name}] Simulator direction '{self.simulator_direction}' "
                        f"requests CoT output, but no CoT output host/port is "
                        f"configured — skipping that target."
                    )
            if sim_targets:
                sim = CotSimulator(
                    self.name, self.simulator_interval, self.simulator_tracks,
                    groups=self.simulator_groups,
                )
                self._tasks.append(asyncio.create_task(
                    sim.run(sim_targets),
                    name=f"{self.name}-simulator"
                ))
            else:
                logger.warning(
                    f"[{self.name}] Simulator enabled but no valid target "
                    f"(direction='{self.simulator_direction}') — not started."
                )

        # Virtual Chat User (announces a stationary contact for broadcast chat)
        if self.virtual_chat_enabled:
            self._tasks.append(asyncio.create_task(
                self._virtual_chat_pli_loop(),
                name=f"{self.name}-virtual-chat"
            ))

        # CoT output client (HTTP/HTTPS, UDP, or TCP)
        if self.cot_output_protocol in ("http", "https") and self.cot_output_http_url:
            http_cli = CotHttpClient(
                self.cot_output_http_url,
                client_cert=self.http_client_cert,
                client_key=self.http_client_key,
                ca_cert=self.http_ca_cert,
                bridge_name=self.name,
            )
            self._tasks.append(asyncio.create_task(
                http_cli.start(self._cot_outbound),
                name=f"{self.name}-http-cli"
            ))
        elif self.cot_output_host and self.cot_output_port:
            if self.cot_output_protocol == "udp":
                udp_cli = CotUdpClient(self.cot_output_host, self.cot_output_port, self.name)
                self._tasks.append(asyncio.create_task(
                    udp_cli.start(self._cot_outbound),
                    name=f"{self.name}-udp-cli"
                ))
            else:
                tcp_cli = CotTcpClient(self.cot_output_host, self.cot_output_port, self.name)
                self._tasks.append(asyncio.create_task(
                    tcp_cli.start(self._cot_outbound),
                    name=f"{self.name}-tcp-cli"
                ))

        # Surface any task that dies unexpectedly. Without this a task that
        # raises at startup (e.g. an HTTP transport with an unreadable cert)
        # would fail silently, since tasks are only awaited again on stop().
        for task in self._tasks:
            task.add_done_callback(self._log_task_exception)

        self.status = BridgeStatus.RUNNING
        logger.info(f"[{self.name}] Bridge running ({len(self._tasks)} tasks)")

    def _log_task_exception(self, task: asyncio.Task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                f"[{self.name}] Task {task.get_name()} exited with error: {exc!r}"
            )

    async def stop(self):
        """Stop all bridge components."""
        logger.info(f"[{self.name}] Stopping bridge")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.status = BridgeStatus.STOPPED
        logger.info(f"[{self.name}] Bridge stopped")

    async def _convert_inbound(self):
        """Convert FederatedEvent protobuf -> CoT XML and put on outbound queue.

        Contact-only events (FederatedEvent.contact set, no .event) carry no
        CoT payload — they are federation control messages used to populate
        the peer's clientUid → subscription map. The external CoT consumer
        has no use for them, so we log and drop instead of emitting an XML
        skeleton with empty uid/type/coords (which would otherwise corrupt
        downstream tracks).
        """
        while True:
            try:
                event = await self._grpc_inbound.get()
                if not event.HasField("event"):
                    if event.HasField("contact"):
                        logger.debug(
                            f"[{self.name}] Dropping inbound contact-only event: "
                            f"uid={event.contact.uid} callsign={event.contact.callsign} "
                            f"op={event.contact.operation}"
                        )
                    continue
                cot_xml = federated_event_to_cot_xml(
                    event,
                    embed_groups=self.embed_federate_groups,
                    group_map=self._fedhub_to_cot,
                )
                if cot_xml is None:
                    continue
                # Virtual Chat User: a direct chat addressed to the virtual
                # contact is re-broadcast as a broadcast GeoChat on the output.
                if self.virtual_chat_enabled and virtual_chat.is_chat_targeting_virtual(
                    event.event, self.virtual_chat_uid, self.virtual_chat_callsign
                ):
                    cot_xml = virtual_chat.rewrite_chat_to_broadcast(
                        cot_xml, self.virtual_chat_uid, self.virtual_chat_callsign
                    )
                    logger.info(
                        f"[{self.name}] Virtual Chat User: re-broadcasting direct "
                        f"chat (uid={event.event.uid}) as broadcast to "
                        f"'{virtual_chat.BROADCAST_ROOM}'"
                    )
                # Apply egress transforms (callsign affix, redaction,
                # classification) to the CoT now that it is fully reconstructed.
                if self._transform_out.enabled():
                    cot_xml = self._transform_out.apply(cot_xml)
                log_from_fedhub(self.name, event, cot_xml)
                await self._cot_outbound.put(cot_xml)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.name}] Inbound conversion error: {e}")

    async def _convert_outbound(self):
        """Convert CoT XML -> FederatedEvent protobuf and put on gRPC outbound queue.

        For every CoT message whose detail carries a <contact callsign=...>
        we first emit a FederatedEvent.contact (ContactListEntry CRUD.CREATE).
        This is what TAKserver's DistributedFederationManager.addLocalContact
        does for its local clients and it is *required* for GeoChat routing:
        the receiving TAKserver registers clientUid → federate-subscription
        on the CREATE, and only then can it resolve EXPLICIT_UID_KEY matches
        when an ATAK client replies. We re-announce every
        CONTACT_REANNOUNCE_INTERVAL_SEC for robustness against peer restarts
        and to repopulate FedHub's per-stream contact cache.
        """
        from .proto_gen import fig_pb2
        converted = 0
        while True:
            try:
                cot_xml = await self._cot_inbound.get()

                # Apply ingress transforms before parsing so rewritten callsigns
                # reach screenName/contact-announce and a stamped access reaches
                # the FederatedEvent.
                if self._transform_in.enabled():
                    cot_xml = self._transform_in.apply(cot_xml)

                # Group precedence: an explicit group_override always wins
                # (operator intent). Otherwise restore any federation groups a
                # peer bridge (or our own simulator) embedded in the CoT, so the
                # source group selection flows through to this FedHub. Carried
                # groups are translated from their CoT-side names to this
                # FedHub's names. Restoration is INDEPENDENT of the embed toggle
                # — that toggle only controls whether *outgoing* CoT gets groups
                # embedded; an incoming <__fedhubgroups> is always honored (and
                # always stripped by the converter).
                groups = None
                if self.group_override:
                    groups = [g.strip() for g in self.group_override.split(",") if g.strip()]
                else:
                    carried = extract_federate_groups(cot_xml)
                    if carried:
                        groups = translate_groups(carried, self._cot_to_fedhub)

                provenance = fig_pb2.FederateProvenance(
                    federationServerId=f"federation-bridge-{self.id}",
                    federationServerName=f"federation-bridge-{self.name}",
                )

                await self._maybe_announce_contact(cot_xml, groups, provenance)

                event = cot_xml_to_federated_event(cot_xml)
                if groups:
                    event.federateGroups[:] = groups
                # Add federateProvenance so FedHub doesn't loop-drop the event
                event.federateProvenance.append(provenance)
                log_to_fedhub(self.name, cot_xml, event)
                await self._grpc_outbound.put(event)
                converted += 1
                if converted <= 3 or converted % 20 == 0:
                    logger.info(
                        f"[{self.name}] Converted XML->proto #{converted} "
                        f"(uid={event.event.uid}, groups={list(event.federateGroups)})"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.name}] Outbound conversion error: {e}")

    async def _maybe_announce_contact(self, cot_xml: str, groups, provenance):
        """Emit a ContactListEntry CRUD.CREATE for newly seen / stale UIDs.

        See `_convert_outbound` docstring for why this is required. Skipped
        silently for CoT messages without a <contact callsign=...> (e.g.
        chat messages, drawings) since those don't represent a contact.
        """
        info = extract_contact_info(cot_xml)
        if info is None:
            return

        uid = info["uid"]
        callsign = info["callsign"]
        now = time.monotonic()
        prev = self._announced_contacts.get(uid)
        needs_announce = (
            prev is None
            or prev["callsign"] != callsign
            or (now - prev["last_announced"]) >= CONTACT_REANNOUNCE_INTERVAL_SEC
        )
        if not needs_announce:
            return

        contact_event = cot_xml_to_contact_event(
            cot_xml,
            federate_groups=groups,
        )
        if contact_event is None:
            return
        contact_event.federateProvenance.append(provenance)
        await self._grpc_outbound.put(contact_event)
        self._announced_contacts[uid] = {
            "callsign": callsign,
            "last_announced": now,
        }
        if prev is None:
            logger.info(
                f"[{self.name}] Announced new contact to FedHub: "
                f"uid={uid} callsign={callsign} groups={groups or []}"
            )
        else:
            logger.debug(
                f"[{self.name}] Re-announced contact: uid={uid} callsign={callsign}"
            )

    async def _virtual_chat_pli_loop(self):
        """Periodically announce the Virtual Chat User's presence.

        Puts a stationary PLI for the virtual contact on the outbound CoT queue.
        It then rides the normal outbound path: _convert_outbound emits the
        ContactListEntry CREATE (via _maybe_announce_contact) and forwards the
        PLI to FedHub, so ATAK clients see it as an addressable contact.
        """
        logger.info(
            f"[{self.name}] Virtual Chat User enabled: "
            f"uid={self.virtual_chat_uid} callsign={self.virtual_chat_callsign}"
        )
        while True:
            try:
                pli = virtual_chat.build_virtual_pli_xml(
                    self.virtual_chat_uid, self.virtual_chat_callsign
                )
                await self._cot_inbound.put(pli)
                await asyncio.sleep(VIRTUAL_CHAT_PLI_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
