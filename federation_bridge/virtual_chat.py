# SPDX-License-Identifier: AGPL-3.0-or-later
"""Virtual Chat User support.

A bridge with the Virtual Chat User enabled announces a single stationary
contact into the federation (a PLI at lat/lon 0,0, exactly like the simulator's
tracks become real contacts). Because it is announced as a contact
(ContactListEntry CREATE, see cot_converter.cot_xml_to_contact_event), ATAK
clients can select it from their contact list and send it a *direct* chat.

When such a direct chat arrives back on the bridge's inbound stream, we rewrite
it from a point-to-point GeoChat into a *broadcast* GeoChat ("All Chat Rooms")
and emit it on the bridge's CoT output. Net effect: messaging the virtual
contact broadcasts to everyone.
"""

import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Optional

# Standard ATAK broadcast chatroom. A GeoChat with id/chatroom == this and the
# matching chatgrp is delivered to every connected client as a broadcast.
BROADCAST_ROOM = "All Chat Rooms"

# CoT type for the virtual contact's presence (friendly ground unit), matching
# the simulator so it is rendered/handled identically by TAKserver/ATAK.
VIRTUAL_PLI_TYPE = "a-f-G-U-C"


def virtual_uid(bridge_id: str) -> str:
    """Stable, unique UID for a bridge's virtual chat contact."""
    return f"fedhub-vcu-{bridge_id}"


def default_callsign(bridge_name: str) -> str:
    """Default callsign when the operator hasn't set one."""
    return f"fedhub-broadcast-chat-{bridge_name}"


def build_virtual_pli_xml(uid: str, callsign: str,
                          lat: float = 0.0, lon: float = 0.0,
                          stale_minutes: int = 5) -> str:
    """Build a stationary PLI CoT for the virtual contact.

    Carries <contact callsign endpoint> so TAKserver registers it as an
    addressable contact (the bridge additionally emits a ContactListEntry
    CREATE for it via the normal outbound path).
    """
    now = datetime.now(timezone.utc)
    stale = now + timedelta(minutes=stale_minutes)
    time_str = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    stale_str = stale.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # platform="ATAK-CIV" is deliberate: TAK Server only routes inbound direct
    # messages to clients it considers "chat-capable" and silently drops DMs to
    # other platforms (documented in terminaltak's BuildPLI). The virtual
    # contact MUST receive DMs to do its job, so it advertises as ATAK-CIV.
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<event version="2.0" uid="{uid}" type="{VIRTUAL_PLI_TYPE}" '
        f'time="{time_str}" start="{time_str}" stale="{stale_str}" how="m-g">'
        f'<point lat="{lat:.6f}" lon="{lon:.6f}" hae="0" ce="9999999" le="9999999"/>'
        f'<detail>'
        f'<contact callsign="{callsign}" endpoint="*:-1:stcp"/>'
        f'<__group name="Cyan" role="Team Member"/>'
        f'<takv device="Federation Bridge" platform="ATAK-CIV" os="linux" version="5.4.0.29"/>'
        f'<uid Droid="{callsign}"/>'
        f'<status battery="100"/>'
        f'<track speed="0" course="0"/>'
        f'<precisionlocation altsrc="USER" geopointsrc="USER"/>'
        f'</detail>'
        f'</event>'
    )


def is_chat_targeting_virtual(geo, uid: str, callsign: str) -> bool:
    """True if a GeoEvent is a direct chat addressed to the virtual contact.

    Direct chats federate with the destination promoted to ptpUids /
    ptpCallsigns (see cot_converter.cot_xml_to_federated_event), so we match on
    either the virtual UID or its callsign.
    """
    if not (geo.type or "").startswith("b-t-f"):
        return False
    return uid in geo.ptpUids or callsign in geo.ptpCallsigns


def rewrite_chat_to_broadcast(cot_xml: str, virtual_uid: str,
                              virtual_callsign: str,
                              broadcast_room: str = BROADCAST_ROOM) -> str:
    """Rewrite a point-to-point GeoChat CoT into a broadcast GeoChat.

    Retains the original sender, message text and position; only the
    destination/room is changed to the broadcast room. Returns the input
    unchanged if it cannot be parsed (never drop a message).
    """
    try:
        root = ET.fromstring(cot_xml)
    except ET.ParseError:
        return cot_xml

    detail = root.find("detail")
    if detail is None:
        return cot_xml

    # Determine the sender UID for the rebuilt GeoChat event uid.
    sender_uid = ""
    chat = detail.find("__chat")
    if chat is not None:
        chatgrp = chat.find("chatgrp")
        if chatgrp is not None:
            sender_uid = chatgrp.get("uid0", "")
    if not sender_uid:
        link = detail.find("link")
        if link is not None:
            sender_uid = link.get("uid", "")

    # __chat: point the room at the broadcast room.
    if chat is not None:
        chat.set("chatroom", broadcast_room)
        chat.set("id", broadcast_room)
        chat.set("parent", "RootContactGroup")
        chatgrp = chat.find("chatgrp")
        if chatgrp is not None:
            chatgrp.set("uid1", broadcast_room)
            chatgrp.set("id", broadcast_room)

    # remarks: address the broadcast room.
    remarks = detail.find("remarks")
    if remarks is not None:
        remarks.set("to", broadcast_room)

    # A broadcast GeoChat carries NO <marti> element — ATAK (and terminaltak's
    # BuildGeoChat) only add <marti><dest callsign/> for direct messages. The
    # server adds its own __serverdestination on forward. So we strip the
    # point-to-point <marti> rather than rewriting it.
    marti = detail.find("marti")
    if marti is not None:
        detail.remove(marti)

    # Rebuild the GeoChat event uid so it reflects the broadcast destination.
    if sender_uid:
        msg_id = str(uuid.uuid4())
        root.set("uid", f"GeoChat.{sender_uid}.{broadcast_room}.{msg_id}")

    return ET.tostring(root, encoding="unicode", xml_declaration=True)
