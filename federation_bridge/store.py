# SPDX-License-Identifier: AGPL-3.0-or-later
"""YAML-backed store for bridge configs.

The single source of truth is a human-editable YAML file (a top-level list of
bridge mappings). The file is round-tripped with ruamel.yaml so a save from the
web UI keeps hand-added comments and key order. Every read reads the file fresh
and every mutation re-reads, modifies the freshly loaded document, and writes it
atomically — so a concurrent hand-edit is never silently clobbered.
"""

import logging
import os

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .config import BRIDGES_FILE
from .models import BridgeConfig, new_id, now_iso

logger = logging.getLogger("federation-bridge.store")


class BridgeStore:
    """Read/write bridge configs in a YAML file. Methods are synchronous; the
    file is tiny so they are safe to call directly from async handlers."""

    def __init__(self, path: str = BRIDGES_FILE):
        self.path = path
        self._yaml = YAML()  # round-trip mode (preserves comments)
        self._yaml.preserve_quotes = True
        self._yaml.default_flow_style = False
        self._yaml.width = 4096  # keep long JWT/URL strings on one line

    # -- low-level file I/O ---------------------------------------------------

    def _read_seq(self) -> CommentedSeq:
        if not os.path.exists(self.path):
            return CommentedSeq()
        with open(self.path, "r", encoding="utf-8") as f:
            data = self._yaml.load(f)
        if data is None:
            return CommentedSeq()
        # Tolerate either a bare top-level list or a {bridges: [...]} mapping.
        if isinstance(data, dict):
            data = data.get("bridges") or CommentedSeq()
        return data

    def _write_seq(self, seq: CommentedSeq) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            self._yaml.dump(seq, f)
        os.replace(tmp, self.path)  # atomic on POSIX

    # -- startup --------------------------------------------------------------

    def load(self) -> None:
        """Assign a stable id to any entry missing one and persist it once, so
        hand-written bridges keep the same id across restarts."""
        seq = self._read_seq()
        changed = False
        for entry in seq:
            if not entry.get("id"):
                entry["id"] = new_id()
                changed = True
        if changed:
            self._write_seq(seq)
            logger.info("Assigned ids to bridges missing one in %s", self.path)

    # -- reads (fresh each call) ----------------------------------------------

    def list_bridges(self) -> list[BridgeConfig]:
        return [BridgeConfig.from_mapping(dict(entry)) for entry in self._read_seq()]

    def get(self, bridge_id: str) -> BridgeConfig | None:
        for entry in self._read_seq():
            if entry.get("id") == bridge_id:
                return BridgeConfig.from_mapping(dict(entry))
        return None

    # -- mutations (read -> modify -> atomic write) ---------------------------

    def create(self, cfg: BridgeConfig) -> BridgeConfig:
        if not cfg.id:
            cfg.id = new_id()
        if not cfg.created_at:
            cfg.created_at = now_iso()
        seq = self._read_seq()
        entry = CommentedMap()
        for key, value in cfg.to_mapping().items():
            entry[key] = value
        seq.append(entry)
        self._write_seq(seq)
        return cfg

    def update(self, bridge_id: str, fields: dict) -> BridgeConfig | None:
        """Set only the given keys on the matching entry (in place, so comments
        and untouched fields such as `enabled`/`created_at` survive)."""
        seq = self._read_seq()
        for entry in seq:
            if entry.get("id") == bridge_id:
                for key, value in fields.items():
                    entry[key] = value
                self._write_seq(seq)
                return BridgeConfig.from_mapping(dict(entry))
        return None

    def set_enabled(self, bridge_id: str, value: bool) -> BridgeConfig | None:
        return self.update(bridge_id, {"enabled": value})

    def delete(self, bridge_id: str) -> bool:
        seq = self._read_seq()
        for i, entry in enumerate(seq):
            if entry.get("id") == bridge_id:
                del seq[i]
                self._write_seq(seq)
                return True
        return False
