/* eyecam.js — phone camera -> POST /camera/push, shared by / and /camera.
 *
 * Why a shared module: Android Chrome freezes a background tab's video
 * track but keeps a throttled setInterval alive, so a camera page left in
 * another tab kept re-sending its last frame forever and the fly "saw" a
 * still image. This module (1) stops pushing the moment the page is hidden
 * or the track reports muted, (2) restarts when the page is visible again,
 * and (3) lets the dashboard itself own the camera so no tab switch is
 * needed at all.
 *
 * Usage:  const cam = EyeCam({onStatus: txt => ..., fps: 8, width: 320, facing: 'environment'});
 *         cam.start(); cam.stop(); cam.running
 */
function EyeCam(opts) {
  const o = Object.assign({ fps: 10, width: 128, facing: 'environment', timeoutMs: 1500, onStatus: () => {} }, opts || {});
  const video = document.createElement('video');
  video.setAttribute('playsinline', ''); video.muted = true; video.autoplay = true;
  const canvas = document.createElement('canvas');
  let stream = null, timer = null, sent = 0, failed = 0, paused = false, inflight = false, fps = 0, lastDone = performance.now();
  const api = { running: false, video, sent: () => sent };

  function status(t) { o.onStatus(t, api); }

  function track() { return stream ? stream.getVideoTracks()[0] : null; }

  function live() {
    const t = track();
    return t && t.readyState === 'live' && !t.muted && !document.hidden && video.videoWidth > 0;
  }

  function push() {
    if (!api.running) return;
    if (!live()) {
      if (!paused) { paused = true; status('paused — page hidden or camera muted; nothing is sent'); }
      return;
    }
    if (paused) { paused = false; status('streaming again'); }
    if (inflight) return;                       // never queue frames behind a slow link
    // Raw pixels, not JPEG. canvas.toBlob('image/jpeg') took ~1 s per frame
    // on a phone whose CPU the brain keeps busy, so the fly saw one still
    // photo a second and the stream kept dropping out. The retina only
    // uses 64x48, so grab a 4:3 crop at 128x96 and send its RGB bytes
    // (36 KB): getImageData costs ~1 ms.
    const W = o.width, H = Math.round(o.width * 3 / 4);
    const vw = video.videoWidth, vh = video.videoHeight;
    let sx = 0, sy = 0, sw = vw, sh = vh;
    if (vw / vh > 4 / 3) { sw = Math.round(vh * 4 / 3); sx = (vw - sw) >> 1; }
    else { sh = Math.round(vw * 3 / 4); sy = Math.max(0, vh - sh - ((vh - sh) >> 2)); }   // keep the floor end
    const t0 = performance.now();
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(video, sx, sy, sw, sh, 0, 0, W, H);
    const rgba = ctx.getImageData(0, 0, W, H).data, rgb = new Uint8Array(W * H * 3);
    for (let i = 0, j = 0; i < rgba.length; i += 4, j += 3) { rgb[j] = rgba[i]; rgb[j + 1] = rgba[i + 1]; rgb[j + 2] = rgba[i + 2]; }
    const t1 = performance.now();
    inflight = true;
    // A push that never answers used to hold `inflight` forever and freeze
    // the stream ("sent 9" and nothing after). Abort it so the next tick
    // sends a fresh frame instead.
    const ctl = new AbortController(), kill = setTimeout(() => ctl.abort(), o.timeoutMs);
    fetch('/camera/push', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream', 'X-Frame': `rgb ${W} ${H}` },
                            body: rgb, signal: ctl.signal, cache: 'no-store' })
      .then(r => { if (r.ok) sent++; else failed++; })
      .catch(() => failed++)
      .finally(() => {
        clearTimeout(kill); inflight = false;
        const now = performance.now();
        fps = 0.8 * fps + 0.2 * (1000 / Math.max(1, now - lastDone)); lastDone = now;
        status(`live ${W}x${H} · ${fps.toFixed(1)} fps · sent ${sent}` + (failed ? ` dropped ${failed}` : '') +
               ` · grab ${Math.round(t1 - t0)} ms, upload ${Math.round(now - t1)} ms`);
      });
  }

  api.start = async function (overrides) {
    Object.assign(o, overrides || {});
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status('this browser blocks the camera on plain http — open http://127.0.0.1:8642/ on the phone, or use IP Webcam');
      return false;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: o.facing }, width: { ideal: 640 } }, audio: false });
    } catch (e) { status('camera error: ' + e.message); return false; }
    video.srcObject = stream;
    try { await video.play(); } catch (e) { /* autoplay policies: user already tapped */ }
    const t = track();
    if (t) {
      t.onmute = () => status('camera muted by the browser (tab in background?)');
      t.onunmute = () => status('camera live');
      t.onended = () => api.stop('camera was taken away');
    }
    api.running = true; paused = false; sent = 0; failed = 0;
    timer = setInterval(push, 1000 / o.fps);
    status('starting…');
    return true;
  };

  api.stop = function (why) {
    clearInterval(timer); timer = null;
    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
    video.srcObject = null;
    api.running = false;
    status(why || 'stopped');
  };

  document.addEventListener('visibilitychange', () => { if (api.running) push(); });
  addEventListener('pagehide', () => { if (api.running) api.stop('page closed'); });
  return api;
}
