"""Camera sources for the fly. The phone is the camera.

Three ways to get frames in, all producing the same Frame object:

* MjpegCamera   — an MJPEG stream such as Android "IP Webcam"
                  (http://<phone-ip>:8080/video). Works on the laptop today
                  and on the phone itself later (http://127.0.0.1:8080/video).
* SnapshotCamera — polls a single-JPEG URL (IP Webcam's /shot.jpg).
* PushCamera    — frames POSTed by the browser page at /camera (dashboard.py)
                  using getUserMedia. No app install needed on the phone.

Frames are decoded with Pillow (no OpenCV: keeps Termux installs simple),
converted to a small grayscale array for the retina encoder, and a small JPEG
thumbnail is kept for the dashboard.
"""
from __future__ import annotations

import io
import threading
import time
import urllib.request
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

RETINA_SIZE = (64, 48)      # (width, height) fed to the encoder
THUMB_SIZE = (192, 144)     # dashboard preview


@dataclass
class Frame:
    gray: np.ndarray            # float32 HxW in 0..1, RETINA_SIZE
    thumb_jpeg: bytes           # small JPEG for the dashboard
    timestamp: float = field(default_factory=time.monotonic)
    rgb: np.ndarray | None = None   # float32 HxWx3 in 0..1, same crop as gray


def _crop_to_aspect(img: Image.Image, w: int, h: int) -> Image.Image:
    """Centre-crop to w:h instead of squashing. A phone held upright sends
    320x427; squeezing that into 64x48 made every object short and wide."""
    iw, ih = img.size
    target = w / h
    if iw / ih > target:                       # too wide: trim the sides
        nw = int(round(ih * target))
        x0 = (iw - nw) // 2
        return img.crop((x0, 0, x0 + nw, ih))
    nh = int(round(iw / target))               # too tall: keep the lower part,
    y0 = max(0, ih - nh - (ih - nh) // 4)      # where the floor and obstacles are
    return img.crop((0, y0, iw, y0 + nh))


def decode_jpeg(data: bytes) -> Frame | None:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    return frame_from_image(img)


def frame_from_image(img: Image.Image) -> Frame:
    rgb_img = _crop_to_aspect(img.convert("RGB"), *RETINA_SIZE)
    small = rgb_img.resize(RETINA_SIZE, Image.BILINEAR)
    rgb = np.asarray(small, np.float32) / 255.0
    gray = np.asarray(small.convert("L"), np.float32) / 255.0
    buf = io.BytesIO()
    rgb_img.resize(THUMB_SIZE, Image.BILINEAR).save(buf, "JPEG", quality=60)
    return Frame(gray=gray, thumb_jpeg=buf.getvalue(), rgb=rgb)


class CameraSource:
    name = "none"

    def latest(self) -> Frame | None:
        return None

    def close(self) -> None:
        pass


class NoCamera(CameraSource):
    pass


class PushCamera(CameraSource):
    """Frames arrive from the /camera/push HTTP endpoint (see dashboard.py)."""
    name = "push"

    def __init__(self, stale_after: float = 2.0):
        self._frame: Frame | None = None
        self._raw: tuple[bytes, float] | None = None   # newest JPEG, not yet decoded
        self._lock = threading.Lock()
        self.stale_after = stale_after
        self.pushes = 0
        self.error: str | None = None

    def push(self, data: bytes, fmt: str | None = None) -> bool:
        """`data` is a JPEG, or raw RGB bytes when `fmt` is "rgb W H" (what
        eyecam.js sends: no JPEG encode on the phone's busy CPU).
        Just keep the bytes and answer at once: decoding here, on every
        push, cost CPU for frames the brain never used; latest() decodes
        only the newest one."""
        if fmt:
            try:
                kind, w, h = fmt.split()
                w, h = int(w), int(h)
            except ValueError:
                return False
            if kind != "rgb" or not (8 <= w <= 640 and 8 <= h <= 640) or len(data) != w * h * 3:
                return False
            item = ("rgb", w, h, data)
        else:
            if len(data) < 4 or data[:2] != b"\xff\xd8":
                return False
            item = ("jpeg", 0, 0, data)
        with self._lock:
            self._raw = (item, time.monotonic())
            self.pushes += 1
        return True

    def latest(self) -> Frame | None:
        with self._lock:
            raw, self._raw = self._raw, None
        if raw is not None:
            kind, w, h, data = raw[0]
            frame = (frame_from_image(Image.frombytes("RGB", (w, h), data)) if kind == "rgb"
                     else decode_jpeg(data))
            if frame is not None:
                frame.timestamp = raw[1]          # age counts from arrival
                with self._lock:
                    self._frame = frame
                self.error = None
            else:
                self.error = "bad frame from the browser"
        with self._lock:
            frame = self._frame
        if frame and time.monotonic() - frame.timestamp > self.stale_after:
            return None
        return frame

    def age(self) -> float | None:
        with self._lock:
            ts = self._raw[1] if self._raw else (self._frame.timestamp if self._frame else None)
        return None if ts is None else time.monotonic() - ts


class _ThreadedCamera(CameraSource):
    def __init__(self, url: str, stale_after: float = 2.0):
        self.url = url
        self.stale_after = stale_after
        self._frame: Frame | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name=self.name)
        self._thread.start()

    def _store(self, jpeg: bytes) -> None:
        frame = decode_jpeg(jpeg)
        if frame is not None:
            with self._lock:
                self._frame = frame

    def latest(self) -> Frame | None:
        with self._lock:
            frame = self._frame
        if frame and time.monotonic() - frame.timestamp > self.stale_after:
            return None
        return frame

    def close(self) -> None:
        self._stop.set()

    def _run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class MjpegCamera(_ThreadedCamera):
    name = "mjpeg"

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with urllib.request.urlopen(self.url, timeout=5) as resp:
                    self.error = None
                    buf = b""
                    while not self._stop.is_set():
                        chunk = resp.read(16384)
                        if not chunk:
                            break
                        buf += chunk
                        # Extract complete JPEGs (SOI .. EOI); tolerate any
                        # multipart boundary format by ignoring the headers.
                        while True:
                            start = buf.find(b"\xff\xd8")
                            if start < 0:
                                buf = buf[-2:]
                                break
                            end = buf.find(b"\xff\xd9", start + 2)
                            if end < 0:
                                buf = buf[start:]
                                break
                            self._store(buf[start:end + 2])
                            buf = buf[end + 2:]
                        if len(buf) > 4_000_000:   # runaway without a valid frame
                            buf = b""
            except Exception as exc:  # network hiccup: retry
                self.error = str(exc)
                time.sleep(1.0)


class SnapshotCamera(_ThreadedCamera):
    name = "snapshot"

    def __init__(self, url: str, hz: float = 8.0, **kw):
        self.period = 1.0 / hz
        super().__init__(url, **kw)

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                with urllib.request.urlopen(self.url, timeout=3) as resp:
                    self._store(resp.read())
                self.error = None
            except Exception as exc:
                self.error = str(exc)
                time.sleep(0.5)
            time.sleep(max(0.0, self.period - (time.monotonic() - started)))


class WebcamCamera(_ThreadedCamera):
    """Local webcam via OpenCV (laptop only; optional dependency
    opencv-python-headless). spec "webcam" or a device index like "0"."""
    name = "webcam"

    def __init__(self, index: int = 0, hz: float = 10.0, **kw):
        self.index = index
        self.period = 1.0 / hz
        super().__init__(f"webcam:{index}", **kw)

    def _run(self) -> None:
        try:
            import cv2
        except ImportError:
            self.error = "pip install opencv-python-headless for --camera webcam"
            return
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW) if hasattr(cv2, "CAP_DSHOW") else cv2.VideoCapture(self.index)
        if not cap.isOpened():
            self.error = f"could not open webcam {self.index}"
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                ok, bgr = cap.read()
                if ok:
                    ok2, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok2:
                        self._store(jpeg.tobytes())
                        self.error = None
                else:
                    self.error = "webcam read failed"
                    time.sleep(0.2)
                time.sleep(max(0.0, self.period - (time.monotonic() - started)))
        finally:
            cap.release()


def open_camera(spec: str) -> CameraSource:
    """spec: "none", "push", "webcam" / a device index, or a URL. URLs ending
    in /video (or containing "mjpeg") are MJPEG streams, others snapshot URLs."""
    spec = (spec or "none").strip()
    if spec == "none":
        return NoCamera()
    if spec == "push":
        return PushCamera()
    if spec == "webcam" or spec.isdigit():
        return WebcamCamera(0 if spec == "webcam" else int(spec))
    lowered = spec.lower()
    if lowered.endswith("/video") or "mjpeg" in lowered or "mjpg" in lowered:
        return MjpegCamera(spec)
    return SnapshotCamera(spec)
