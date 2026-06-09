# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the per-direction CoT transformation layer."""

import xml.etree.ElementTree as ET

from federation_bridge.cot_transform import CotTransformer


def _cot(access_attr="", remarks="hello", callsign="ALPHA"):
    access = f' access="{access_attr}"' if access_attr else ""
    return (
        '<?xml version="1.0"?>'
        f'<event version="2.0" uid="x" type="a-f-G"{access}>'
        '<point lat="59.3" lon="18.0" hae="10" ce="5" le="5"/>'
        '<detail>'
        f'<contact callsign="{callsign}" endpoint="1.2.3.4:4242:tcp"/>'
        '<takv device="dev" version="5"/>'
        '<_flow-tags_ a="1"/>'
        '<__chat senderCallsign="BRAVO"><chatgrp/></__chat>'
        '<marti><dest callsign="CHARLIE"/></marti>'
        f'<remarks>{remarks}</remarks>'
        '</detail></event>'
    )


def _parse(xml):
    return ET.fromstring(xml)


# -- callsign rewrite ---------------------------------------------------------

def test_add_suffix_on_all_callsign_nodes():
    out = _parse(CotTransformer(callsign_rewrite="add-suffix:@AREA1").apply(_cot()))
    assert out.find("detail/contact").get("callsign") == "ALPHA@AREA1"
    assert out.find("detail/__chat").get("senderCallsign") == "BRAVO@AREA1"
    assert out.find("detail/marti/dest").get("callsign") == "CHARLIE@AREA1"


def test_add_prefix():
    out = _parse(CotTransformer(callsign_rewrite="add-prefix:@A_").apply(_cot(callsign="UNIT")))
    assert out.find("detail/contact").get("callsign") == "@A_UNIT"


def test_strip_suffix_roundtrips_add():
    added = CotTransformer(callsign_rewrite="add-suffix:@AREA1").apply(_cot())
    out = _parse(CotTransformer(callsign_rewrite="strip-suffix:@AREA1").apply(added))
    assert out.find("detail/contact").get("callsign") == "ALPHA"
    assert out.find("detail/marti/dest").get("callsign") == "CHARLIE"


def test_strip_prefix():
    out = _parse(CotTransformer(callsign_rewrite="strip-prefix:@A_").apply(_cot(callsign="@A_UNIT")))
    assert out.find("detail/contact").get("callsign") == "UNIT"


def test_add_suffix_is_idempotent():
    once = CotTransformer(callsign_rewrite="add-suffix:@AREA1").apply(_cot())
    twice = CotTransformer(callsign_rewrite="add-suffix:@AREA1").apply(once)
    assert _parse(twice).find("detail/contact").get("callsign") == "ALPHA@AREA1"


# -- redaction ----------------------------------------------------------------

def test_remove_elements():
    out = _parse(CotTransformer(redact="-takv, -_flow-tags_").apply(_cot()))
    assert out.find("detail/takv") is None
    assert out.find("detail/_flow-tags_") is None
    # untouched siblings remain
    assert out.find("detail/contact") is not None


def test_remove_attribute():
    out = _parse(CotTransformer(redact="-contact@endpoint").apply(_cot()))
    assert out.find("detail/contact").get("endpoint") is None
    assert out.find("detail/contact").get("callsign") == "ALPHA"


def test_set_attribute():
    out = _parse(CotTransformer(redact="point@hae=0").apply(_cot()))
    assert out.find("point").get("hae") == "0"
    assert out.find("point").get("lat") == "59.3"  # other coords untouched


def test_zero_point():
    out = _parse(CotTransformer(redact="zero-point").apply(_cot()))
    pt = out.find("point")
    assert (pt.get("lat"), pt.get("lon"), pt.get("hae"), pt.get("ce"), pt.get("le")) == ("0", "0", "0", "0", "0")


def test_zero_point_adds_missing_coords_for_valid_cot():
    # A source <point> missing hae/ce/le must come out complete and zeroed,
    # not as a half-populated (invalid) point.
    partial = (
        '<event version="2.0" uid="x" type="a-f-G">'
        '<point lat="59.3" lon="18.0"/><detail/></event>'
    )
    pt = _parse(CotTransformer(redact="zero-point").apply(partial)).find("point")
    assert (pt.get("lat"), pt.get("lon"), pt.get("hae"), pt.get("ce"), pt.get("le")) == ("0", "0", "0", "0", "0")


# -- classification -----------------------------------------------------------

def test_classify_access_when_missing():
    out = _parse(CotTransformer(classify_access="S3CRET").apply(_cot(access_attr="")))
    assert out.get("access") == "S3CRET"


def test_classify_access_when_undefined():
    out = _parse(CotTransformer(classify_access="S3CRET").apply(_cot(access_attr="Undefined")))
    assert out.get("access") == "S3CRET"


def test_classify_access_does_not_override_existing():
    out = _parse(CotTransformer(classify_access="S3CRET").apply(_cot(access_attr="TOPSECRET")))
    assert out.get("access") == "TOPSECRET"


def test_classify_remarks_override_matches():
    t = CotTransformer(classify_access="S3CRET", classify_remarks="#UNCLASS=UNCLASSIFIED")
    out = _parse(t.apply(_cot(access_attr="", remarks="#UNCLASS please downgrade")))
    assert out.get("access") == "UNCLASSIFIED"


def test_classify_remarks_no_match_keeps_default():
    t = CotTransformer(classify_access="S3CRET", classify_remarks="#UNCLASS=UNCLASSIFIED")
    out = _parse(t.apply(_cot(access_attr="", remarks="ordinary text")))
    assert out.get("access") == "S3CRET"


# -- robustness / wiring ------------------------------------------------------

def test_invalid_xml_passes_through_unchanged():
    assert CotTransformer(redact="-takv").apply("not xml at all") == "not xml at all"


def test_disabled_transformer_is_identity_and_not_enabled():
    t = CotTransformer()
    assert t.enabled() is False
    sample = _cot()
    assert t.apply(sample) == sample


def test_enabled_true_when_any_configured():
    assert CotTransformer(callsign_rewrite="add-suffix:@X").enabled() is True
    assert CotTransformer(redact="-takv").enabled() is True
    assert CotTransformer(classify_access="S").enabled() is True
    assert CotTransformer(classify_remarks="#U=UNCLASS").enabled() is True


def test_combined_transforms_apply_together():
    t = CotTransformer(
        callsign_rewrite="add-suffix:@AREA1",
        redact="-takv, zero-point",
        classify_access="S3CRET",
    )
    out = _parse(t.apply(_cot(access_attr="Undefined")))
    assert out.find("detail/contact").get("callsign") == "ALPHA@AREA1"
    assert out.find("detail/takv") is None
    assert out.find("point").get("lat") == "0"
    assert out.get("access") == "S3CRET"
