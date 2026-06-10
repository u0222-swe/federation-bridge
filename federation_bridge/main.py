# SPDX-License-Identifier: AGPL-3.0-or-later
"""Federation Bridge Manager - FastAPI application."""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import FEDHUB_DEFAULT_ADDRESS, FEDHUB_DEFAULT_PORT
from .models import BridgeConfig
from .store import BridgeStore
from .bridge import BridgeStatus
from .bridge_manager import BridgeManager
from .version import get_version

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("federation-bridge")

manager = BridgeManager()
# Module-level singletons. Tests substitute these (with a temp-file store and a
# fake manager) to exercise the handlers without touching real config or
# starting live bridges.
store = BridgeStore()

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.load()
    await manager.load_and_start_saved(store)
    logger.info(f"Federation Bridge Manager {get_version()} started")
    yield
    await manager.shutdown_all()
    logger.info("Federation Bridge Manager stopped")


app = FastAPI(title="Federation Bridge Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
# Shown in the base template's footer on every page.
templates.env.globals["app_version"] = get_version()


@app.get("/health")
async def health():
    return {"status": "ok", "version": get_version()}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    bridges = store.list_bridges()
    statuses = manager.get_all_statuses()
    return templates.TemplateResponse(request, "bridges.html", {
        "bridges": bridges,
        "statuses": statuses,
        "defaults": {"address": FEDHUB_DEFAULT_ADDRESS, "port": FEDHUB_DEFAULT_PORT},
    })


@app.post("/bridges")
async def create_bridge(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    fedhub_address: str = Form(FEDHUB_DEFAULT_ADDRESS),
    fedhub_port: int = Form(FEDHUB_DEFAULT_PORT),
    jwt_token: str = Form(...),
    cot_output_host: str = Form(""),
    cot_output_port: int = Form(0),
    cot_output_protocol: str = Form("tcp"),
    cot_input_port: int = Form(0),
    cot_input_udp_port: int = Form(0),
    cot_input_http_port: int = Form(0),
    cot_output_http_url: str = Form(""),
    http_client_cert: str = Form(""),
    http_client_key: str = Form(""),
    http_ca_cert: str = Form(""),
    http_server_cert: str = Form(""),
    http_server_key: str = Form(""),
    http_server_ca: str = Form(""),
    group_override: str = Form(""),
    embed_federate_groups: str = Form(""),
    group_translation: str = Form(""),
    simulator_enabled: str = Form(""),
    simulator_interval: int = Form(5),
    simulator_tracks: int = Form(5),
    simulator_direction: str = Form("fedhub"),
    simulator_groups: str = Form(""),
    virtual_chat_enabled: str = Form(""),
    virtual_chat_callsign: str = Form(""),
    callsign_rewrite_out: str = Form(""),
    callsign_rewrite_in: str = Form(""),
    redact_out: str = Form(""),
    redact_in: str = Form(""),
    classify_access_out: str = Form(""),
    classify_access_in: str = Form(""),
    classify_remarks_out: str = Form(""),
    classify_remarks_in: str = Form(""),
):
    cfg = BridgeConfig(
        name=name,
        description=description,
        fedhub_address=fedhub_address,
        fedhub_port=fedhub_port,
        jwt_token=jwt_token,
        cot_output_host=cot_output_host,
        cot_output_port=cot_output_port,
        cot_output_protocol=cot_output_protocol,
        cot_input_port=cot_input_port,
        cot_input_udp_port=cot_input_udp_port,
        cot_input_http_port=cot_input_http_port,
        cot_output_http_url=cot_output_http_url,
        http_client_cert=http_client_cert,
        http_client_key=http_client_key,
        http_ca_cert=http_ca_cert,
        http_server_cert=http_server_cert,
        http_server_key=http_server_key,
        http_server_ca=http_server_ca,
        group_override=group_override,
        embed_federate_groups=embed_federate_groups == "true",
        group_translation=group_translation,
        simulator_enabled=simulator_enabled == "true",
        simulator_interval=simulator_interval,
        simulator_tracks=simulator_tracks,
        simulator_direction=simulator_direction,
        simulator_groups=simulator_groups,
        virtual_chat_enabled=virtual_chat_enabled == "true",
        virtual_chat_callsign=virtual_chat_callsign,
        callsign_rewrite_out=callsign_rewrite_out,
        callsign_rewrite_in=callsign_rewrite_in,
        redact_out=redact_out,
        redact_in=redact_in,
        classify_access_out=classify_access_out,
        classify_access_in=classify_access_in,
        classify_remarks_out=classify_remarks_out,
        classify_remarks_in=classify_remarks_in,
    )
    store.create(cfg)
    await manager.start_bridge_from_model(cfg)
    return RedirectResponse(url="/", status_code=303)


@app.get("/bridges/{bridge_id}", response_class=HTMLResponse)
async def bridge_detail(request: Request, bridge_id: str):
    bridge = store.get(bridge_id)
    if not bridge:
        return RedirectResponse(url="/")
    runtime = manager.get_bridge(bridge_id)
    status = runtime.status if runtime else BridgeStatus.STOPPED
    return templates.TemplateResponse(request, "bridge_detail.html", {
        "bridge": bridge, "status": status,
    })


@app.get("/bridges/{bridge_id}/edit", response_class=HTMLResponse)
async def edit_bridge_form(request: Request, bridge_id: str):
    bridge = store.get(bridge_id)
    if not bridge:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(request, "bridge_edit.html", {"bridge": bridge})


@app.post("/bridges/{bridge_id}/edit")
async def update_bridge(
    request: Request,
    bridge_id: str,
    name: str = Form(...),
    description: str = Form(""),
    fedhub_address: str = Form(...),
    fedhub_port: int = Form(...),
    jwt_token: str = Form(""),
    cot_output_host: str = Form(""),
    cot_output_port: int = Form(0),
    cot_output_protocol: str = Form("tcp"),
    cot_input_port: int = Form(0),
    cot_input_udp_port: int = Form(0),
    cot_input_http_port: int = Form(0),
    cot_output_http_url: str = Form(""),
    http_client_cert: str = Form(""),
    http_client_key: str = Form(""),
    http_ca_cert: str = Form(""),
    http_server_cert: str = Form(""),
    http_server_key: str = Form(""),
    http_server_ca: str = Form(""),
    group_override: str = Form(""),
    embed_federate_groups: str = Form(""),
    group_translation: str = Form(""),
    simulator_enabled: str = Form(""),
    simulator_interval: int = Form(5),
    simulator_tracks: int = Form(5),
    simulator_direction: str = Form("fedhub"),
    simulator_groups: str = Form(""),
    virtual_chat_enabled: str = Form(""),
    virtual_chat_callsign: str = Form(""),
    callsign_rewrite_out: str = Form(""),
    callsign_rewrite_in: str = Form(""),
    redact_out: str = Form(""),
    redact_in: str = Form(""),
    classify_access_out: str = Form(""),
    classify_access_in: str = Form(""),
    classify_remarks_out: str = Form(""),
    classify_remarks_in: str = Form(""),
):
    existing = store.get(bridge_id)
    if not existing:
        return RedirectResponse(url="/", status_code=303)

    # Empty jwt_token in the form = keep the current secret, so a cosmetic edit
    # doesn't force the operator to re-paste the token.
    jwt_value = jwt_token if jwt_token.strip() else existing.jwt_token

    # Write only the editable keys; `enabled` and `created_at` are not in the
    # form and are preserved by the in-place store update.
    fields = {
        "name": name,
        "description": description,
        "fedhub_address": fedhub_address,
        "fedhub_port": fedhub_port,
        "jwt_token": jwt_value,
        "cot_output_host": cot_output_host,
        "cot_output_port": cot_output_port,
        "cot_output_protocol": cot_output_protocol,
        "cot_input_port": cot_input_port,
        "cot_input_udp_port": cot_input_udp_port,
        "cot_input_http_port": cot_input_http_port,
        "cot_output_http_url": cot_output_http_url,
        "http_client_cert": http_client_cert,
        "http_client_key": http_client_key,
        "http_ca_cert": http_ca_cert,
        "http_server_cert": http_server_cert,
        "http_server_key": http_server_key,
        "http_server_ca": http_server_ca,
        "group_override": group_override,
        "embed_federate_groups": embed_federate_groups == "true",
        "group_translation": group_translation,
        "simulator_enabled": simulator_enabled == "true",
        "simulator_interval": simulator_interval,
        "simulator_tracks": simulator_tracks,
        "simulator_direction": simulator_direction,
        "simulator_groups": simulator_groups,
        "virtual_chat_enabled": virtual_chat_enabled == "true",
        "virtual_chat_callsign": virtual_chat_callsign,
        "callsign_rewrite_out": callsign_rewrite_out,
        "callsign_rewrite_in": callsign_rewrite_in,
        "redact_out": redact_out,
        "redact_in": redact_in,
        "classify_access_out": classify_access_out,
        "classify_access_in": classify_access_in,
        "classify_remarks_out": classify_remarks_out,
        "classify_remarks_in": classify_remarks_in,
    }
    updated = store.update(bridge_id, fields)

    if updated and updated.enabled:
        await manager.restart_bridge_from_model(updated)
    return RedirectResponse(url=f"/bridges/{bridge_id}", status_code=303)


@app.post("/bridges/{bridge_id}/start")
async def start_bridge(bridge_id: str):
    cfg = store.set_enabled(bridge_id, True)
    if cfg:
        await manager.start_bridge_from_model(cfg)
    return RedirectResponse(url=f"/bridges/{bridge_id}", status_code=303)


@app.post("/bridges/{bridge_id}/stop")
async def stop_bridge(bridge_id: str):
    store.set_enabled(bridge_id, False)
    await manager.stop_bridge(bridge_id)
    return RedirectResponse(url=f"/bridges/{bridge_id}", status_code=303)


@app.post("/bridges/{bridge_id}/delete")
async def delete_bridge(bridge_id: str):
    await manager.stop_bridge(bridge_id)
    store.delete(bridge_id)
    return RedirectResponse(url="/", status_code=303)
