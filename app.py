#!/usr/bin/env python3
"""DownTube — A local web GUI wrapper for yt-dlp."""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path


def check_dependencies():
    """Check for required external tools and offer to install missing ones."""
    missing = []

    if not shutil.which("yt-dlp"):
        missing.append(("yt-dlp", "brew install yt-dlp", "pip install yt-dlp"))

    if not shutil.which("ffmpeg"):
        missing.append(("ffmpeg", "brew install ffmpeg", None))

    if not missing:
        return

    print("\n  Missing dependencies detected:\n")
    for name, brew_cmd, pip_cmd in missing:
        print(f"    - {name}")

    # Check if Homebrew is available
    has_brew = shutil.which("brew") is not None

    for name, brew_cmd, pip_cmd in missing:
        print(f"\n  Install {name}?")
        if has_brew:
            print(f"    [1] {brew_cmd} (recommended)")
            if pip_cmd:
                print(f"    [2] {pip_cmd}")
            print(f"    [s] Skip")
            choice = input(f"\n  Choice [1]: ").strip().lower() or "1"
        elif pip_cmd:
            print(f"    [1] {pip_cmd}")
            print(f"    [s] Skip")
            choice = input(f"\n  Choice [1]: ").strip().lower() or "1"
        else:
            print(f"    Please install manually: {brew_cmd}")
            print(f"    [s] Skip (continue anyway)")
            choice = input(f"\n  Choice [s]: ").strip().lower() or "s"

        if choice == "s":
            print(f"  Skipping {name} — some features may not work.\n")
            continue

        if choice == "1":
            cmd = brew_cmd if has_brew else (pip_cmd or brew_cmd)
        elif choice == "2" and pip_cmd:
            cmd = pip_cmd
        else:
            cmd = brew_cmd if has_brew else (pip_cmd or brew_cmd)

        print(f"\n  Running: {cmd}")
        result = subprocess.run(cmd.split(), capture_output=False)
        if result.returncode != 0:
            print(f"  Failed to install {name}. Please install manually and try again.")
            sys.exit(1)
        print(f"  {name} installed successfully!")

    print()


# Check for Flask (handle case where script is run without venv/flask)
try:
    from flask import Flask, Response, jsonify, render_template_string, request
except ImportError:
    print("\n  Flask is not installed.")
    print("  Setting up virtual environment...\n")

    venv_dir = Path(__file__).parent / ".venv"
    if not venv_dir.exists():
        print(f"  Creating venv at {venv_dir}")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    pip_path = venv_dir / "bin" / "pip"
    print("  Installing Flask...")
    subprocess.run([str(pip_path), "install", "flask"], check=True)

    # Re-exec with the venv Python
    python_path = venv_dir / "bin" / "python"
    print(f"\n  Restarting with venv Python...\n")
    os.execv(str(python_path), [str(python_path)] + sys.argv)

check_dependencies()

app = Flask(__name__)

DOWNLOAD_DIR = Path.home() / "Downloads" / "DownTube"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Track active downloads: {download_id: {status, progress, filename, ...}}
downloads = {}
downloads_lock = threading.Lock()

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DownTube</title>
<style>
  :root {
    --bg: #0f0f0f;
    --surface: #1a1a1a;
    --surface2: #252525;
    --border: #333;
    --text: #e8e8e8;
    --text-dim: #888;
    --accent: #ff4444;
    --accent-hover: #ff6666;
    --green: #4caf50;
    --blue: #2196f3;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .container {
    max-width: 720px;
    margin: 0 auto;
    padding: 2rem 1.5rem;
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 2rem;
  }
  header h1 {
    font-size: 1.75rem;
    font-weight: 700;
    color: var(--accent);
  }
  header .subtitle {
    color: var(--text-dim);
    font-size: 0.85rem;
    margin-top: 0.15rem;
  }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
  }
  .card h2 {
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
    margin-bottom: 1rem;
  }
  .input-row {
    display: flex;
    gap: 0.75rem;
  }
  input[type="text"], select {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.7rem 1rem;
    color: var(--text);
    font-size: 0.95rem;
    outline: none;
    transition: border-color 0.2s;
  }
  input[type="text"]:focus, select:focus {
    border-color: var(--accent);
  }
  input[type="text"] { flex: 1; }
  select { cursor: pointer; min-width: 120px; }
  button {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 0.7rem 1.25rem;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
    white-space: nowrap;
  }
  button:hover:not(:disabled) { background: var(--accent-hover); }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary {
    background: var(--surface2);
    border: 1px solid var(--border);
  }
  .btn-secondary:hover:not(:disabled) { background: var(--border); }
  .options-row {
    display: flex;
    gap: 0.75rem;
    margin-top: 1rem;
    flex-wrap: wrap;
  }
  .options-row label {
    font-size: 0.85rem;
    color: var(--text-dim);
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    flex: 1;
    min-width: 140px;
  }
  .mode-buttons {
    display: flex;
    gap: 0.5rem;
    margin-top: 1rem;
  }
  .mode-btn {
    flex: 1;
    padding: 0.6rem;
    background: var(--surface2);
    border: 2px solid var(--border);
    border-radius: 8px;
    color: var(--text-dim);
    font-size: 0.85rem;
    font-weight: 600;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s;
  }
  .mode-btn:hover { border-color: var(--text-dim); color: var(--text); }
  .mode-btn.active {
    border-color: var(--accent);
    color: var(--accent);
    background: rgba(255, 68, 68, 0.08);
  }
  .video-info {
    display: flex;
    gap: 1rem;
    align-items: center;
  }
  .video-info img {
    width: 160px;
    border-radius: 8px;
    flex-shrink: 0;
  }
  .video-info .meta h3 {
    font-size: 1rem;
    margin-bottom: 0.25rem;
    line-height: 1.3;
  }
  .video-info .meta p {
    font-size: 0.8rem;
    color: var(--text-dim);
  }
  .progress-container {
    margin-top: 1rem;
    display: none;
  }
  .progress-container.visible { display: block; }
  .progress-bar-outer {
    background: var(--surface2);
    border-radius: 6px;
    height: 8px;
    overflow: hidden;
    margin-top: 0.5rem;
  }
  .progress-bar-inner {
    background: var(--accent);
    height: 100%;
    width: 0%;
    border-radius: 6px;
    transition: width 0.3s;
  }
  .progress-text {
    font-size: 0.8rem;
    color: var(--text-dim);
    margin-top: 0.35rem;
    display: flex;
    justify-content: space-between;
  }
  .history-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.6rem 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.85rem;
  }
  .history-item:last-child { border-bottom: none; }
  .history-item .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .history-item .badge {
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 600;
    margin-left: 0.75rem;
    flex-shrink: 0;
  }
  .badge-video { background: rgba(33, 150, 243, 0.15); color: var(--blue); }
  .badge-audio { background: rgba(76, 175, 80, 0.15); color: var(--green); }
  .badge-both { background: rgba(255, 68, 68, 0.15); color: var(--accent); }
  .spinner {
    display: inline-block;
    width: 16px;
    height: 16px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: middle;
    margin-right: 0.5rem;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .hidden { display: none; }
  .error-msg {
    color: #ff6b6b;
    font-size: 0.85rem;
    margin-top: 0.5rem;
  }
  #download-dir {
    font-size: 0.75rem;
    color: var(--text-dim);
    margin-top: 0.5rem;
    word-break: break-all;
  }
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1>DownTube</h1>
      <div class="subtitle">yt-dlp GUI wrapper</div>
    </div>
  </header>

  <!-- URL + Cookie Browser -->
  <div class="card">
    <h2>URL</h2>
    <div class="input-row">
      <input type="text" id="url" placeholder="Paste video URL here..." autofocus>
      <button id="fetch-btn" onclick="fetchInfo()">Fetch</button>
    </div>
    <div class="options-row">
      <label>
        Cookies from browser
        <select id="cookie-browser">
          <option value="brave" selected>Brave</option>
          <option value="chrome">Chrome</option>
          <option value="firefox">Firefox</option>
          <option value="safari">Safari</option>
          <option value="edge">Edge</option>
          <option value="">None</option>
        </select>
      </label>
    </div>
    <div id="fetch-error" class="error-msg hidden"></div>
  </div>

  <!-- Video Info + Options (hidden until fetched) -->
  <div id="info-card" class="card hidden">
    <h2>Video Info</h2>
    <div class="video-info">
      <img id="thumb" src="" alt="">
      <div class="meta">
        <h3 id="title"></h3>
        <p id="channel"></p>
        <p id="duration"></p>
      </div>
    </div>

    <h2 style="margin-top:1.25rem;">Download Mode</h2>
    <div class="mode-buttons">
      <div class="mode-btn active" data-mode="video+audio" onclick="setMode(this)">Video + Audio</div>
      <div class="mode-btn" data-mode="video" onclick="setMode(this)">Video Only</div>
      <div class="mode-btn" data-mode="audio" onclick="setMode(this)">Audio Only</div>
    </div>

    <div class="options-row">
      <label id="quality-label">
        Quality
        <select id="quality"></select>
      </label>
      <label id="audio-format-label" class="hidden">
        Audio Format
        <select id="audio-format">
          <option value="mp3">MP3</option>
          <option value="m4a">M4A</option>
          <option value="opus">Opus</option>
          <option value="best">Best</option>
        </select>
      </label>
    </div>

    <div style="margin-top:1rem;">
      <button id="download-btn" onclick="startDownload()">Download</button>
    </div>
    <div id="download-dir"></div>

    <div id="progress" class="progress-container">
      <div style="font-size:0.85rem;"><span class="spinner"></span><span id="progress-status">Downloading...</span></div>
      <div class="progress-bar-outer"><div id="progress-bar" class="progress-bar-inner"></div></div>
      <div class="progress-text">
        <span id="progress-pct">0%</span>
        <span id="progress-speed"></span>
      </div>
    </div>
  </div>

  <!-- Download History -->
  <div id="history-card" class="card hidden">
    <h2>Downloads</h2>
    <div id="history"></div>
  </div>
</div>

<script>
let videoData = null;
let currentMode = 'video+audio';

function setMode(el) {
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  currentMode = el.dataset.mode;
  updateQualityOptions();
}

function updateQualityOptions() {
  if (!videoData) return;
  const sel = document.getElementById('quality');
  const audioLabel = document.getElementById('audio-format-label');
  sel.innerHTML = '';

  if (currentMode === 'audio') {
    audioLabel.classList.remove('hidden');
    // Show audio quality options
    const audioBitrates = ['best', '320k', '256k', '192k', '128k'];
    audioBitrates.forEach(q => {
      const opt = document.createElement('option');
      opt.value = q;
      opt.textContent = q === 'best' ? 'Best Available' : q;
      sel.appendChild(opt);
    });
  } else {
    audioLabel.classList.add('hidden');
    // Collect unique video resolutions
    const resolutions = new Map();
    (videoData.formats || []).forEach(f => {
      if (f.vcodec && f.vcodec !== 'none' && f.height) {
        const key = f.height;
        if (!resolutions.has(key) || (f.fps && f.fps > (resolutions.get(key).fps || 0))) {
          resolutions.set(key, f);
        }
      }
    });
    const sorted = [...resolutions.entries()].sort((a, b) => b[0] - a[0]);

    // Add "best" option first
    const bestOpt = document.createElement('option');
    bestOpt.value = 'best';
    bestOpt.textContent = 'Best Available';
    sel.appendChild(bestOpt);

    sorted.forEach(([h, f]) => {
      const opt = document.createElement('option');
      opt.value = h;
      let label = h + 'p';
      if (f.fps && f.fps > 30) label += ` ${f.fps}fps`;
      opt.textContent = label;
      sel.appendChild(opt);
    });
  }
}

function formatDuration(sec) {
  if (!sec) return '';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

async function fetchInfo() {
  const url = document.getElementById('url').value.trim();
  if (!url) return;

  const btn = document.getElementById('fetch-btn');
  const errEl = document.getElementById('fetch-error');
  errEl.classList.add('hidden');
  btn.disabled = true;
  btn.textContent = 'Fetching...';

  const browser = document.getElementById('cookie-browser').value;

  try {
    const resp = await fetch('/api/info', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url, browser})
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Failed to fetch info');

    videoData = data;
    document.getElementById('thumb').src = data.thumbnail || '';
    document.getElementById('title').textContent = data.title || 'Unknown';
    document.getElementById('channel').textContent = data.uploader || data.channel || '';
    document.getElementById('duration').textContent = formatDuration(data.duration);
    document.getElementById('info-card').classList.remove('hidden');

    updateQualityOptions();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Fetch';
  }
}

function startDownload() {
  const url = document.getElementById('url').value.trim();
  if (!url) return;

  const browser = document.getElementById('cookie-browser').value;
  const quality = document.getElementById('quality').value;
  const audioFormat = document.getElementById('audio-format').value;

  const btn = document.getElementById('download-btn');
  btn.disabled = true;

  const prog = document.getElementById('progress');
  prog.classList.add('visible');
  document.getElementById('progress-bar').style.width = '0%';
  document.getElementById('progress-pct').textContent = '0%';
  document.getElementById('progress-speed').textContent = '';
  document.getElementById('progress-status').textContent = 'Starting download...';
  document.getElementById('download-dir').textContent = '';

  const params = new URLSearchParams({url, mode: currentMode, quality, audio_format: audioFormat, browser});
  const es = new EventSource('/api/download?' + params.toString());

  es.onmessage = function(e) {
    const data = JSON.parse(e.data);

    if (data.status === 'downloading') {
      document.getElementById('progress-status').textContent = 'Downloading...';
      if (data.percent != null) {
        document.getElementById('progress-bar').style.width = data.percent + '%';
        document.getElementById('progress-pct').textContent = data.percent.toFixed(1) + '%';
      }
      if (data.speed) {
        document.getElementById('progress-speed').textContent = data.speed;
      }
    } else if (data.status === 'merging') {
      document.getElementById('progress-status').textContent = 'Merging formats...';
      document.getElementById('progress-bar').style.width = '100%';
      document.getElementById('progress-pct').textContent = '100%';
    } else if (data.status === 'done') {
      document.getElementById('progress-status').textContent = 'Complete!';
      document.getElementById('progress-bar').style.width = '100%';
      document.getElementById('progress-pct').textContent = '100%';
      document.getElementById('progress-speed').textContent = '';
      document.getElementById('download-dir').textContent = 'Saved to: ' + data.path;
      btn.disabled = false;
      addHistory(data.filename, currentMode);
      es.close();
      // Hide progress after a moment
      setTimeout(() => { prog.classList.remove('visible'); }, 3000);
    } else if (data.status === 'error') {
      document.getElementById('progress-status').textContent = 'Error: ' + data.message;
      btn.disabled = false;
      es.close();
    }
  };

  es.onerror = function() {
    document.getElementById('progress-status').textContent = 'Connection lost';
    btn.disabled = false;
    es.close();
  };
}

function addHistory(filename, mode) {
  const card = document.getElementById('history-card');
  card.classList.remove('hidden');
  const container = document.getElementById('history');

  const badgeClass = mode === 'audio' ? 'badge-audio' : mode === 'video' ? 'badge-video' : 'badge-both';
  const badgeText = mode === 'audio' ? 'Audio' : mode === 'video' ? 'Video' : 'Video+Audio';

  const item = document.createElement('div');
  item.className = 'history-item';
  item.innerHTML = `<span class="name">${filename}</span><span class="badge ${badgeClass}">${badgeText}</span>`;
  container.prepend(item);
}

// Submit on Enter key
document.getElementById('url').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') fetchInfo();
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/info", methods=["POST"])
def api_info():
    data = request.get_json()
    url = data.get("url", "").strip()
    browser = data.get("browser", "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = ["yt-dlp", "--dump-json", "--no-download"]
    if browser:
        cmd += ["--cookies-from-browser", browser]
    cmd.append(url)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip().split("\n")[-1] if result.stderr else "yt-dlp failed"
            return jsonify({"error": error_msg}), 400

        info = json.loads(result.stdout.strip().split("\n")[0])
        return jsonify(info)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 504
    except json.JSONDecodeError:
        return jsonify({"error": "Failed to parse video info"}), 500


@app.route("/api/download")
def api_download():
    url = request.args.get("url", "").strip()
    mode = request.args.get("mode", "video+audio")
    quality = request.args.get("quality", "best")
    audio_format = request.args.get("audio_format", "mp3")
    browser = request.args.get("browser", "").strip()

    if not url:
        return Response("data: " + json.dumps({"status": "error", "message": "No URL"}) + "\n\n",
                        content_type="text/event-stream")

    def generate():
        cmd = ["yt-dlp", "--newline", "--no-colors", "-o", str(DOWNLOAD_DIR / "%(title)s.%(ext)s")]

        if browser:
            cmd += ["--cookies-from-browser", browser]

        if mode == "audio":
            cmd += ["-x"]
            if audio_format != "best":
                cmd += ["--audio-format", audio_format]
            if quality != "best":
                cmd += ["--audio-quality", quality.replace("k", "")]
        elif mode == "video":
            if quality != "best":
                cmd += ["-f", f"bestvideo[height<={quality}][ext=mp4]/bestvideo[height<={quality}]"]
            else:
                cmd += ["-f", "bestvideo[ext=mp4]/bestvideo"]
        else:  # video+audio
            if quality != "best":
                cmd += ["-f", f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/best[height<={quality}][ext=mp4]/best[height<={quality}]"]
            else:
                cmd += ["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]"]
            cmd += ["--merge-output-format", "mp4"]

        cmd.append(url)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )

        filename = ""
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            # Try to extract progress
            pct_match = re.search(r"(\d+\.?\d*)%", line)
            speed_match = re.search(r"at\s+(\S+/s)", line)
            dest_match = re.search(r"\[download\] Destination:\s*(.+)", line)
            merge_match = re.search(r"\[Merger\]|Merging formats", line)
            already_match = re.search(r"has already been downloaded", line)

            if dest_match:
                filename = os.path.basename(dest_match.group(1))

            if merge_match:
                yield f"data: {json.dumps({'status': 'merging'})}\n\n"
            elif pct_match:
                pct = float(pct_match.group(1))
                speed = speed_match.group(1) if speed_match else ""
                yield f"data: {json.dumps({'status': 'downloading', 'percent': pct, 'speed': speed})}\n\n"
            elif already_match:
                # File already exists
                already_file = re.search(r"\[download\]\s*(.+?)\s+has already been downloaded", line)
                if already_file:
                    filename = os.path.basename(already_file.group(1))

        proc.wait()

        if proc.returncode == 0:
            # Try to find the downloaded file if we don't have a filename
            if not filename:
                # Get most recent file in download dir
                files = sorted(DOWNLOAD_DIR.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
                filename = files[0].name if files else "unknown"

            yield f"data: {json.dumps({'status': 'done', 'filename': filename, 'path': str(DOWNLOAD_DIR / filename)})}\n\n"
        else:
            yield f"data: {json.dumps({'status': 'error', 'message': 'Download failed (exit code ' + str(proc.returncode) + ')'})}\n\n"

    return Response(generate(), content_type="text/event-stream")


if __name__ == "__main__":
    print(f"\n  DownTube is running at http://localhost:8080")
    print(f"  Downloads will be saved to {DOWNLOAD_DIR}\n")
    app.run(host="127.0.0.1", port=8080, debug=False)
