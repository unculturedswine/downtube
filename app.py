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
from urllib.parse import quote as urlquote
from urllib.request import urlopen, Request
from urllib.error import URLError


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

PLEX_DIR = DOWNLOAD_DIR / "Plex"

CONFIG_DIR = Path.home() / ".config" / "downtube"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Track active downloads: {download_id: {status, progress, filename, ...}}
downloads = {}
downloads_lock = threading.Lock()

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"}
AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac", ".wma"}
IGNORE_EXTS = {".part", ".ytdl", ".webp", ".jpg", ".jpeg", ".png", ".tmp"}
ALLOWED_EXTS = VIDEO_EXTS | AUDIO_EXTS


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def tmdb_request(path, params=None):
    cfg = load_config()
    api_key = cfg.get("tmdb_api_key", "")
    if not api_key:
        return None, "No TMDB API key configured"
    base = "https://api.themoviedb.org/3"
    qs = f"api_key={urlquote(api_key)}"
    if params:
        for k, v in params.items():
            qs += f"&{urlquote(k)}={urlquote(str(v))}"
    url = f"{base}{path}?{qs}"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()), None
    except URLError as e:
        return None, str(e)
    except json.JSONDecodeError:
        return None, "Invalid response from TMDB"


def format_size(size_bytes):
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def get_file_type(ext):
    ext = ext.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


def sanitize_filename(name):
    """Remove characters that are problematic in filenames."""
    return re.sub(r'[<>:"/\\|?*]', '', name).strip().rstrip('.')


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
    --orange: #ff9800;
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
    justify-content: space-between;
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
  .settings-btn {
    background: none;
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 0.5rem;
    border-radius: 8px;
    cursor: pointer;
    font-size: 1.1rem;
    line-height: 1;
    transition: all 0.2s;
  }
  .settings-btn:hover { border-color: var(--text-dim); color: var(--text); background: none; }
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
  input[type="text"], input[type="number"], select {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.7rem 1rem;
    color: var(--text);
    font-size: 0.95rem;
    outline: none;
    transition: border-color 0.2s;
  }
  input[type="text"]:focus, input[type="number"]:focus, select:focus {
    border-color: var(--accent);
  }
  input[type="text"], input[type="number"] { flex: 1; }
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
  .btn-small {
    padding: 0.35rem 0.75rem;
    font-size: 0.75rem;
    border-radius: 6px;
  }
  .btn-plex {
    background: var(--orange);
    color: #000;
    font-weight: 700;
  }
  .btn-plex:hover:not(:disabled) { background: #ffb74d; }
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
  /* File browser */
  .file-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.6rem 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.85rem;
    gap: 0.5rem;
  }
  .file-item:last-child { border-bottom: none; }
  .file-item .file-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .file-item .file-size {
    color: var(--text-dim);
    font-size: 0.75rem;
    flex-shrink: 0;
    margin-right: 0.5rem;
  }
  .file-item .badge {
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 600;
    flex-shrink: 0;
  }
  .badge-video { background: rgba(33, 150, 243, 0.15); color: var(--blue); }
  .badge-audio { background: rgba(76, 175, 80, 0.15); color: var(--green); }
  .badge-other { background: rgba(136, 136, 136, 0.15); color: var(--text-dim); }
  .file-actions { display: flex; gap: 0.35rem; flex-shrink: 0; }
  .empty-state {
    color: var(--text-dim);
    font-size: 0.85rem;
    text-align: center;
    padding: 1rem 0;
  }
  /* Modal */
  .modal-overlay {
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.7);
    z-index: 100;
    justify-content: center;
    align-items: center;
  }
  .modal-overlay.visible { display: flex; }
  .modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
    width: 90%;
    max-width: 520px;
    max-height: 80vh;
    overflow-y: auto;
  }
  .modal h2 {
    font-size: 1.1rem;
    margin-bottom: 1rem;
    color: var(--text);
    text-transform: none;
    letter-spacing: normal;
  }
  .modal .field {
    margin-bottom: 1rem;
  }
  .modal .field label {
    display: block;
    font-size: 0.8rem;
    color: var(--text-dim);
    margin-bottom: 0.35rem;
  }
  .modal .field input, .modal .field select {
    width: 100%;
  }
  .modal .field input[type="number"] {
    width: 80px;
    flex: none;
  }
  .modal-buttons {
    display: flex;
    gap: 0.75rem;
    margin-top: 1.25rem;
    justify-content: flex-end;
  }
  .plex-path-preview {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.75rem;
    font-size: 0.8rem;
    color: var(--green);
    word-break: break-all;
    margin-top: 0.5rem;
    font-family: monospace;
  }
  .tmdb-results {
    max-height: 200px;
    overflow-y: auto;
    margin-top: 0.5rem;
  }
  .tmdb-result {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem;
    border-radius: 8px;
    cursor: pointer;
    transition: background 0.15s;
  }
  .tmdb-result:hover { background: var(--surface2); }
  .tmdb-result.selected { background: rgba(255, 152, 0, 0.15); border: 1px solid var(--orange); }
  .tmdb-result img {
    width: 40px;
    height: 60px;
    object-fit: cover;
    border-radius: 4px;
    flex-shrink: 0;
    background: var(--surface2);
  }
  .tmdb-result .tmdb-info { flex: 1; min-width: 0; }
  .tmdb-result .tmdb-title { font-size: 0.85rem; font-weight: 600; }
  .tmdb-result .tmdb-year { font-size: 0.75rem; color: var(--text-dim); }
  .inline-row { display: flex; gap: 0.75rem; align-items: flex-end; }
  .inline-row .field { flex: 1; }
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
  .success-msg {
    color: var(--green);
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
    <button class="settings-btn" onclick="openSettings()" title="Settings">&#9881;</button>
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

  <!-- Live File Browser -->
  <div class="card">
    <h2>Downloads</h2>
    <div id="file-list">
      <div class="empty-state">Loading...</div>
    </div>
  </div>
</div>

<!-- Settings Modal -->
<div id="settings-modal" class="modal-overlay">
  <div class="modal">
    <h2>Settings</h2>
    <div class="field">
      <label>TMDB API Key (for Prep for Plex)</label>
      <input type="text" id="settings-tmdb-key" placeholder="Enter your TMDB API key...">
    </div>
    <div style="font-size:0.75rem; color:var(--text-dim); margin-bottom:1rem;">
      Get a free API key at <a href="https://www.themoviedb.org/settings/api" target="_blank" style="color:var(--blue);">themoviedb.org/settings/api</a>
    </div>
    <div id="settings-msg"></div>
    <div class="modal-buttons">
      <button class="btn-secondary" onclick="closeSettings()">Cancel</button>
      <button onclick="saveSettings()">Save</button>
    </div>
  </div>
</div>

<!-- Plex Prep Modal -->
<div id="plex-modal" class="modal-overlay">
  <div class="modal">
    <h2>Prep for Plex</h2>
    <div style="font-size:0.8rem; color:var(--text-dim); margin-bottom:1rem; word-break:break-all;" id="plex-filename"></div>

    <div class="field">
      <label>Content Type</label>
      <div class="mode-buttons" style="margin-top:0.35rem;">
        <div class="mode-btn active" data-ptype="movie" onclick="setPlexType(this)">Movie</div>
        <div class="mode-btn" data-ptype="tv" onclick="setPlexType(this)">TV Show</div>
        <div class="mode-btn" data-ptype="music" onclick="setPlexType(this)">Music</div>
      </div>
    </div>

    <!-- Movie / TV search -->
    <div id="plex-tmdb-section">
      <div class="field">
        <label>Search TMDB</label>
        <div class="input-row">
          <input type="text" id="plex-search" placeholder="Search for title...">
          <button class="btn-small" onclick="searchTMDB()">Search</button>
        </div>
      </div>
      <div id="tmdb-results" class="tmdb-results"></div>
      <div id="tmdb-error" class="error-msg hidden"></div>
    </div>

    <!-- TV episode fields -->
    <div id="plex-tv-fields" class="hidden">
      <div class="inline-row">
        <div class="field">
          <label>Season #</label>
          <input type="number" id="plex-season" min="0" value="1" onchange="fetchEpisodes()">
        </div>
        <div class="field">
          <label>Episode #</label>
          <input type="number" id="plex-episode" min="1" value="1" onchange="updateEpisodeTitle()">
        </div>
      </div>
      <div class="field" id="plex-ep-title-field" class="hidden">
        <label>Episode Title</label>
        <input type="text" id="plex-ep-title" placeholder="Episode title (auto-filled from TMDB)">
      </div>
    </div>

    <!-- Music fields -->
    <div id="plex-music-fields" class="hidden">
      <div class="field">
        <label>Artist</label>
        <input type="text" id="plex-artist" placeholder="Artist name">
      </div>
      <div class="field">
        <label>Album</label>
        <input type="text" id="plex-album" placeholder="Album name">
      </div>
      <div class="inline-row">
        <div class="field">
          <label>Track #</label>
          <input type="number" id="plex-track-num" min="1" value="1" style="width:80px;">
        </div>
        <div class="field">
          <label>Track Title</label>
          <input type="text" id="plex-track-title" placeholder="Track title">
        </div>
      </div>
    </div>

    <!-- Path preview -->
    <div class="field">
      <label>Plex Path Preview</label>
      <div id="plex-path-preview" class="plex-path-preview">Select content type and details above...</div>
    </div>

    <div id="plex-msg"></div>
    <div class="modal-buttons">
      <button class="btn-secondary" onclick="closePlexModal()">Cancel</button>
      <button class="btn-plex" onclick="confirmPlexPrep()" id="plex-confirm-btn">Move to Plex</button>
    </div>
  </div>
</div>

<script>
let videoData = null;
let currentMode = 'video+audio';
let fileListTimer = null;
let plexState = { filename: '', type: 'movie', tmdbId: null, tmdbTitle: '', tmdbYear: '', episodes: [] };

// ---- Download mode / quality ----

function setMode(el) {
  document.querySelectorAll('#info-card .mode-btn').forEach(b => b.classList.remove('active'));
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
    const audioBitrates = ['best', '320k', '256k', '192k', '128k'];
    audioBitrates.forEach(q => {
      const opt = document.createElement('option');
      opt.value = q;
      opt.textContent = q === 'best' ? 'Best Available' : q;
      sel.appendChild(opt);
    });
  } else {
    audioLabel.classList.add('hidden');
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

// ---- Fetch info ----

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

// ---- Download ----

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
      es.close();
      refreshFiles();
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

// ---- Live file browser ----

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

async function refreshFiles() {
  try {
    const resp = await fetch('/api/files');
    const files = await resp.json();
    const container = document.getElementById('file-list');

    if (files.length === 0) {
      container.innerHTML = '<div class="empty-state">No files yet. Download something!</div>';
      return;
    }

    container.innerHTML = files.map(f => {
      const badgeClass = f.type === 'video' ? 'badge-video' : f.type === 'audio' ? 'badge-audio' : 'badge-other';
      const badgeText = f.type === 'video' ? 'Video' : f.type === 'audio' ? 'Audio' : f.ext.toUpperCase();
      return `<div class="file-item">
        <span class="file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
        <span class="file-size">${f.size_fmt}</span>
        <span class="badge ${badgeClass}">${badgeText}</span>
        <div class="file-actions">
          <button class="btn-small btn-plex" onclick="openPlexModal('${escapeHtml(f.name).replace(/'/g, "\\'")}')">Prep for Plex</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    console.error('Failed to refresh files:', e);
  }
}

function startFilePolling() {
  refreshFiles();
  fileListTimer = setInterval(refreshFiles, 5000);
}

// ---- Settings modal ----

async function openSettings() {
  try {
    const resp = await fetch('/api/settings');
    const cfg = await resp.json();
    document.getElementById('settings-tmdb-key').value = cfg.tmdb_api_key || '';
  } catch (e) {}
  document.getElementById('settings-msg').innerHTML = '';
  document.getElementById('settings-modal').classList.add('visible');
}

function closeSettings() {
  document.getElementById('settings-modal').classList.remove('visible');
}

async function saveSettings() {
  const key = document.getElementById('settings-tmdb-key').value.trim();
  try {
    const resp = await fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({tmdb_api_key: key})
    });
    if (resp.ok) {
      document.getElementById('settings-msg').innerHTML = '<div class="success-msg">Settings saved!</div>';
      setTimeout(closeSettings, 1000);
    }
  } catch (e) {
    document.getElementById('settings-msg').innerHTML = '<div class="error-msg">Failed to save</div>';
  }
}

// ---- Plex prep modal ----

function setPlexType(el) {
  document.querySelectorAll('#plex-modal .mode-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  plexState.type = el.dataset.ptype;
  plexState.tmdbId = null;
  plexState.tmdbTitle = '';
  plexState.tmdbYear = '';
  plexState.episodes = [];

  document.getElementById('tmdb-results').innerHTML = '';
  document.getElementById('tmdb-error').classList.add('hidden');

  const isTmdb = plexState.type === 'movie' || plexState.type === 'tv';
  document.getElementById('plex-tmdb-section').style.display = isTmdb ? 'block' : 'none';
  document.getElementById('plex-tv-fields').classList.toggle('hidden', plexState.type !== 'tv');
  document.getElementById('plex-music-fields').classList.toggle('hidden', plexState.type !== 'music');

  updatePlexPreview();
}

function openPlexModal(filename) {
  plexState.filename = filename;
  plexState.type = 'movie';
  plexState.tmdbId = null;
  plexState.tmdbTitle = '';
  plexState.tmdbYear = '';
  plexState.episodes = [];

  document.getElementById('plex-filename').textContent = filename;
  document.getElementById('plex-search').value = filename.replace(/\.[^.]+$/, '').replace(/[_\-\.]/g, ' ');
  document.getElementById('tmdb-results').innerHTML = '';
  document.getElementById('tmdb-error').classList.add('hidden');
  document.getElementById('plex-msg').innerHTML = '';

  // Reset type buttons
  document.querySelectorAll('#plex-modal .mode-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.ptype === 'movie');
  });
  document.getElementById('plex-tmdb-section').style.display = 'block';
  document.getElementById('plex-tv-fields').classList.add('hidden');
  document.getElementById('plex-music-fields').classList.add('hidden');

  // Pre-fill music fields if audio
  const ext = filename.split('.').pop().toLowerCase();
  const audioExts = ['mp3','m4a','opus','ogg','flac','wav','aac','wma'];
  if (audioExts.includes(ext)) {
    // Default to music for audio files
    document.querySelectorAll('#plex-modal .mode-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.ptype === 'music');
    });
    plexState.type = 'music';
    document.getElementById('plex-tmdb-section').style.display = 'none';
    document.getElementById('plex-music-fields').classList.remove('hidden');
    document.getElementById('plex-artist').value = '';
    document.getElementById('plex-album').value = '';
    document.getElementById('plex-track-title').value = filename.replace(/\.[^.]+$/, '').replace(/[_\-\.]/g, ' ');
    document.getElementById('plex-track-num').value = '1';
  }

  updatePlexPreview();
  document.getElementById('plex-modal').classList.add('visible');
}

function closePlexModal() {
  document.getElementById('plex-modal').classList.remove('visible');
}

async function searchTMDB() {
  const query = document.getElementById('plex-search').value.trim();
  if (!query) return;

  const errEl = document.getElementById('tmdb-error');
  errEl.classList.add('hidden');
  const container = document.getElementById('tmdb-results');
  container.innerHTML = '<div style="color:var(--text-dim);font-size:0.8rem;">Searching...</div>';

  try {
    const type = plexState.type === 'tv' ? 'tv' : 'movie';
    const resp = await fetch(`/api/tmdb/search?q=${encodeURIComponent(query)}&type=${type}`);
    const data = await resp.json();

    if (!resp.ok) {
      errEl.textContent = data.error || 'Search failed';
      errEl.classList.remove('hidden');
      container.innerHTML = '';
      return;
    }

    if (data.results.length === 0) {
      container.innerHTML = '<div style="color:var(--text-dim);font-size:0.8rem;">No results found</div>';
      return;
    }

    container.innerHTML = data.results.map(r => {
      const title = r.title || r.name || '';
      const year = (r.release_date || r.first_air_date || '').substring(0, 4);
      const poster = r.poster_path ? `https://image.tmdb.org/t/p/w92${r.poster_path}` : '';
      const imgTag = poster ? `<img src="${poster}" alt="">` : `<img src="" alt="" style="background:var(--surface2);">`;
      return `<div class="tmdb-result" onclick="selectTMDB(${r.id}, '${escapeHtml(title).replace(/'/g, "\\'")}', '${year}')">
        ${imgTag}
        <div class="tmdb-info">
          <div class="tmdb-title">${escapeHtml(title)}</div>
          <div class="tmdb-year">${year}</div>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    errEl.textContent = 'Search failed: ' + e.message;
    errEl.classList.remove('hidden');
    container.innerHTML = '';
  }
}

function selectTMDB(id, title, year) {
  plexState.tmdbId = id;
  plexState.tmdbTitle = title;
  plexState.tmdbYear = year;

  document.querySelectorAll('.tmdb-result').forEach(el => el.classList.remove('selected'));
  event.currentTarget.classList.add('selected');

  if (plexState.type === 'tv') {
    document.getElementById('plex-tv-fields').classList.remove('hidden');
    fetchEpisodes();
  }

  updatePlexPreview();
}

async function fetchEpisodes() {
  if (!plexState.tmdbId || plexState.type !== 'tv') return;
  const season = document.getElementById('plex-season').value;

  try {
    const resp = await fetch(`/api/tmdb/tv/${plexState.tmdbId}/season/${season}`);
    const data = await resp.json();
    if (resp.ok && data.episodes) {
      plexState.episodes = data.episodes;
      updateEpisodeTitle();
    }
  } catch (e) {}
  updatePlexPreview();
}

function updateEpisodeTitle() {
  const epNum = parseInt(document.getElementById('plex-episode').value) || 1;
  const ep = plexState.episodes.find(e => e.episode_number === epNum);
  document.getElementById('plex-ep-title').value = ep ? ep.name : '';
  updatePlexPreview();
}

function getPlexPath() {
  const ext = '.' + plexState.filename.split('.').pop();

  if (plexState.type === 'movie') {
    if (!plexState.tmdbTitle) return null;
    const name = plexState.tmdbYear ? `${plexState.tmdbTitle} (${plexState.tmdbYear})` : plexState.tmdbTitle;
    return `Plex/Movies/${name}/${name}${ext}`;
  }

  if (plexState.type === 'tv') {
    if (!plexState.tmdbTitle) return null;
    const season = parseInt(document.getElementById('plex-season').value) || 1;
    const episode = parseInt(document.getElementById('plex-episode').value) || 1;
    const epTitle = document.getElementById('plex-ep-title').value.trim();
    const sNum = String(season).padStart(2, '0');
    const eNum = String(episode).padStart(2, '0');
    const showName = plexState.tmdbTitle;
    let filename = `${showName} - S${sNum}E${eNum}`;
    if (epTitle) filename += ` - ${epTitle}`;
    filename += ext;
    return `Plex/TV Shows/${showName}/Season ${sNum}/${filename}`;
  }

  if (plexState.type === 'music') {
    const artist = document.getElementById('plex-artist').value.trim();
    const album = document.getElementById('plex-album').value.trim();
    const trackNum = parseInt(document.getElementById('plex-track-num').value) || 1;
    const trackTitle = document.getElementById('plex-track-title').value.trim();
    if (!artist || !album || !trackTitle) return null;
    const num = String(trackNum).padStart(2, '0');
    return `Plex/Music/${artist}/${album}/${num} - ${trackTitle}${ext}`;
  }

  return null;
}

function updatePlexPreview() {
  const path = getPlexPath();
  const el = document.getElementById('plex-path-preview');
  if (path) {
    el.textContent = '~/Downloads/DownTube/' + path;
    el.style.color = 'var(--green)';
  } else {
    el.textContent = 'Fill in the details above to see the path...';
    el.style.color = 'var(--text-dim)';
  }
}

// Listen for input changes in music fields to update preview
document.addEventListener('DOMContentLoaded', () => {
  ['plex-artist', 'plex-album', 'plex-track-num', 'plex-track-title'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updatePlexPreview);
  });
  ['plex-season', 'plex-episode', 'plex-ep-title'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updatePlexPreview);
  });
});

async function confirmPlexPrep() {
  const path = getPlexPath();
  if (!path) {
    document.getElementById('plex-msg').innerHTML = '<div class="error-msg">Please fill in all required fields</div>';
    return;
  }

  const btn = document.getElementById('plex-confirm-btn');
  btn.disabled = true;
  btn.textContent = 'Moving...';
  document.getElementById('plex-msg').innerHTML = '';

  try {
    const resp = await fetch('/api/plex-prep', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ filename: plexState.filename, plex_path: path })
    });
    const data = await resp.json();

    if (resp.ok) {
      document.getElementById('plex-msg').innerHTML = `<div class="success-msg">Moved to ${escapeHtml(data.path)}</div>`;
      refreshFiles();
      setTimeout(closePlexModal, 1500);
    } else {
      document.getElementById('plex-msg').innerHTML = `<div class="error-msg">${escapeHtml(data.error)}</div>`;
    }
  } catch (e) {
    document.getElementById('plex-msg').innerHTML = `<div class="error-msg">Failed: ${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Move to Plex';
  }
}

// ---- Init ----

document.getElementById('url').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') fetchInfo();
});

document.getElementById('plex-search').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') { e.preventDefault(); searchTMDB(); }
});

// Close modals on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', function(e) {
    if (e.target === this) this.classList.remove('visible');
  });
});

startFilePolling();
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


@app.route("/api/files")
def api_files():
    """List all files in the download directory."""
    files = []
    try:
        for f in DOWNLOAD_DIR.iterdir():
            if not f.is_file() or f.name.startswith('.'):
                continue
            # Get the real extension (skip .part, .ytdl suffixes)
            ext = f.suffix.lower()
            if ext in IGNORE_EXTS:
                continue
            # Also skip files with double extensions like .mp4.part
            if any(f.name.lower().endswith(ie) for ie in IGNORE_EXTS):
                continue
            # Only show video and audio files
            if ext not in ALLOWED_EXTS:
                continue
            stat = f.stat()
            files.append({
                "name": f.name,
                "ext": ext.lstrip('.'),
                "size": stat.st_size,
                "size_fmt": format_size(stat.st_size),
                "modified": stat.st_mtime,
                "type": get_file_type(ext),
                })
    except OSError:
        pass
    files.sort(key=lambda x: x["modified"], reverse=True)
    return jsonify(files)


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(load_config())


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.get_json()
    cfg = load_config()
    if "tmdb_api_key" in data:
        cfg["tmdb_api_key"] = data["tmdb_api_key"].strip()
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/tmdb/search")
def api_tmdb_search():
    query = request.args.get("q", "").strip()
    media_type = request.args.get("type", "movie")

    if not query:
        return jsonify({"error": "No query"}), 400

    endpoint = "/search/movie" if media_type == "movie" else "/search/tv"
    data, err = tmdb_request(endpoint, {"query": query})

    if err:
        return jsonify({"error": err}), 400

    # Return only the fields we need (limit to 8 results)
    results = []
    for r in (data.get("results") or [])[:8]:
        results.append({
            "id": r.get("id"),
            "title": r.get("title") or r.get("name"),
            "release_date": r.get("release_date") or r.get("first_air_date", ""),
            "first_air_date": r.get("first_air_date", ""),
            "poster_path": r.get("poster_path"),
            "name": r.get("name"),
        })

    return jsonify({"results": results})


@app.route("/api/tmdb/tv/<int:tv_id>/season/<int:season_num>")
def api_tmdb_season(tv_id, season_num):
    data, err = tmdb_request(f"/tv/{tv_id}/season/{season_num}")

    if err:
        return jsonify({"error": err}), 400

    episodes = []
    for ep in (data.get("episodes") or []):
        episodes.append({
            "episode_number": ep.get("episode_number"),
            "name": ep.get("name", ""),
        })

    return jsonify({"episodes": episodes})


@app.route("/api/plex-prep", methods=["POST"])
def api_plex_prep():
    data = request.get_json()
    filename = data.get("filename", "").strip()
    plex_path = data.get("plex_path", "").strip()

    if not filename or not plex_path:
        return jsonify({"error": "Missing filename or path"}), 400

    # Validate source file exists
    source = DOWNLOAD_DIR / filename
    if not source.is_file():
        return jsonify({"error": "Source file not found"}), 404

    # Sanitize each path component
    parts = plex_path.split("/")
    safe_parts = [sanitize_filename(p) for p in parts]
    dest = DOWNLOAD_DIR / os.path.join(*safe_parts)

    # Ensure we're still within DOWNLOAD_DIR
    try:
        dest.resolve().relative_to(DOWNLOAD_DIR.resolve())
    except ValueError:
        return jsonify({"error": "Invalid destination path"}), 400

    # Create parent dirs and move
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(source), str(dest))
    except OSError as e:
        return jsonify({"error": f"Failed to move file: {e}"}), 500

    return jsonify({"ok": True, "path": str(dest)})


if __name__ == "__main__":
    print(f"\n  DownTube is running at http://localhost:8080")
    print(f"  Downloads will be saved to {DOWNLOAD_DIR}\n")
    app.run(host="127.0.0.1", port=8080, debug=False)
