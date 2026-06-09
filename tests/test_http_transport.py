# SPDX-License-Identifier: AGPL-3.0-or-later
"""Round-trip tests for the HTTP(S) CoT transport (server + client, mTLS)."""

import asyncio
import shutil
import socket
import ssl
import subprocess

import aiohttp
import pytest

from federation_bridge.http_client import CotHttpClient
from federation_bridge.http_server import CotHttpServer

SAMPLE = (
    '<?xml version="1.0"?>'
    '<event version="2.0" uid="TEST-1" type="a-f-G-U-C">'
    '<point lat="59.3" lon="18.0" hae="0" ce="9" le="9"/>'
    '<detail><contact callsign="TEST-1"/></detail></event>'
)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _run_server(server, queue):
    task = asyncio.create_task(server.start(queue))
    await asyncio.sleep(0.25)  # let TCPSite bind before clients connect
    return task


async def _stop(*tasks):
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
def certs(tmp_path):
    """A self-signed cert/key pair (also usable as its own CA). Skips without openssl."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost"],
        check=True, capture_output=True,
    )
    return str(cert), str(key)


async def test_plain_http_roundtrip():
    port = _free_port()
    inbound = asyncio.Queue()
    outbound = asyncio.Queue()
    server = CotHttpServer(port, bridge_name="t")
    client = CotHttpClient(f"http://127.0.0.1:{port}/cot", bridge_name="t")
    srv_task = await _run_server(server, inbound)
    cli_task = asyncio.create_task(client.start(outbound))
    try:
        await outbound.put(SAMPLE)
        received = await asyncio.wait_for(inbound.get(), timeout=5)
        assert "TEST-1" in received
    finally:
        await _stop(cli_task, srv_task)


async def test_multi_event_body_is_split():
    port = _free_port()
    inbound = asyncio.Queue()
    server = CotHttpServer(port, bridge_name="t")
    srv_task = await _run_server(server, inbound)
    try:
        body = SAMPLE + SAMPLE  # two </event>-delimited events in one POST
        async with aiohttp.ClientSession() as s:
            async with s.post(f"http://127.0.0.1:{port}/cot", data=body) as resp:
                assert resp.status == 200
        first = await asyncio.wait_for(inbound.get(), timeout=5)
        second = await asyncio.wait_for(inbound.get(), timeout=5)
        assert first.endswith("</event>") and second.endswith("</event>")
    finally:
        await _stop(srv_task)


async def test_non_post_rejected():
    port = _free_port()
    inbound = asyncio.Queue()
    server = CotHttpServer(port, bridge_name="t")
    srv_task = await _run_server(server, inbound)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/cot") as resp:
                assert resp.status == 405
    finally:
        await _stop(srv_task)


async def test_mtls_roundtrip(certs):
    cert, key = certs
    port = _free_port()
    inbound = asyncio.Queue()
    outbound = asyncio.Queue()
    # Server requires + verifies a client cert (the self-signed cert is its own CA).
    server = CotHttpServer(port, server_cert=cert, server_key=key, ca_cert=cert, bridge_name="t")
    # Client presents that cert; ca_cert empty => server verification off (no SAN needed).
    client = CotHttpClient(
        f"https://127.0.0.1:{port}/cot",
        client_cert=cert, client_key=key, ca_cert="", bridge_name="t",
    )
    srv_task = await _run_server(server, inbound)
    cli_task = asyncio.create_task(client.start(outbound))
    try:
        await outbound.put(SAMPLE)
        received = await asyncio.wait_for(inbound.get(), timeout=5)
        assert "TEST-1" in received
    finally:
        await _stop(cli_task, srv_task)


async def test_mtls_server_requires_client_cert(certs):
    cert, key = certs
    port = _free_port()
    inbound = asyncio.Queue()
    server = CotHttpServer(port, server_cert=cert, server_key=key, ca_cert=cert, bridge_name="t")
    srv_task = await _run_server(server, inbound)
    try:
        # No client cert presented -> TLS handshake must fail.
        no_verify = ssl.create_default_context()
        no_verify.check_hostname = False
        no_verify.verify_mode = ssl.CERT_NONE
        with pytest.raises((aiohttp.ClientError, ssl.SSLError, OSError)):
            async with aiohttp.ClientSession() as s:
                async with s.post(f"https://127.0.0.1:{port}/cot", data=SAMPLE, ssl=no_verify):
                    pass
        assert inbound.empty()
    finally:
        await _stop(srv_task)
