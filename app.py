"""
YouTube Downloader - local web app (Flask + yt-dlp)

Setup:
    pip install flask yt-dlp
    (install ffmpeg too: needed for merging video+audio and MP3 conversion)

Run:
    python app.py
    then open http://127.0.0.1:5000

Only download content you own or have permission to download.
Downloading may violate YouTube's Terms of Service.
"""

import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, jsonify, render_template_string, request, send_file

app = Flask(__name__)

ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def valid_url(url: str) -> bool:
    try:
        u = urlparse(url)
        return u.scheme in ("http", "https") and u.hostname in ALLOWED_HOSTS
    except Exception:
        return False


@app.after_request
def add_cors(resp):
    # Lets a standalone index.html (opened from disk) call this local API.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/api/info", methods=["OPTIONS"])
def preflight():
    return "", 204


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.post("/api/info")
def info():
    url = (request.json or {}).get("url", "").strip()
    if not valid_url(url):
        return jsonify(error="Please enter a valid YouTube URL."), 400
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
            d = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        return jsonify(error=str(e).replace("ERROR: ", "")), 400

    heights = sorted({f["height"] for f in d.get("formats", []) if f.get("height") and f["height"] >= 144}, reverse=True)
    secs = int(d.get("duration") or 0)
    return jsonify(
        title=d.get("title"),
        channel=d.get("uploader"),
        thumbnail=d.get("thumbnail"),
        duration=f"{secs // 60}:{secs % 60:02d}",
        heights=heights,
    )


@app.get("/api/download")
def download():
    url = request.args.get("url", "").strip()
    audio = request.args.get("audio") == "1"
    height = request.args.get("height", type=int)
    if not valid_url(url):
        return "Invalid URL", 400

    tmp = Path(tempfile.mkdtemp(prefix="ytdl_"))
    opts = {
        "outtmpl": str(tmp / "%(title).150s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if audio:
        opts.update(
            format="bestaudio/best",
            postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        )
    else:
        h = f"[height<={height}]" if height else ""
        opts.update(format=f"bestvideo{h}+bestaudio/best{h}", merge_output_format="mp4")

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        files = list(tmp.iterdir())
        if not files:
            raise RuntimeError("No file was produced.")
        file = files[0]
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return f"Download failed: {e}", 500

    resp = send_file(file, as_attachment=True, download_name=file.name)
    resp.call_on_close(lambda: shutil.rmtree(tmp, ignore_errors=True))
    return resp


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YouTube Downloader</title>
<style>
  :root { --bg:#f6f6f4; --card:#fff; --text:#1a1a1a; --muted:#6b6b6b; --accent:#e03131; --border:#e2e2de; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#161616; --card:#212121; --text:#f0f0f0; --muted:#9a9a9a; --border:#333; }
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,sans-serif; background:var(--bg); color:var(--text);
         min-height:100vh; display:flex; justify-content:center; padding:24px 16px; }
  main { width:100%; max-width:560px; }
  h1 { font-size:1.5rem; margin:8px 0 4px; }
  p.sub { color:var(--muted); margin:0 0 20px; font-size:.9rem; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:16px; margin-bottom:16px; }
  .row { display:flex; gap:8px; }
  input[type=text], select { flex:1; padding:11px 12px; border:1px solid var(--border); border-radius:10px;
         font-size:1rem; background:var(--bg); color:var(--text); min-width:0; }
  button { padding:11px 16px; border:0; border-radius:10px; background:var(--accent); color:#fff;
           font-size:1rem; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  button.full { width:100%; margin-top:12px; }
  img { width:100%; border-radius:10px; display:block; margin-bottom:12px; }
  h2 { font-size:1.05rem; margin:0 0 4px; line-height:1.3; }
  .meta { color:var(--muted); font-size:.85rem; margin-bottom:12px; }
  .toggle { display:flex; gap:8px; margin-bottom:12px; }
  .toggle label { flex:1; text-align:center; padding:9px; border:1px solid var(--border); border-radius:10px; cursor:pointer; font-size:.95rem; }
  .toggle input { display:none; }
  .toggle input:checked + span { font-weight:700; color:var(--accent); }
  .toggle label:has(input:checked) { border-color:var(--accent); }
  .error { color:var(--accent); font-size:.9rem; margin-top:10px; }
  .hidden { display:none; }
  .note { color:var(--muted); font-size:.75rem; text-align:center; margin-top:20px; }
</style>
</head>
<body>
<main>
  <h1>YouTube Downloader</h1>
  <p class="sub">Paste a link, pick a format, download.</p>

  <div class="card">
    <div class="row">
      <input id="url" type="text" placeholder="https://www.youtube.com/watch?v=..." autocomplete="off">
      <button id="fetchBtn">Fetch</button>
    </div>
    <div id="error" class="error hidden"></div>
  </div>

  <div id="result" class="card hidden">
    <img id="thumb" alt="">
    <h2 id="title"></h2>
    <div class="meta" id="meta"></div>

    <div class="toggle">
      <label><input type="radio" name="kind" value="video" checked><span>Video (MP4)</span></label>
      <label><input type="radio" name="kind" value="audio"><span>Audio (MP3)</span></label>
    </div>
    <select id="quality"></select>
    <button id="dlBtn" class="full">Download</button>
  </div>

  <p class="note">Only download content you own or have permission to save.</p>
</main>

<script>
const $ = id => document.getElementById(id);
let currentUrl = "";

function showError(msg) { $("error").textContent = msg; $("error").classList.toggle("hidden", !msg); }

$("fetchBtn").onclick = async () => {
  const url = $("url").value.trim();
  if (!url) return;
  showError(""); $("result").classList.add("hidden");
  $("fetchBtn").disabled = true; $("fetchBtn").textContent = "...";
  try {
    const r = await fetch("/api/info", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({url}) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "Something went wrong.");
    currentUrl = url;
    $("thumb").src = d.thumbnail || "";
    $("title").textContent = d.title;
    $("meta").textContent = `${d.channel || ""} \\u00b7 ${d.duration}`;
    $("quality").innerHTML = '<option value="">Best available</option>' +
      d.heights.map(h => `<option value="${h}">${h}p</option>`).join("");
    $("result").classList.remove("hidden");
  } catch (e) { showError(e.message); }
  $("fetchBtn").disabled = false; $("fetchBtn").textContent = "Fetch";
};

$("url").addEventListener("keydown", e => { if (e.key === "Enter") $("fetchBtn").click(); });

document.querySelectorAll('input[name=kind]').forEach(r => r.onchange = () => {
  $("quality").classList.toggle("hidden", r.value === "audio" && r.checked);
});

$("dlBtn").onclick = async () => {
  const audio = document.querySelector('input[name=kind]:checked').value === "audio";
  const params = new URLSearchParams({ url: currentUrl });
  if (audio) params.set("audio", "1");
  else if ($("quality").value) params.set("height", $("quality").value);

  showError("");
  $("dlBtn").disabled = true; $("dlBtn").textContent = "Preparing file...";
  try {
    const r = await fetch("/api/download?" + params);
    if (!r.ok) throw new Error(await r.text());
    const blob = await r.blob();
    const cd = r.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename\\*?=(?:UTF-8'')?"?([^";]+)"?/i);
    const name = m ? decodeURIComponent(m[1]) : (audio ? "audio.mp3" : "video.mp4");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { showError(e.message); }
  $("dlBtn").disabled = false; $("dlBtn").textContent = "Download";
};
</script>
</body>
</html>
"""

if __name__ == "__main__":
    # Bound to localhost only; don't expose this publicly.
    app.run(host="127.0.0.1", port=5000, debug=False)
