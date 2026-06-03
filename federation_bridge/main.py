# SPDX-License-Identifier: AGPL-3.0-or-later
"""Federation Bridge Manager - FastAPI application."""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import FEDHUB_DEFAULT_ADDRESS, FEDHUB_DEFAULT_PORT
from .db import init_db, get_session, async_session
from .models import Bridge as BridgeModel
from .bridge import BridgeStatus
from .bridge_manager import BridgeManager

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("federation-bridge")

manager = BridgeManager()

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with async_session() as session:
        await manager.load_and_start_saved(session)
    logger.info("Federation Bridge Manager started")
    yield
    await manager.shutdown_all()
    logger.info("Federation Bridge Manager stopped")


app = FastAPI(title="Federation Bridge Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(BridgeModel))
    bridges = result.scalars().all()
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
    session: AsyncSession = Depends(get_session),
):
    bridge = BridgeModel(
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
    )
    session.add(bridge)
    await session.commit()
    await session.refresh(bridge)
    await manager.start_bridge_from_model(bridge)
    return RedirectResponse(url="/", status_code=303)


@app.get("/bridges/{bridge_id}", response_class=HTMLResponse)
async def bridge_detail(request: Request, bridge_id: str,
                        session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(BridgeModel).where(BridgeModel.id == bridge_id))
    bridge = result.scalar_one_or_none()
    if not bridge:
        return RedirectResponse(url="/")
    runtime = manager.get_bridge(bridge_id)
    status = runtime.status if runtime else BridgeStatus.STOPPED
    return templates.TemplateResponse(request, "bridge_detail.html", {
        "bridge": bridge, "status": status,
    })


@app.get("/bridges/{bridge_id}/edit", response_class=HTMLResponse)
async def edit_bridge_form(request: Request, bridge_id: str,
                           session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(BridgeModel).where(BridgeModel.id == bridge_id))
    bridge = result.scalar_one_or_none()
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
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(BridgeModel).where(BridgeModel.id == bridge_id))
    bridge = result.scalar_one_or_none()
    if not bridge:
        return RedirectResponse(url="/", status_code=303)

    bridge.name = name
    bridge.description = description
    bridge.fedhub_address = fedhub_address
    bridge.fedhub_port = fedhub_port
    # Empty jwt_token in form = keep current secret. Avoids forcing the
    # operator to re-paste the token for every cosmetic edit.
    if jwt_token.strip():
        bridge.jwt_token = jwt_token
    bridge.cot_output_host = cot_output_host
    bridge.cot_output_port = cot_output_port
    bridge.cot_output_protocol = cot_output_protocol
    bridge.cot_input_port = cot_input_port
    bridge.cot_input_udp_port = cot_input_udp_port
    bridge.group_override = group_override
    bridge.embed_federate_groups = embed_federate_groups == "true"
    bridge.group_translation = group_translation
    bridge.simulator_enabled = simulator_enabled == "true"
    bridge.simulator_interval = simulator_interval
    bridge.simulator_tracks = simulator_tracks
    bridge.simulator_direction = simulator_direction
    bridge.simulator_groups = simulator_groups
    bridge.virtual_chat_enabled = virtual_chat_enabled == "true"
    bridge.virtual_chat_callsign = virtual_chat_callsign
    await session.commit()
    await session.refresh(bridge)

    if bridge.enabled:
        await manager.restart_bridge_from_model(bridge)
    return RedirectResponse(url=f"/bridges/{bridge_id}", status_code=303)


@app.post("/bridges/{bridge_id}/start")
async def start_bridge(bridge_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(BridgeModel).where(BridgeModel.id == bridge_id))
    bridge = result.scalar_one_or_none()
    if bridge:
        bridge.enabled = True
        await session.commit()
        await manager.start_bridge_from_model(bridge)
    return RedirectResponse(url=f"/bridges/{bridge_id}", status_code=303)


@app.post("/bridges/{bridge_id}/stop")
async def stop_bridge(bridge_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(BridgeModel).where(BridgeModel.id == bridge_id))
    bridge = result.scalar_one_or_none()
    if bridge:
        bridge.enabled = False
        await session.commit()
        await manager.stop_bridge(bridge_id)
    return RedirectResponse(url=f"/bridges/{bridge_id}", status_code=303)


@app.post("/bridges/{bridge_id}/delete")
async def delete_bridge(bridge_id: str, session: AsyncSession = Depends(get_session)):
    await manager.stop_bridge(bridge_id)
    result = await session.execute(select(BridgeModel).where(BridgeModel.id == bridge_id))
    bridge = result.scalar_one_or_none()
    if bridge:
        await session.delete(bridge)
        await session.commit()
    return RedirectResponse(url="/", status_code=303)
