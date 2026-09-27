# WARDOGS Presence setup demo transcript

The video uses fictional player, server, and channel details. Its preview images are illustrative Discord-style renders of the app's message payloads.

**Opening.** WARDOGS Presence turns your War Dogs game status into one tidy Discord message. It runs from a single portable Windows executable.

**Download.** Download the Windows EXE from the project releases and open it. The interface, screen reader, and OCR data are already inside. Your settings stay on this computer.

**Connect Discord.** In Discord, create a webhook for each channel you want to update. Paste each URL into Discord channels in the app, test the connection, and enable the destinations you want.

**Calibrate.** With the game pause menu visible, capture a frame. Drag the server, faction, and score zones over the matching parts of the screen. Run the OCR check and adjust until the readback looks right.

**Start.** Start the watcher when you are ready to play. The app reads the game screen locally, then edits one existing message in each enabled channel. Each webhook has its own cooldown and respects Discord retry delays.

**Result.** Your friends see a match, queue, or out-of-game status in one place. The game screenshot stays local; only status text goes to the Discord webhooks you enabled. The full setup guide is in the app and on GitHub Pages.
