# Federation Bridge Manager

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0--or--later-blue.svg)](LICENSE)

A lightweight service that bridges plain **Cursor-on-Target (CoT) XML** and the
**TAK Federation Hub (FedHub)** federation protocol (gRPC / protobuf),
authenticating with a **JWT token** — no client certificates and no full TAK
Server required.

Each "bridge" you create appears to FedHub as an ordinary federated TAK Server
connection, but under the hood it translates between FedHub's gRPC/protobuf
`FederatedEvent` stream and raw CoT XML over TCP/UDP. This lets non-TAK systems
publish and consume CoT through a TAK federation without speaking the federation
protocol themselves.

> **Not affiliated with or endorsed by the TAK Product Center.** "TAK", "ATAK"
> and related terms refer to third-party software. See
> [Acknowledgements](#acknowledgements).

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Cross-Domain & Data Diode Deployments](#cross-domain--data-diode-deployments)
4. [Requirements](#requirements)
5. [Installation](#installation)
6. [Configuration](#configuration)
7. [Web UI Guide](#web-ui-guide)
8. [Creating a Bridge](#creating-a-bridge)
9. [Testing with the Simulator](#testing-with-the-simulator)
10. [Group Override](#group-override)
11. [Virtual Chat User](#virtual-chat-user)
12. [CoT Transformations](#cot-transformations)
13. [HTTP POST Transport (mTLS)](#http-post-transport-mtls)
14. [Operational Guide](#operational-guide)
15. [Troubleshooting](#troubleshooting)
16. [Technical Reference](#technical-reference)
17. [Security Considerations](#security-considerations)
18. [Contributing](#contributing)
19. [License](#license)
20. [Acknowledgements](#acknowledgements)

---

## Overview

Federation Bridge Manager runs as a standalone service with a small web UI. You
point it at a FedHub instance (over the network — it does **not** need to run on
the FedHub host), give it a JWT token, and it federates CoT in both directions.

### Use Cases

- **Cross-domain / data diode federation**: Connect two FedHubs across a
  one-way data diode or a Cross Domain Solution by carrying the federation as
  plain CoT XML datagrams (see
  [Cross-Domain & Data Diode Deployments](#cross-domain--data-diode-deployments)).
- **Sensor integration**: Connect systems that emit CoT XML to a TAK federation
  via FedHub.
- **External system bridging**: Move CoT between isolated networks through
  FedHub's group-based routing.
- **Data forwarding**: Route CoT streams from FedHub to external TCP/UDP
  endpoints.
- **Broadcast relay**: Expose a virtual chat contact that re-broadcasts any
  direct message sent to it (see [Virtual Chat User](#virtual-chat-user)).
- **Testing**: Generate simulated CoT traffic to verify federation connectivity
  and group filtering.

### Key Features

- Web UI for bridge management (create, edit, start, stop, delete)
- JWT token authentication to FedHub (default port 9103, no certificates)
- Bidirectional CoT XML ↔ protobuf conversion
- Multiple concurrent bridges, each independently configured
- TCP, UDP **and** HTTP(S) POST for both CoT input and output, with optional
  mutual TLS on the HTTP transport
- Per-direction CoT transformations: callsign prefix/suffix, element/attribute
  redaction, and classification stamping of the `access` attribute
- Group override to control which FedHub groups receive the traffic
- Optional embedding of FedHub federation groups in CoT so they survive a
  bridge-to-bridge hop
- Built-in CoT simulator for testing
- Virtual Chat User for broadcast-chat relaying
- Optional per-message wire logging for debugging
- Human-editable YAML persistence (bridges survive restarts; provision by file or via the UI)
- Optional systemd installer (`setup.sh`)

---

## Architecture

The bridge is a separate process from FedHub. It can run on the same host as
FedHub or on any other host with network access to FedHub's JWT auth port.

```
                       ┌──────────────────────────────────────────┐
                       │         Federation Bridge Manager        │
                       │        (any host with FedHub access)     │
                       ├──────────────────────────────────────────┤
                       │                                          │
External  ─[CoT XML]──>  TCP/UDP Srv ─> Converter ──> gRPC ──>  FedHub ──> TAK Server
System    (TCP or UDP) │  :10001-10100   XML→Proto    Client     │  :9103
                       │                              (JWT)      │
                       │                                          │
TAK Server ─> FedHub ──>  gRPC ──> Converter ──> TCP/UDP Cli ──>  External
              :9103    │  Recv     Proto→XML     :host:port      │  System
                       │                                          │
                       │  ┌──────────┐  ┌──────────┐             │
                       │  │ Web UI   │  │ YAML     │             │
                       │  │ :8090    │  │ bridges  │             │
                       │  └──────────┘  └──────────┘             │
                       └──────────────────────────────────────────┘
```

### Per Bridge Components

Each bridge runs as a set of asyncio tasks:

| Component | Purpose |
|-----------|---------|
| **gRPC Client** | Connects to FedHub via JWT token auth, bidirectional streaming |
| **TCP Server** | Listens for incoming CoT XML on a configured TCP port |
| **UDP Server** | Listens for incoming CoT XML on a configured UDP port (datagrams) |
| **HTTP Server** | (optional) Receives CoT XML via HTTP POST on a configured port, optionally mTLS |
| **TCP/UDP/HTTP Client** | Sends CoT XML to a configured destination (TCP/UDP host:port, or HTTP(S) POST to a URL with optional mTLS) |
| **Converter (inbound)** | `FederatedEvent` protobuf → CoT XML |
| **Converter (outbound)** | CoT XML → `FederatedEvent` protobuf |
| **Transformer** | (optional) Applies per-direction callsign/redaction/classification transforms to the CoT |
| **Simulator** | (optional) Generates random CoT events for testing |
| **Virtual Chat User** | (optional) Announces a stationary contact; direct chats to it are re-broadcast on the CoT output |

### Protocol Translation

```
CoT XML (input):
  <event uid="ALPHA-1" type="a-f-G-U-C" time="..." start="..." stale="..." how="h-e">
    <point lat="59.33" lon="18.07" hae="0" ce="9999999" le="9999999"/>
    <detail>
      <contact callsign="ALPHA-1"/>
      <__group name="Cyan" role="Team Member"/>
      <track speed="5.0" course="180.0"/>
    </detail>
  </event>

       ↕ (bidirectional conversion)

FederatedEvent protobuf (federation):
  GeoEvent {
    uid: "ALPHA-1", type: "a-f-G-U-C", coordSource: "h-e"
    lat: 59.33, lon: 18.07, sendTime: 1713800000000
    screenName: "ALPHA-1", groupName: "Cyan"
    speed: 5.0, course: 180.0, other: "<remaining xml detail>"
  }
```

---

## Cross-Domain & Data Diode Deployments

A primary design goal of this project is to **federate two FedHubs that are
separated by a data diode or a Cross Domain Solution (CDS)** — for example a
high-side and a low-side network that may only be connected through a one-way,
hardware-enforced link or a content-inspecting guard.

Native TAK/FedHub federation cannot traverse such a boundary on its own: it is
**bidirectional gRPC over TLS** (with mutual handshakes, ACKs and client
certificates). A data diode has no return path for the TCP/TLS handshake, and a
CDS generally cannot inspect or release opaque encrypted protobuf. The bridge
solves this by reducing the federation to a stream of **self-contained,
human-readable CoT XML datagrams** that a diode or guard can actually carry and
inspect.

### Topology (one direction)

```
        HIGH SIDE                                          LOW SIDE

   ┌──────────────┐                                   ┌──────────────┐
   │   FedHub A   │                                   │   FedHub B   │
   └──────┬───────┘                                   └──────▲───────┘
          │ gRPC / JWT                                       │ gRPC / JWT
   ┌──────▼───────┐         CoT / UDP                 ┌──────┴───────┐
   │   Bridge A   │ ───▶ [ DIODE / CDS ] ───▶         │   Bridge B   │
   │  gRPC client │   one-way datagrams,              │  UDP server  │
   │  UDP client  │   plain inspectable XML           │  gRPC client │
   └──────────────┘                                   └──────────────┘
          └──────────────── one-way flow ────────────────────▶
```

Bridge A subscribes to FedHub A over gRPC and emits CoT as **UDP** datagrams
into the diode/guard ingress. Bridge B receives those datagrams on its UDP
server and publishes them into FedHub B over gRPC. For two-way exchange you
deploy a **mirrored pair across a second diode** running in the opposite
direction.

### Why the features exist

| Feature | Role across a diode / CDS |
|---------|---------------------------|
| **UDP CoT I/O** | Data diodes are one-way and connectionless — only UDP datagrams cross cleanly; TCP cannot complete its handshake. |
| **HTTP(S) POST CoT I/O** | Some guards prefer (or only release) CoT over an HTTP POST API, often with mutual TLS, rather than raw UDP. The bridge can POST each event to a guard's ingest URL and/or receive POSTed CoT on a listener. See [HTTP POST Transport](#http-post-transport-mtls). |
| **Plain CoT XML on the wire** | A CDS/guard can parse, validate, and release human-readable XML. It cannot do that with TLS-encrypted protobuf. (Enable [wire logging](#wire-logging-debug) for audit/accreditation.) |
| **CoT transformations** | A guard releases content per policy: stamp a classification into `access` (from a default or a `//remarks` marker), redact elements/attributes that must not cross (e.g. `takv`, location), and tag callsigns with the originating domain. See [CoT Transformations](#cot-transformations). |
| **Embed federation groups in CoT** | Routing groups live only in the protobuf; over the link only the CoT survives. Embedding carries the group selection across so the far FedHub can still route. See [Group Override](#group-override). |
| **Group translation** | The two domains are administratively separate and usually name their groups differently; translate names on each side. |
| **Virtual Chat User** | GeoChat normally needs a round-trip (contact registration, direct addressing, delivery ACKs). Over a one-way link there is no return channel, so it presents a single addressable contact whose incoming direct chats are re-broadcast locally — letting chat traverse a one-way path as broadcast. |
| **Contact announcement** | The receiving side cannot be queried back over a diode, so the bridge proactively (re)announces contacts so PLI/chat routing state is built without a return path. |

> **Note:** A one-way link means no delivery confirmation and no flow control.
> Size UDP for the link's MTU/bandwidth, accept that lost datagrams are not
> retransmitted, and rely on the periodic PLI/contact re-announcements to
> reconcile state on the receiving side.

---

## Requirements

- A reachable **FedHub** instance with JWT token auth enabled (default port 9103)
- A **JWT token** generated from the FedHub Admin UI (Token Group)
- **Python 3.12**
- Network access:
  - To FedHub's JWT auth port (default `9103`)
  - The Web UI port you choose (default `8090`)
  - Any CoT input/output ports you configure (default range `10001–10100`)

---

## Installation

The application ships with pre-generated protobuf stubs, so you do not need to
compile anything to get started.

### Quick start (virtualenv)

```bash
git clone <your-fork-or-clone-url> federation-bridge
cd federation-bridge

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Use a writable data directory for the bridges.yaml config
export BRIDGE_DATA_DIR="$PWD/data"
mkdir -p "$BRIDGE_DATA_DIR"

# Optionally set the default FedHub address shown in the form
export FEDHUB_DEFAULT_ADDRESS="fedhub.example.com"

uvicorn federation_bridge.main:app --host 0.0.0.0 --port 8090
```

Open the Web UI at `http://localhost:8090`.

### Optional: install as a systemd service

`setup.sh` installs the app to `/opt/federation-bridge` and registers a systemd
unit. It is a convenience for Linux hosts; the quick start above works anywhere.

```bash
sudo ./setup.sh
sudo systemctl start federation-bridge
systemctl status federation-bridge
```

If a TAK Federation Hub JAR is present on the host, `setup.sh` will extract the
canonical `.proto` files from it and recompile the stubs; otherwise it uses the
bundled protos.

### Regenerating protobuf stubs (only if you change the protos)

```bash
python -m grpc_tools.protoc \
  -Iproto \
  --python_out=federation_bridge/proto_gen \
  --grpc_python_out=federation_bridge/proto_gen \
  proto/*.proto

# Fix the generated absolute imports to be package-relative
sed -i 's/^import \(.*_pb2\) as/from . import \1 as/' \
  federation_bridge/proto_gen/*_pb2.py \
  federation_bridge/proto_gen/*_pb2_grpc.py
```

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BRIDGE_DATA_DIR` | `/opt/federation-bridge/data` | Directory holding `bridges.yaml`. Must be writable. |
| `BRIDGES_CONFIG` | `${BRIDGE_DATA_DIR}/bridges.yaml` | Full path to the bridge config YAML file (override the location directly). |
| `FEDHUB_DEFAULT_ADDRESS` | `127.0.0.1` | Default FedHub address pre-filled in the create form. Point it at your FedHub host. |
| `FEDHUB_DEFAULT_PORT` | `9103` | Default FedHub JWT auth port. |
| `COT_PORT_RANGE_START` | `10001` | Start of the allowed CoT input port range. |
| `COT_PORT_RANGE_END` | `10100` | End of the allowed CoT input port range. |
| `FEDBRIDGE_WIRE_LOG` | _(unset)_ | Set to `1` to enable per-message wire logging to disk. See [Wire logging](#wire-logging-debug). |
| `FEDBRIDGE_WIRE_DIR` | `/tmp/fedbridge-wire` | Directory for wire log files when `FEDBRIDGE_WIRE_LOG=1`. |

### Data & State

| Path | Description |
|------|-------------|
| `${BRIDGE_DATA_DIR}/bridges.yaml` | YAML file holding bridge configs (including JWT tokens). See [Provisioning via YAML](#provisioning-via-yaml). |
| `proto/` | Protobuf definitions |
| `federation_bridge/proto_gen/` | Generated protobuf Python stubs |

### Provisioning via YAML

Bridge configs live in a single human-editable file (`bridges.yaml`). The Web UI
reads and writes the same file, so you can provision bridges by editing it
directly (and version-control it) or through the UI — both produce the same YAML.
A UI save preserves hand-added comments and key order on the other bridges.

The file is a top-level list of bridge entries. Only `name` is required;
everything else takes a default. `id` is auto-assigned on first load if omitted.
Booleans are bare `true`/`false`; ports are integers.

```yaml
# bridges.yaml
- name: partners
  fedhub_address: 127.0.0.1
  fedhub_port: 9103
  jwt_token: eyJhbGciOi...        # plaintext — protect this file
  cot_input_udp_port: 10001
  cot_output_protocol: https
  cot_output_http_url: https://guard.example:10400/ieg/input/cot
  http_client_cert: /etc/fedbridge/client.pem
  http_client_key: /etc/fedbridge/client.key
  http_ca_cert: /etc/fedbridge/ca.pem
  callsign_rewrite_out: add-suffix:@AREA1
  classify_access_out: S3CRET
  enabled: true
- name: quicktest                 # minimal: gets an id + defaults on load
  fedhub_address: 127.0.0.1
  jwt_token: eyJ...
```

Set `enabled: false` to keep a bridge in the file without auto-starting it.
Changes made by hand take effect on the next restart; changes via the UI apply
immediately.

---

## Web UI Guide

The Web UI has **no authentication** — keep it on a trusted network or behind a
reverse proxy / VPN (see [Security Considerations](#security-considerations)).

### Main Page

- List of configured bridges with status (running/stopped)
- FedHub address/port, CoT input ports, and output destination per bridge
- Start / Stop / Edit / Delete controls
- Form to create a new bridge

### Bridge Detail Page

Click a bridge name to see its full configuration (JWT token truncated), group
override, simulator and Virtual Chat User status, plus lifecycle controls.

---

## Creating a Bridge

### Step 1: Generate a JWT Token in FedHub

1. Open the FedHub Admin UI (e.g. `https://fedhub.example.com:9100`).
2. Go to **Policy Manager** → load your policy.
3. Drag a **Token Group** node onto the canvas.
4. Name it and click **Generate Token**, then copy the JWT.
5. Connect the Token Group to other nodes (CA Groups) with edges to define
   traffic flow.
6. Save and upload the policy.

### Step 2: Create the Bridge

1. Open the Bridge Manager Web UI (e.g. `http://localhost:8090`).
2. Fill in the form. Required fields:
   - **Name**: Descriptive name (e.g. "Sensor-Bridge-North")
   - **FedHub Address**: hostname/IP of your FedHub (e.g. `fedhub.example.com`
     or `127.0.0.1` if co-located)
   - **FedHub Port**: `9103` (JWT token auth port)
   - **JWT Token**: paste the token from Step 1

   Optional fields:
   - **Description**: free-form text
   - **CoT Input Port (TCP)** / **(UDP)** / **(HTTP POST)**: ports to receive
     CoT XML. Set to `0` to disable a listener. The TCP/UDP ports must be inside
     the `COT_PORT_RANGE_*` window (default `10001–10100`). The HTTP listener
     optionally takes mTLS certs. See
     [HTTP POST Transport](#http-post-transport-mtls).
   - **CoT Output Protocol** (`tcp`/`udp`/`http`/`https`) plus either a **CoT
     Output Host/Port** (TCP/UDP) or a **CoT Output URL** (HTTP, optionally
     mTLS): where to forward CoT received from FedHub. Leave blank to disable
     output.
   - **CoT Transformations** (per direction): callsign rewrite, redaction, and
     classification stamping. See [CoT Transformations](#cot-transformations).
   - **Group Override**: comma-separated FedHub group names to stamp on outgoing
     events. See [Group Override](#group-override).
   - **Enable CoT Simulator** + interval/tracks: see
     [Testing with the Simulator](#testing-with-the-simulator).
   - **Enable Virtual Chat User** + callsign: see
     [Virtual Chat User](#virtual-chat-user).
3. Click **Create Bridge**.

### Step 3: Verify

```bash
# Send a test CoT event to the bridge's input port
echo '<event version="2.0" uid="TEST-1" type="a-f-G-U-C"
  time="2026-01-01T00:00:00Z" start="2026-01-01T00:00:00Z"
  stale="2026-01-01T00:05:00Z" how="h-e">
  <point lat="59.33" lon="18.07" hae="0" ce="9999999" le="9999999"/>
  <detail><contact callsign="TEST-1"/></detail>
</event>' | nc <bridge-host> 10001
```

If the bridge is working, the event is converted to protobuf and sent to FedHub,
which routes it according to its policy graph.

---

## Testing with the Simulator

The built-in simulator generates random CoT events (with positions in Sweden)
for testing federation connectivity.

### Enable Simulator

When creating a bridge, check **Enable CoT Simulator** and configure:
- **Simulator Interval**: seconds between position updates (default 5)
- **Simulator Tracks**: number of simulated units (default 5)
- **Simulator Direction**: where the synthetic events are sent —
  - `FedHub` (default): into FedHub over gRPC, as if received on the CoT input.
  - `CoT Output`: out the configured CoT output host/port, as if received from
    FedHub — useful for exercising the receiving side of a
    [data diode](#cross-domain--data-diode-deployments) without a live FedHub.
  - `Both`: sent both ways at once.

  `CoT Output` and `Both` require a **CoT Output Host/Port** to be configured;
  if it is missing, that target is skipped (logged as a warning).
- **Simulator Group(s)**: optional comma-separated FedHub federation group(s)
  to tag the simulated events with. The group is **always applied toward
  FedHub** (it lands in `federateGroups`). Toward the **CoT output** it is
  embedded as `<__fedhubgroups>` only when group embedding is enabled — handy
  for exercising group routing on the receiving side of a
  [data diode](#cross-domain--data-diode-deployments).

Each track has a persistent UID and callsign, so it appears as a moving unit in
ATAK / TAK Server.

### Verifying Simulator Output

1. Create a bridge with the simulator enabled and a group override (e.g. `test`).
2. Check FedHub's Active Connections to confirm the bridge is connected.
3. Open ATAK or WebTAK connected to the TAK Server.
4. Simulated tracks should appear if the FedHub policy allows the traffic.

---

## Group Override

By default, outgoing `FederatedEvent` messages carry no group information and
FedHub routes them by its policy graph alone.

With **Group Override** you explicitly set which FedHub groups the bridge's
outgoing traffic belongs to. Useful for:

- Directing a sensor system's traffic to a particular TAK Server group
- Testing group-based routing in FedHub
- Isolating bridge traffic from other federation traffic

### Usage

Set **Group Override** to one or more comma-separated group names:

```
sensors
```
or
```
sensors, command
```

Every outgoing `FederatedEvent` then has its `federateGroups` field set to those
groups, and FedHub routes the traffic according to the matching policy edges.

### How FedHub Routing Works with Groups

1. Bridge sends a `FederatedEvent` with `federateGroups: ["sensors"]`.
2. FedHub checks its policy graph for edges from the bridge's Token Group.
3. If an edge filter allows `sensors` (or uses "Allow All Messages"), it passes.
4. TAK Server receives the event and (with `automaticGroupMapping=true`) places
   it in the local `sensors` group.

### End-to-end with a mapped group

If you set Group Override to a local group (e.g. `command`) and the FedHub policy
edge from the bridge's Token Group to TAK Server allows that group (or uses
"Allow All Messages"), the event is delivered all the way to ATAK clients
subscribed to that group. On the TAK Server side this requires:

- An `INBOUND <group>` configured on the FedHub federate.
- An `inboundGroupMapping` `<group> -> <group>` (required because
  `matchingCA=false` for JWT bridges, so automatic mapping is disabled).

The practical contract: pick a group permitted by the FedHub policy, ensure the
TAK Server maps it inbound, and the event lands in the same-named local group
and is broadcast to all clients in it.

### Carrying groups across a bridge pair (Embed Federation Groups in CoT)

`federateGroups` lives only in the protobuf — CoT XML has no native field for
it. So when a bridge converts an inbound `FederatedEvent` to CoT XML and ships
it over TCP/UDP to a peer bridge, the group routing is dropped: the receiving
bridge has nothing to restore it from and (unless a static **Group Override**
is set) forwards the event to its FedHub with no groups at all.

Enable **Embed FedHub federation groups in CoT** so that, on the
FedHub → CoT side, the bridge writes the event's groups into a non-standard
`<__fedhubgroups groups="blue,green"/>` element inside `<detail>` (multiple
groups are comma-separated).

The reverse — CoT → FedHub — does **not** depend on the toggle: a bridge
*always* reads an incoming `<__fedhubgroups>` back into
`FederatedEvent.federateGroups` and strips it (so it never leaks downstream).
The toggle only governs whether *outgoing* CoT gets groups embedded.

This lets the group selection you make in the source FedHub flow through to the
destination FedHub automatically, no manual override required. Notes:

- It deviates from the CoT standard, hence the opt-in for embedding. Consumers
  that aren't another federation bridge (ATAK, TAK Server) ignore the unknown
  element.
- Enable it on the bridge that **emits** CoT (the FedHub → CoT / sending side).
  The receiving bridge restores an incoming `<__fedhubgroups>` automatically, so
  a pure diode *receiver* does not need it enabled.
- It does **not** collide with ATAK's `<__group name="Cyan" role="...">`
  (team/role); that element is preserved untouched.
- **Group Override** still takes precedence: if set on the receiving bridge,
  its groups are used instead of the carried ones.

#### Group translation

Two federations rarely use the same group names. With group embedding enabled
you can also define a **Group Translation** table so each conversion remaps
group names between the CoT wire and FedHub. Enter one mapping per line as
`cot_name = fedhub_name`:

```
blue = ALPHA
green = BRAVO
```

- **CoT → FedHub (incoming):** a carried group `blue` is sent to FedHub as
  `ALPHA`.
- **FedHub → CoT (outgoing):** a FedHub group `ALPHA` is embedded on the wire
  as `blue` (the reverse mapping).

Groups not listed in the table pass through unchanged. The table is applied
only when group embedding is on; **Group Override**, if set, still wins and is
used verbatim (no translation). For clean bidirectional behavior keep the
mappings 1:1.

---

## Virtual Chat User

The **Virtual Chat User** turns a bridge into a broadcast relay. When enabled,
the bridge announces a single stationary contact into the federation, and any
*direct* chat an ATAK client sends to that contact is re-broadcast as a broadcast
GeoChat ("All Chat Rooms") on the bridge's CoT output.

It is, in particular, the mechanism for moving chat across a **one-way data
diode or CDS** (see
[Cross-Domain & Data Diode Deployments](#cross-domain--data-diode-deployments)).
Normal GeoChat needs a return path — direct addressing, contact resolution and
delivery ACKs — which a one-way link cannot provide. The virtual contact gives
operators a single, always-present address to send to; the bridge converts that
to a broadcast that crosses the link in the one direction it flows.

### How it works

1. The bridge periodically emits a presence PLI (at lat/lon `0,0`) for the
   virtual contact and a `ContactListEntry` CREATE for it — the same mechanism
   that makes simulator tracks appear as addressable contacts. ATAK clients in
   the federation then see it in their contact list.
2. An operator opens a private (direct) chat to the virtual contact and sends a
   message.
3. FedHub routes that direct chat back to the bridge. The bridge recognizes the
   destination as the virtual contact, rewrites the message from point-to-point
   to a broadcast GeoChat ("All Chat Rooms") — `__chat`/`chatgrp` room set to the
   broadcast room and the `<marti>` destination stripped, the shape ATAK uses for
   a broadcast — and emits it on the configured CoT output. The original sender,
   message text and position are preserved.

The virtual contact advertises `platform="ATAK-CIV"` in its presence so TAK
Server treats it as chat-capable and routes direct messages to it
(non-chat-capable platforms can have their inbound DMs silently dropped).

Messages that are *already* broadcast, or directed at any other contact, pass
through untouched — so there is no re-broadcast loop.

### Usage

When creating or editing a bridge:

- Check **Enable Virtual Chat User**.
- Optionally set **Virtual Chat User Callsign**. Leave it blank to use the
  default `fedhub-broadcast-chat-<bridge name>`.

The contact appears as a stationary marker at `0,0`. Make sure the bridge has a
**CoT Output Host/Port** configured — that is where the re-broadcast GeoChat is
sent.

---

## CoT Transformations

A bridge can rewrite the CoT XML as it crosses the boundary. This is primarily
for **cross-domain / CDS deployments**, where a guard releases content per
policy: you tag callsigns with their originating domain, strip elements that
must not cross, and stamp a classification the guard can act on.

Each transform is configured **per direction**, independently:

- **Outbound** — applied to CoT leaving toward the **CoT output** (the
  FedHub → CoT path), i.e. what you send toward a guard/CDS.
- **Inbound** — applied to CoT arriving on the **CoT input** (the CoT → FedHub
  path) before it is federated, e.g. to undo a marker the far side added.

In the Web UI, tick **Enable outbound transforms** / **Enable inbound
transforms** and fill in any of the fields below (empty = that transform is off).

### Callsign Rewrite

Add or strip a marker on callsigns (`<contact callsign>`, `<marti><dest
callsign>`, `<__chat senderCallsign>`) so operators can see where a track
originates. Format `op:value`. **Suffix** places the marker *after* the
callsign, **prefix** places it *before*:

| op | Placement | Example (value `@AREA1`) |
|----|-----------|--------------------------|
| `add-suffix` | marker **after** the callsign | `NISSE` → `NISSE@AREA1` |
| `add-prefix` | marker **before** the callsign | `NISSE` → `@AREA1NISSE` |
| `strip-suffix` | removes the marker from the **end** | `NISSE@AREA1` → `NISSE` |
| `strip-prefix` | removes the marker from the **start** | `@AREA1NISSE` → `NISSE` |

All four ops are available in **both** directions (outbound and inbound) — pick
the combination that fits your topology. The typical pairing is
`add-suffix:@AREA1` outbound on the sending bridge and `strip-suffix:@AREA1`
inbound on the receiving bridge, but tagging *arriving* tracks instead (e.g.
`add-suffix:@AREA1` inbound) works just as well. Adds are idempotent (a
callsign that already carries the marker is left unchanged).

### Redact

Remove or rewrite elements/attributes that should not cross. Comma- or
newline-separated directives:

| Directive | Effect |
|-----------|--------|
| `-takv` | Remove every `<takv>` element |
| `-contact@endpoint` | Remove the `endpoint` attribute from `<contact>` |
| `point@hae=0` | Set the `hae` attribute on `<point>` to `0` |
| `zero-point` | Zero the `lat`/`lon`/`hae`/`ce`/`le` coordinates of `<point>` |

Example obfuscation: `-takv, -_flow-tags_, zero-point`.

> **`zero-point` strips the real position, it does not fuzz it.** It sets the
> coordinates to `0,0` ("null island"), a valid point that TAK/ATAK accept and
> **display** — the marker simply appears at `0,0`, off the coast of Africa. Use
> it to remove the true location while still emitting a well-formed event. To
> keep an *approximate, in-area* position instead, relocate to a decoy with a
> `set` directive, e.g. `point@lat=59.3, point@lon=18.0` (optionally widen
> `point@ce=10000` to reflect the reduced accuracy).
>
> `zero-point` writes **all five** coordinates (`lat/lon/hae/ce/le`) as `0`,
> which matters: an *incomplete* `<point>` (missing some coords) is what breaks
> the receiving side, not the `0,0` value. (Confirmed against TAKServer: lat/lon
> are parsed with `Double.parseDouble`, and `0,0` passes its value-scrubber range
> check. The federation wire format is proto3, so a `0.0` coordinate is simply
> omitted on the wire and read back as `0.0` by the peer — normal and
> wire-correct.)

### Classification (access attribute)

Stamp the event's `access` attribute so a CDS can release per policy:

- **Classification (access)** — a value (e.g. `S3CRET`) written to
  `event/@access` when it is unset or the literal `Undefined`.
- **Classification from Remarks** — `preamble=ACCESS` (e.g.
  `#UNCLASS=UNCLASSIFIED`). If any `<remarks>` text starts with the preamble, the
  `access` attribute is forced to `ACCESS` — letting an operator downgrade a
  specific event by typing a marker in chat/remarks. This rule overrides the
  default above.

### Example: sending toward a CDS

On the bridge that emits CoT toward the guard, enable **outbound** transforms:

```
Callsign Rewrite:           add-suffix:@AREA1
Redact:                     -takv, -_flow-tags_, zero-point
Classification (access):    S3CRET
Classification from Remarks: #UNCLASS=UNCLASSIFIED
```

On the bridge that ingests CoT on the far side, enable **inbound** transforms to
remove the marker:

```
Callsign Rewrite:  strip-suffix:@AREA1
```

> Transforms operate on the CoT XML; if a message is not well-formed XML it is
> passed through unchanged (and a warning is logged).

---

## HTTP POST Transport (mTLS)

In addition to TCP and UDP, a bridge can send and receive CoT over **HTTP
POST**, with optional **mutual TLS**. This suits guards/CDS ingest APIs that
speak HTTP(S) rather than raw UDP, and gives a reliable, connection-oriented
path (with backpressure) where a diode is not strictly one-way.

### Output (HTTP client)

Set **CoT Output Protocol** to `HTTP POST` or `HTTPS POST (mTLS)` and provide a
**CoT Output URL**. Each event received from FedHub is POSTed to that URL with
`Content-Type: application/xml` over a persistent session (one transient-error
retry). For HTTPS, supply mTLS material as file paths:

| Field | Purpose |
|-------|---------|
| **CoT Output URL** | e.g. `https://guard.example:10400/ieg/input/cot` |
| **HTTP Client Cert** | Client certificate presented for mTLS |
| **HTTP Client Key** | Client private key |
| **HTTP CA Cert** | CA used to verify the server (omit to disable verification — logged) |

### Input (HTTP server)

Set a **CoT Input Port (HTTP POST)**. The bridge accepts `POST` of CoT XML on
that port (other methods get `405`); the body may contain one or more
`</event>`-delimited events. TLS material (file paths):

| Field | Purpose |
|-------|---------|
| **HTTP Server Cert** | Server certificate (enables TLS) |
| **HTTP Server Key** | Server private key |
| **HTTP Server CA** | CA to require **and** verify a client certificate (mutual TLS) |

Leave the cert fields empty for plain HTTP; cert + key give one-way TLS; adding
the CA requires a client certificate (mTLS).

### Verify

```bash
# Plain HTTP input on port 18443
curl -X POST --data-binary @event.xml http://<bridge-host>:18443/cot

# mTLS input
curl -X POST --data-binary @event.xml \
  --cert client.pem --key client.key --cacert ca.pem \
  https://<bridge-host>:18443/cot
```

A `200 OK` means the event was accepted and queued toward FedHub. Enable
[wire logging](#wire-logging-debug) to confirm it was converted and forwarded.

---

## Operational Guide

### Running

- **Quick start / foreground**: run the `uvicorn ...` command from
  [Installation](#installation).
- **systemd**: `systemctl {start,stop,restart,status} federation-bridge` and
  `journalctl -u federation-bridge -f` for logs (after `setup.sh`).

### Bridge Lifecycle

- Bridges are stored in `bridges.yaml` and persist across restarts.
- Bridges marked `enabled` auto-start when the service starts.
- Starting/stopping via the Web UI updates the `enabled` flag.
- Deleting a bridge stops it and removes it from the file.

### Backup

The YAML file holds all bridge configuration (including JWT tokens):

```bash
cp "${BRIDGE_DATA_DIR}/bridges.yaml" "${BRIDGE_DATA_DIR}/bridges.yaml.bak"
```

---

## Troubleshooting

### Bridge won't connect to FedHub

**Symptom**: status shows "running" but no traffic flows.

**Check**:
1. FedHub is reachable from the bridge host (`nc -vz <fedhub-host> 9103`).
2. JWT token auth is enabled on the FedHub broker (`federationTokenAuthServers`).
3. Bridge logs for gRPC errors (`journalctl -u federation-bridge | grep -i grpc`
   or the foreground console).
4. The JWT token is valid and not expired (FedHub Admin UI → Token Group).
5. A FedHub policy is loaded and active with the Token Group connected.

### No traffic flowing despite connection

**Symptom**: bridge connects to FedHub but CoT events don't reach TAK Server.

**Check**:
1. FedHub policy: the Token Group has edges to CA Groups with correct filters.
2. Group override: if set, FedHub edges allow those groups (or "Allow All
   Messages").
3. TAK Server: `federatedGroupMapping` / `automaticGroupMapping` enabled and the
   FedHub federate has the expected inbound/outbound groups.

### Web UI not accessible

**Check**:
1. The service/process is running.
2. The port is listening (`ss -tlnp | grep 8090`).
3. Host firewall / network ACLs allow the Web UI port from your client.

### Wire logging (debug)

To inspect raw payloads — e.g. to see whether an attribute that's missing on the
receiving side ever arrived, or was stripped during conversion — enable wire
logging:

```bash
export FEDBRIDGE_WIRE_LOG=1
# Optional, default is /tmp/fedbridge-wire:
export FEDBRIDGE_WIRE_DIR=/var/log/fedbridge-wire
# (or set these as Environment= lines in the systemd unit, then restart)
```

This writes every message in both directions:

| File | Contains |
|------|----------|
| `${FEDBRIDGE_WIRE_DIR}/to-fedhub.log`   | Local CoT XML in → `FederatedEvent` proto out (one block per message) |
| `${FEDBRIDGE_WIRE_DIR}/from-fedhub.log` | `FederatedEvent` proto in → local CoT XML out (one block per message) |

Each block starts with a header `=== <iso-timestamp> bridge=<name> uid=<uid> ===`
followed by the inbound and converted outbound payloads in plain text — easy to
`grep` and diff. The `CoT XML in` payload is the message exactly as received
(before any ingress transform); when a [CoT transform](#cot-transformations)
changes it, the result is shown in an extra `CoT XML in (after transform)` block
so redaction/rewrite stays auditable. Files rotate at 50 MB and keep 3 backups.
Direction names use FedHub as the fixed reference point. Disable by unsetting
`FEDBRIDGE_WIRE_LOG`.

> Wire logs contain full message payloads. Treat them as sensitive and clean
> them up after debugging.

---

## Technical Reference

### Proto Files

The bundled `.proto` files originate from the TAK Federation Hub and define the
federation wire format:

| File | Package | Key Messages |
|------|---------|-------------|
| `fig.proto` | `com.atakmap` | `FederatedEvent`, `GeoEvent`, `FederatedChannel` (gRPC service) |
| `cotevent.proto` | `atakmap.commoncommo.protobuf.v1` | `CotEvent` |
| `detail.proto` | `atakmap.commoncommo.protobuf.v1` | `Detail` |
| `contact.proto` | `atakmap.commoncommo.protobuf.v1` | `Contact` |
| `group.proto` | `atakmap.commoncommo.protobuf.v1` | `Group` |
| `track.proto` | `atakmap.commoncommo.protobuf.v1` | `Track` |
| `binarypayload.proto` | `gov.tak.cop.proto.v1` | `BinaryPayload` |

### gRPC Service: `FederatedChannel`

| RPC | Direction | Description |
|-----|-----------|-------------|
| `ClientEventStream` | Server → Client | Receive events from FedHub (server streaming) |
| `ServerEventStream` | Client → Server | Send events to FedHub (client streaming) |
| `SendOneEvent` | Unary | Send a single event |
| `HealthCheck` | Unary | Check connection health |

### FederatedEvent Message

```protobuf
message FederatedEvent {
    GeoEvent event = 1;                   // Position and CoT data
    ContactListEntry contact = 2;         // Contact list operations
    repeated string federateGroups = 3;   // Group routing (set by group override)
    repeated FederateProvenance federateProvenance = 4;
    FederateHops federateHops = 5;
}
```

### GeoEvent → CoT XML Mapping

| GeoEvent Field | CoT XML | Notes |
|---------------|---------|-------|
| `uid` | `<event uid="...">` | Track identifier |
| `type` | `<event type="...">` | CoT type (e.g. `a-f-G-U-C`) |
| `coordSource` | `<event how="...">` | Position source |
| `sendTime` / `startTime` / `staleTime` | `<event time/start/stale="...">` | ms ↔ ISO 8601 |
| `lat`, `lon` | `<point lat="..." lon="...">` | WGS-84 coordinates |
| `hae`, `ce`, `le` | `<point hae="..." ce="..." le="...">` | Altitude and error |
| `screenName` | `<detail><contact callsign="..."/>` | Callsign |
| `groupName` | `<detail><__group name="..."/>` | Team/group |
| `speed`, `course` | `<detail><track speed="..." course="..."/>` | Movement |
| `other` | `<detail>...remaining XML...` | Passthrough for unmapped detail elements |

### REST API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (includes the app version) |
| `GET` | `/` | Bridge list (HTML) |
| `POST` | `/bridges` | Create bridge |
| `GET` | `/bridges/{id}` | Bridge detail (HTML) |
| `GET` | `/bridges/{id}/edit` | Edit form (HTML) |
| `POST` | `/bridges/{id}/edit` | Update bridge |
| `POST` | `/bridges/{id}/start` | Start bridge |
| `POST` | `/bridges/{id}/stop` | Stop bridge |
| `POST` | `/bridges/{id}/delete` | Delete bridge |

---

## Security Considerations

This tool is intended for use on **trusted networks** by **authorized
operators**. In particular:

- **The Web UI has no authentication.** Anyone who can reach it can create,
  edit, and delete bridges and read truncated tokens. Put it behind a VPN,
  reverse proxy with auth, or a firewall — never expose it to the public
  internet.
- **JWT tokens are stored in plaintext** in `bridges.yaml`. Protect the
  `BRIDGE_DATA_DIR` (filesystem permissions, full-disk encryption) and back it
  up securely.
- **The gRPC connection to FedHub uses an insecure channel** (JWT provides
  authentication, not transport encryption). Run it over localhost or a trusted
  network, or tunnel it.
- **TCP/UDP CoT input/output is unauthenticated and unencrypted.** Restrict
  those ports to trusted sources with host/network firewalls. The HTTP transport
  can use mutual TLS (client-cert auth + encryption) — prefer
  [HTTPS POST with mTLS](#http-post-transport-mtls) when CoT crosses an untrusted
  segment.
- **Wire logs** (when enabled) contain full message payloads; treat them as
  sensitive.

---

## Contributing

Contributions are welcome. Please:

1. Open an issue to discuss substantial changes before sending a PR.
2. Keep changes focused and match the existing code style.
3. Verify the app starts and your change works end-to-end where feasible.
4. By contributing, you agree your contributions are licensed under AGPL-3.0.

---

## License

Licensed under the **GNU Affero General Public License v3.0 or later**
(`AGPL-3.0-or-later`). See [LICENSE](LICENSE) for the full text.

Source files carry an SPDX identifier:

```
SPDX-License-Identifier: AGPL-3.0-or-later
```

The AGPL requires that if you run a modified version of this software as a
network service, you must offer the corresponding source to its users.

---

## Acknowledgements

This project interoperates with the TAK Federation Hub and TAK Server. "TAK",
"ATAK", "WinTAK", "TAK Server" and the Cursor-on-Target format are products and
specifications of the **TAK Product Center**. This project is an independent,
unofficial tool and is **not affiliated with or endorsed by** the TAK Product
Center.

The bundled `.proto` files are part of the TAK Federation Hub and remain subject
to their original license; they are included here only to build a compatible
wire format.
