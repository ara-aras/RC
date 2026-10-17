# FruitFly host controller

Runs the MaleCNS fruit-fly connectome (`flybrain`, 166,700 neurons) on the
host — laptop now, phone (Termux + Debian) later — and sends only high-level
intents to the ESP32. The firmware keeps final authority over obstacle
stops, emergency stop, motor GPIO and command timeouts.

```text
phone camera ──► Retina (vision.py) ──► LPLC2 / LC4 / LC10a
robot range  ─┘                            │
                                 FlyBrain.step() × N   (untrained connectome)
                                           │
                        DNp01 escape · DNa02 steer · DNg100 forward · MDN back
                                           │
                             decoder (fly_controller.py) ──► FORWARD / LEFT /
                                                             RIGHT / ROTATE_* / STOP
                                           │
                                  ESP32  POST /api/command
```

## Files

| File | Role |
|---|---|
| `fruitfly_bridge.py` | main loop, CLI, safety gating |
| `robot_client.py` | ESP32 HTTP client + `SimulatedRobot` for bench-free runs |
| `camera.py` | phone camera input: MJPEG URL, snapshot URL, or browser push |
| `vision.py` | retina: frame + range → population-coded drive on the fly's visual projection neurons |
| `fly_controller.py` | brain stepping, readout EMAs, saccade/avoidance state machine |
| `dashboard.py` + `static/` | live dashboard (`/`), phone camera page (`/camera`), manual drive page (`/controls`) |
| `calibrate.py` | measures readout rates per stimulus; use it to tune thresholds |
| `FLYBRAIN_NOTES.md` | what the connectome model is, findings, improvement roadmap |
| `run.ps1` / `run.sh` | launchers (Windows / Linux+Termux) |

## Quick start (laptop)

```powershell
cd C:\Projects\RC\FruitFlyPrototype
.\.venv\Scripts\python.exe -m pip install -r FruitFlyHost\requirements.txt
python -m flybrain download        # once, ~260 MB into ~/fly-data

# 1. No hardware: watch the brain react to a simulated range sweep
.\FruitFlyHost\run.ps1 -Sim
# open http://localhost:8642/
```

Then with the robot powered and the laptop joined to the `RobotPrototype`
Wi-Fi AP:

```powershell
# 2. Dry run against the real robot (only STOP/heartbeat is ever sent)
python FruitFlyHost\fruitfly_bridge.py --camera push
# 3. Bench test, wheels off the floor
python FruitFlyHost\fruitfly_bridge.py --camera push --enable-motors --demo-forward --allow-turns
```

## Camera

Pick one of:

**A. Browser push (no app).** Start the bridge with `--camera push`, open
`http://<host-ip>:8642/` on the phone and tap *Camera* (bottom bar) — the
dashboard streams the camera itself via `static/eyecam.js`, so no tab
switching; a background tab's camera is frozen by Android, and `eyecam.js`
stops sending in that case. `/camera` is the standalone page; tap *Start streaming*. The page
captures the rear camera with `getUserMedia` and POSTs JPEGs to the host.
Mobile browsers only allow camera access from HTTPS or `localhost`; for a
plain-HTTP laptop host enable
`chrome://flags/#unsafely-treat-insecure-origin-as-secure` for
`http://<host-ip>:8642`. When the host runs on the phone itself this is
`http://127.0.0.1:8642/camera` and works without flags.

**B. IP Webcam app (Android).** Start the app, note its URL, then
`--camera http://<phone-ip>:8080/video` (MJPEG) or
`--camera http://<phone-ip>:8080/shot.jpg` (snapshot polling). On the phone
itself this becomes `http://127.0.0.1:8080/video`.

**C. ESP32-CAM on the robot.** Flash `../ESP32CAM/` onto an AI Thinker
ESP32-CAM (see `../WIRING.md` §6). It joins the robot's AP at
`192.168.4.20` and streams MJPEG; run with
`--camera http://192.168.4.20/stream`. No phone camera needed, and the
phone can stay in your pocket.

The phone and the host must be on the same network as the robot (join the
robot's AP; it allows several clients).

## Driving by hand (`/controls`)

`http://<host>:8642/controls` is a phone page with a *Fly brain / Manual*
switch, a speed slider (120–255) and a hold-to-drive pad (forward, back,
arc left/right, rotate left/right). In manual mode the bridge sends what
you hold and STOPs the moment you let go or the page loses contact; the
brain keeps running so the dashboard stays live. Manual mode works **without**
`--enable-motors` — pressing the pad is the explicit consent.

Manual is **RC-car drive**: the bridge marks each command `"rc":1` and the
ESP32 then ignores its range sensor, bumpers and cliff sensors for it — you
can drive right up to a wall or with your hand over the sensor. Only two
things still stop it: the latched emergency stop, and losing the link (a
press goes stale after 1.0 s on the host; the ESP32 halts after 1.5 s
without heartbeats). Switch back to *Fly brain* to hand control to the
connectome; its commands never carry `rc`, so all firmware safety returns.

API behind the page:

| Route | Body / result |
|---|---|
| `GET /api/mode` | `{"mode": "fly" \| "manual"}` |
| `POST /api/mode` | `{"mode": "fly" \| "manual"}`; switching always clears any held press |
| `POST /api/manual` | `{"action": "FORWARD" \| "BACKWARD" \| "LEFT" \| "RIGHT" \| "ROTATE_LEFT" \| "ROTATE_RIGHT" \| "STOP", "speed": 0..255}`; repeat while held, a press older than 1.0 s becomes STOP (the page repeats every 150 ms); 409 unless mode is manual |
| `POST /api/estop`, `POST /api/clear` | emergency stop / clear, forwarded to the ESP32 |

`--mode manual` starts the bridge in manual mode.

## Dashboard (`http://<host>:8642/`)

* **Anatomy** — 20k of the 166,700 neurons at their measured MaleCNS
  positions in 3-D (brain up, nerve cord down; drag to rotate). Every spike
  this tick flashes; the watched left/right populations are blue/orange.
* **Eyes** — what each half of the camera frame looks like to the retina,
  with the "object" fraction that drives the looming cells.
* **Decision** — the verb the decoder chose, why, and what was actually sent
  (dry run always sends stop).
* **Signal path** — retina → LPLC2 / LC4 / LC10a → DNp01 / DNa02, left and
  right lanes lighting with live rates. DNp01's ceiling is 0.5 spikes/step.
* **Spikes, last 20 s** — a raster of the watched populations per eye; red
  ticks on the top edge mark escape decisions.
* **Emergency stop / Clear stop** — sent straight to the ESP32.
* **Drive** — link to `/controls`. The Robot section also shows the current
  mode and the avoidance state (`cruise`, `freeze`, `reverse`, `turn`,
  `refractory`) with how many escapes happened in the last 8 s.

Fonts load from Google Fonts when online and fall back to the system face
on the robot's AP; nothing else leaves the host.

If CuPy dies with `cudaErrorIllegalAddress` (seen once on this laptop), run
with `--device cpu`; the CPU path is the one the phone uses anyway.

## Safety gates

| Flag | Effect |
|---|---|
| (none) | dry-run: brain runs, dashboard live, robot only receives STOP + heartbeat |
| `--enable-motors` | decoder output is sent; still STOP unless the next two are set |
| `--demo-forward` | FORWARD (and BACKWARD during an avoid) allowed when nothing looms |
| `--allow-turns` | ROTATE away after an escape; LEFT/RIGHT from DNa02 asymmetry |
| `--max-speed N` | PWM for cruising and turning, default **200**/255 |
| `--min-speed N` | never send a lower PWM than this, default **120** (brushed BO motors stall below ~110) |
| `--clear-cm N` | a turn may end once the range reads at least this far, default **45** |
| `--mode fly\|manual` | start in fly or manual (`/controls`) mode, default fly |

Independent of these, the ESP32 stops at ≤ 30 cm (or LD2420 presence),
latches its own e-stop, and halts if heartbeats stop for 1.5 s. The range,
bumper and cliff stops are skipped for manual (`"rc":1`) commands only.

## How it avoids things

The old decoder turned for a fixed 0.8 s and re-armed immediately, which on
a wall produced forward → turn → forward → turn for ever. The decoder is now
a saccade state machine, after the optic-flow robots built on fly
behaviour: one trigger starts one committed manoeuvre.

```text
cruise ──trigger──► freeze ──► (reverse) ──► turn until clear ──► refractory ──► cruise
```

* **Triggers:** DNp01 looming above `escape_threshold` (turn only), the
  range sensor at or under `stop_cm` (back up, then turn), or the front
  bumper (back up, then turn).
* **Turn until clear:** the rotation ends when the range reads ≥ `clear_cm`
  *and* the retina's loom drive is under `loom_clear`, but never before
  `turn_min_s` and never after `turn_max_s`.
* **Direction memory:** escapes within `memory_s` of each other keep the
  same turning direction, so two facing walls do not make it ping-pong.
  Otherwise it turns away from the DNp01 side, else away from the fuller
  eye, else a coin flip.
* **Escalation:** the `escalate_after`-th escape inside `memory_s` backs up
  twice as long and turns about twice the minimum — it gives up on that
  heading.
* **Cruise speed** scales with DNg100 activity between `min_speed` and
  `max_speed` and drops 30 % inside `2 × stop_cm`.

Tunables in `fly_controller.py: Policy` (times are seconds; the controller
converts them to ticks with `--hz`, so they mean the same at 5 Hz on the
phone and 10 Hz on the laptop):

| Field | Default | |
|---|---|---|
| `stop_cm` | 30 | range trigger (matches the firmware's own stop) |
| `clear_cm` | 45 | range needed to end a turn (`--clear-cm`) |
| `freeze_s` | 0.2 | STOP before the manoeuvre |
| `reverse_s` | 0.7 | back-up time for range/bumper triggers |
| `turn_min_s` | 0.6 | shortest saccade |
| `turn_max_s` | 4.0 | give up turning after this |
| `refractory_s` | 0.8 | ignore DNp01 after a manoeuvre |
| `memory_s` | 8 | window for direction memory and escalation |
| `escalate_after` | 3 | n-th escape in the window escalates |
| `loom_clear` | 0.3 | retina loom drive that still counts as clear |
| `escape_threshold` / `steer_threshold` | 0.2 / 0.2 | DNp01 / DNa02 EMA thresholds |

Independent of these, the ESP32 stops at ≤ 30 cm (or LD2420 presence),
latches its own e-stop, and halts if heartbeats stop for 1.5 s.

## Tuning

`python FruitFlyHost\calibrate.py` prints readout rates at rest and under
each stimulus. With the shipped brain build:

```text
condition                escape_L  escape_R  steer_L  steer_R
rest                        0.000     0.013    0.013    0.007
loom L 0.4 (LPLC2)          0.233     0.027    0.027    0.000
loom+threat L 0.8           0.900     0.027    0.000    0.020
chase L 0.6 (LC10a)         0.007     0.007    0.067    0.000
```

Escape is strongly ipsilateral and clean; steering (DNa02) is weak.
Encoder gains live in `vision.py: ENCODER`.
