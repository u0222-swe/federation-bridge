# SPDX-License-Identifier: AGPL-3.0-or-later
import os

DATA_DIR = os.getenv("BRIDGE_DATA_DIR", "/opt/federation-bridge/data")
DB_PATH = os.path.join(DATA_DIR, "bridges.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

FEDHUB_DEFAULT_ADDRESS = os.getenv("FEDHUB_DEFAULT_ADDRESS", "127.0.0.1")
FEDHUB_DEFAULT_PORT = int(os.getenv("FEDHUB_DEFAULT_PORT", "9103"))

COT_PORT_RANGE_START = int(os.getenv("COT_PORT_RANGE_START", "10001"))
COT_PORT_RANGE_END = int(os.getenv("COT_PORT_RANGE_END", "10100"))
