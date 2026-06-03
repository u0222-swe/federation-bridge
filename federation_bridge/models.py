# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, DateTime
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Bridge(Base):
    __tablename__ = "bridges"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    description = Column(String, default="")
    fedhub_address = Column(String, nullable=False)
    fedhub_port = Column(Integer, nullable=False, default=9103)
    jwt_token = Column(String, nullable=False)
    cot_output_host = Column(String, default="")
    cot_output_port = Column(Integer, default=0)
    cot_output_protocol = Column(String, default="tcp")  # "tcp" or "udp"
    cot_input_port = Column(Integer, default=0)
    cot_input_udp_port = Column(Integer, default=0)
    group_override = Column(String, default="")
    # Carry FedHub federation groups across the CoT hop via a non-standard
    # <__fedhubgroups> detail element so a peer bridge can restore them.
    # Opt-in because it deviates from the CoT standard. Enable on both bridges
    # of a pair. When group_override is set, it takes precedence over carried
    # groups on the receiving side.
    embed_federate_groups = Column(Boolean, default=False)
    # Optional group translation table, applied when embed_federate_groups is
    # on. Free-form "cot_name = fedhub_name" pairs (newline/comma separated):
    # incoming CoT groups are mapped to FedHub names, outgoing FedHub groups
    # are mapped back to CoT names. Unlisted groups pass through unchanged.
    group_translation = Column(String, default="")
    simulator_enabled = Column(Boolean, default=False)
    simulator_interval = Column(Integer, default=5)
    simulator_tracks = Column(Integer, default=5)
    # Where simulated CoT events are sent: "fedhub", "cot_output", or "both".
    simulator_direction = Column(String, default="fedhub")
    # Comma-separated FedHub federation group(s) embedded in simulated events.
    simulator_groups = Column(String, default="")
    # Virtual Chat User: announces a stationary contact into the federation;
    # any direct chat sent TO it is re-broadcast as a CoT broadcast GeoChat on
    # the bridge's CoT output. Empty callsign falls back to
    # "fedhub-broadcast-chat-<bridge name>".
    virtual_chat_enabled = Column(Boolean, default=False)
    virtual_chat_callsign = Column(String, default="")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
