# Running the fly brain on an Android phone

This guide takes a phone with nothing on it to a phone that runs the fruit-fly
connectome, watches through a camera and drives the ESP32 robot. Nothing else
is needed once it is set up: no laptop, no internet.

Every command is in its own box. Copy the box, paste it into the terminal,
press Enter, and read the "you should see" line before moving on. If what you
see does not match, stop and check the troubleshooting section at the end
before trying the next command.

Two words you will see a lot:

* **Termux** — a Linux terminal app for Android. This is where you type.
* **Debian** — a full Linux installed *inside* Termux. The fly host runs in
  here. You "log in" to it from Termux and "exit" back out.

---

## 0. What you need

* An Android phone, Android 8 or newer, 64-bit (`arm64`). Any phone from
  the last five years is fine. At least 4 GB of RAM; the brain needs about
  600 MB while running.
* About 2 GB of free storage (Debian, Python packages and the 260 MB brain
  files).
* Internet (Wi-Fi or mobile data) for the setup steps only. When the phone
  is joined to the robot's Wi-Fi it has no internet, so everything is
  downloaded first.
* The robot, with its ESP32 already flashed. You will not need a cable to
  the robot at any point; everything is over Wi-Fi.
* About 45 minutes the first time.

---

## 1. Install Termux from GitHub

Do **not** install Termux from the Google Play Store. That build was
abandoned in 2020 and cannot install packages any more. Get it from GitHub
(or F-Droid, see below).

1. On the phone, open this page in the browser:
   https://github.com/termux/termux-app/releases
2. Under the newest release, open **Assets** and download the file named like
   `termux-app_v0.118.x+github-debug_universal.apk`. If you know the phone
   is 64-bit you can take the `arm64-v8a` one instead; `universal` always
   works.
3. Open the downloaded file. Android will say the browser is not allowed to
   install unknown apps: tap **Settings**, turn on **Allow from this
   source**, go back and tap **Install**.
4. Open Termux once. You should see a black screen with a `$` prompt.

Alternative: install the **F-Droid** app from https://f-droid.org, then
install Termux from inside F-Droid. Same app, and F-Droid keeps it updated
for you. Do not mix the two: pick GitHub or F-Droid and stay with it.

Tips for typing in Termux:

* The row of extra keys above the keyboard has **CTRL**, **ESC**, **TAB**
  and arrows. Ctrl+C is: tap **CTRL**, then tap **C**. If the row is not
  visible, Volume-Up + Q toggles it.
* Volume-Down works as Ctrl too: Volume-Down + C is also Ctrl+C.
* Long-press the screen to get **Paste**. Copy text elsewhere, long-press in
  Termux, tap Paste.
* A command is only run when you press Enter.

---

## 2. First Termux setup

Update the package lists and upgrade everything:

```bash
pkg update -y && pkg upgrade -y -o Dpkg::Options::=--force-confnew
```

You should see: a lot of downloading, then the `$` prompt again. If a
question about a config file appears anyway, press Enter to accept the
default.

Give Termux access to the phone's shared storage (photos, downloads):

```bash
termux-setup-storage
```

Android shows a permission dialog: tap **Allow**. This creates a folder
`~/storage` in Termux that points at `/sdcard`, the same files you see in the
Files app. It is optional for the robot, but useful for copying files in
and out.

Install the tools this guide needs:

```bash
pkg install -y proot-distro git wget termux-api
```

You should see: `proot-distro` among the installed packages and the `$`
prompt.

Stop Android from killing Termux while the robot runs. In the phone's
**Settings → Apps → Termux → Battery**, choose **Unrestricted** (on some
phones this is called "Don't optimise" or "No restrictions"). Also turn off
any "adaptive battery" or "app sleeping" for Termux if your phone has it
(Samsung: Settings → Battery → Background usage limits → Never sleeping apps
→ add Termux).

Ask Termux to keep the CPU awake:

```bash
termux-wake-lock
```

You should see: a permanent Termux notification saying "wake lock held".
Run this every time before a long session.

---

## 3. Install Debian inside Termux

Download and unpack Debian (about 150 MB, takes a few minutes):

```bash
proot-distro install debian
```

You should see: `Installing debian...` and finally something like
`[*] Installation completed`.

Log in to Debian:

```bash
proot-distro login debian
```

You should see: the prompt changes to `root@localhost:~#`. That `root@` is
how you tell you are **inside Debian**. Everything in sections 4 to 11 is
typed at this prompt.

To leave Debian and go back to plain Termux:

```bash
exit
```

The prompt returns to `$`. To get back in, run `proot-distro login debian`
again. Nothing is lost between logins; the files stay where you left them.

---

## 4. Set up Debian

Make sure you are inside Debian (prompt starts with `root@localhost`), then
install the basics:

```bash
apt update && apt install -y python3 python3-venv python3-pip git curl nano
```

You should see: packages downloading, then `root@localhost:~#`.

Check the phone is 64-bit:

```bash
uname -m
```

You should see: `aarch64`. If it prints `armv7l` or `armv8l`, the phone is
running 32-bit and the brain cannot be installed; stop here and tell the
project owner.

Check the Python version:

```bash
python3 --version
```

You should see: `Python 3.10` or higher (Debian 12 gives 3.11, Debian 13
gives 3.13, both fine).

---

## 5. Get the code

The project lives in a **private** GitHub repository, so cloning it needs
permission. Three ways, pick one:

**Option A — the owner adds you as a collaborator.** Then use your own
GitHub username and a token (below) when git asks.

**Option B — the owner makes the repository public.** Then the plain clone
below works with no token.

**Option C — the owner gives you a token.** A GitHub *personal access
token* is a long string that acts as a password for git. The owner creates
it on github.com: profile picture → **Settings** → **Developer settings** →
**Personal access tokens** → **Fine-grained tokens** → **Generate new
token**, choose the `RC` repository, and under Repository permissions set
**Contents: Read-only**. Copy the token; it is shown only once.

Plain clone (options A and B; git will ask for username and password —
paste the token as the password):

```bash
git clone https://github.com/ara-aras/RC.git ~/RC
```

Clone with the token in the address (option C; replace `<TOKEN>` with the
token, keep the `@`):

```bash
git clone https://<TOKEN>@github.com/ara-aras/RC.git ~/RC
```

You should see: `Receiving objects: 100%` and the prompt. If you see
`Repository not found` or `Authentication failed`, the token is wrong or
does not cover this repository.

Go into the project:

```bash
cd ~/RC/FruitFlyPrototype
```

Check it worked:

```bash
ls
```

You should see: `FruitFlyHost  RobotPrototype  ESP32CAM  WIRING.md  ...`.

---

## 6. Python environment

Still inside Debian, inside `~/RC/FruitFlyPrototype`. Create a private
Python environment so packages do not mix with the system:

```bash
python3 -m venv .venv
```

Activate it (note the leading dot and space):

```bash
. .venv/bin/activate
```

You should see: the prompt now starts with `(.venv)`. **Every time** you log
back in to Debian you must run this activate command again before running
the fly host, otherwise Python cannot find the packages.

Upgrade the installer:

```bash
pip install --upgrade pip
```

Install the packages (about 200 MB; a few minutes):

```bash
pip install -r FruitFlyHost/requirements.txt
```

You should see: lines like `Downloading numba-...-manylinux_..._aarch64.whl`
and at the end `Successfully installed flybrain-... numba-... numpy-...`.

If instead you see `Building wheel for llvmlite` or `Building wheel for
numba` that runs for many minutes, press Ctrl+C and stop: pip did not find
a ready-made 64-bit package and is trying to compile it, which will fail on
a phone. Re-check `uname -m` in section 4 and tell the project owner.

Confirm everything imports:

```bash
python -c "import flybrain, numba, numpy, scipy, PIL; print('ok')"
```

You should see: `ok`.

---

## 7. Download the brain files

The connectome itself is a separate 260 MB download (needs internet, once):

```bash
python -m flybrain download
```

You should see: a progress bar for `brain.npz` and `weights.npz`, then
`sha256 ok` or similar. The files land in `~/fly-data` inside Debian.

Check:

```bash
flybrain info
```

You should see: neuron and synapse counts (166,700 neurons).

Optional, only if the phone is short on internal storage: keep the brain on
shared storage instead. Move the folder once and tell the host where it is:

```bash
mv ~/fly-data /sdcard/fly-data
```

```bash
echo 'export FLY_DATA=/sdcard/fly-data' >> ~/.bashrc
```

```bash
export FLY_DATA=/sdcard/fly-data
```

---

## 8. Smoke test: brain only, no robot, no camera

Go to the host folder:

```bash
cd ~/RC/FruitFlyPrototype/FruitFlyHost
```

Make the launcher executable (once):

```bash
chmod +x run.sh
```

Run the brain against a simulated robot:

```bash
./run.sh --sim-robot --quiet
```

The first run compiles the simulation kernels; expect 30 to 90 seconds of
`warming up numba ...` with nothing else happening. Then you should see:

```
brain device=cpu  robot=SIM  camera=none
dashboard    http://127.0.0.1:8642/
controls     http://127.0.0.1:8642/controls
```

Leave that running. Open **Chrome** on the phone and go to:

```
http://127.0.0.1:8642/
```

You should see the dashboard: a cloud of dots shaped like a fly brain that
flickers as neurons fire, and "robot simulated" at the top. Split-screen is
handy here (recent-apps button → tap the Termux icon → Split screen).

Check the brain speed on this phone:

```bash
curl -s http://127.0.0.1:8642/api/state | grep -o '"step_ms": *[0-9.]*'
```

(Run this in a second Termux session: swipe in from the left edge of the
screen → **New session**, then `proot-distro login debian` there. Or just
read "brain step" on the dashboard.) Under 100 ms is comfortable. Above
150 ms is still usable at the default 5 ticks per second; tell the owner
the number.

Stop the host: go back to the Termux tab where it runs and press Ctrl+C
(tap **CTRL** on the extra-keys row, then **C**; or Volume-Down + C).

---

## 9. Choose a camera

The fly needs eyes. Three options; A is the simplest.

### Option A — the phone's own camera, through Chrome (no app)

Start the host with the push camera:

```bash
./run.sh --sim-robot --camera push --quiet
```

In Chrome open the dashboard itself:

```
http://127.0.0.1:8642/
```

Tap the **Camera** button in the bottom bar (on a laptop screen it is
**Use this camera** in the header) and allow the camera. The dashboard now
streams the phone camera to the fly by itself — one tab, nothing to switch.
The two "eye" tiles show the picture and "camera push" turns green.

Do not use a second tab for the camera. Android freezes the camera of any
tab that is not in front, so the fly would keep seeing the last picture
from that tab. If you do use `http://127.0.0.1:8642/camera`, it now stops
sending when it is hidden, and the dashboard says "push, no frames".

Keep Chrome in the foreground while the fly runs (split-screen with Termux
is fine). The line under the decision on the dashboard says "streaming
320x240 sent N" while frames are flowing, and "paused" when they are not.

### Option B — the IP Webcam app (survives screen-off)

Install **IP Webcam** (by Pavel Khlebovich) from the Play Store. Open it,
scroll to the bottom, tap **Start server**. It shows an address; ignore it,
the host reads it locally:

```bash
./run.sh --sim-robot --camera http://127.0.0.1:8080/video --quiet
```

### Option C — the ESP32-CAM on the robot (no phone camera needed)

If the robot has its camera module fitted and powered, it appears on the
robot's Wi-Fi at `192.168.4.20`. Once the phone is on the robot's Wi-Fi
(next section):

```bash
./run.sh --camera http://192.168.4.20/stream --quiet
```

Test the picture first by opening `http://192.168.4.20/stream` in Chrome.

Whichever option: the dashboard's "camera" indicator must be green and the
eye tiles must show an image before moving on.

---

## 10. Connect the phone to the robot

1. Power on the robot. Wait 10 seconds.
2. Phone **Settings → Wi-Fi**, join the network **RobotPrototype**,
   password:

```
robot1234
```

3. Android will complain "This network has no internet access". Tap **Keep
   connection** / **Stay connected** / **Use this network anyway**. If the
   phone keeps jumping back to mobile data, open the network's settings and
   turn off "Switch to mobile data automatically" (Samsung: Wi-Fi →
   Advanced → "Switch to mobile data"; Pixel: Network → Internet →
   Network preferences → "Switch to mobile data automatically").
4. Some phones show a "Sign in to network" notification; ignore it.

Check the phone can talk to the robot (inside Debian):

```bash
curl -s -m 3 http://192.168.4.1/api/status
```

You should see one line of JSON containing `"distance_cm":` and
`"emergency_stop":false`. If nothing prints, or `Connection timed out`, the
phone is not on the RobotPrototype Wi-Fi; go back to step 2.

Now open a **second Termux session** for the wake lock: swipe in from the
left edge of the screen, tap **New session**. In that new `$` prompt (plain
Termux, not Debian):

```bash
termux-wake-lock
```

Swipe from the left edge again and tap the first session to go back to
Debian.

---

## 11. Drive

Three stages. Do them in order; each one proves something before the next
adds risk. Put the robot on the floor with space around it, or on a box
with the wheels in the air for the first two.

### Stage 1 — dry run (robot receives only stop)

```bash
./run.sh --camera push --quiet
```

(Swap `--camera push` for your option B or C address if that is what you
chose.) Open the dashboard `http://127.0.0.1:8642/`. You should see:
**robot online**, the range value changing when you put a hand in front of
the ultrasonic sensor, the big word at the top changing between "forward",
"stop" and "rotate ..." as you wave something at the camera, and "dry run:
robot receives stop only". The wheels do not move. This is correct.

### Stage 2 — manual control (you drive, from the phone)

With the host still running, open:

```
http://127.0.0.1:8642/controls
```

Switch the toggle at the top to **Manual**. Set the speed slider (200 is a
good start). Press and **hold** an arrow: the robot moves while your finger
is down and stops when you let go. Manual mode works even though you did
not start the host with `--enable-motors`; touching the pad is your
permission. Manual is RC-car drive: the robot does **not** stop for its
range sensor or bumpers, so it will drive into a wall if you tell it to.
Only **Emergency stop** and losing the Wi-Fi (it stops within about a
second) override you.

If left and right are swapped, tell the owner: there is a one-line setting
in the firmware for that.

Switch back to **Fly brain** when done (the pad greys out).

### Stage 3 — the fly drives

Stop the host (Ctrl+C) and start it with motors enabled:

```bash
./run.sh --camera push --enable-motors --demo-forward --allow-turns
```

What to expect:

* It drives forward at about 200 out of 255 when the way is clear.
* Something looming at the camera, or the ultrasonic reading 30 cm or less,
  makes it freeze for a moment, back up for under a second, then rotate
  away. The rotation keeps going until the range sensor reads at least
  45 cm **and** the eyes see nothing growing, then it drives on.
* Turns that follow each other quickly go the same way (so it does not
  bounce between two walls); the third one in a short time backs up
  further and turns roughly twice as far.
* Without `--quiet` the terminal prints one line per tick with the
  decision and the reason.

**Emergency stop** is the red button on the dashboard and on the controls
page. It latches: the robot ignores everything until **Clear stop**. Also,
closing the host, or the phone dropping the Wi-Fi, stops the robot within
1.5 seconds on its own.

Stop with Ctrl+C; the host sends a final stop to the robot as it exits.

---

## 12. Every day after setup

The short version. Each line is a separate command.

```bash
termux-wake-lock
```

```bash
proot-distro login debian
```

```bash
cd ~/RC/FruitFlyPrototype
```

```bash
. .venv/bin/activate
```

```bash
cd FruitFlyHost
```

```bash
./run.sh --camera push --enable-motors --demo-forward --allow-turns
```

Then join the RobotPrototype Wi-Fi, open `http://127.0.0.1:8642/`, tap
**Camera** in the bottom bar, and watch the fly drive.

To get the newest code when the owner has changed something (needs
internet, so do it before joining the robot Wi-Fi):

```bash
cd ~/RC && git pull
```

If `git pull` reports changed `requirements.txt`, repeat the
`pip install -r FruitFlyHost/requirements.txt` line from section 6 with the
venv active.

---

## 13. Troubleshooting

| What you see | What it means | Do this |
|---|---|---|
| `proot-distro: command not found` | Section 2 install did not finish, or you are inside Debian | `exit` to Termux, then `pkg install -y proot-distro` |
| `pkg: command not found` / `Unable to locate package` | Termux from the Play Store | Uninstall it, install from GitHub (section 1) |
| `ERROR: No matching distribution found for numba` | 32-bit phone or blocked package index | Check `uname -m` is `aarch64`; try again on a different internet connection |
| `Building wheel for llvmlite` running for minutes | Same as above | Ctrl+C, do not wait for it; report to the owner |
| `ModuleNotFoundError: No module named 'flybrain'` | The venv is not active | `cd ~/RC/FruitFlyPrototype` then `. .venv/bin/activate` (prompt shows `(.venv)`) |
| `FileNotFoundError ... brain.npz` | Brain files missing or `FLY_DATA` points to the wrong place | `python -m flybrain download`, or check `echo $FLY_DATA` |
| Chrome says "site can't be reached" for 127.0.0.1:8642 | The host is not running, or you typed the address wrong | Look at the Termux tab: is the `dashboard http://...` line there? Address is `http://127.0.0.1:8642/` (not https) |
| Dashboard shows "push, no frames" | Camera not started, or Chrome is in the background | Tap **Camera** on the dashboard; keep Chrome on screen (split-screen). Don't run the camera in a second tab |
| Dashboard shows "robot offline" | The phone is not on the RobotPrototype Wi-Fi, or the robot is off | Re-join the Wi-Fi, run the `curl ... /api/status` check from section 10 |
| Robot goes offline every minute or so | Android switched back to mobile data | Disable "switch to mobile data automatically" for this network; keep the phone near the robot |
| `connection error ... sending no motion` in the host output | Same Wi-Fi drop, seen from the host | As above; the robot stops itself after 1.5 s without contact |
| Termux disappears or the host stops when the screen is off | Battery optimisation killed it | Settings → Apps → Termux → Battery → Unrestricted; run `termux-wake-lock` in a plain Termux session |
| `warming up numba ...` for more than 3 minutes | Compile is starved of CPU | Ctrl+C, then `export NUMBA_NUM_THREADS=4` and run again |
| `brain step` on the dashboard above 150 ms | Slow phone | Fine at the default 5 Hz; do not add `--hz` higher than 5. Close other apps |
| Decision is always "stop", reason `range NN cm` | Something is within 30 cm of the ultrasonic sensor | Expected; move the robot or the object |
| The controls page says "switch to manual first" | Toggle is on Fly brain | Tap Manual at the top of the page |
| Left and right are swapped when driving | Motor wiring is mirrored | Tell the owner; `SWAP_LEFT_RIGHT` in the firmware fixes it |
| `git pull` asks for a password | Token needed | Paste the personal access token as the password (section 5) |

To see the full error when something crashes, run the host without
`--quiet` and read the last lines.

---

## 14. Flags you can add to `./run.sh`

`run.sh` runs `fruitfly_bridge.py` with phone defaults already set: CPU
brain (`FLY_DEVICE=cpu`), 5 ticks per second (`--hz 5`), one 20 ms brain
step per tick (`--steps-per-tick 1`). Anything you add after `./run.sh`
is passed straight through.

| Flag | Meaning | Default |
|---|---|---|
| `--sim-robot` | No robot; fake range sensor sweeping 15–150 cm. For testing the brain and camera | off |
| `--camera X` | `none`, `push` (Chrome page), `webcam` (laptop only), or a URL (`http://127.0.0.1:8080/video`, `http://192.168.4.20/stream`) | `none` |
| `--enable-motors` | Let the fly's decisions reach the wheels. Without it, only stop is sent | off |
| `--demo-forward` | With motors enabled: drive forward when the way is clear (and back up when blocked) | off |
| `--allow-turns` | With motors enabled: allow rotating and steering | off |
| `--max-speed N` | Fastest PWM sent, 0–255 | 200 |
| `--min-speed N` | Slowest PWM ever sent (the motors stall below this) | 120 |
| `--clear-cm N` | A turn may end once the range sensor reads at least this | 45 |
| `--mode fly` / `--mode manual` | Which mode to start in; the controls page can switch later | `fly` |
| `--hz N` | Ticks per second; leave at 5 on a phone | 5 (set by run.sh) |
| `--quiet` | No per-tick line in the terminal | off |
| `--device cpu` | Force the CPU brain (run.sh already does this) | cpu |
| `--robot-url http://...` | Robot address, if not `http://192.168.4.1` | `http://192.168.4.1` |
| `--escape-threshold N`, `--steer-threshold N` | How easily DNp01 / DNa02 activity triggers a manoeuvre; leave alone unless the owner says | 0.2 / 0.2 |

Example: slower, calmer robot for a small room:

```bash
./run.sh --camera push --enable-motors --demo-forward --allow-turns --max-speed 160 --clear-cm 60
```
