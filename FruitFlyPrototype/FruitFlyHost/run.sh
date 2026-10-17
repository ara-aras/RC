#!/usr/bin/env bash
# Linux / Termux-Debian launcher. Same flags as fruitfly_bridge.py, plus
# sensible phone defaults: CPU brain, 5 Hz, one 20 ms brain step per tick.
#   ./run.sh --sim-robot
#   ./run.sh --camera http://127.0.0.1:8080/video --enable-motors --demo-forward
set -euo pipefail
cd "$(dirname "$0")"
if [ -x ../.venv/bin/python ]; then PY=../.venv/bin/python; else PY=python3; fi
export FLY_DEVICE="${FLY_DEVICE:-cpu}"
# Leave two cores free: on a phone, Chrome (camera capture + JPEG encode)
# and the dashboard server share this CPU with the brain. With every core
# given to numba the camera stream stalled.
CORES=$(nproc); BRAIN=$(( CORES > 3 ? CORES - 2 : 1 ))
export NUMBA_NUM_THREADS="${NUMBA_NUM_THREADS:-$BRAIN}"
exec "$PY" fruitfly_bridge.py --hz "${FLY_HZ:-5}" --steps-per-tick "${FLY_STEPS:-1}" "$@"
