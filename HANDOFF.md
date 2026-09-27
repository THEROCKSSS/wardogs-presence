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
- Final portable EXE repeated the packaged smoke successfully. The tagged
  release is a prerelease while live game and clean-PC validation remain open.
- The first hosted release build stopped on a legacy Tk wizard layout test:
  its 784px content exceeded the runner's 768px virtual display. The Electron
  build gate now excludes that obsolete UI test; the other tests remain.

## Remaining validation

- Run the final EXE on a clean Windows machine without the developer tools.
- Calibrate and verify OCR in a live War Dogs match.
- Verify the revised GitHub Actions release build on `main`.

Keep webhook URLs, AppData config, captured screens, and local build outputs out
of commits and issues.
