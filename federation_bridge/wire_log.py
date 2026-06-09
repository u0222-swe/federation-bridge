# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wire-level logging for the federation bridge.

When FEDBRIDGE_WIRE_LOG=1, every message in both directions is appended to
per-direction log files for later inspection. Off by default.

Direction names use FedHub as the fixed reference point so they are
unambiguous regardless of which side you reason from:

  to-fedhub.log    — local CoT XML in  → FederatedEvent proto out
  from-fedhub.log  — FederatedEvent proto in → local CoT XML out
"""

import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional

_ENABLED = os.environ.get("FEDBRIDGE_WIRE_LOG", "").lower() in ("1", "true", "yes")
_DIR = os.environ.get("FEDBRIDGE_WIRE_DIR", "/tmp/fedbridge-wire")

_MAX_BYTES = 50 * 1024 * 1024
_BACKUP_COUNT = 3


def _build_logger(name: str, filename: str) -> Optional[logging.Logger]:
    path = os.path.join(_DIR, filename)
    try:
        os.makedirs(_DIR, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT)
    except OSError as e:
        logging.getLogger("federation-bridge").error(
            f"wire log: cannot open {path}: {e}; wire logging disabled"
        )
        return None
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    return logger


_to_logger = _build_logger("federation-bridge.wire.to-fedhub", "to-fedhub.log") if _ENABLED else None
_from_logger = _build_logger("federation-bridge.wire.from-fedhub", "from-fedhub.log") if _ENABLED else None

if _ENABLED:
    logging.getLogger("federation-bridge").info(
        f"Wire logging enabled, writing to {_DIR}/{{to,from}}-fedhub.log"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log_to_fedhub(bridge_name: str, cot_xml: str, event,
                  transformed_cot: Optional[str] = None) -> None:
    """Log a message flowing local → FedHub. No-op if wire logging is off.

    ``cot_xml`` is the CoT exactly as received (before any ingress transform),
    so the log reflects what actually arrived. When an ingress transform changed
    it, pass the result as ``transformed_cot`` and it is shown as a separate
    block so the redaction/rewrite is auditable.
    """
    if _to_logger is None:
        return
    uid = event.event.uid if event.HasField("event") else ""
    extra = ""
    if transformed_cot is not None and transformed_cot != cot_xml:
        extra = f"--- CoT XML in (after transform) ---\n{transformed_cot}\n"
    _to_logger.info(
        f"=== {_now()} bridge={bridge_name} uid={uid} ===\n"
        f"--- CoT XML in ---\n{cot_xml}\n"
        f"{extra}"
        f"--- FederatedEvent proto out ---\n{event}"
    )


def log_from_fedhub(bridge_name: str, event, cot_xml: str) -> None:
    """Log a message flowing FedHub → local. No-op if wire logging is off."""
    if _from_logger is None:
        return
    uid = event.event.uid if event.HasField("event") else ""
    _from_logger.info(
        f"=== {_now()} bridge={bridge_name} uid={uid} ===\n"
        f"--- FederatedEvent proto in ---\n{event}"
        f"--- CoT XML out ---\n{cot_xml}\n"
    )
