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
  const o = Object.assign({ fps: 8, width: 320, facing: 'environment', quality: 0.6, timeoutMs: 1500, onStatus: () => {} }, opts || {});
  const video = document.createElement('video');
  video.setAttribute('playsinline', ''); video.muted = true; video.autoplay = true;
  const canvas = document.createElement('canvas');
  let stream = null, timer = null, sent = 0, failed = 0, paused = false, inflight = false;
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
    const w = o.width, h = Math.round(w * video.videoHeight / video.videoWidth);
    canvas.width = w; canvas.height = h;
    canvas.getContext('2d').drawImage(video, 0, 0, w, h);
    inflight = true;
    // A push that never answers used to hold `inflight` forever and freeze
    // the stream ("sent 9" and nothing after). Abort it so the next tick
    // sends a fresh frame instead.
    const ctl = new AbortController(), kill = setTimeout(() => ctl.abort(), o.timeoutMs);
    canvas.toBlob(b => {
      if (!b) { clearTimeout(kill); inflight = false; return; }
      fetch('/camera/push', { method: 'POST', headers: { 'Content-Type': 'image/jpeg' }, body: b, signal: ctl.signal, cache: 'no-store' })
        .then(r => { if (r.ok) sent++; else failed++; })
        .catch(() => failed++)
        .finally(() => { clearTimeout(kill); inflight = false; status(`streaming ${w}x${h}  sent ${sent}` + (failed ? `  dropped ${failed}` : '')); });
    }, 'image/jpeg', o.quality);
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
