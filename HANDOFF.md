# Project handoff

## Current state

WARDOGS Presence 1.1.1 is an Electron/React desktop app with a bundled Python
screen-reading engine and Tesseract OCR. The Windows release uses Electron
Builder's portable target to produce one EXE. The app stores settings under
`%APPDATA%\WardogsPresence` and loads its interface from local bundled files.

The build includes independent Discord webhook destinations, draggable capture
zones, OCR readback, and setup guidance in the app and on GitHub Pages. The
guide now includes fictional Discord status previews and a locally generated,
narrated walkthrough. The finished video is bundled with the desktop app;
voice profile source files are not part of this repository.

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
  build gate now excludes that obsolete UI test; the other tests remain. The
  revised 1.1.0 hosted build passed 98 tests and produced its EXE artifact.
- The 1.1.1 guide media rendered and played in a local browser. Its six
  narration segments were generated through a local Voicebox MCP clone;
  the final MP4 has an AAC audio track and was bundled by Vite successfully.
- The final 1.1.1 portable EXE launched with isolated AppData. Its React UI
  loaded from `file:`, bridge capture and OCR succeeded, two displays were
  detected, and the in-app guide loaded the 78.1-second bundled MP4 and all
  three status preview images.
- Hosted 1.1.1 release run 36313663379 and Pages run 36313661354 passed.
  The EXE downloaded from GitHub Releases (169,004,686 bytes, SHA-256
  `491899c8517fc2b4c7ef1a161b5200797a6dc32ff11f26eb7f5f484001c91a71`)
  passed the same packaged smoke with isolated AppData. Its hash matches the
  release checksum and GitHub's asset digest. The EXE is unsigned and remains
  a prerelease while live-game OCR and clean-PC verification are outstanding.

## Remaining validation

- Run the final EXE on a clean Windows machine without the developer tools.
- Calibrate and verify OCR in a live War Dogs match.

Keep webhook URLs, AppData config, captured screens, and local build outputs out
of commits and issues.
