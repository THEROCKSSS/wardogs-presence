# WARDOGS Presence

A local Windows desktop app that reads your War Dogs status from the game screen and updates a message in each Discord channel you choose. Built with Electron, React, a Python OCR engine, and bundled Tesseract. The downloadable release is **one portable EXE**; players need no Node, Python, OCR installation, or local server.

## Download and use

Download `WardogsPresence-*-win-x64.exe` from [Releases](https://github.com/THEROCKSSS/wardogs-presence/releases), then read the [player guide](docs/GETTING-STARTED.md). The same guide is available as **How it works** inside the app. A [web guide](https://therocksss.github.io/wardogs-presence/) is included in this repository.

Your webhook URLs and calibration settings are saved in `%APPDATA%\WardogsPresence\config.json`. Treat that file as a secret. The app sends status text to enabled Discord webhooks; screen captures remain local. Optional server directory enrichment is off by default.

## Features

- Save multiple webhook destinations; enable one or several at a time.
- Each destination keeps its own message and rate-limit retry state.
- Capture a game frame, drag the three reading zones, and inspect OCR output.
- Pause the watcher without deleting settings.
- Local React interface with an in-app setup and troubleshooting guide.

## Build from source (Windows)

Requirements: Node.js/npm, Python 3.13, and a local Tesseract OCR installation with English data. `packaging/WardogsPresence.spec` uses `C:\Program Files\Tesseract-OCR` by default; set `TESSERACT_SRC` to another installation directory if needed. The build checks that Tesseract and the frozen engine actually run.

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python packaging\build_desktop.py
```

The result is `dist/WardogsPresence-1.1.0-win-x64.exe`. Electron Builder's portable target packs its runtime, React assets, Python engine, and Tesseract resources into that single download. It extracts to a temporary directory at launch and keeps user settings under AppData. The UI uses bundled files; it does not run a local HTTP server.

For interface development, use `npm ci` in `frontend/`, then `npm run dev`. The browser preview has disabled device controls. Build the engine with the build script, then run `npm run desktop` from the repository root to exercise the native bridge.

## Contributing and security

Issues and pull requests are welcome. Do not post webhook URLs, screenshots containing personal data, or `%APPDATA%\WardogsPresence` files in issues. Run `.venv\Scripts\python -m pytest tests -q` and `npm run build` in `frontend/` before opening a PR. See [SECURITY.md](SECURITY.md) for private vulnerability reports.

## Credits and license

The game screen parsing approach derives from [msmcpeake/wardogs-discord-status](https://github.com/msmcpeake/wardogs-discord-status) (MIT). See [NOTICE](NOTICE) for attribution. This project is MIT licensed; bundled Tesseract is Apache 2.0 licensed.
