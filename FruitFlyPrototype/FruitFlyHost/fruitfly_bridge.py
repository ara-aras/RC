"""FruitFly host controller for the ESP32 robot.

    phone camera + robot range sensor
        -> Retina (vision.py)                 drives LPLC2 / LC4 / LC10a
        -> FlyBrain (MaleCNS connectome)      untrained, 166,700 neurons
        -> descending-neuron readouts         DNp01 / DNa02 / DNg100 / MDN
        -> FORWARD / LEFT / RIGHT / ROTATE_* / STOP + speed
        -> ESP32 /api/command                 firmware keeps final authority

Default behaviour is dry-run: the brain runs and the dashboard shows it, but
only STOP/heartbeat is sent. Lift the wheels before --enable-motors.

Examples
    python fruitfly_bridge.py --sim-robot                       # no hardware, dashboard only
    python fruitfly_bridge.py --camera http://192.168.4.3:8080/video
    python fruitfly_bridge.py --camera push                     # phone browser -> /camera
    python fruitfly_bridge.py --enable-motors --demo-forward --allow-turns --camera push
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camera import PushCamera, open_camera            # noqa: E402
from dashboard import Dashboard, NeuronLayout         # noqa: E402
from fly_controller import WATCH_TYPES, Decision, FruitFlyController, Policy  # noqa: E402
from robot_client import ConnectionErrors, RobotClient, RobotStatus, SimulatedRobot  # noqa: E402


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.4.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--robot-url", default=os.environ.get("ROBOT_URL", "http://192.168.4.1"))
    p.add_argument("--sim-robot", action="store_true", help="no ESP32: simulate telemetry")
    p.add_argument("--sim-distance", type=float, default=None,
                   help="with --sim-robot: hold the range at this many cm (camera-only tests)")
    p.add_argument("--camera", default=os.environ.get("FLY_CAMERA", "none"),
                   help="none | push | webcam | MJPEG/snapshot URL (e.g. http://<phone>:8080/video)")
    p.add_argument("--device", choices=("auto", "cuda", "cpu"), default=os.environ.get("FLY_DEVICE", "auto"))
    p.add_argument("--hz", type=float, default=10.0, help="control ticks per second (1-25)")
    p.add_argument("--steps-per-tick", type=int, default=2, help="brain steps (20 ms each) per tick")
    p.add_argument("--seed", type=int, default=64)
    p.add_argument("--dashboard-port", type=int, default=int(os.environ.get("FLY_DASHBOARD_PORT", "8642")))
    p.add_argument("--no-dashboard", action="store_true")
    p.add_argument("--enable-motors", action="store_true")
    p.add_argument("--demo-forward", action="store_true")
    p.add_argument("--allow-turns", action="store_true")
    p.add_argument("--max-speed", type=int, default=Policy.max_speed, help="PWM 0..255 for cruising and turning")
    p.add_argument("--min-speed", type=int, default=Policy.min_speed, help="never send a slower PWM than this (motor stall floor)")
    p.add_argument("--clear-cm", type=float, default=Policy.clear_cm, help="a turn may end once the range reads this far")
    p.add_argument("--mode", choices=("fly", "manual"), default="fly", help="start in fly or manual (/controls page) mode")
    p.add_argument("--escape-threshold", type=float, default=Policy.escape_threshold)
    p.add_argument("--steer-threshold", type=float, default=Policy.steer_threshold)
    p.add_argument("--quiet", action="store_true", help="no per-tick console line")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.hz <= 0 or args.hz > 25:
        sys.exit("--hz must be between 0 and 25")
    if args.max_speed < 0 or args.max_speed > 255:
        sys.exit("--max-speed must be 0..255")
    if args.min_speed < 0 or args.min_speed > args.max_speed:
        sys.exit("--min-speed must be 0..max-speed")

    from flybrain import FlyBrain   # slow import (numba); after arg errors

    robot = SimulatedRobot(fixed_cm=args.sim_distance) if args.sim_robot else RobotClient(args.robot_url)
    camera = open_camera(args.camera)
    policy = Policy(enable_motors=args.enable_motors, demo_forward=args.demo_forward,
                    allow_turns=args.allow_turns, max_speed=args.max_speed, min_speed=args.min_speed,
                    clear_cm=args.clear_cm,
                    escape_threshold=args.escape_threshold, steer_threshold=args.steer_threshold)

    print("loading MaleCNS connectome ...", flush=True)
    brain = FlyBrain(device=args.device, seed=args.seed, sensory_input=False)
    controller = FruitFlyController(brain, policy, steps_per_tick=args.steps_per_tick, hz=args.hz)

    dashboard = None
    if not args.no_dashboard:
        layout = NeuronLayout(brain, WATCH_TYPES)
        dashboard = Dashboard(args.dashboard_port, layout,
                              push_camera=camera if isinstance(camera, PushCamera) else None,
                              mode=args.mode)
        dashboard.start()
        ip = local_ip()
        print(f"dashboard    http://{ip}:{args.dashboard_port}/")
        print(f"controls     http://{ip}:{args.dashboard_port}/controls")
        if isinstance(camera, PushCamera):
            print(f"phone camera http://{ip}:{args.dashboard_port}/camera")

    print(f"brain device={brain.device}  robot={'SIM' if args.sim_robot else args.robot_url}  camera={camera.name}")
    print(f"motors={'ENABLED' if args.enable_motors else 'DRY-RUN'} demo_forward={args.demo_forward} "
          f"turns={args.allow_turns} speed={args.min_speed}..{args.max_speed} hz={args.hz:g} "
          f"steps/tick={args.steps_per_tick} mode={args.mode}", flush=True)
    print("warming up numba ...", flush=True)
    controller.tick(None, RobotStatus())

    period = 1.0 / args.hz
    tick = 0
    last_heartbeat = 0.0
    last_status = RobotStatus()
    connected = False
    try:
        while True:
            started = time.monotonic()
            tick += 1
            frame = camera.latest()
            gray = frame.gray if frame else None
            sent = ("STOP", 0)
            try:
                status = robot.status()
                connected = True
                last_status = status
                decision = controller.tick(gray, status)

                # Dashboard buttons: e-stop / clear go straight to the robot.
                for req in (dashboard.pop_requests() if dashboard else []):
                    robot.command(req, 0)
                    print(f"dashboard -> {req}", flush=True)

                mode = dashboard.mode if dashboard else "fly"
                if mode == "manual":
                    # /controls page drives like an RC car. A press is
                    # repeated by the page while held; anything older than
                    # manual_stale is a dropped connection and becomes STOP.
                    # rc=True: the ESP32 does not block it for range,
                    # bumpers or cliffs — only e-stop and link loss stop it.
                    m_action, m_speed = dashboard.manual_command()
                    robot.command(m_action, m_speed, rc=True)
                    sent = (m_action, m_speed)
                    decision = Decision(m_action, m_speed, "manual control from /controls")
                elif args.enable_motors:
                    robot.command(decision.action, decision.speed)
                    sent = (decision.action, decision.speed)
                else:
                    # Dry-run must actively cancel any motion left by an
                    # earlier process; merely withholding commands is not
                    # enough because the ESP32 may still be inside its
                    # command timeout window.
                    robot.stop()
                now = time.monotonic()
                if now - last_heartbeat >= 0.4:
                    robot.heartbeat()
                    last_heartbeat = now
                if not args.quiet:
                    r = controller.rates
                    print(f"t={tick:5d} dist={status.distance_cm:6.1f} cam={'Y' if gray is not None else '-'} "
                          f"esc L{r['escape_L']:.2f} R{r['escape_R']:.2f} steer L{r['steer_L']:.2f} R{r['steer_R']:.2f} "
                          f"-> {decision.action:12s} {decision.speed:3d}  {decision.reason}", flush=True)
            except ConnectionErrors as exc:
                connected = False
                decision = None
                print(f"connection error: {exc}; sending no motion", flush=True)
                if args.enable_motors:
                    try:
                        robot.stop()
                    except ConnectionErrors:
                        pass
                # keep the brain ticking so the dashboard stays alive
                controller.tick(gray, last_status)

            if dashboard:
                snap = controller.snapshot()
                snap["fired_local"] = dashboard.layout.fired_local(controller.last_fired)
                dashboard.publish({
                    "ok": True,
                    "tick": tick,
                    "time": time.time(),
                    "robot": {**last_status.as_dict(), "connected": connected, "sim": args.sim_robot},
                    "camera": {"source": camera.name, "has_frame": gray is not None,
                               "error": getattr(camera, "error", None),
                               # what the retina sees, 32x24 grey levels for the eye views
                               "eye": (np.round(gray[::2, ::2] * 255).astype(int).ravel().tolist()
                                       if gray is not None else None),
                               "eye_mask": (controller.retina.mask[::2, ::2].astype(int).ravel().tolist()
                                            if gray is not None and controller.retina.mask is not None else None),
                               "eye_w": 32, "eye_h": 24},
                    "policy": policy.__dict__,
                    "mode": dashboard.mode,
                    "decision": (decision.__dict__ if decision else {"action": "STOP", "speed": 0, "reason": "robot offline"}),
                    "sent": {"action": sent[0], "speed": sent[1]},
                    "brain": snap,
                }, frame.thumb_jpeg if frame else None)

            time.sleep(max(0.0, period - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    finally:
        try:
            robot.stop()
        except ConnectionErrors:
            pass
        camera.close()
        if dashboard:
            dashboard.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
