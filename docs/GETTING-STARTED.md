# Getting started with WARDOGS Presence

Watch the [narrated setup demo](https://therocksss.github.io/wardogs-presence/#demo) or read its [transcript](media/VIDEO-TRANSCRIPT.md). The demo and previews use fictional example data.

## What your Discord channel will show

The app creates one message per enabled webhook and edits it as your status changes. These illustrative previews are rendered from the app's message-building code; Discord's emoji and spacing may vary.

![Illustrative match status with server ID and scores](media/status-match.png)

![Illustrative queue status with position](media/status-queue.png)

![Illustrative out-of-game status](media/status-offline.png)

## 1. Download the one EXE

On Windows 10 or 11, download `WardogsPresence-*-win-x64.exe` from the project's [GitHub Releases](https://github.com/THEROCKSSS/wardogs-presence/releases). Keep it anywhere you like and double-click it. No installer, Python, Tesseract, or browser server is needed. Windows may warn about an unsigned app; inspect the publisher and SHA-256 digest on the release before deciding whether to run it.

## 2. Create a Discord webhook

In a channel you can manage, open **Edit Channel → Integrations → Webhooks → New Webhook**, then copy the URL. A webhook URL can post to its channel, so keep it private. You can create several webhooks for different channels.

## 3. Connect the app

Enter your display name on **Overview**. Open **Discord channels**, add a channel, paste its webhook URL, and select **Test connection**. The test checks the URL and channel access; it does not post a message. Turn on only the destinations you want active. Select **Save changes**.

## 4. Calibrate the screen

Open War Dogs on the monitor you want to watch and show the pause menu in a match. In **Screen capture**, select that monitor and choose **Capture in 5 sec** so you can switch back to the game. Select each reading zone, then drag its box on the screenshot:

| Zone | Place it around |
|---|---|
| Server panel | `CURRENT SERVER` and `SERVER ID` text |
| Faction icon | Your team icon |
| Scoreboard | The three score numbers |

In the game, set **Settings → Interface → Faction → Always On**. Select **Run OCR check** in the app to see the exact text it reads. Adjust boxes until the server, faction, and scores are recognized, then save.

## 5. Start the watcher

On **Overview**, select **Start watcher**. The app checks for the game before taking captures. When status changes, it edits one Discord message per enabled destination. Unchanged status uses a bounded heartbeat; score updates are throttled, and Discord retry delays are honored. Use **Stop watcher** to pause it. Closing the window stops the watcher.

## Troubleshooting

| Symptom | Check |
|---|---|
| No status in Discord | Save settings, start the watcher, confirm the game is running, and test each enabled webhook. |
| Wrong or missing server | Re-capture and move the server panel zone; inspect raw OCR text. |
| Missing faction | Turn on **Faction → Always On** in game, then move the icon zone. |
| Wrong scores | Move the scoreboard zone around the visible numbers. |
| Wrong display | Change the selected monitor and re-capture. |

Settings and webhook URLs live in `%APPDATA%\WardogsPresence\config.json`; logs live in the same folder. Never paste that config into a public issue. The EXE's interface and OCR engine run locally. Status text goes only to your enabled Discord webhooks, unless you choose to enable optional server directory enrichment.
