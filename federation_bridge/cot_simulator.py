# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate random CoT events for testing."""

import asyncio
import random
import string
import time
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("federation-bridge.simulator")

# Swedish locations (lat/lon bounding box roughly covering Sweden)
LOCATIONS = [
    (59.3293, 18.0686, "Stockholm"),
    (57.7089, 11.9746, "Gothenburg"),
    (55.6050, 13.0038, "Malmo"),
    (63.8258, 20.2630, "Umea"),
    (67.8558, 20.2253, "Kiruna"),
    (58.4108, 15.6214, "Linkoping"),
    (59.8586, 17.6389, "Uppsala"),
    (56.0465, 12.6945, "Helsingborg"),
    (65.5848, 17.5350, "Vilhelmina"),
    (60.6749, 17.1413, "Gavle"),
]

COT_TYPES = [
    "a-f-G-U-C",       # friendly ground unit combat (matches BUCKEYE)
]

CALLSIGNS = [
    "ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO",
    "FOXTROT", "GOLF", "HOTEL", "INDIA", "JULIET",
]


def _random_uid() -> str:
    return "SIM-" + "".join(random.choices(string.hexdigits[:16], k=8))


def generate_cot_xml(uid: str = None, callsign: str = None, groups=None) -> str:
    """Generate a random CoT XML event.

    If ``groups`` (a list of FedHub federation group names) is given, they are
    embedded as a <__fedhubgroups groups="a,b"/> element so the simulated event
    carries its federation group across the wire — the same carrier the bridge
    uses for real traffic. This makes the group survive the CoT Output / data
    diode direction and, when group embedding is enabled, the FedHub direction.
    """
    if not uid:
        uid = _random_uid()
    if not callsign:
        callsign = random.choice(CALLSIGNS) + "-" + str(random.randint(1, 99))

    lat, lon, location_name = random.choice(LOCATIONS)
    # Add some randomness to position (+/- ~5km)
    lat += random.uniform(-0.05, 0.05)
    lon += random.uniform(-0.05, 0.05)

    cot_type = random.choice(COT_TYPES)
    now = datetime.now(timezone.utc)
    stale = now + timedelta(minutes=5)
    speed = random.uniform(0, 30)
    course = random.uniform(0, 360)

    time_str = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    stale_str = stale.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    fedhub_groups = ""
    if groups:
        fedhub_groups = f'<__fedhubgroups groups="{",".join(groups)}"/>'

    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<event version="2.0" uid="{uid}" type="{cot_type}" '
        f'time="{time_str}" start="{time_str}" stale="{stale_str}" how="h-e">'
        f'<point lat="{lat:.6f}" lon="{lon:.6f}" hae="0" ce="9999999" le="9999999"/>'
        f'<detail>'
        f'<contact callsign="{callsign}" endpoint="*:-1:stcp"/>'
        f'<__group name="Cyan" role="Team Member"/>'
        f'<takv device="Federation Bridge" platform="SIM" os="linux" version="1.0"/>'
        f'<uid Droid="{callsign}"/>'
        f'<track speed="{speed:.1f}" course="{course:.1f}"/>'
        f'<status battery="100"/>'
        f'<precisionlocation altsrc="GPS" geopointsrc="GPS"/>'
        f'{fedhub_groups}'
        f'</detail>'
        f'</event>'
    )


class CotSimulator:
    """Generates random CoT events at a configurable interval."""

    def __init__(self, bridge_name: str, interval: float = 5.0, num_tracks: int = 5,
                 groups=None):
        self.bridge_name = bridge_name
        self.interval = interval
        self.num_tracks = num_tracks
        self.groups = groups or None
        self._running = False
        # Persistent track UIDs/callsigns so they update position over time
        self._tracks = [
            (_random_uid(), random.choice(CALLSIGNS) + "-" + str(i + 1))
            for i in range(num_tracks)
        ]

    async def run(self, targets):
        """Generate CoT events and put each on every target.

        ``targets`` is a list of ``{"queue": asyncio.Queue, "embed_groups": bool}``.
        The same simulated track is routed to each target (e.g. toward FedHub
        via the CoT->protobuf queue, toward the CoT output via the protobuf->CoT
        queue, or both). For targets with ``embed_groups`` True the configured
        federation group(s) are embedded as <__fedhubgroups>; otherwise the
        event is emitted without them.
        """
        self._running = True
        logger.info(
            f"[{self.bridge_name}] Simulator started: {self.num_tracks} tracks, "
            f"{self.interval}s interval, {len(targets)} target(s)"
        )
        cycle = 0
        while self._running:
            try:
                for uid, callsign in self._tracks:
                    for t in targets:
                        groups = self.groups if t.get("embed_groups") else None
                        cot = generate_cot_xml(uid=uid, callsign=callsign, groups=groups)
                        await t["queue"].put(cot)
                cycle += 1
                if cycle <= 3 or cycle % 5 == 0:
                    logger.info(
                        f"[{self.bridge_name}] Simulator cycle #{cycle}: queued {self.num_tracks} CoT events "
                        f"to {len(targets)} target(s)"
                    )
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
        logger.info(f"[{self.bridge_name}] Simulator stopped")

    def stop(self):
        self._running = False
