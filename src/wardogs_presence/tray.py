"""Tray icon, and the scheduled-task registration used for autostart.

Ported from upstream's ``run_tray`` (MIT), with two packaging changes:
  * the tray menu offers the wizard and the API toggle, not just regions;
  * autostart registration targets *this* install's executable path, and uses
    its own task name — never upstream's ``WardogsDiscordStatus``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import pystray
from PIL import Image

from . import APP_TITLE
from .config import Config
from .loop import PresenceLoop

log = logging.getLogger("wardogs.tray")

TASK_NAME = "WardogsPresence"


def _icon_path() -> Path:
    """Locates the tray icon in both a source checkout and a frozen build."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        for candidate in (base / "assets" / "tray_icon.png", base / "tray_icon.png"):
            if candidate.is_file():
                return candidate
    here = Path(__file__).resolve().parent
    for candidate in (
        here.parent.parent / "assets" / "tray_icon.png",
        here / "tray_icon.png",
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("tray_icon.png not found")


def _launch_wizard() -> None:
    """The wizard must run as its own process: Tkinter wants to own its
    thread's event loop, and pystray already owns the main one."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--setup"]
    else:
        cmd = [sys.executable, "-m", "wardogs_presence.cli", "--setup"]
    subprocess.Popen(cmd)


def _launch_target() -> list[str]:
    """The command that starts this app, frozen or from source.

    Frozen builds launch their own executable; source runs go through the
    module entry point. Critically, this never assumes a Python on PATH — the
    whole point of the packaged build is that a friend needs none."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "wardogs_presence.cli"]


def _startup_dir() -> Path:
    """The per-user Startup folder, which every Windows account has and which
    requires no elevation to write to."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _startup_shortcut() -> Path:
    return _startup_dir() / f"{TASK_NAME}.cmd"


def register_autostart() -> tuple[bool, str]:
    """Registers login-time autostart by dropping a launcher in the user's
    Startup folder.

    Why not a Scheduled Task: ``schtasks /SC ONLOGON`` is **refused with
    "Access is denied"** unless the process is already elevated. Verified on a
    standard (non-elevated) account — the same situation every friend will be
    in. A task would therefore have failed silently for everyone who is not
    running as administrator.

    The Startup folder needs no elevation, is the mechanism Windows itself
    documents for per-user autostart, and is trivially removable (delete one
    file). It also makes the registration auditable — the user can see and open
    the launcher.
    """
    target = _launch_target()
    # `start ""` detaches so no console window lingers; the app is a tray
    # application and should not hold a terminal open at login.
    command = " ".join(f'"{part}"' if " " in part else part for part in target)
    script = f'@echo off\r\nstart "" {command}\r\n'

    try:
        path = _startup_shortcut()
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" so Python does not translate our explicit CRLF a second
        # time — that turned the file into "@echo off\r\r\n", which confuses
        # cmd.exe's line handling.
        with open(path, "w", encoding="ascii", newline="") as handle:
            handle.write(script)
    except OSError as exc:
        return False, f"Could not write the startup launcher: {exc}"

    if not path.is_file():
        return False, "The startup launcher was not created."
    return True, f"Autostart registered ({path.name})."


def unregister_autostart() -> tuple[bool, str]:
    """Removes the Startup-folder launcher. Also cleans up a scheduled task of
    the same name if an earlier version managed to create one, so an upgrade
    cannot leave two autostart entries fighting."""
    removed = []

    try:
        path = _startup_shortcut()
        if path.is_file():
            path.unlink()
            removed.append(path.name)
    except OSError as exc:
        return False, f"Could not remove the startup launcher: {exc}"

    # Best-effort sweep of a legacy scheduled task.
    try:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            removed.append(f"scheduled task '{TASK_NAME}'")
    except (OSError, subprocess.SubprocessError):
        pass

    if removed:
        return True, "Autostart removed: " + ", ".join(removed)
    return True, "Autostart was not registered."


def run_tray(cfg: Config) -> None:
    """Blocks until the user quits from the tray menu."""
    stop_event = threading.Event()
    refs: dict = {}

    def on_status(status):
        icon = refs.get("icon")
        if icon and status:
            # Tray tooltips have an OS-level length limit.
            icon.title = f"{APP_TITLE}: {status}"[:127]

    def on_quit(icon, _item):
        log.info("Quit requested from the tray.")
        stop_event.set()
        icon.stop()

    def on_settings(_icon, _item):
        _launch_wizard()

    def on_toggle_api(icon, _item):
        cfg.enrich_from_api = not cfg.enrich_from_api
        cfg.save()
        icon.title = f"{APP_TITLE}: API enrichment {'ON' if cfg.enrich_from_api else 'OFF'}"
        log.info("API enrichment set to %s (restart to apply).", cfg.enrich_from_api)

    def setup(icon):
        # pystray only pushes title updates once visible is True, and a custom
        # setup callback is responsible for setting that itself.
        icon.visible = True
        thread = threading.Thread(
            target=PresenceLoop(cfg, dry_run=False, on_status=on_status).run,
            args=(stop_event,),
            daemon=True,
        )
        thread.start()
        refs["thread"] = thread

    image = Image.open(_icon_path())
    menu = pystray.Menu(
        pystray.MenuItem("Set up...", on_settings, default=True),
        pystray.MenuItem(
            lambda _item: f"API enrichment: {'ON' if cfg.enrich_from_api else 'OFF'}",
            on_toggle_api,
        ),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("wardogs_presence", image, f"{APP_TITLE}: starting...", menu)
    refs["icon"] = icon

    icon.run(setup=setup)

    thread = refs.get("thread")
    if thread:
        thread.join(timeout=cfg.poll_interval_seconds + 5)
