# SPDX-License-Identifier: AGPL-3.0-or-later
"""Round-trip tests for CoT XML <-> FederatedEvent protobuf conversion."""

import xml.etree.ElementTree as ET

from federation_bridge.cot_converter import (
    cot_xml_to_federated_event,
    federated_event_to_cot_xml,
    extract_contact_info,
    extract_federate_groups,
    parse_group_translation,
    translate_groups,
    cot_xml_to_contact_event,
)

_BASE = (
    '<event version="2.0" uid="U1" type="a-f-G-U-C" '
    'time="2026-01-01T00:00:00Z" start="2026-01-01T00:00:00Z" '
    'stale="2026-01-01T00:05:00Z" how="h-e"{access}>{point}'
    '<detail>{detail}</detail></event>'
)


def _event(point='<point lat="59.3" lon="18.0" hae="42" ce="9" le="9"/>',
           detail='<contact callsign="ALPHA"/>', access=""):
    return _BASE.format(point=point, detail=detail, access=access)


def _roundtrip(cot):
    return federated_event_to_cot_xml(cot_xml_to_federated_event(cot))


def test_point_roundtrips_values():
    pt = ET.fromstring(_roundtrip(_event())).find("point")
    assert float(pt.get("lat")) == 59.3
    assert float(pt.get("lon")) == 18.0
    assert float(pt.get("hae")) == 42.0


def test_zeroed_point_roundtrips_complete():
    # Regression: a fully-zeroed point must come back complete (all five coords
    # present), not partially dropped.
    pt = ET.fromstring(_roundtrip(_event(
        point='<point lat="0" lon="0" hae="0" ce="0" le="0"/>'))).find("point")
    assert pt is not None
    for attr in ("lat", "lon", "hae", "ce", "le"):
        assert float(pt.get(attr)) == 0.0


def test_callsign_roundtrips_via_screenname():
    ev = cot_xml_to_federated_event(_event(detail='<contact callsign="BRAVO"/>'))
    assert ev.event.screenName == "BRAVO"
    out = ET.fromstring(federated_event_to_cot_xml(ev))
    assert out.find("detail/contact").get("callsign") == "BRAVO"


def test_access_caveat_releasable_roundtrip():
    cot = _event(access=' access="S3CRET" caveat="NF" releasableTo="SWE"')
    ev = cot_xml_to_federated_event(cot)
    assert ev.event.access == "S3CRET"
    assert ev.event.caveat == "NF"
    assert ev.event.releaseableTo == "SWE"
    out = ET.fromstring(federated_event_to_cot_xml(ev))
    assert out.get("access") == "S3CRET"
    assert out.get("caveat") == "NF"
    assert out.get("releasableTo") == "SWE"


def test_marti_dest_promoted_to_ptp_and_rematerialized():
    cot = _event(detail='<contact callsign="A"/>'
                        '<marti><dest uid="UID-9"/><dest callsign="CS-9"/></marti>')
    ev = cot_xml_to_federated_event(cot)
    assert list(ev.event.ptpUids) == ["UID-9"]
    assert list(ev.event.ptpCallsigns) == ["CS-9"]
    dests = ET.fromstring(federated_event_to_cot_xml(ev)).findall("detail/marti/dest")
    assert "UID-9" in {d.get("uid") for d in dests}
    assert "CS-9" in {d.get("callsign") for d in dests}


def test_track_speed_course_roundtrip():
    cot = _event(detail='<contact callsign="A"/><track speed="5.5" course="180"/>')
    ev = cot_xml_to_federated_event(cot)
    assert ev.event.speed == 5.5
    assert ev.event.course == 180.0
    track = ET.fromstring(federated_event_to_cot_xml(ev)).find("detail/track")
    assert float(track.get("speed")) == 5.5
    assert float(track.get("course")) == 180.0


def test_extract_contact_info():
    assert extract_contact_info(_event(detail='<contact callsign="ZULU"/>')) == {
        "uid": "U1", "callsign": "ZULU"}
    assert extract_contact_info(_event(detail="<remarks>x</remarks>")) is None


def test_group_embed_extract_and_translation():
    ev = cot_xml_to_federated_event(_event())
    ev.federateGroups[:] = ["ALPHA"]
    out = federated_event_to_cot_xml(ev, embed_groups=True, group_map={"ALPHA": "blue"})
    assert extract_federate_groups(out) == ["blue"]
    # translation helpers (CoT name -> FedHub name)
    assert translate_groups(["blue"], parse_group_translation("blue = ALPHA")) == ["ALPHA"]


def test_contact_event_built_from_pli():
    ev = cot_xml_to_contact_event(_event(detail='<contact callsign="C"/>'))
    assert ev is not None
    assert ev.contact.uid == "U1"
    assert ev.contact.callsign == "C"


def test_invalid_xml_raises():
    import pytest
    with pytest.raises(ET.ParseError):
        cot_xml_to_federated_event("not xml")
