# SPDX-License-Identifier: AGPL-3.0-or-later
"""Convert between CoT XML and FederatedEvent protobuf.

Mirrors TAKserver's tak.server.federation.ProtoBufHelper so the CoT we emit
on the FederatedEvent stream — and the CoT we reconstruct from it — is wire
compatible with another TAKserver's input port.

Key invariants:
  * GeoEvent.other holds the *complete* <detail> element as XML (i.e. starts
    with the <detail> tag). On encode we serialize the cleaned <detail> in
    full; on decode we parse it and use its root as the event's <detail>,
    then re-attach the typed fields TAKserver pulls out (track, etc.). This
    avoids producing nested <detail><detail/></detail> or duplicated children.
  * GeoChat routing depends on FederatedEvent.contact (ContactListEntry with
    CRUD.CREATE) being sent for every UID that should be reachable for chat.
    Without it the receiving TAKserver has no clientUid → subscription
    mapping and GeoChat to that UID fails with `b-t-f-s` (delivery failure).
    See `cot_xml_to_contact_event`.
  * Explicit chat destinations (<marti><dest uid|callsign/></marti>) must be
    promoted to GeoEvent.ptpUids / ptpCallsigns; TAKserver re-injects those
    as EXPLICIT_UID_KEY / EXPLICIT_CALLSIGN_KEY on receive so the chat is
    routed to the specific subscription rather than being dropped.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

from .proto_gen import fig_pb2


def _iso_to_ms(iso_str: str) -> int:
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def _ms_to_iso(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# Sentinel strings that some upstream JS-based senders emit when serializing
# an unset variable (`undefined` / `null`) directly into XML. Treat them as
# missing so we don't propagate the noise through federation.
_PLACEHOLDER_VALUES = frozenset({"undefined", "null"})


def _clean(value: Optional[str]) -> Optional[str]:
    """Return the value, or None if it is missing/empty/a known placeholder."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped.lower() in _PLACEHOLDER_VALUES:
        return None
    return value


def extract_contact_info(cot_xml: str) -> Optional[dict]:
    """Return {'uid', 'callsign'} if the CoT looks like a contact-bearing
    message (PLI/SA with a callsign), else None. Used by the bridge to
    decide when to emit a ContactListEntry CREATE on the federation stream.
    """
    try:
        root = ET.fromstring(cot_xml)
    except ET.ParseError:
        return None

    uid = root.get("uid", "")
    if not uid:
        return None

    detail = root.find("detail")
    if detail is None:
        return None
    contact = detail.find("contact")
    if contact is None:
        return None
    callsign = contact.get("callsign", "")
    if not callsign:
        return None

    return {"uid": uid, "callsign": callsign}


def extract_federate_groups(cot_xml: str) -> list[str]:
    """Return the federation groups carried in a <__fedhubgroups groups=...>
    element, or [] if the element is absent or the XML is unparseable.

    This is the counterpart to the embedding done by
    `federated_event_to_cot_xml(embed_groups=True)`. The element is a
    non-standard extension to CoT, opt-in per bridge, used to carry
    FederatedEvent.federateGroups across the CoT hop between two bridges so
    routing groups survive the protobuf -> CoT -> protobuf round-trip
    (otherwise they are lost, since CoT has no native field for them).
    Supports multiple groups via comma-separation.
    """
    try:
        root = ET.fromstring(cot_xml)
    except ET.ParseError:
        return []
    detail = root.find("detail")
    if detail is None:
        return []
    fg = detail.find("__fedhubgroups")
    if fg is None:
        return []
    raw = fg.get("groups", "")
    return [g.strip() for g in raw.split(",") if g.strip()]


def parse_group_translation(spec: str) -> dict[str, str]:
    """Parse a group translation table into a {cot_group: fedhub_group} dict.

    The spec is a free-form list of ``cot_name = fedhub_name`` pairs separated
    by newlines and/or commas, e.g.::

        blue = ALPHA
        green = BRAVO

    The left side is the group name as it travels in CoT (the value carried in
    the <__fedhubgroups> element); the right side is the group name used in
    FedHub (protobuf federateGroups). Whitespace around names/entries is
    ignored and malformed entries (missing '=') are skipped.
    """
    mapping: dict[str, str] = {}
    if not spec:
        return mapping
    for chunk in spec.replace("\n", ",").split(","):
        if "=" not in chunk:
            continue
        cot_name, fedhub_name = chunk.split("=", 1)
        cot_name, fedhub_name = cot_name.strip(), fedhub_name.strip()
        if cot_name and fedhub_name:
            mapping[cot_name] = fedhub_name
    return mapping


def translate_groups(groups, mapping: dict[str, str]) -> list[str]:
    """Translate each group name via ``mapping``, passing names not present in
    the table through unchanged. Order and duplicates are preserved."""
    if not mapping:
        return list(groups)
    return [mapping.get(g, g) for g in groups]


def cot_xml_to_contact_event(
    cot_xml: str,
    operation: int = fig_pb2.CREATE,
    federate_groups: Optional[list[str]] = None,
) -> Optional[fig_pb2.FederatedEvent]:
    """Build a FederatedEvent carrying a ContactListEntry (CRUD operation).

    Returns None if the CoT XML lacks the required uid/callsign fields.
    Mirrors TAKserver's `DistributedFederationManager.addLocalContact`,
    which emits this message so peer TAKservers can register
    clientUid → federate-subscription for chat routing.
    """
    info = extract_contact_info(cot_xml)
    if info is None:
        return None

    contact = fig_pb2.ContactListEntry(
        operation=operation,
        uid=info["uid"],
        callsign=info["callsign"],
    )
    event = fig_pb2.FederatedEvent(contact=contact)
    if federate_groups:
        event.federateGroups[:] = federate_groups
    return event


def build_delete_contact_event(
    uid: str,
    callsign: str,
    federate_groups: Optional[list[str]] = None,
) -> fig_pb2.FederatedEvent:
    """Build a FederatedEvent with a CRUD.DELETE ContactListEntry."""
    contact = fig_pb2.ContactListEntry(
        operation=fig_pb2.DELETE,
        uid=uid,
        callsign=callsign,
    )
    event = fig_pb2.FederatedEvent(contact=contact)
    if federate_groups:
        event.federateGroups[:] = federate_groups
    return event


def cot_xml_to_federated_event(cot_xml: str) -> fig_pb2.FederatedEvent:
    """Parse CoT XML string and build a FederatedEvent protobuf message."""
    root = ET.fromstring(cot_xml)

    geo = fig_pb2.GeoEvent()
    geo.uid = root.get("uid", "")
    geo.type = root.get("type", "")
    geo.coordSource = root.get("how", "")
    geo.sendTime = _iso_to_ms(root.get("time", "1970-01-01T00:00:00Z"))
    geo.startTime = _iso_to_ms(root.get("start", root.get("time", "1970-01-01T00:00:00Z")))
    geo.staleTime = _iso_to_ms(root.get("stale", root.get("time", "1970-01-01T00:00:00Z")))

    access = _clean(root.get("access"))
    if access:
        geo.access = access
    caveat = _clean(root.get("caveat"))
    if caveat:
        geo.caveat = caveat
    releasable_to = _clean(root.get("releasableTo"))
    if releasable_to:
        geo.releaseableTo = releasable_to

    point = root.find("point")
    if point is not None:
        geo.lat = float(point.get("lat", 0))
        geo.lon = float(point.get("lon", 0))
        geo.hae = float(point.get("hae", 999999))
        geo.ce = float(point.get("ce", 999999))
        geo.le = float(point.get("le", 999999))

    detail = root.find("detail")
    if detail is not None:
        contact = detail.find("contact")
        if contact is not None:
            geo.screenName = contact.get("callsign", "")

        group_el = detail.find("__group")
        if group_el is not None:
            geo.groupName = group_el.get("name", "")
            geo.groupRole = group_el.get("role", "")

        # Strip our non-standard federation-group carrier so it never ends up
        # in geo.other (and thus never leaks into the CoT we forward onward).
        # The bridge reads these groups separately via extract_federate_groups
        # and applies them to FederatedEvent.federateGroups when group carrying
        # is enabled.
        fedgroups_el = detail.find("__fedhubgroups")
        if fedgroups_el is not None:
            detail.remove(fedgroups_el)

        track_el = detail.find("track")
        if track_el is not None:
            geo.speed = float(track_el.get("speed", 0))
            geo.course = float(track_el.get("course", 0))
            # TAKserver removes <track> from <detail> before setting other,
            # then re-adds it from typed fields on the receive side. We must
            # do the same or the receiver will see two <track> elements.
            detail.remove(track_el)

        # Promote <marti><dest uid|callsign/></marti> to ptpUids/ptpCallsigns
        # so the receiving TAKserver routes chat (b-t-f, b-t-f-d, …) to the
        # specific destination subscription via EXPLICIT_UID_KEY /
        # EXPLICIT_CALLSIGN_KEY. TAKserver itself strips <marti> from
        # detail in StreamingEndpointRewriteFilter before federating, so we
        # mirror that here to avoid the receiving side seeing a stale
        # <marti> element after proto2cot rebuilds the detail.
        marti_el = detail.find("marti")
        if marti_el is not None:
            for dest in marti_el.findall("dest"):
                dest_uid = dest.get("uid")
                dest_callsign = dest.get("callsign")
                dest_mission = dest.get("mission")
                if dest_uid:
                    geo.ptpUids.append(dest_uid)
                if dest_callsign:
                    geo.ptpCallsigns.append(dest_callsign)
                if dest_mission:
                    geo.missionNames.append(dest_mission)
            detail.remove(marti_el)

        # geo.other carries the entire <detail> element (including the
        # <detail> tag) — matches TAKserver's detailE.asXML().
        geo.other = ET.tostring(detail, encoding="unicode")

    event = fig_pb2.FederatedEvent()
    event.event.CopyFrom(geo)
    return event


def federated_event_to_cot_xml(
    event: fig_pb2.FederatedEvent,
    embed_groups: bool = False,
    group_map: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """Convert a FederatedEvent protobuf message to CoT XML string.

    Returns None for contact-only messages (FederatedEvent.contact set,
    no event) since those carry no CoT payload — they exist purely to
    manage federation routing state on the peer TAKserver.

    When ``embed_groups`` is True, the event's federateGroups are written
    into a non-standard <__fedhubgroups groups="a,b,c"/> detail element so a
    peer bridge can restore them with extract_federate_groups(). This is
    opt-in per bridge because it deviates from the CoT standard; consumers
    that aren't another federation bridge simply ignore the unknown element.

    ``group_map`` (FedHub name -> CoT name) translates each group before it is
    embedded, so the wire carries the operator-chosen CoT-side name. Names not
    in the map pass through unchanged.
    """
    if not event.HasField("event"):
        return None

    geo = event.event

    root = ET.Element("event")
    root.set("version", "2.0")
    root.set("uid", geo.uid)
    root.set("type", geo.type)
    root.set("how", geo.coordSource)
    root.set("time", _ms_to_iso(geo.sendTime))
    root.set("start", _ms_to_iso(geo.startTime))
    root.set("stale", _ms_to_iso(geo.staleTime))

    access = _clean(geo.access)
    if access:
        root.set("access", access)
    caveat = _clean(geo.caveat)
    if caveat:
        root.set("caveat", caveat)
    releasable_to = _clean(geo.releaseableTo)
    if releasable_to:
        root.set("releasableTo", releasable_to)

    point = ET.SubElement(root, "point")
    point.set("lat", str(geo.lat))
    point.set("lon", str(geo.lon))
    point.set("hae", str(geo.hae))
    point.set("ce", str(geo.ce))
    point.set("le", str(geo.le))

    # Mirror TAKserver's proto2cot: geo.other is the full <detail> element.
    # Parse it and use its root as the event's <detail>, then re-attach
    # typed fields (track) that the encoder stripped out. Do NOT rebuild
    # <contact>/<__group> from typed fields — TAKserver leaves them inside
    # geo.other, so re-adding them would duplicate them and (combined with
    # a wrapper merge) produced the nested <detail> we just fixed.
    detail = None
    if geo.other:
        try:
            parsed = ET.fromstring(geo.other)
            if parsed.tag == "detail":
                detail = parsed
            else:
                # Defensive: some senders may have stored detail children
                # without the wrapping <detail> tag. Wrap them.
                detail = ET.Element("detail")
                detail.append(parsed)
        except ET.ParseError:
            detail = None

    if detail is None:
        detail = ET.Element("detail")
        # No upstream <detail> available — fall back to rebuilding from
        # typed fields so consumers still get callsign/group.
        if geo.screenName:
            ET.SubElement(detail, "contact", {"callsign": geo.screenName})
        if geo.groupName:
            attrs = {"name": geo.groupName}
            if geo.groupRole:
                attrs["role"] = geo.groupRole
            ET.SubElement(detail, "__group", attrs)

    if (geo.speed or geo.course) and detail.find("track") is None:
        ET.SubElement(
            detail,
            "track",
            {"speed": str(geo.speed), "course": str(geo.course)},
        )

    # Re-materialize <marti> from typed fields so consuming systems that
    # parse marti/dest (rather than the proto-only ptp* fields) still see
    # the explicit chat destinations.
    if geo.ptpUids or geo.ptpCallsigns or geo.missionNames:
        marti = detail.find("marti")
        if marti is None:
            marti = ET.SubElement(detail, "marti")
        for uid in geo.ptpUids:
            ET.SubElement(marti, "dest", {"uid": uid})
        for callsign in geo.ptpCallsigns:
            ET.SubElement(marti, "dest", {"callsign": callsign})
        for mission in geo.missionNames:
            ET.SubElement(marti, "dest", {"mission": mission})

    # Carry FedHub federation groups across the CoT hop (opt-in). CoT has no
    # native field for them, so without this they are lost between two bridges
    # — forcing a manual group_override on the receiving side. Supports
    # multiple groups (repeated federateGroups) via comma-separation.
    if embed_groups and event.federateGroups:
        out_groups = translate_groups(event.federateGroups, group_map or {})
        fg = detail.find("__fedhubgroups")
        if fg is None:
            fg = ET.SubElement(detail, "__fedhubgroups")
        fg.set("groups", ",".join(out_groups))

    root.append(detail)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
