# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the YAML-backed BridgeStore."""

import os

import pytest

from federation_bridge.models import BridgeConfig
from federation_bridge.store import BridgeStore


@pytest.fixture
def store(tmp_path):
    return BridgeStore(str(tmp_path / "bridges.yaml"))


def _cfg(name="a", **kw):
    return BridgeConfig.from_mapping({"name": name, "fedhub_address": "127.0.0.1",
                                      "jwt_token": "tok", **kw})


def test_missing_file_lists_empty(store):
    assert store.list_bridges() == []
    assert store.get("nope") is None


def test_create_writes_file_and_round_trips_types(store):
    cfg = store.create(_cfg("partners", cot_input_udp_port=10001, enabled=True,
                            embed_federate_groups=True))
    assert os.path.exists(store.path)
    got = store.get(cfg.id)
    assert got.name == "partners"
    assert got.cot_input_udp_port == 10001 and isinstance(got.cot_input_udp_port, int)
    assert got.enabled is True and isinstance(got.enabled, bool)
    assert got.embed_federate_groups is True
    assert got.id and got.created_at  # stamped on create


def test_update_only_sets_passed_keys(store):
    cfg = store.create(_cfg("a", enabled=True))
    created = cfg.created_at
    store.update(cfg.id, {"name": "b", "cot_input_port": 10009})
    got = store.get(cfg.id)
    assert got.name == "b" and got.cot_input_port == 10009
    # untouched fields preserved
    assert got.enabled is True
    assert got.created_at == created


def test_set_enabled_and_delete(store):
    cfg = store.create(_cfg("a", enabled=True))
    store.set_enabled(cfg.id, False)
    assert store.get(cfg.id).enabled is False
    assert store.delete(cfg.id) is True
    assert store.list_bridges() == []
    assert store.delete(cfg.id) is False


def test_atomic_write_leaves_no_tmp(store):
    store.create(_cfg("a"))
    assert not os.path.exists(store.path + ".tmp")


def test_load_assigns_missing_ids_and_persists(store):
    # Hand-write entries without ids
    with open(store.path, "w") as f:
        f.write("- name: one\n  jwt_token: t1\n- name: two\n  jwt_token: t2\n")
    store.load()
    ids = [b.id for b in store.list_bridges()]
    assert all(ids) and len(set(ids)) == 2
    # stable across a second load
    store.load()
    assert [b.id for b in store.list_bridges()] == ids


def test_forgiving_partial_and_unknown_keys(store):
    with open(store.path, "w") as f:
        f.write("- name: minimal\n- {name: extra, bogus: 1, cot_input_port: 10010}\n")
    bridges = store.list_bridges()
    assert {b.name for b in bridges} == {"minimal", "extra"}
    minimal = next(b for b in bridges if b.name == "minimal")
    assert minimal.fedhub_port == 9103 and minimal.enabled is True  # defaults
    extra = next(b for b in bridges if b.name == "extra")
    assert extra.cot_input_port == 10010


def test_comment_preserved_on_update(store):
    with open(store.path, "w") as f:
        f.write(
            "# keep this comment\n"
            "- id: keep-1\n  name: one\n  jwt_token: t1\n"
            "- id: keep-2\n  name: two\n  jwt_token: t2\n"
        )
    # Edit a *different* bridge; the comment must survive.
    store.update("keep-2", {"name": "two-edited"})
    text = open(store.path).read()
    assert "# keep this comment" in text
    assert store.get("keep-2").name == "two-edited"
    assert store.get("keep-1").name == "one"


def test_multiline_field_round_trips(store):
    cfg = store.create(_cfg("a", group_translation="blue = ALPHA\ngreen = BRAVO"))
    assert store.get(cfg.id).group_translation == "blue = ALPHA\ngreen = BRAVO"


def test_top_level_mapping_form_is_tolerated(store):
    with open(store.path, "w") as f:
        f.write("bridges:\n  - name: one\n    id: m-1\n    jwt_token: t\n")
    assert [b.name for b in store.list_bridges()] == ["one"]
