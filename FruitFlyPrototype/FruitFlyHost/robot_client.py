"""HTTP client for the ESP32 robot, plus a simulator for bench-free testing.

The ESP32 stays authoritative for obstacle safety, emergency stop, motor GPIO
and command timeouts. This module only talks to its /api endpoints.
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class RobotStatus:
    distance_cm: float = 999.0
    obstacle: bool = False
    emergency_stop: bool = False
    fault: bool = False
    source: str = ""
    command: str = ""
    speed: int = 0
    bump_front: bool = False
    bump_rear: bool = False
    cliff_front: bool = False
    cliff_rear: bool = False

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "RobotStatus":
        return cls(
            distance_cm=float(payload.get("distance_cm", 999.0)),
            obstacle=bool(payload.get("obstacle", False)),
            emergency_stop=bool(payload.get("emergency_stop", False)),
            fault=bool(payload.get("fault", False)),
            source=str(payload.get("source", "")),
            command=str(payload.get("command", "")),
            speed=int(payload.get("speed", 0)),
            bump_front=bool(payload.get("bump_front", False)),
            bump_rear=bool(payload.get("bump_rear", False)),
            cliff_front=bool(payload.get("cliff_front", False)),
            cliff_rear=bool(payload.get("cliff_rear", False)),
        )

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class RobotClient:
    def __init__(self, base_url: str, timeout: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, method: str = "GET", payload: dict | None = None) -> dict:
        data = None
        headers = {}
        if payload is not None:
            # The ESP32's intentionally small parser expects the compact
            # form {"action":"STOP","speed":0}; it does not tolerate
            # whitespace between JSON keys and values.
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def status(self) -> RobotStatus:
        return RobotStatus.from_json(self._request("/api/status"))

    def command(self, action: str, speed: int = 0, rc: bool = False) -> dict:
        # rc=True is manual RC drive: the firmware skips range/bumper/cliff
        # blocking for it (e-stop and the heartbeat timeout still apply).
        payload = {"action": action, "speed": int(speed)}
        if rc:
            payload["rc"] = 1
        return self._request("/api/command", "POST", payload)

    def heartbeat(self) -> dict:
        return self.command("HEARTBEAT", 0)

    def stop(self) -> dict:
        # Use the JSON command route rather than /api/stop. Some ESP32
        # WebServer builds reject an empty POST body on the convenience route.
        return self.command("STOP", 0)


class SimulatedRobot:
    """Stands in for the ESP32 so the brain + dashboard can be exercised on a
    laptop or phone with no hardware attached. Distance oscillates so the
    looming pathway gets exercised; the obstacle hysteresis mirrors config.h."""

    def __init__(self, period_s: float = 12.0, fixed_cm: float | None = None):
        self.t0 = time.monotonic()
        self.period = period_s
        self.fixed_cm = fixed_cm      # hold the range constant (camera-only tests)
        self.obstacle = False
        self.estop = False
        self.last_action = "STOP"
        self.last_speed = 0

    def status(self) -> RobotStatus:
        t = time.monotonic() - self.t0
        # Sweep 15 cm .. 150 cm
        dist = self.fixed_cm if self.fixed_cm is not None else 82.5 + 67.5 * math.sin(2 * math.pi * t / self.period)
        if self.obstacle and dist >= 35.0:
            self.obstacle = False
        elif not self.obstacle and dist <= 30.0:
            self.obstacle = True
        return RobotStatus(
            distance_cm=round(dist, 1),
            obstacle=self.obstacle,
            emergency_stop=self.estop,
            source="SIM",
            command=self.last_action,
            speed=self.last_speed,
        )

    def command(self, action: str, speed: int = 0, rc: bool = False) -> dict:
        if action == "EMERGENCY_STOP":
            self.estop = True
        elif action == "CLEAR_EMERGENCY":
            self.estop = False
        elif action != "HEARTBEAT":
            self.last_action, self.last_speed = action, int(speed)
        return {"ok": True}

    def heartbeat(self) -> dict:
        return {"ok": True}

    def stop(self) -> dict:
        return self.command("STOP", 0)


ConnectionErrors = (OSError, urllib.error.URLError, json.JSONDecodeError)
