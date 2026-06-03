#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Federation Bridge Manager - optional systemd installation helper.
#
# Installs the service to /opt/federation-bridge and runs it under systemd.
# This is one convenient way to run it on a Linux host; see the README for a
# plain virtualenv quick start that works on any platform. The host only needs
# network access to a FedHub instance (it does not have to be the FedHub host).
set -e

BRIDGE_DIR="/opt/federation-bridge"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Installing Federation Bridge Manager ==="

# Install Python 3.12 + pip if not present
if ! command -v python3.12 &>/dev/null; then
    echo "Installing Python 3.12..."
    dnf install -y python3.12 python3.12-pip
fi

# Copy application files (skip if running in-place)
mkdir -p "${BRIDGE_DIR}/federation_bridge/proto_gen"
mkdir -p "${BRIDGE_DIR}/federation_bridge/templates"
mkdir -p "${BRIDGE_DIR}/federation_bridge/static"
mkdir -p "${BRIDGE_DIR}/proto"
mkdir -p "${BRIDGE_DIR}/data"

if [ "${SCRIPT_DIR}" != "${BRIDGE_DIR}" ]; then
    echo "Copying application files to ${BRIDGE_DIR}..."
    cp "${SCRIPT_DIR}/requirements.txt" "${BRIDGE_DIR}/"
    cp "${SCRIPT_DIR}/federation_bridge/"*.py "${BRIDGE_DIR}/federation_bridge/"
    cp "${SCRIPT_DIR}/federation_bridge/templates/"*.html "${BRIDGE_DIR}/federation_bridge/templates/"
    cp "${SCRIPT_DIR}/federation_bridge/static/"*.css "${BRIDGE_DIR}/federation_bridge/static/"
    cp "${SCRIPT_DIR}/proto/"*.proto "${BRIDGE_DIR}/proto/"
else
    echo "Running in-place, skipping file copy."
fi

# Install Python dependencies
echo "Installing Python dependencies..."
pip3.12 install --no-cache-dir -r "${BRIDGE_DIR}/requirements.txt"

# Extract proto files from FedHub JAR (if available, overrides bundled protos)
PROTO_JAR=$(find /opt/tak -name "federation-hub-manager.jar" 2>/dev/null | head -1)
if [ -n "$PROTO_JAR" ]; then
    echo "Extracting proto files from FedHub JAR: ${PROTO_JAR}"
    cd "${BRIDGE_DIR}/proto"
    jar xf "$PROTO_JAR" fig.proto cotevent.proto takmessage.proto contact.proto \
        detail.proto group.proto track.proto status.proto precisionlocation.proto \
        binarypayload.proto takv.proto takcontrol.proto 2>/dev/null || true
    cd -
fi

# Compile proto files
echo "Compiling proto files..."
python3.12 -m grpc_tools.protoc \
    -I"${BRIDGE_DIR}/proto" \
    --python_out="${BRIDGE_DIR}/federation_bridge/proto_gen" \
    --grpc_python_out="${BRIDGE_DIR}/federation_bridge/proto_gen" \
    "${BRIDGE_DIR}/proto/"*.proto

# Fix relative imports in generated files
sed -i 's/^import \(.*_pb2\) as/from . import \1 as/' \
    "${BRIDGE_DIR}/federation_bridge/proto_gen/"*_pb2.py \
    "${BRIDGE_DIR}/federation_bridge/proto_gen/"*_pb2_grpc.py

# Ensure __init__.py files exist
touch "${BRIDGE_DIR}/federation_bridge/__init__.py"
touch "${BRIDGE_DIR}/federation_bridge/proto_gen/__init__.py"

# Create systemd service
echo "Creating systemd service..."
cat > /etc/systemd/system/federation-bridge.service << 'EOF'
[Unit]
Description=Federation Bridge Manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/federation-bridge
ExecStart=/usr/bin/python3.12 -m uvicorn federation_bridge.main:app --host 0.0.0.0 --port 8090
Restart=always
RestartSec=5
Environment=BRIDGE_DATA_DIR=/opt/federation-bridge/data
# Default FedHub address shown in the Web UI form. Point it at your FedHub
# host (127.0.0.1 if FedHub runs on this same host, otherwise its hostname/IP).
Environment=FEDHUB_DEFAULT_ADDRESS=127.0.0.1
Environment=FEDHUB_DEFAULT_PORT=9103

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable federation-bridge

echo "=== Installation complete ==="
echo "Start with: systemctl start federation-bridge"
echo "Web UI: http://<host>:8090"
