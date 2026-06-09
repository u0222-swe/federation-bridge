# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the BridgeConfig dataclass (defaults, coercion, mapping)."""

from dataclasses import fields

import pytest

from federation_bridge.models import BridgeConfig


def test_defaults_for_minimal_mapping():
    cfg = BridgeConfig.from_mapping({"name": "x"})
    assert cfg.name == "x"
    assert cfg.id  # auto-assigned
    assert cfg.enabled is True
    assert cfg.fedhub_port == 9103
    assert cfg.cot_output_protocol == "tcp"
    assert cfg.simulator_interval == 5
    assert cfg.cot_input_port == 0
    assert cfg.group_translation == ""


def test_empty_name_rejected():
    with pytest.raises(ValueError):
        BridgeConfig.from_mapping({"name": "  "})
    with pytest.raises(ValueError):
        BridgeConfig.from_mapping({"fedhub_address": "1.2.3.4"})


def test_bool_coercion_from_strings():
    cfg = BridgeConfig.from_mapping({
        "name": "x", "enabled": "false", "simulator_enabled": "true",
        "embed_federate_groups": "1", "virtual_chat_enabled": "no",
    })
    assert cfg.enabled is False
    assert cfg.simulator_enabled is True
    assert cfg.embed_federate_groups is True
    assert cfg.virtual_chat_enabled is False


def test_bool_passthrough_for_real_bools():
    cfg = BridgeConfig.from_mapping({"name": "x", "enabled": True, "simulator_enabled": False})
    assert cfg.enabled is True
    assert cfg.simulator_enabled is False


def test_int_coercion_and_empty_default():
    cfg = BridgeConfig.from_mapping({
        "name": "x", "cot_input_udp_port": "10001", "fedhub_port": "9999",
        "cot_output_port": "", "simulator_tracks": None,
    })
    assert cfg.cot_input_udp_port == 10001 and isinstance(cfg.cot_input_udp_port, int)
    assert cfg.fedhub_port == 9999
    assert cfg.cot_output_port == 0          # empty -> default
    assert cfg.simulator_tracks == 5         # None -> default


def test_unknown_keys_ignored():
    cfg = BridgeConfig.from_mapping({"name": "x", "bogus": 1, "id": "fixed-id"})
    assert cfg.id == "fixed-id"
    assert not hasattr(cfg, "bogus")


def test_none_string_field_becomes_empty():
    cfg = BridgeConfig.from_mapping({"name": "x", "description": None})
    assert cfg.description == ""


def test_to_mapping_round_trips_all_fields():
    cfg = BridgeConfig.from_mapping({"name": "x", "cot_input_port": 10005})
    m = cfg.to_mapping()
    assert set(m) == {f.name for f in fields(BridgeConfig)}
    assert m["name"] == "x" and m["cot_input_port"] == 10005
    # round-trip through from_mapping yields an equal config
    assert BridgeConfig.from_mapping(m) == cfg
