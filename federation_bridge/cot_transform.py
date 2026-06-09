# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configurable CoT XML transformations applied at the bridge's CoT boundary.

These mirror the behaviour proven in the standalone udp2post.rb / post2udp.rb
sidecars that previously fed a Cross Domain Solution (CDS):

  * callsign affixing  — append/prepend (or strip) a configurable marker on
    callsigns so operators can tell which domain a track originates from
    (GitHub issue #2).
  * redaction          — remove or rewrite elements/attributes that should not
    cross the boundary, e.g. drop <takv>/<_flow-tags_> or zero out a <point>
    (GitHub issue #3).
  * classification     — stamp the event/@access attribute from a default and/or
    a "//remarks starts with <preamble>" rule so a CDS guard can release the
    CoT per policy (GitHub issue #4).

A `CotTransformer` is direction-scoped: the bridge builds one for the egress
(FedHub -> CoT output) path and one for the ingress (CoT input -> FedHub) path,
each from its own per-bridge config, and applies it at the matching converter
chokepoint. Transforms operate on the raw CoT XML string with ElementTree, the
same library and defensive style used by cot_converter.py / virtual_chat.py.
"""

import logging
import xml.etree.ElementTree as ET
from typing import Optional

logger = logging.getLogger("federation-bridge.transform")

# Nodes whose callsign-bearing attribute the affix transform rewrites. Mirrors
# the //contact, //dest and //__chat selectors used by the Ruby reference.
_CALLSIGN_TARGETS = (
    ("contact", "callsign"),
    ("dest", "callsign"),
    ("__chat", "senderCallsign"),
)

_CALLSIGN_OPS = frozenset({"add-suffix", "add-prefix", "strip-suffix", "strip-prefix"})

# CoT considers the access attribute unset when absent or the literal
# "Undefined" placeholder TAK emits; classify_access fills it in those cases.
_UNSET_ACCESS = "Undefined"

# Point coordinate attributes zeroed by the `zero-point` redaction shorthand.
_POINT_COORDS = ("lat", "lon", "hae", "ce", "le")


def _split_directives(spec: str) -> list[str]:
    """Split a free-form directive list on newlines/commas, dropping blanks.

    Matches the lenient parsing of parse_group_translation in cot_converter.py.
    """
    if not spec:
        return []
    return [chunk.strip() for chunk in spec.replace("\n", ",").split(",") if chunk.strip()]


def _parse_callsign_rewrite(spec: str) -> Optional[tuple[str, str]]:
    """Parse `op:value` (e.g. ``add-suffix:@AREA1``) into (op, value) or None."""
    spec = (spec or "").strip()
    if not spec or ":" not in spec:
        return None
    op, value = spec.split(":", 1)
    op = op.strip().lower()
    if op not in _CALLSIGN_OPS or not value:
        return None
    return op, value


def _parse_redact(spec: str) -> list[tuple]:
    """Parse redaction directives into typed ops applied in order.

    Supported directive forms:
      ``-tag``         -> ("remove_element", tag)
      ``-tag@attr``    -> ("remove_attr", tag, attr)
      ``tag@attr=val`` -> ("set_attr", tag, attr, val)
      ``zero-point``   -> ("zero_point",)
    """
    ops: list[tuple] = []
    for directive in _split_directives(spec):
        if directive == "zero-point":
            ops.append(("zero_point",))
        elif directive.startswith("-"):
            target = directive[1:].strip()
            if "@" in target:
                tag, attr = target.split("@", 1)
                if tag.strip() and attr.strip():
                    ops.append(("remove_attr", tag.strip(), attr.strip()))
            elif target:
                ops.append(("remove_element", target))
        elif "@" in directive and "=" in directive:
            tag_attr, value = directive.split("=", 1)
            tag, attr = tag_attr.split("@", 1)
            if tag.strip() and attr.strip():
                ops.append(("set_attr", tag.strip(), attr.strip(), value.strip()))
        else:
            logger.warning("Ignoring unrecognised redact directive: %r", directive)
    return ops


def _parse_classify_remarks(spec: str) -> Optional[tuple[str, str]]:
    """Parse `preamble=ACCESS` (e.g. ``#UNCLASS=UNCLASSIFIED``) -> (preamble, access)."""
    spec = (spec or "").strip()
    if "=" not in spec:
        return None
    preamble, access = spec.split("=", 1)
    preamble, access = preamble.strip(), access.strip()
    if not preamble or not access:
        return None
    return preamble, access


class CotTransformer:
    """Applies the configured callsign/redaction/classification transforms.

    All parameters are optional; an unset transform is simply skipped. Parsing
    happens once at construction so `apply` stays cheap on the hot path.
    """

    def __init__(self, callsign_rewrite: str = "", redact: str = "",
                 classify_access: str = "", classify_remarks: str = ""):
        self._callsign = _parse_callsign_rewrite(callsign_rewrite)
        self._redact = _parse_redact(redact)
        self._classify_access = (classify_access or "").strip()
        self._classify_remarks = _parse_classify_remarks(classify_remarks)

    def enabled(self) -> bool:
        """True if any transform is configured (lets callers skip a no-op parse)."""
        return bool(
            self._callsign
            or self._redact
            or self._classify_access
            or self._classify_remarks
        )

    def apply(self, cot_xml: str) -> str:
        """Return the transformed CoT XML, or the input unchanged on parse error."""
        if not self.enabled():
            return cot_xml
        try:
            root = ET.fromstring(cot_xml)
        except ET.ParseError as e:
            logger.warning("CoT transform skipped, unparseable XML: %s", e)
            return cot_xml

        if self._redact:
            self._apply_redaction(root)
        if self._classify_access or self._classify_remarks:
            self._apply_classification(root)
        if self._callsign:
            self._apply_callsign(root)

        return ET.tostring(root, encoding="unicode", xml_declaration=True)

    # -- individual transforms ------------------------------------------------

    def _apply_redaction(self, root: ET.Element) -> None:
        # ElementTree has no parent pointers; build a child->parent map so
        # descendant elements can be removed regardless of nesting depth
        # (mirrors Nokogiri's //tag .remove).
        parents = {child: parent for parent in root.iter() for child in parent}
        for op in self._redact:
            kind = op[0]
            if kind == "remove_element":
                tag = op[1]
                for el in list(root.iter(tag)):
                    parent = parents.get(el)
                    if parent is not None:
                        parent.remove(el)
            elif kind == "remove_attr":
                _, tag, attr = op
                for el in root.iter(tag):
                    el.attrib.pop(attr, None)
            elif kind == "set_attr":
                _, tag, attr, value = op
                for el in root.iter(tag):
                    el.set(attr, value)
            elif kind == "zero_point":
                # Set ALL five coordinates, not only the ones already present.
                # CoT requires lat/lon/hae/ce/le on <point>; zeroing just the
                # existing attributes would leave an invalid point whenever the
                # source omitted any of them.
                for el in root.iter("point"):
                    for coord in _POINT_COORDS:
                        el.set(coord, "0")

    def _apply_classification(self, root: ET.Element) -> None:
        events = list(root.iter("event"))
        if self._classify_access:
            for event in events:
                access = event.get("access")
                if access is None or access == _UNSET_ACCESS:
                    event.set("access", self._classify_access)
        if self._classify_remarks:
            preamble, access = self._classify_remarks
            triggered = any(
                (remarks.text or "").strip().startswith(preamble)
                for remarks in root.iter("remarks")
            )
            if triggered:
                for event in events:
                    event.set("access", access)

    def _apply_callsign(self, root: ET.Element) -> None:
        op, value = self._callsign
        for tag, attr in _CALLSIGN_TARGETS:
            for el in root.iter(tag):
                current = el.get(attr)
                if not current:
                    continue
                el.set(attr, _affix(op, current, value))


def _affix(op: str, callsign: str, value: str) -> str:
    """Apply a single callsign affix op idempotently."""
    if op == "add-suffix":
        return callsign if callsign.endswith(value) else callsign + value
    if op == "add-prefix":
        return callsign if callsign.startswith(value) else value + callsign
    if op == "strip-suffix":
        return callsign[: -len(value)] if callsign.endswith(value) else callsign
    if op == "strip-prefix":
        return callsign[len(value):] if callsign.startswith(value) else callsign
    return callsign
