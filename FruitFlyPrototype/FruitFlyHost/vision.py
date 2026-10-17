"""Retina: turns a camera frame (+ the range sensor) into drive for the fly's
own visual projection neurons.

Why not feed pixels to the photoreceptors? flybrain's authors found the
photoreceptor -> lamina relay does not carry signal in a spiking model (the
real neurons are graded). The route that works is to drive the identified
feature-detector types directly, on the side where the thing is:

    LPLC2  looming (angular growth of an object)      -> DNp01 escape
    LC4    fast looming / imminent collision           -> DNp01/02/04 escape
    LC10a  a target worth tracking                     -> DNa02 steering
    LC11   small moving object                         (motion, not used for control)

The encoder below is deliberately simple and hand-tuned. It is *a model* of
the fly's eye; everything downstream of these neurons is the connectome.

How "object" is found (the camera moves with the robot, so a learned still
background does not work — every pixel changes as the robot drives, and the
phone's auto-exposure shifts the whole frame's brightness):

    floor model   colour (r, g chromaticity) and brightness relative to the
                  frame median, learned from a patch just in front of the
                  wheels — only while the range sensor says the way is clear,
                  so it never learns an obstacle as floor
    obstacle      a pixel below the horizon that is not floor, and the one
                  above it is not floor either (kills single-pixel noise)
    nearness      per column: how little free floor there is between the
                  bottom of the image and the first obstacle (0 = floor to
                  the horizon, 1 = obstacle at the bumper). This is the
                  classic appearance-based free-space method for monocular
                  robots; it ignores walls far away and the ceiling.

Per eye (left / right half of the frame):
    size      nearness of that half (mean blended with its 90th percentile,
              so a chair leg counts as well as a wall), smoothed over frames
    growth    increase of the smoothed size (looming)
    motion    mean absolute difference of the normalised frame (moving stuff)
    open      1 - size (free floor ahead)

The range sensor is fused as a "threat" so the fly still flinches when the
camera is off or dark.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Population code. The brain's tonic input parks every neuron at ~0.78 V
# (threshold 1.0), so any voltage above ~0.22 fires a cell on every step:
# graded *voltage* is nearly binary. Instead each channel's intensity (0..1)
# selects the FRACTION of that cell type that receives `volt`, like an object
# covering part of the eye. Rates downstream then scale with intensity.
ENCODER = {
    "loom_gain": 12.0,      # growth of smoothed nearness (per frame) -> LPLC2 intensity
    "loom_size": 0.8,       # nearness -> LPLC2, looming cells also like big close things
    "threat_gain": 1.0,     # (range-derived + collision) -> LC4 intensity
    "collision_size": 0.60, # nearness above this counts as "about to hit"
    "chase_gain": 0.5,      # open-floor asymmetry -> LC10a (steer toward open side)
    "motion_gain": 2.0,     # motion -> LC11 (display only by default)
    "volt": 0.5,            # voltage given to the selected cells
    "min_frac": 0.03,       # below this intensity nothing is driven
    "range_near_cm": 30.0,  # ultrasonic: full threat here
    "range_far_cm": 60.0,   # ...and none here; also: floor may be learned beyond this
    "horizon": 0.35,        # rows above this fraction of the height are never obstacles
    "floor_k": 3.0,         # non-floor = more than k std from the floor model in any feature
    "floor_min_std": 0.035, # std never below this, so a very uniform floor is not hair-trigger
    "floor_max_std": 0.10,  # ...and never above this, so the model cannot go blind
    "relearn_frames": 15,   # clear-range frames in a row that disagree before relearning (~3 s at 5 Hz)
    "floor_rate": 0.08,     # floor model learning rate per frame (while the way is clear)
    "smooth": 0.5,          # weight of the new frame in the size EMA
}

CHANNELS = {
    "loom": ["LPLC2"],
    "threat": ["LC4"],
    "chase": ["LC10a"],
    "motion": ["LC11"],
}


@dataclass
class RetinaState:
    """Per-eye features from the last frame, for the dashboard."""
    size: dict = field(default_factory=lambda: {"L": 0.0, "R": 0.0})
    growth: dict = field(default_factory=lambda: {"L": 0.0, "R": 0.0})
    motion: dict = field(default_factory=lambda: {"L": 0.0, "R": 0.0})
    open: dict = field(default_factory=lambda: {"L": 0.0, "R": 0.0})
    range_threat: float = 0.0
    has_frame: bool = False


class Retina:
    def __init__(self, brain, **encoder):
        unknown = set(encoder) - set(ENCODER)
        if unknown:
            raise ValueError(f"unknown encoder parameters: {sorted(unknown)}")
        self.p = {**ENCODER, **encoder}
        self.cells = {ch: {s: np.sort(brain.cells(types, s)) for s in "LR"} for ch, types in CHANNELS.items()}
        self.prev_norm: np.ndarray | None = None     # last normalised brightness, for motion
        self.floor_mu: np.ndarray | None = None      # floor model: per-feature mean ...
        self.floor_sd: np.ndarray | None = None      # ... and spread
        self.disagree = 0                            # clear frames whose patch did not match the model
        self.mask: np.ndarray | None = None          # last obstacle mask, for the dashboard
        self.floor_seen: np.ndarray | None = None    # pixels that matched the floor last frame
        self.prev_size = {"L": 0.0, "R": 0.0}        # smoothed size from the last frame
        self.state = RetinaState()
        # drive amounts from the last call, keyed "loomL", "threatR", ...
        self.last: dict[str, float] = {f"{ch}{s}": 0.0 for ch in CHANNELS for s in "LR"}

    # ------------------------------------------------------------------
    def range_threat(self, distance_cm: float, obstacle: bool) -> float:
        p = self.p
        if obstacle or distance_cm <= p["range_near_cm"]:
            return 1.0
        if distance_cm >= p["range_far_cm"]:
            return 0.0
        return float((p["range_far_cm"] - distance_cm) / (p["range_far_cm"] - p["range_near_cm"]))

    def _pixel_features(self, img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(features HxWxF, normalised brightness HxW). Features are
        exposure-invariant: brightness / median brightness of the floor seen
        last frame (the whole-frame median moves when a big bright or dark
        object fills half the view), plus r and g chromaticity when the
        frame has colour."""
        if img.ndim == 3:
            s = img.sum(axis=2) + 1e-3
            lum = img.mean(axis=2)
        else:
            lum = img
        ref = self.floor_seen
        if ref is not None and ref.shape == lum.shape and ref.sum() >= 50:
            base = float(np.median(lum[ref]))
        else:
            base = float(np.median(lum))
        norm = lum / max(base, 0.05)
        norm = np.clip(norm, 0.0, 3.0) / 3.0          # 0..1, median ~0.33
        if img.ndim == 3:
            feats = np.stack([norm, img[..., 0] / s, img[..., 1] / s], axis=2)
        else:
            feats = norm[..., None]
        return feats.astype(np.float32), norm.astype(np.float32)

    def _relearn(self, patch: np.ndarray) -> None:
        # Median / MAD: half the patch can be junk and the model still
        # describes the other half.
        mu = np.median(patch, axis=0)
        self.floor_mu = mu
        self.floor_sd = 1.4826 * np.median(np.abs(patch - mu), axis=0)
        self.disagree = 0

    def _features(self, img: np.ndarray, way_clear: bool) -> dict[str, dict[str, float]]:
        p = self.p
        feats, norm = self._pixel_features(img)
        h, w = norm.shape
        half = w // 2
        hz = int(round(h * p["horizon"]))

        # Floor reference: the patch right in front of the wheels.
        patch = feats[h - max(3, h // 8):, int(w * 0.3):int(w * 0.7)].reshape(-1, feats.shape[2])
        if self.floor_mu is None or self.floor_mu.shape[0] != feats.shape[2]:
            self._relearn(patch)
        else:
            # Learn only from patch pixels that already look like floor: an
            # object beside the ultrasonic's narrow beam can sit in the
            # patch and must not be absorbed. Faster while the range sensor
            # confirms the way is clear, slowly otherwise (lighting drifts).
            sd0 = np.maximum(self.floor_sd, p["floor_min_std"])
            inl = (np.abs(patch - self.floor_mu) / sd0).max(axis=1) <= p["floor_k"]
            if inl.mean() >= 0.6:
                self.disagree = 0
                r = p["floor_rate"] * (1.0 if way_clear else 0.25)
                self.floor_mu = (1 - r) * self.floor_mu + r * patch[inl].mean(0)
                self.floor_sd = (1 - r) * self.floor_sd + r * patch[inl].std(0)
            else:
                # The model no longer matches what is under the camera: the
                # floor changed (rug -> tiles), the first model was bad, or
                # the phone is not on the robot (held in a hand), so the
                # robot's range sensor says nothing about this view. Relearn
                # after ~1 s if the range is clear, ~3 s if not.
                self.disagree += 1
                if self.disagree >= (p["relearn_frames"] // 3 if way_clear else p["relearn_frames"]):
                    self._relearn(patch)
        sd = np.clip(self.floor_sd, p["floor_min_std"], p["floor_max_std"])

        # Not floor in any feature, below the horizon, and confirmed by the
        # pixel above it (a lone speck of noise is not an obstacle).
        dev = (np.abs(feats - self.floor_mu) / sd).max(axis=2)
        nf = dev > p["floor_k"]
        nf[:hz] = False
        obj = nf.copy()
        obj[1:] &= nf[:-1]
        obj[hz] = False
        self.mask = obj
        floor_seen = ~nf
        floor_seen[:hz] = False
        self.floor_seen = floor_seen

        # Nearness per column: free floor between the bottom row and the
        # lowest obstacle pixel, as a fraction of the floor region.
        region = h - hz
        flipped = obj[::-1]                          # row 0 = bottom of the image
        has = flipped.any(axis=0)
        first = np.where(has, flipped.argmax(axis=0), region)
        near = 1.0 - np.minimum(first, region) / float(region)

        diff = np.abs(norm - self.prev_norm) if self.prev_norm is not None else np.zeros_like(norm)
        out: dict[str, dict[str, float]] = {"size": {}, "growth": {}, "motion": {}, "open": {}}
        a = p["smooth"]
        for side, sl in (("L", slice(0, half)), ("R", slice(half, w))):
            col = near[sl]
            raw = 0.5 * float(col.mean()) + 0.5 * float(np.percentile(col, 90))
            size = a * raw + (1 - a) * self.prev_size[side]
            out["size"][side] = size
            out["growth"][side] = max(0.0, size - self.prev_size[side])
            out["motion"][side] = float(diff[:, sl].mean())
            out["open"][side] = 1.0 - size
            self.prev_size[side] = size
        self.prev_norm = norm
        return out

    # ------------------------------------------------------------------
    def inject(self, image: np.ndarray | None, distance_cm: float, obstacle: bool) -> list:
        """Build the (neuron indices, voltage) list for brain.step(inject=...).
        `image` is HxWx3 RGB or HxW grey, values 0..1."""
        p = self.p
        drive = {key: 0.0 for key in self.last}
        rt = self.range_threat(distance_cm, obstacle)
        st = RetinaState(range_threat=rt, has_frame=image is not None)

        if image is not None:
            way_clear = not obstacle and distance_cm >= p["range_far_cm"]
            f = self._features(image, way_clear)
            st.size, st.growth, st.motion, st.open = f["size"], f["growth"], f["motion"], f["open"]
            for s in "LR":
                drive[f"loom{s}"] = min(1.0, f["growth"][s] * p["loom_gain"] + f["size"][s] * p["loom_size"])
                collision = 1.0 if f["size"][s] > p["collision_size"] else 0.0
                drive[f"threat{s}"] = min(1.0, max(rt, collision) * p["threat_gain"])
                drive[f"motion{s}"] = min(1.0, f["motion"][s] * p["motion_gain"])
            # Steer toward the more open side: LC10a on that side.
            gap = f["open"]["L"] - f["open"]["R"]
            if abs(gap) > 0.05:
                s = "L" if gap > 0 else "R"
                drive[f"chase{s}"] = min(1.0, abs(gap) * p["chase_gain"] * 4.0)
        else:
            # No camera: range sensor alone, symmetric.
            for s in "LR":
                drive[f"threat{s}"] = min(1.0, rt * p["threat_gain"])
                drive[f"loom{s}"] = min(1.0, rt * 0.5)

        self.state = st
        self.last = drive
        inject = []
        for key, frac in drive.items():
            if frac < p["min_frac"]:
                continue
            cells = self.cells[key[:-1]][key[-1]]
            k = max(1, int(round(frac * len(cells))))
            inject.append((cells[:k], float(p["volt"])))
        return inject
