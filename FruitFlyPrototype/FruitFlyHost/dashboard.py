"""Live dashboard + phone camera page, served from a background thread.

Stdlib only (http.server) so it runs unchanged on the laptop and inside
Termux/Debian on the phone.

Routes
    GET  /               dashboard (brain scatter, readouts, retina, camera)
    GET  /api/state      current tick as JSON
    GET  /api/neurons    static neuron layout for the scatter (once)
    GET  /api/frame.jpg  latest camera thumbnail
    GET  /camera         phone page: getUserMedia -> POST /camera/push
    POST /camera/push    raw JPEG body (used by the /camera page)
    POST /api/estop      ask the bridge to send EMERGENCY_STOP
    POST /api/clear      ask the bridge to send CLEAR_EMERGENCY
    GET  /controls       phone page: fly / manual switch and a drive pad
    GET  /api/mode       {"mode": "fly"|"manual"}
    POST /api/mode       {"mode": "fly"|"manual"}
    POST /api/manual     {"action": "FORWARD", "speed": 180}; repeat while held,
                         the bridge sends STOP when a press goes stale
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

STATIC = Path(__file__).parent / "static"


class NeuronLayout:
    """A 2-D projection of a subsample of neurons for the scatter plot.
    fired indices from the brain are mapped into the subsample each tick."""

    def __init__(self, brain, watch_types: list[str], background: int = 20000, seed: int = 1):
        pos = brain.positions
        valid = np.flatnonzero(~np.isnan(pos).any(axis=1)) if pos is not None else np.empty(0, np.int64)
        watch_mask = np.isin(brain.cell_type, watch_types)
        watch_idx = np.intersect1d(valid, np.flatnonzero(watch_mask))
        rest = np.setdiff1d(valid, watch_idx)
        rng = np.random.default_rng(seed)
        bg = rng.choice(rest, size=min(background, len(rest)), replace=False) if len(rest) else rest
        self.sample = np.concatenate([watch_idx, bg]).astype(np.int64)
        self.lookup = np.full(brain.n, -1, np.int32)
        self.lookup[self.sample] = np.arange(len(self.sample), dtype=np.int32)

        p = pos[self.sample] if len(self.sample) else np.zeros((0, 3), np.float32)
        if len(p):
            # Anatomical axes from the data itself: body axis = direction from
            # the ventral nerve cord to the brain; lateral axis = left to right.
            # Output columns: 0 = body (brain positive), 1 = lateral (right
            # positive), 2 = the remaining (dorsal/ventral) axis.
            sc_s = brain.superclass[self.sample].astype(str)
            side_s = brain.side[self.sample].astype(str)
            body = p[np.char.startswith(sc_s, "cb_")].mean(0) - p[np.char.startswith(sc_s, "vnc_")].mean(0)
            axis_body = int(np.argmax(np.abs(body)))
            lateral = p[side_s == "R"].mean(0) - p[side_s == "L"].mean(0)
            lateral[axis_body] = 0
            axis_lat = int(np.argmax(np.abs(lateral)))
            axis_dv = ({0, 1, 2} - {axis_body, axis_lat}).pop()
            xyz = p[:, [axis_body, axis_lat, axis_dv]].astype(np.float32)
            xyz[:, 0] *= np.sign(body[axis_body])
            xyz[:, 1] *= np.sign(lateral[axis_lat])
            lo, hi = np.percentile(xyz, 0.5, axis=0), np.percentile(xyz, 99.5, axis=0)
            ext = np.maximum(hi - lo, 1e-6)
            xyz = (np.clip(xyz, lo, hi) - (lo + hi) / 2) / ext[0] * 2   # body axis spans -1..1
        else:
            xyz = np.zeros((0, 3), np.float32)
        sc = brain.superclass[self.sample] if brain.superclass is not None else np.array(["?"] * len(self.sample))
        self.classes = sorted(set(map(str, sc)))
        cls_index = {c: i for i, c in enumerate(self.classes)}
        ct = brain.cell_type[self.sample]
        side = brain.side[self.sample]
        self.payload = {
            "x": np.round(xyz[:, 0], 4).tolist(),
            "y": np.round(xyz[:, 1], 4).tolist(),
            "z": np.round(xyz[:, 2], 4).tolist(),
            "cls": [cls_index[str(c)] for c in sc],
            "classes": self.classes,
            "watch": {t: {s: np.flatnonzero((ct == t) & (side == s)).tolist() for s in "LR"} for t in watch_types},
            "n_total": int(brain.n),
            "n_sample": int(len(self.sample)),
        }

    def fired_local(self, fired: np.ndarray) -> list[int]:
        if fired is None or len(fired) == 0:
            return []
        local = self.lookup[np.asarray(fired, np.int64)]
        return local[local >= 0].tolist()


class Dashboard:
    MANUAL_ACTIONS = {"STOP", "FORWARD", "BACKWARD", "LEFT", "RIGHT", "ROTATE_LEFT", "ROTATE_RIGHT"}

    def __init__(self, port: int, layout: NeuronLayout | None, push_camera=None,
                 mode: str = "fly", manual_stale: float = 1.0):
        self.port = port
        self.layout = layout
        self.push_camera = push_camera
        self.mode = mode                      # "fly" | "manual"
        self.manual_stale = manual_stale
        self._manual = ("STOP", 0, 0.0)       # action, speed, monotonic time of the press
        self._lock = threading.Lock()
        self._state: dict = {"ok": False, "note": "waiting for first tick"}
        self._frame: bytes = b""
        self.requests: list[str] = []         # e-stop / clear requests for the bridge
        self._server = ThreadingHTTPServer(("0.0.0.0", port), self._handler())
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="dashboard")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()

    def publish(self, state: dict, frame_jpeg: bytes | None) -> None:
        with self._lock:
            self._state = state
            if frame_jpeg:
                self._frame = frame_jpeg

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self.mode = mode
            self._manual = ("STOP", 0, 0.0)   # switching modes never carries a press over

    def set_manual(self, action: str, speed: int) -> None:
        with self._lock:
            self._manual = (action, max(0, min(255, int(speed))), time.monotonic())

    def manual_command(self) -> tuple[str, int]:
        with self._lock:
            action, speed, when = self._manual
        if action == "STOP":
            return "STOP", 0
        age = time.monotonic() - when
        if age > self.manual_stale:
            # Log once per dropped press: a held button that goes stale is
            # Wi-Fi loss between phone and host, not the firmware stopping.
            with self._lock:
                if self._manual[2] == when:
                    self._manual = ("STOP", 0, when)
                    print(f"manual: {action} press went stale ({age:.2f} s without a repeat) -> STOP", flush=True)
            return "STOP", 0
        return action, speed

    def pop_requests(self) -> list[str]:
        with self._lock:
            reqs, self.requests = self.requests, []
        return reqs

    # ------------------------------------------------------------------
    def _handler(self):
        dash = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200) -> None:
                self._send(code, json.dumps(obj).encode(), "application/json")

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/":
                    self._send(200, (STATIC / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
                elif path == "/camera":
                    self._send(200, (STATIC / "camera.html").read_bytes(), "text/html; charset=utf-8")
                elif path == "/controls":
                    self._send(200, (STATIC / "controls.html").read_bytes(), "text/html; charset=utf-8")
                elif path == "/eyecam.js":
                    self._send(200, (STATIC / "eyecam.js").read_bytes(), "application/javascript; charset=utf-8")
                elif path == "/api/mode":
                    self._json({"mode": dash.mode})
                elif path == "/api/state":
                    with dash._lock:
                        state = dash._state
                    self._json(state)
                elif path == "/api/neurons":
                    self._json(dash.layout.payload if dash.layout else {"x": [], "y": [], "cls": [], "classes": [], "watch": {}})
                elif path == "/api/frame.jpg":
                    with dash._lock:
                        frame = dash._frame
                    if frame:
                        self._send(200, frame, "image/jpeg")
                    else:
                        self._send(404, b"no frame", "text/plain")
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self):
                path = self.path.split("?", 1)[0]
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if path == "/camera/push":
                    if dash.push_camera is None:
                        self._json({"ok": False, "error": "start the bridge with --camera push"}, 400)
                    elif dash.push_camera.push(body):
                        self._json({"ok": True})
                    else:
                        self._json({"ok": False, "error": "bad jpeg"}, 400)
                elif path == "/api/mode":
                    try:
                        mode = json.loads(body or b"{}").get("mode")
                    except json.JSONDecodeError:
                        mode = None
                    if mode not in ("fly", "manual"):
                        self._json({"ok": False, "error": "mode must be fly or manual"}, 400)
                    else:
                        dash.set_mode(mode)
                        self._json({"ok": True, "mode": mode})
                elif path == "/api/manual":
                    try:
                        req = json.loads(body or b"{}")
                    except json.JSONDecodeError:
                        req = {}
                    action = str(req.get("action", "")).upper()
                    if action not in dash.MANUAL_ACTIONS:
                        self._json({"ok": False, "error": "bad action"}, 400)
                    elif dash.mode != "manual":
                        self._json({"ok": False, "error": "switch to manual first"}, 409)
                    else:
                        dash.set_manual(action, int(req.get("speed", 0)))
                        self._json({"ok": True})
                elif path in ("/api/estop", "/api/clear"):
                    with dash._lock:
                        dash.requests.append("EMERGENCY_STOP" if path == "/api/estop" else "CLEAR_EMERGENCY")
                    self._json({"ok": True})
                else:
                    self._send(404, b"not found", "text/plain")

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

        return Handler
