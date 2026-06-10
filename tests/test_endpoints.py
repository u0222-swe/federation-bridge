# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoint tests: the full web CRUD against a temp YAML store + a fake manager.

The handlers use module-level `store`/`manager` singletons; we substitute a
temp-file store and a no-op manager so the whole flow runs without touching real
config or starting live bridges (no gRPC/FedHub)."""

import pytest
from fastapi.testclient import TestClient

from federation_bridge import main
from federation_bridge.store import BridgeStore


class FakeManager:
    """Records calls; starts nothing."""

    def __init__(self):
        self.calls = []

    async def load_and_start_saved(self, store):
        self.calls.append(("load", None))

    async def start_bridge_from_model(self, cfg):
        self.calls.append(("start", cfg.name))

    async def restart_bridge_from_model(self, cfg):
        self.calls.append(("restart", cfg.name))

    async def stop_bridge(self, bridge_id):
        self.calls.append(("stop", bridge_id))

    async def shutdown_all(self):
        self.calls.append(("shutdown", None))

    def get_bridge(self, bridge_id):
        return None

    def get_all_statuses(self):
        return {}


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = BridgeStore(str(tmp_path / "bridges.yaml"))
    fake = FakeManager()
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "manager", fake)
    with TestClient(main.app) as c:
        c.store = store
        c.fake = fake
        yield c


def _create(client, name="partners", **extra):
    data = {"name": name, "jwt_token": "tok", "fedhub_address": "127.0.0.1",
            "fedhub_port": "9103"}
    data.update(extra)
    r = client.post("/bridges", data=data, follow_redirects=False)
    assert r.status_code == 303
    return client.store.list_bridges()[-1]


def test_index_empty(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "No bridges configured" in r.text


def test_version_in_health_and_footer(client):
    from federation_bridge.version import get_version, __version__
    assert get_version().startswith(__version__)
    r = client.get("/health")
    assert r.json()["version"] == get_version()
    assert f"Federation Bridge Manager {get_version()}" in client.get("/").text


def test_create_persists_and_starts(client):
    cfg = _create(client, name="partners", cot_input_udp_port="10001")
    assert cfg.name == "partners" and cfg.cot_input_udp_port == 10001
    assert cfg.enabled is True and cfg.created_at
    assert ("start", "partners") in client.fake.calls
    assert "partners" in client.get("/").text


def test_detail_and_edit_get(client):
    cfg = _create(client, name="alpha")
    assert client.get(f"/bridges/{cfg.id}").status_code == 200
    assert client.get(f"/bridges/{cfg.id}/edit").status_code == 200
    missing = client.get("/bridges/nope", follow_redirects=False)
    assert missing.status_code in (302, 307)  # redirect to index


def test_update_keeps_blank_jwt_and_preserves_enabled(client):
    cfg = _create(client, name="alpha")
    r = client.post(f"/bridges/{cfg.id}/edit", follow_redirects=False, data={
        "name": "alpha2", "fedhub_address": "127.0.0.1", "fedhub_port": "9103",
        "jwt_token": "",  # blank -> keep existing token
    })
    assert r.status_code == 303
    got = client.store.get(cfg.id)
    assert got.name == "alpha2"
    assert got.jwt_token == "tok"
    assert got.enabled is True  # not in form, preserved by in-place update
    assert ("restart", "alpha2") in client.fake.calls


def test_update_changes_jwt_when_provided(client):
    cfg = _create(client, name="alpha")
    client.post(f"/bridges/{cfg.id}/edit", follow_redirects=False, data={
        "name": "alpha", "fedhub_address": "127.0.0.1", "fedhub_port": "9103",
        "jwt_token": "newtok",
    })
    assert client.store.get(cfg.id).jwt_token == "newtok"


def test_stop_then_start_flips_enabled(client):
    cfg = _create(client, name="alpha")
    client.post(f"/bridges/{cfg.id}/stop", follow_redirects=False)
    assert client.store.get(cfg.id).enabled is False
    assert ("stop", cfg.id) in client.fake.calls
    client.post(f"/bridges/{cfg.id}/start", follow_redirects=False)
    assert client.store.get(cfg.id).enabled is True
    assert ("start", "alpha") in client.fake.calls


def test_delete_removes_and_stops(client):
    cfg = _create(client, name="alpha")
    client.post(f"/bridges/{cfg.id}/delete", follow_redirects=False)
    assert client.store.list_bridges() == []
    assert ("stop", cfg.id) in client.fake.calls
