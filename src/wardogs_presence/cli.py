"""WARDOGS Presence — entry point.

Modes:
    (no args)      run with a tray icon (the normal, packaged way to run)
    --setup        open the setup wizard
    --once         capture + OCR + parse once and print (no Discord calls)
    --dry-run      run continuously, logging instead of publishing
    --no-tray      run continuously in the console, publishing for real
    --validate     check the saved config and report problems, then exit

Derived from msmcpeake/wardogs-discord-status (MIT). See NOTICE.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import io
import json
import logging
import sys
import threading
import time

from . import APP_TITLE, __version__
from .config import LOG_PATH, Config


def setup_logging(to_file: bool) -> None:
    handlers: list[logging.Handler] = []
    if to_file:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(LOG_PATH, encoding="utf-8"))
    else:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def run_once(cfg: Config) -> int:
    """One capture + parse, printed. Deliberately does not require a webhook or
    a running game — this is the tool for checking that the capture regions
    actually line up with the game's UI."""
    from . import screen

    path = screen.configure_tesseract(cfg.tesseract_cmd)
    if not path:
        print("Tesseract was not found. Run --setup, or install it from")
        print("https://github.com/UB-Mannheim/tesseract/wiki")
        return 2

    text, status, team, scores = screen.capture_and_parse(cfg)
    print("--- raw OCR text ---")
    print(text or "(nothing read)")
    print("--- parsed server status ---")
    print(status if status else "(no match — the capture region probably misses the panel)")
    print("--- detected team ---")
    print(team if team else "(none detected)")
    print("--- scores (blue - red - green) ---")
    print(" - ".join(str(x) for x in scores) if scores else "(scoreboard not readable)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wardogs-presence", description=f"{APP_TITLE} {__version__}")
    parser.add_argument("--setup", action="store_true", help="open the setup wizard")
    parser.add_argument("--once", action="store_true", help="capture + OCR + parse once, then exit")
    parser.add_argument("--dry-run", action="store_true", help="run continuously without calling Discord")
    parser.add_argument("--no-tray", action="store_true", help="run continuously in the console")
    parser.add_argument("--validate", action="store_true", help="check configuration and exit")
    parser.add_argument("--watch", action="store_true", help="watch in the background without a tray")
    parser.add_argument("--bridge", choices=("config", "monitors", "capture", "ocr", "save-config", "webhook"),
                        help="JSON control channel for the Electron desktop shell")
    args = parser.parse_args(argv)

    if args.bridge:
        return _run_bridge(args.bridge)

    cfg = Config.load()

    # First run, or explicitly asked: the wizard owns configuration.
    if args.setup or not cfg.setup_complete:
        setup_logging(to_file=False)
        from .wizard import run_wizard

        cfg = run_wizard(cfg)
        if cfg is None or not cfg.setup_complete:
            print("Setup was not completed.")
            return 1
        if not args.setup:
            print("Setup complete. Run again with no arguments to start watching.")

    if args.validate:
        setup_logging(to_file=False)
        problems = cfg.missing_required()
        if problems:
            for problem in problems:
                print(f"PROBLEM: {problem}")
            return 1
        print("Configuration looks valid.")
        return 0

    if args.once:
        setup_logging(to_file=False)
        return run_once(cfg)

    if args.dry_run or args.no_tray:
        setup_logging(to_file=False)
        return _run_console(cfg, dry_run=args.dry_run)

    if args.watch:
        setup_logging(to_file=True)
        return _run_console(cfg, dry_run=False)

    return _run_tray(cfg)


def _run_bridge(action: str) -> int:
    """One JSON request on stdin, one JSON response on stdout.

    The shell never passes webhook URLs on command lines or opens a localhost
    HTTP port. Screenshots remain in this local process pipe.
    """
    from . import screen
    from .discord_webhook import parse_webhook_url, validate_webhook_url

    try:
        request = json.load(sys.stdin) if action not in ("config", "monitors") else {}
        if action == "config":
            result = {"ok": True, "config": asdict(Config.load())}
        elif action == "monitors":
            result = {"ok": True, "monitors": screen.list_monitors()}
        elif action == "webhook":
            ok, message = validate_webhook_url(str(request.get("url", "")))
            result = {"ok": ok, "message": message}
        elif action == "capture":
            delay = max(0, min(5, int(request.get("delay", 0))))
            if delay:
                time.sleep(delay)
            image = screen.grab_region("0,0,1,1", int(request["monitor_index"]))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=78)
            result = {"ok": True, "image": "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")}
        elif action == "ocr":
            raw = request["config"]
            cfg = Config(**{k: v for k, v in raw.items() if k in Config.__dataclass_fields__})
            if not screen.configure_tesseract(cfg.tesseract_cmd):
                raise RuntimeError("Bundled Tesseract was not found.")
            text, status, team, scores = screen.capture_and_parse(cfg)
            result = {"ok": True, "text": text, "status": status, "team": team, "scores": scores}
        elif action == "save-config":
            raw = request["config"]
            cfg = Config.load()
            for key in ("display_name", "avatar_url", "webhook_profiles", "monitor_index",
                        "capture_region", "team_icon_region", "score_region", "enrich_from_api"):
                if key in raw:
                    setattr(cfg, key, raw[key])
            if not isinstance(cfg.webhook_profiles, list):
                raise ValueError("Webhook profiles must be a list.")
            ids = set()
            for item in cfg.webhook_profiles:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise ValueError("Each webhook needs a unique ID.")
                if item["id"] in ids:
                    raise ValueError("Webhook IDs must be unique.")
                ids.add(item["id"])
                url = str(item.get("url", "")).strip()
                if url and not parse_webhook_url(url):
                    raise ValueError("A saved Discord webhook URL has an invalid format.")
            cfg.webhook_url = ""  # migrated profiles are the source of truth
            problems = cfg.missing_required()
            if problems:
                raise ValueError(" ".join(problems))
            cfg.setup_complete = True
            cfg.save()
            result = {"ok": True}
        else:
            raise ValueError("Unsupported bridge action")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def _run_console(cfg: Config, dry_run: bool) -> int:
    from . import screen
    from .loop import PresenceLoop

    if not screen.configure_tesseract(cfg.tesseract_cmd):
        print("Tesseract was not found. Install it, then re-run with --setup.")
        return 2

    if not dry_run:
        problems = cfg.missing_required()
        if problems:
            for problem in problems:
                print(f"PROBLEM: {problem}")
            return 1

    stop_event = threading.Event()
    loop = PresenceLoop(cfg, dry_run=dry_run)
    try:
        loop.run(stop_event)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def _run_tray(cfg: Config) -> int:
    """Runs with a system-tray icon, logging to a file (there is no console
    when launched at login)."""
    setup_logging(to_file=True)
    from .tray import run_tray

    problems = cfg.missing_required()
    if problems:
        for problem in problems:
            logging.error("PROBLEM: %s", problem)
        return 1
    run_tray(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
