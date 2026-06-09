# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bridge configuration model.

A plain dataclass (no ORM): bridges are persisted as YAML by store.BridgeStore.
Field names and defaults mirror the former SQLite columns so the templates,
the web handlers and the runtime manager consume it unchanged. `from_mapping`
is forgiving — it applies defaults for missing keys, coerces scalar types, and
ignores unknown keys — so a hand-written YAML entry only needs a `name`.
"""

import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Fields coerced from YAML/form scalars to a concrete Python type on load.
_INT_FIELDS = frozenset({
    "fedhub_port", "cot_output_port", "cot_input_port", "cot_input_udp_port",
    "cot_input_http_port", "simulator_interval", "simulator_tracks",
})
_BOOL_FIELDS = frozenset({
    "embed_federate_groups", "simulator_enabled", "virtual_chat_enabled", "enabled",
})


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _to_int(value, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class BridgeConfig:
    id: str = field(default_factory=new_id)
    name: str = ""
    description: str = ""
    fedhub_address: str = ""
    fedhub_port: int = 9103
    jwt_token: str = ""
    # CoT output (tcp/udp use host+port; http/https use the URL + client certs)
    cot_output_host: str = ""
    cot_output_port: int = 0
    cot_output_protocol: str = "tcp"  # tcp | udp | http | https
    cot_input_port: int = 0
    cot_input_udp_port: int = 0
    cot_input_http_port: int = 0
    cot_output_http_url: str = ""
    http_client_cert: str = ""
    http_client_key: str = ""
    http_ca_cert: str = ""
    http_server_cert: str = ""
    http_server_key: str = ""
    http_server_ca: str = ""
    # Group handling
    group_override: str = ""
    embed_federate_groups: bool = False
    group_translation: str = ""
    # Simulator
    simulator_enabled: bool = False
    simulator_interval: int = 5
    simulator_tracks: int = 5
    simulator_direction: str = "fedhub"  # fedhub | cot_output | both
    simulator_groups: str = ""
    # Virtual chat user
    virtual_chat_enabled: bool = False
    virtual_chat_callsign: str = ""
    # CoT transforms (see cot_transform.py); _out = egress, _in = ingress
    callsign_rewrite_out: str = ""
    callsign_rewrite_in: str = ""
    redact_out: str = ""
    redact_in: str = ""
    classify_access_out: str = ""
    classify_access_in: str = ""
    classify_remarks_out: str = ""
    classify_remarks_in: str = ""
    # Lifecycle
    enabled: bool = True
    created_at: str = ""

    @classmethod
    def from_mapping(cls, data: dict) -> "BridgeConfig":
        """Build from a (possibly partial) mapping. Missing keys take the field
        default; bool/int fields are coerced; unknown keys are ignored.
        Raises ValueError if `name` is empty."""
        kwargs = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            if f.name in _BOOL_FIELDS:
                value = _to_bool(value)
            elif f.name in _INT_FIELDS:
                value = _to_int(value, f.default)
            else:
                value = "" if value is None else str(value)
            kwargs[f.name] = value
        cfg = cls(**kwargs)
        if not cfg.name.strip():
            raise ValueError("bridge config requires a non-empty 'name'")
        return cfg

    def to_mapping(self) -> dict:
        """Ordered plain dict of all fields, for YAML serialization."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


# Backwards-compatible alias: importers historically used `Bridge`.
Bridge = BridgeConfig
