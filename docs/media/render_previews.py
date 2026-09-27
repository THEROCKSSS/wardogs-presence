"""Render fictional, source-shaped Discord previews for the public guide.

All names, server IDs, and channels below are examples. No capture, webhook,
or user configuration is read by this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_presence.discord_webhook import build_body  # noqa: E402
from wardogs_presence.loop import build_description  # noqa: E402

OUT = Path(__file__).resolve().parent
FONT = Path("C:/Windows/Fonts/segoeui.ttf")
BOLD = Path("C:/Windows/Fonts/seguisb.ttf")
DISPLAY = Path("C:/Windows/Fonts/seguibl.ttf")
BG = "#11171c"
PANEL = "#1a2228"
DISCORD = "#313338"
EMBED = "#2b2d31"
INK = "#f1f0e9"
MUTED = "#aebbbf"
GOLD = "#d8a35d"
TEAL = "#76bebd"


def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    return ImageFont.truetype({"regular": FONT, "bold": BOLD, "display": DISPLAY}[weight], size)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, size: int, *,
         fill: str = INK, weight: str = "regular") -> None:
    draw.text(xy, value, font=font(size, weight), fill=fill)


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *,
            fill: str, radius: int = 18, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def badge(draw: ImageDraw.ImageDraw, label: str, x: int, y: int, color: str = GOLD) -> None:
    rounded(draw, (x, y, x + 190, y + 34), fill="#2b3030", radius=7, outline="#48524f")
    text(draw, (x + 12, y + 7), label, 16, fill=color, weight="bold")


EXAMPLES = [
    ("match", "IN A MATCH", "Lonestar · Europe #145 · ID 123456", (118, 92, 74),
     "One message is edited as scores change."),
    ("queue", "IN QUEUE", "Queued for Europe #145 (position 3 of 8)", None,
     "The queue timer is ignored so it won't spam updates."),
    ("offline", "OUT OF GAME", "Alex is not in a game", None,
     "The status stays clear when you leave the match."),
]


def body_for(status: str, scores: tuple[int, int, int] | None):
    description = build_description(status, "Alex", scores=scores)
    return build_body(description, "Current Wardogs Server — Alex", scores is not None,
                      "2026-09-27T12:00:00+00:00", "Alex")


def discord_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                 status: str, scores: tuple[int, int, int] | None) -> None:
    x, y, r, b = box
    payload = body_for(status, scores)
    embed = payload["embeds"][0]
    rounded(draw, box, fill=DISCORD, radius=16)
    draw.ellipse((x + 25, y + 23, x + 75, y + 73), fill="#d8a35d")
    text(draw, (x + 40, y + 35), "W", 24, fill="#161b1f", weight="display")
    text(draw, (x + 91, y + 20), payload["username"], 23, fill="#f5f5f5", weight="bold")
    rounded(draw, (x + 146, y + 27, x + 188, y + 49), fill="#5865f2", radius=4)
    text(draw, (x + 152, y + 30), "APP", 12, weight="bold")
    text(draw, (x + 202, y + 27), "Today at 12:00", 14, fill="#aeb1b7")
    ex, ey = x + 90, y + 67
    rounded(draw, (ex, ey, r - 27, b - 24), fill=EMBED, radius=7)
    draw.rectangle((ex, ey + 7, ex + 4, b - 31), fill="#4e9d78" if scores else "#74808c")
    text(draw, (ex + 20, ey + 14), embed["title"], 22, fill="#ffffff", weight="bold")
    lines = embed["description"].splitlines()
    yline = ey + 55
    colors = {"🔵": "#4f90e6", "🔴": "#e06a6a", "🟢": "#65b77b"}
    for line in lines:
        if not line:
            yline += 13
            continue
        # Draw colored circles in place of platform emoji to keep these
        # examples legible even without an emoji font installed.
        if line[:1] in colors and " │ " in line:
            draw.ellipse((ex + 21, yline + 7, ex + 37, yline + 23), fill=colors[line[0]])
            draw.line((ex + 55, yline + 3, ex + 55, yline + 27), fill="#aeb1b7", width=2)
            text(draw, (ex + 68, yline), line.split(" │ ", 1)[1], 20, fill="#dce0e3")
        elif line.startswith("Score: "):
            text(draw, (ex + 20, yline), "Score:", 20, fill="#dce0e3")
            cursor = ex + 96
            for index, (symbol, score) in enumerate(zip(colors, scores or ())):
                draw.ellipse((cursor, yline + 7, cursor + 16, yline + 23), fill=colors[symbol])
                cursor += 23
                score_text = str(score)
                text(draw, (cursor, yline), score_text, 20, fill="#dce0e3")
                cursor += int(draw.textlength(score_text, font=font(20))) + 18
                if index < 2:
                    text(draw, (cursor, yline), "–", 20, fill="#dce0e3")
                    cursor += 31
        else:
            text(draw, (ex + 20, yline), line, 20, fill="#dce0e3")
        yline += 35
    text(draw, (ex + 20, b - 62), "Last updated  •  12:00", 15, fill="#9da2a8")


def render_preview(key: str, label: str, status: str, scores, note: str) -> None:
    image = Image.new("RGB", (1200, 440), BG)
    d = ImageDraw.Draw(image)
    d.rectangle((0, 0, 8, 440), fill=GOLD)
    text(d, (42, 32), "WARDOGS  /  DISCORD PREVIEW", 17, fill=GOLD, weight="bold")
    badge(d, label, 955, 31)
    discord_card(d, (42, 84, 1158, 365), status, scores)
    text(d, (42, 387), note, 19, fill=MUTED)
    text(d, (955, 391), "EXAMPLE DATA", 14, fill=GOLD, weight="bold")
    image.save(OUT / f"status-{key}.png", optimize=True)


def slide_base(number: str, eyebrow: str, headline: str, subline: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGB", (1280, 720), BG)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 14, 720), fill=GOLD)
    d.line((68, 87, 1212, 87), fill="#35434b", width=2)
    text(d, (68, 42), "WARDOGS  /  PRESENCE", 24, fill=INK, weight="display")
    text(d, (1035, 47), f"FIELD GUIDE   {number}", 17, fill=GOLD, weight="bold")
    text(d, (68, 130), eyebrow, 20, fill=GOLD, weight="bold")
    text(d, (68, 171), headline, 58, fill=INK, weight="display")
    text(d, (70, 255), subline, 24, fill=MUTED)
    d.line((68, 666, 1212, 666), fill="#35434b", width=2)
    text(d, (69, 680), "ONE PORTABLE EXE  /  LOCAL OCR  /  DISCORD WEBHOOKS", 15, fill="#829298", weight="bold")
    return im, d


def callout(d, x, y, n, title, detail, width=338):
    rounded(d, (x, y, x + width, y + 222), fill=PANEL, radius=14, outline="#35434b", width=2)
    text(d, (x + 20, y + 17), n, 18, fill=GOLD, weight="bold")
    text(d, (x + 20, y + 63), title, 28, weight="bold")
    for i, line in enumerate(detail):
        text(d, (x + 20, y + 117 + i * 29), line, 19, fill=MUTED)


def render_slides() -> None:
    slides = []
    im, d = slide_base("00", "A LOCAL WINDOWS APP", "Your squad knows where you are.",
                       "Game screen to Discord status, from one portable EXE.")
    discord_card(d, (69, 315, 1210, 630), EXAMPLES[0][2], EXAMPLES[0][3])
    slides.append(im)

    im, d = slide_base("01", "STEP ONE", "Download and open the EXE.",
                       "Everything runs on your Windows PC. No installer or server setup.")
    callout(d, 69, 349, "01  /  DOWNLOAD", "GitHub Releases", ["Get the Windows x64 EXE", "and keep it anywhere."])
    callout(d, 471, 349, "02  /  OPEN", "Double-click", ["The React interface and", "OCR engine are bundled."])
    callout(d, 873, 349, "03  /  LOCAL", "Your settings", ["Saved under AppData", "on this computer."])
    slides.append(im)

    im, d = slide_base("02", "STEP TWO", "Connect the Discord channels.",
                       "Create a webhook for each channel you want to update.")
    callout(d, 69, 349, "DISCORD", "New Webhook", ["Edit Channel → Integrations", "→ Webhooks → New Webhook"], 535)
    callout(d, 675, 349, "WARDOGS PRESENCE", "Add destination", ["Paste the URL and test it.", "Enable one or several."], 535)
    slides.append(im)

    im, d = slide_base("03", "STEP THREE", "Calibrate what the app reads.",
                       "With the pause menu visible, capture a frame and drag three zones.")
    for x, n, title, detail in [(69, "01", "Server panel", ["CURRENT SERVER", "and SERVER ID"]),
                                (471, "02", "Faction icon", ["Your team icon", "on screen"]),
                                (873, "03", "Scoreboard", ["The three score", "numbers"])]:
        callout(d, x, 349, n, title, detail)
    slides.append(im)

    im, d = slide_base("04", "STEP FOUR", "Start the watcher. Play.",
                       "A status change edits one existing message in each enabled channel.")
    callout(d, 69, 349, "LOCAL", "Screen capture", ["Only while the game", "and watcher are running."])
    callout(d, 471, 349, "LOCAL", "OCR and parse", ["Read server, queue,", "faction, and score."])
    callout(d, 873, 349, "DISCORD", "Message edit", ["Respect retry delays", "and per-hook cooldowns."])
    slides.append(im)

    im, d = slide_base("05", "WHAT FRIENDS SEE", "One tidy status message.",
                       "Match, queue, and out-of-game states use the same message slot.")
    discord_card(d, (69, 315, 1210, 630), EXAMPLES[1][2], EXAMPLES[1][3])
    slides.append(im)

    for n, slide in enumerate(slides):
        slide.save(OUT / f"slide-{n:02}.png", optimize=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for example in EXAMPLES:
        render_preview(*example)
    render_slides()
    print("Rendered 3 Discord previews and 6 video slides from fictional data.")
