# Project handoff

## Current state

WARDOGS Presence 1.1.0 is an Electron/React desktop app with a bundled Python
screen-reading engine and Tesseract OCR. The Windows release uses Electron
Builder's portable target to produce one EXE. The app stores settings under
`%APPDATA%\WardogsPresence` and loads its interface from local bundled files.

The build includes independent Discord webhook destinations, draggable capture
zones, OCR readback, and setup guidance in the app and on GitHub Pages.

## Verification

- Python suite: 105 passed locally.
- React production build: passed locally.
- Frozen bridge: detected 2 displays and found bundled English OCR data.
- First portable EXE: launched, rendered the React interface, and completed
  capture and OCR calls through the Electron bridge against a desktop screen.

## Remaining validation

- Run the final EXE on a clean Windows machine without the developer tools.
- Calibrate and verify OCR in a live War Dogs match.
- Verify public GitHub Actions release and Pages deployments after publication.

Keep webhook URLs, AppData config, captured screens, and local build outputs out
of commits and issues.
