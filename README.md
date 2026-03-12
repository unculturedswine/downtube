# DownTube

A local web GUI wrapper for [yt-dlp](https://github.com/yt-dlp/yt-dlp). Paste a URL, pick your format and quality, and download — all from your browser.

![Python](https://img.shields.io/badge/python-3.8+-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **Video + Audio, Video Only, or Audio Only** download modes
- **Quality selection** from available formats (1080p, 720p, etc.)
- **Audio format choice** (MP3, M4A, Opus) for audio-only downloads
- **Browser cookie support** — authenticate via Brave, Chrome, Firefox, Safari, or Edge
- **Real-time progress** bar with speed display
- **Session download history**
- **Dark theme** UI

## Quick Start

```bash
git clone https://github.com/your-username/DownTube.git
cd DownTube
python3 app.py
```

That's it. On first run, the app will:

1. Create a `.venv` and install Flask if needed
2. Prompt to install `yt-dlp` and `ffmpeg` if they're missing

Then open **http://localhost:8080** in your browser.

## Requirements

- **Python 3.8+**
- **yt-dlp** — for downloading (`brew install yt-dlp` or `pip install yt-dlp`)
- **ffmpeg** — for merging video/audio and format conversion (`brew install ffmpeg`)

All dependencies are checked and offered for installation automatically on startup.

## Usage

1. Paste a video URL (YouTube, Vimeo, or any [yt-dlp supported site](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md))
2. Select your browser for cookie authentication (or "None" if not needed)
3. Click **Fetch** to load video info and available qualities
4. Choose your download mode and quality
5. Click **Download** and watch the progress bar

Downloads are saved to `~/Downloads/DownTube/`.

## How It Works

DownTube is a single Python file (`app.py`) that runs a local Flask server with an embedded HTML/CSS/JS frontend. It shells out to `yt-dlp` for fetching video metadata and downloading, streaming progress back to the browser via Server-Sent Events (SSE).

## License

MIT
