# SPDX-License-Identifier: AGPL-3.0-or-later
import os

DATA_DIR = os.getenv("BRIDGE_DATA_DIR", "/opt/federation-bridge/data")
# Bridge configs live in a single human-editable YAML file. Override the whole
# path with BRIDGES_CONFIG, otherwise it sits under BRIDGE_DATA_DIR.
BRIDGES_FILE = os.getenv("BRIDGES_CONFIG", os.path.join(DATA_DIR, "bridges.yaml"))

FEDHUB_DEFAULT_ADDRESS = os.getenv("FEDHUB_DEFAULT_ADDRESS", "127.0.0.1")
FEDHUB_DEFAULT_PORT = int(os.getenv("FEDHUB_DEFAULT_PORT", "9103"))

COT_PORT_RANGE_START = int(os.getenv("COT_PORT_RANGE_START", "10001"))
COT_PORT_RANGE_END = int(os.getenv("COT_PORT_RANGE_END", "10100"))
