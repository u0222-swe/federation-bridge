# SPDX-License-Identifier: AGPL-3.0-or-later
"""Manages lifecycle of all bridges."""

import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .bridge import Bridge, BridgeStatus
from .models import Bridge as BridgeModel

logger = logging.getLogger("federation-bridge.manager")


class BridgeManager:
    """Manages lifecycle of all bridge instances."""

    def __init__(self):
        self._bridges: dict[str, Bridge] = {}

    async def load_and_start_saved(self, session: AsyncSession):
        """Load enabled bridges from DB and start them."""
        result = await session.execute(
            select(BridgeModel).where(BridgeModel.enabled == True)
        )
        for row in result.scalars():
            try:
                await self.start_bridge_from_model(row)
            except Exception as e:
                logger.error(f"Failed to start bridge '{row.name}': {e}")

    async def start_bridge_from_model(self, model: BridgeModel) -> Bridge:
        """Create and start a bridge from a DB model."""
        if model.id in self._bridges:
            existing = self._bridges[model.id]
            if existing.status == BridgeStatus.RUNNING:
                return existing
            await existing.stop()

        bridge = Bridge(
            bridge_id=model.id,
            name=model.name,
            fedhub_address=model.fedhub_address,
            fedhub_port=model.fedhub_port,
            jwt_token=model.jwt_token,
            cot_input_port=model.cot_input_port,
            cot_input_udp_port=model.cot_input_udp_port or 0,
            cot_output_host=model.cot_output_host,
            cot_output_port=model.cot_output_port,
            cot_output_protocol=model.cot_output_protocol or "tcp",
            group_override=model.group_override or "",
            embed_federate_groups=bool(model.embed_federate_groups),
            group_translation=model.group_translation or "",
            simulator_enabled=model.simulator_enabled,
            simulator_interval=model.simulator_interval or 5,
            simulator_tracks=model.simulator_tracks or 5,
            simulator_direction=model.simulator_direction or "fedhub",
            simulator_groups=model.simulator_groups or "",
            virtual_chat_enabled=model.virtual_chat_enabled,
            virtual_chat_callsign=model.virtual_chat_callsign or "",
        )
        await bridge.start()
        self._bridges[model.id] = bridge
        return bridge

    async def stop_bridge(self, bridge_id: str):
        bridge = self._bridges.get(bridge_id)
        if bridge:
            await bridge.stop()

    async def restart_bridge_from_model(self, model: BridgeModel) -> Bridge:
        """Force stop+start so an updated DB row takes effect on a running bridge.

        start_bridge_from_model returns early if the bridge is already RUNNING,
        which is the right idempotent behavior for /start but wrong for /edit —
        edited config would never be picked up.
        """
        existing = self._bridges.pop(model.id, None)
        if existing:
            await existing.stop()
        return await self.start_bridge_from_model(model)

    def get_bridge(self, bridge_id: str) -> Optional[Bridge]:
        return self._bridges.get(bridge_id)

    def get_all_statuses(self) -> dict[str, BridgeStatus]:
        return {bid: b.status for bid, b in self._bridges.items()}

    async def shutdown_all(self):
        logger.info("Shutting down all bridges")
        for bridge in self._bridges.values():
            await bridge.stop()
        self._bridges.clear()
