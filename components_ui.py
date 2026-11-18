"""
Two tiny Streamlit components, written as plain HTML/JS (no build step). Their files are generated into a
temp folder at start-up, so the repo only needs this one .py file.

back_camera()  - capture a photo with the BACK camera.
                 * "Take photo" uses <input capture="environment">: on phones it opens the native camera app on
                   the rear camera (best quality, autofocus, no permission prompt).
                 * "Live view" uses getUserMedia with facingMode "environment" (exact first, then ideal for
                   laptops that have no rear camera), with a capture button.
                 Photos are downscaled in the browser (max 2048 px) before being sent to Python.
auth_store()   - remember a signed sign-in token in the browser's localStorage so a refresh doesn't log you out.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit.components.v1 as components

CAMERA_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  html,body{margin:0;padding:0;background:transparent;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;}
  .btn{display:block;width:100%;box-sizing:border-box;padding:16px 14px;border:0;border-radius:12px;margin:0 0 10px;
       font-size:1.05rem;font-weight:600;cursor:pointer;background:#ff4b4b;color:#fff;}
  .btn.alt{background:rgba(128,128,128,.22);color:inherit;}
  .btn:disabled{opacity:.6}
  video{width:100%;border-radius:12px;background:#000;display:block;margin-bottom:10px;max-height:70vh;object-fit:cover}
  #msg{font-size:.85rem;opacity:.8;margin:4px 0 8px;min-height:1em}
  #livebox{display:none}
</style></head><body>
<div id="root">
<button class="btn" id="native" type="button">📷 Take photo (back camera)</button>
<input id="file" type="file" accept="image/*" capture="environment" style="display:none">
<button class="btn alt" id="live" type="button">🎥 Live view (back camera)</button>
<div id="livebox">
  <video id="v" playsinline autoplay muted></video>
  <button class="btn" id="shot" type="button">● Capture</button>
  <button class="btn alt" id="stop" type="button">Close camera</button>
</div>
<div id="msg"></div>
</div>
<script>
  const MAX = 2048, QUALITY = 0.9;
  const sid = Math.random().toString(36).slice(2);
  let counter = 0, stream = null;
  const $ = (id) => document.getElementById(id);
  const post = (type, data) => window.parent.postMessage(Object.assign({isStreamlitMessage: true, type}, data), "*");
  let lastH = -1;
  const fit = () => {
    const h = Math.max(40, Math.ceil($("root").getBoundingClientRect().height) + 2);
    if (h !== lastH) { lastH = h; post("streamlit:setFrameHeight", {height: h}); }
  };
  const say = (t) => { $("msg").textContent = t || ""; fit(); };

  function emit(canvas, source) {
    counter += 1;
    post("streamlit:setComponentValue", {
      dataType: "json",
      value: {image: canvas.toDataURL("image/jpeg", QUALITY), id: sid, n: counter, source, w: canvas.width, h: canvas.height},
    });
  }
  function scaled(srcW, srcH, draw) {
    const k = Math.min(1, MAX / Math.max(srcW, srcH));
    const c = document.createElement("canvas");
    c.width = Math.round(srcW * k); c.height = Math.round(srcH * k);
    draw(c.getContext("2d"), c.width, c.height);
    return c;
  }

  // 1) native camera app / file chooser (rear camera on phones via capture="environment")
  $("native").onclick = () => { $("file").value = ""; $("file").click(); };
  $("file").onchange = (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    say("Preparing photo…");
    const url = URL.createObjectURL(f), img = new Image();
    img.onload = () => {            // createImageBitmap/Image apply the EXIF rotation in modern browsers
      emit(scaled(img.naturalWidth, img.naturalHeight, (g, w, h) => g.drawImage(img, 0, 0, w, h)), "native");
      URL.revokeObjectURL(url); say("Photo sent — reading it…");
    };
    img.onerror = () => say("Couldn't open that photo. Try again.");
    img.src = url;
  };

  // 2) live view, back camera
  async function openLive() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      say("This browser can't open the camera here. Use “Take photo” instead."); return;
    }
    const base = {width: {ideal: 1920}, height: {ideal: 1080}};
    const tries = [{video: Object.assign({facingMode: {exact: "environment"}}, base), audio: false},
                   {video: Object.assign({facingMode: {ideal: "environment"}}, base), audio: false},
                   {video: true, audio: false}];
    let err = null;
    for (const c of tries) {
      try { stream = await navigator.mediaDevices.getUserMedia(c); break; } catch (e) { err = e; stream = null; }
    }
    if (!stream) { say("Camera unavailable (" + (err && err.name || "blocked") + "). Allow camera access, or use “Take photo”."); return; }
    const track = stream.getVideoTracks()[0];
    try { await track.applyConstraints({advanced: [{focusMode: "continuous"}]}); } catch (e) {}
    $("v").srcObject = stream;
    $("livebox").style.display = "block"; $("live").style.display = "none"; $("native").style.display = "none";
    say("Hold steady, fill the frame with the question, then tap Capture.");
  }
  function closeLive() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null; $("v").srcObject = null;
    $("livebox").style.display = "none"; $("live").style.display = "block"; $("native").style.display = "block"; say("");
  }
  $("live").onclick = openLive;
  $("stop").onclick = closeLive;
  $("shot").onclick = () => {
    const v = $("v");
    if (!v.videoWidth) { say("Camera is still starting — try again in a second."); return; }
    emit(scaled(v.videoWidth, v.videoHeight, (g, w, h) => g.drawImage(v, 0, 0, w, h)), "live");
    closeLive(); say("Photo sent — reading it…");
  };

  window.addEventListener("message", (ev) => { if (ev.data && ev.data.type === "streamlit:render") fit(); });
  post("streamlit:componentReady", {apiVersion: 1});
  if (window.ResizeObserver) new ResizeObserver(fit).observe($("root"));
  $("v").addEventListener("loadedmetadata", fit);
  fit();
</script></body></html>
"""

AUTH_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"></head><body style="margin:0">
<script>
  const KEY = "aipc01_auth_v1";
  const post = (type, data) => window.parent.postMessage(Object.assign({isStreamlitMessage: true, type}, data), "*");
  let last = "__unset__";
  const read = () => { try { return localStorage.getItem(KEY); } catch (e) { return null; } };
  window.addEventListener("message", (ev) => {
    if (!ev.data || ev.data.type !== "streamlit:render") return;
    const a = ev.data.args || {};
    try {
      if (a.clear) localStorage.removeItem(KEY);
      if (a.set && a.set !== localStorage.getItem(KEY)) localStorage.setItem(KEY, a.set);
    } catch (e) {}
    const tok = read();
    if (tok !== last) { last = tok; post("streamlit:setComponentValue", {dataType: "json", value: {ready: true, token: tok}}); }
  });
  post("streamlit:componentReady", {apiVersion: 1});
  post("streamlit:setFrameHeight", {height: 0});
</script></body></html>
"""


def _declare(name: str, html: str):
    folder = Path(tempfile.gettempdir()) / f"aipc01_component_{name}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "index.html"
    if not target.exists() or target.read_text(encoding="utf-8") != html:
        target.write_text(html, encoding="utf-8")
    return components.declare_component(name, path=str(folder))


_camera = _declare("aipc01_back_camera", CAMERA_HTML)
_auth = _declare("aipc01_auth_store", AUTH_HTML)


def back_camera(key: str):
    """-> None, or {'image': 'data:image/jpeg;base64,...', 'id': str, 'n': int, 'source': 'native'|'live', 'w', 'h'}"""
    return _camera(key=key, default=None)


def auth_store(set_token: str | None = None, clear: bool = False, key: str = "auth_store"):
    """-> None until the browser answers, then {'ready': True, 'token': str | None}."""
    return _auth(set=set_token, clear=bool(clear), key=key, default=None)
