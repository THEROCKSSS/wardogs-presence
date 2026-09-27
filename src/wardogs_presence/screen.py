"""Screen capture + OCR, and the parsing that turns pixels into a status.

The capture regions, the preprocessing decision, the faction-hue detection and
the score-read strictness are all ported from
``msmcpeake/wardogs-discord-status`` (MIT) — those values were measured against
the real game and are preserved deliberately rather than re-derived.

Two changes from upstream, both about packaging rather than logic:
  * ``tesseract_cmd`` is resolved by ``resolve_tesseract()`` so it can come
    from a bundled copy inside the app folder, not just a system install.
  * Parsing functions are pure and take text as input, so they can be unit
    tested against captured fixtures without a screen or the game.
"""

from __future__ import annotations

import colorsys
import logging
import math
import os
import re
import sys
from pathlib import Path

import mss
import psutil
import pytesseract
from PIL import Image, ImageOps

log = logging.getLogger("wardogs.screen")


# === Tesseract resolution ===================================================
def app_dir() -> Path:
    """Folder the app actually lives in — handles both a normal checkout and a
    PyInstaller bundle (where ``sys._MEIPASS`` is the unpacked temp dir)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def resolve_tesseract(override: str = "") -> str:
    """Finds ``tesseract.exe``, preferring (in order):

      1. an explicit user override from config,
      2. a copy bundled next to the app (the friend-distribution path),
      3. the standard system install location.

    Returns the path that ``pytesseract`` should use. Raises nothing — the
    caller decides whether a missing Tesseract is fatal.
    """
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    base = app_dir()
    candidates += [
        base / "tesseract" / "tesseract.exe",
        base.parent / "tesseract" / "tesseract.exe",
        base.parent.parent / "tesseract" / "tesseract.exe",
    ]
    if os.environ.get("PROGRAMFILES"):
        candidates.append(Path(os.environ["PROGRAMFILES"]) / "Tesseract-OCR" / "tesseract.exe")
    if os.environ.get("PROGRAMFILES(X86)"):
        candidates.append(Path(os.environ["PROGRAMFILES(X86)"]) / "Tesseract-OCR" / "tesseract.exe")
    candidates.append(Path("C:/Program Files/Tesseract-OCR/tesseract.exe"))

    for candidate in candidates:
        try:
            if candidate and Path(candidate).is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def configure_tesseract(override: str = "") -> str:
    """Points pytesseract at the resolved binary. Returns the path used, or an
    empty string when nothing was found."""
    path = resolve_tesseract(override)
    if path:
        pytesseract.pytesseract.tesseract_cmd = path
    return path


# === Capture ===============================================================
def parse_region(region_str: str):
    """Parses "left,top,right,bottom" (0-1 screen fractions). Raises
    ValueError with a readable message if malformed or nonsensical."""
    try:
        left, top, right, bottom = (float(x) for x in region_str.split(","))
    except (ValueError, AttributeError):
        raise ValueError(f'"{region_str}" is not four comma-separated numbers (left,top,right,bottom)')
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError(f'"{region_str}" needs 0 <= left < right <= 1 and 0 <= top < bottom <= 1')
    return left, top, right, bottom


def list_monitors() -> list[dict]:
    """Every real monitor as ``{index, width, height, primary}``. Index 1 is
    the primary display (mss reserves index 0 for the virtual all-monitors
    surface, which is why the app's MONITOR_INDEX starts at 1)."""
    with mss.MSS() as sct:
        out = []
        for i, mon in enumerate(sct.monitors):
            if i < 1:
                continue
            out.append(
                {
                    "index": i,
                    "width": mon["width"],
                    "height": mon["height"],
                    "left": mon["left"],
                    "top": mon["top"],
                    "primary": bool(mon.get("is_primary")),
                }
            )
        return out


def grab_region(region_str: str, monitor_index: int = 1) -> Image.Image:
    """Grab one region off the chosen monitor, as 0-1 fractions of that
    monitor's own bounds."""
    left_f, top_f, right_f, bottom_f = parse_region(region_str)
    with mss.MSS() as sct:
        monitors = sct.monitors
        index = monitor_index
        if not 1 <= index < len(monitors):
            log.warning(
                "monitor_index=%d does not exist (this machine has 1-%d); using 1.",
                index,
                len(monitors) - 1,
            )
            index = 1
        mon = monitors[index]
        w, h = mon["width"], mon["height"]
        box = {
            "left": mon["left"] + int(w * left_f),
            "top": mon["top"] + int(h * top_f),
            "width": int(w * (right_f - left_f)),
            "height": int(h * (bottom_f - top_f)),
        }
        shot = sct.grab(box)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def preprocess(img: Image.Image) -> Image.Image:
    """Grayscale + autocontrast. No upscaling — upstream measured that
    upscaling cost ~4x the pixels and detected *less* text on a sharp
    display, so it was pure overhead. Revisit only if real testing on a
    lower-resolution display shows misreads."""
    return ImageOps.autocontrast(img.convert("L"))


# === Game-process gate =====================================================
def is_game_running(substring: str = "wardogs") -> bool:
    """True when a process name contains ``substring``. This is the gate that
    keeps the app from screenshotting a desktop forever."""
    needle = (substring or "").lower()
    if not needle:
        return False
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if needle in name:
            return True
    return False


# === Team / faction detection ==============================================
# Reference hues (degrees) per faction, from upstream. Lonestar was measured
# against a live sample (RGB 80,228,255 -> hue ~189); Valkyra/Manticore are
# their icon colours (red/green). All three were since confirmed live by the
# upstream author.
TEAM_HUE_DEGREES = {"Lonestar": 189, "Valkyra": 0, "Manticore": 120}


def detect_team(img: Image.Image):
    """Classifies the faction icon's colour in the bottom-right HUD corner.
    Returns the faction name, or None when no confidently-coloured icon is
    there (HUD hidden, occluded, or wrong region for this resolution).

    Ported from upstream: circular mean of hue weighted by saturation*value,
    a 0.35 weight floor to ignore washed-out background, and a minimum pixel
    *count* (not proportion) because icon shapes vary — a proportion
    threshold that suited Lonestar's filled square wrongly discarded
    Valkyra's thinner chevron."""
    rgb_img = img.convert("RGB")
    sin_sum = cos_sum = weight_sum = 0.0
    colorful_count = 0
    for r, g, b in rgb_img.getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        weight = s * v
        if weight < 0.35:
            continue
        colorful_count += 1
        angle = h * 2 * math.pi
        sin_sum += weight * math.sin(angle)
        cos_sum += weight * math.cos(angle)
        weight_sum += weight

    if colorful_count < 40 or weight_sum <= 0:
        return None

    mean_hue_deg = math.degrees(math.atan2(sin_sum, cos_sum)) % 360

    def circular_distance(a, b):
        d = abs(a - b) % 360
        return min(d, 360 - d)

    return min(TEAM_HUE_DEGREES, key=lambda t: circular_distance(mean_hue_deg, TEAM_HUE_DEGREES[t]))


# === Score reading =========================================================
SCORE_TEAMS = ("Lonestar", "Valkyra", "Manticore")  # left-to-right = blue, red, green
SCORE_DIGITS_WIDTH_FRACTION = 0.74  # digits occupy this share of each panel
SCORE_DIGITS_MIN_HEIGHT_PX = 60


def read_scores(img: Image.Image):
    """Reads the three big team scores. Returns a 3-tuple of ints, or None
    unless *all three* panels read as exactly three digits.

    That strictness is load-bearing: it is what stops menus or map screens
    from producing phantom scores."""
    third = img.width / 3
    scores = []
    for i in range(3):
        left = round(i * third)
        cell = img.crop((left, 0, left + round(third * SCORE_DIGITS_WIDTH_FRACTION), img.height))
        if cell.height < SCORE_DIGITS_MIN_HEIGHT_PX and cell.height > 0:
            factor = -(-SCORE_DIGITS_MIN_HEIGHT_PX // cell.height)  # ceil
            cell = cell.resize((cell.width * factor, cell.height * factor), Image.LANCZOS)
        cell = ImageOps.autocontrast(cell.convert("L"))
        text = pytesseract.image_to_string(
            cell, config="--psm 7 -c tessedit_char_whitelist=0123456789"
        ).strip()
        if not re.fullmatch(r"\d{3}", text):
            return None
        scores.append(int(text))  # int() drops the leading zeros
    return tuple(scores)


# === Text parsing (pure — unit tested against captured fixtures) ===========
NAME_RE = re.compile(r"CURRENT\s*SERVER[:.\s]*(.+)", re.IGNORECASE)
ID_RE = re.compile(
    r"SERVER\s*[I1l]D[^0-9OolI]*([0-9OolI]{3}\s*-?\s*[0-9OolI]{3})", re.IGNORECASE
)
REGION_RE = re.compile(r"\(([A-Za-z][^)|#:]*)\)?")
NUM_RE = re.compile(r"#\s*(\d+)")
MENU_RE = re.compile(r"EARLY\s*ACCESS|ANY\s*BUTTON\s*TO\s*START", re.IGNORECASE)
SERVER_BROWSER_RE = re.compile(r"SERVER\s*BROWSER", re.IGNORECASE)
QUEUE_RE = re.compile(
    r"SERVER\s*QUEUE.*?POSITION\s*(\d+)\s*OF\s*(\d+)(.*)", re.IGNORECASE | re.DOTALL
)


def _fix_id(raw: str) -> str:
    """OCR confuses O/o/l/I with 0/1 in a digit field. Upstream normalises
    that mapping; a server id is never ambiguous once normalised."""
    fixed = raw.translate(str.maketrans("OolI", "0011"))
    return re.sub(r"\s*-\s*", "-", fixed)


def parse_server(text: str):
    """Returns the formatted server status, or None unless region, number AND
    id were all read. Requiring all three stops a partial OCR pass from being
    treated as a genuinely different server."""
    name_match = NAME_RE.search(text)
    if not name_match:
        return None
    full_name = name_match.group(1).strip()

    region_match = REGION_RE.search(full_name)
    num_match = NUM_RE.search(full_name)
    id_match = ID_RE.search(text)
    if not (region_match and num_match and id_match):
        return None

    region = region_match.group(1).strip()
    num = num_match.group(1).strip()
    return f"{region} #{num} \u00b7 ID {_fix_id(id_match.group(1))}"


def parse_queue(text: str):
    """Returns a queue status, or None when no queue bar is showing.

    The countdown timer is deliberately excluded — it ticks every second and
    would register as a status change on nearly every poll."""
    m = QUEUE_RE.search(text)
    if not m:
        return None
    position, total, tail = m.group(1), m.group(2), m.group(3)

    region_match = REGION_RE.search(tail)
    num_match = NUM_RE.search(tail)
    if region_match and num_match:
        return (
            f"Queued for {region_match.group(1).strip()} "
            f"#{num_match.group(1).strip()} (position {position} of {total})"
        )

    # The queue bar does not always repeat the target server beside
    # "Position N of M" (a timer or SERVER ID line can sit there instead),
    # so fall back to the id, which is unambiguous when present.
    id_match = ID_RE.search(text)
    if id_match:
        return f"Queued (ID {_fix_id(id_match.group(1))}, position {position} of {total})"
    return f"Queued (position {position} of {total})"


def not_in_game_text(display_name: str) -> str:
    return f"{display_name} is not in a game"


def determine_status(text: str, display_name: str):
    """Server status, queue status, not-in-game, or None.

    None is a real answer: it means "actively playing with the pause menu
    closed, nothing recognisable is on screen", and the caller must leave the
    existing status alone rather than guessing."""
    status = parse_server(text)
    if status:
        return status
    queue_status = parse_queue(text)
    if queue_status:
        return queue_status
    if MENU_RE.search(text) or SERVER_BROWSER_RE.search(text):
        return not_in_game_text(display_name)
    return None


def is_in_match(status: str | None, display_name: str) -> bool:
    """True only for a genuine server line — the only time a team or a score
    means anything."""
    if status is None:
        return False
    return status != not_in_game_text(display_name) and not status.startswith("Queued")


def compose_status(server_status, team, display_name: str):
    """Joins the last known server with the last known team.

    They are tracked separately because the faction icon is not visible while
    the pause menu is open (confirmed in practice) — the exact moment the
    server line is usually read — so the two are almost never readable in the
    same poll. Team is only carried while genuinely in a match."""
    if not is_in_match(server_status, display_name):
        return server_status
    return f"{team} · {server_status}" if team else server_status


def has_team_prefix(text: str) -> str | None:
    """Returns the faction name if ``text`` starts with a known team prefix."""
    for team in TEAM_EMOJIS_FOR_PREFIX:
        if text.startswith(f"{team} · "):
            return team
    return None


TEAM_EMOJIS_FOR_PREFIX = tuple(TEAM_HUE_DEGREES)


# === One full capture cycle ================================================
def capture_and_parse(cfg):
    """Screenshots, OCRs, and parses everything in one pass.

    Returns ``(raw_text, server_status, team, scores)``. Every field other
    than ``raw_text`` can be None, and that is meaningful — see
    ``determine_status``."""
    img = preprocess(grab_region(cfg.capture_region, cfg.monitor_index))
    text = pytesseract.image_to_string(img)
    if not text.strip():
        # Default layout analysis (--psm 3) can give up entirely on a busy
        # background — observed on the "PRESS ANY BUTTON TO START" splash over
        # a detailed 3D scene. --psm 6 is slower but far more reliable there,
        # so it is only paid for when the fast pass found nothing at all.
        text = pytesseract.image_to_string(img, config="--psm 6")

    status = determine_status(text, cfg.display_name)
    team = detect_team(grab_region(cfg.team_icon_region, cfg.monitor_index))
    scores = read_scores(grab_region(cfg.score_region, cfg.monitor_index))
    return text, status, team, scores
