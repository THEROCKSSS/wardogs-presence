"""WARDOGS Presence — configuration.

Stores settings in ``%APPDATA%/WardogsPresence/config.json`` rather than a
``.env`` beside the script. Rationale: the app ships as a one-folder zip that
users may replace wholesale on upgrade, and config must survive that. It also
means a non-technical user never edits a file by hand — the setup wizard owns
this file.

Derived from msmcpeake/wardogs-discord-status (MIT). The config *shape* is
ours; the OCR tuning constants are ported from upstream unchanged, because
those values were measured against the real game.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "WardogsPresence"

# Where user config and runtime state live. %APPDATA% survives a folder
# replacement, which is the documented upgrade path.
def _data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / APP_NAME


DATA_DIR = _data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "state.json"
LOG_PATH = DATA_DIR / "wardogs_presence.log"


# --- OCR tuning defaults ----------------------------------------------------
# Ported verbatim from upstream (wardogs_status_bot.py, MIT). The first two
# were measured on the original author's 4K capture and *will* need retuning
# on other resolutions — the wizard's drag-box flow is the intended fix, not
# hand-editing. Values here are the upstream starting point.
DEFAULT_CAPTURE_REGION = "0,0.65,1.0,1.0"
DEFAULT_TEAM_ICON_REGION = "0.960,0.925,0.990,0.965"
DEFAULT_SCORE_REGION = "0.0169,0.9139,0.1497,0.9514"
DEFAULT_MONITOR_INDEX = 1

# Game-process gate: OCR only runs while this substring matches a running
# process name. Default matches WardogsClient-Win64-Shipping.exe.
DEFAULT_GAME_PROCESS_SUBSTRING = "wardogs"

DEFAULT_POLL_INTERVAL_SECONDS = 4.0
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 300.0
DEFAULT_SCORE_UPDATE_INTERVAL_SECONDS = 30.0


@dataclass
class Config:
    """Everything the app needs to run. One flat object, serialized as JSON."""

    # --- Identity (what other people see in the channel) ---
    display_name: str = ""
    avatar_url: str = ""

    # --- Transport ---
    webhook_url: str = ""  # retained for pre-1.1 settings migration
    webhook_profiles: list[dict] = field(default_factory=list)

    # --- OCR tuning ---
    capture_region: str = DEFAULT_CAPTURE_REGION
    team_icon_region: str = DEFAULT_TEAM_ICON_REGION
    score_region: str = DEFAULT_SCORE_REGION
    monitor_index: int = DEFAULT_MONITOR_INDEX
    game_process_substring: str = DEFAULT_GAME_PROCESS_SUBSTRING

    # --- Timing ---
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    score_update_interval_seconds: float = DEFAULT_SCORE_UPDATE_INTERVAL_SECONDS

    # --- Optional enrichment (default OFF: works with zero external deps) ---
    enrich_from_api: bool = False
    api_base_url: str = "https://api.wardogservers.com"

    # --- Paths the user may override for a custom Tesseract install ---
    tesseract_cmd: str = ""

    # --- Set once the wizard has completed, so first-run detection is exact ---
    setup_complete: bool = False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        """Reads config.json, falling back to defaults for any missing key.

        Deliberately tolerant: a config written by an older version, or with
        one corrupt field, must not brick the app for a non-technical user.
        """
        if not CONFIG_PATH.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        cfg = cls(**{k: v for k, v in raw.items() if k in known})
        if not cfg.webhook_profiles and cfg.webhook_url:
            cfg.webhook_profiles = [{
                "id": "original", "label": "Original channel",
                "url": cfg.webhook_url, "enabled": True,
            }]
        return cfg

    def active_webhooks(self) -> list[dict]:
        """Enabled, distinct destinations. One message per actual webhook URL."""
        from .discord_webhook import parse_webhook_url

        profiles = self.webhook_profiles or ([{
            "id": "original", "label": "Original channel",
            "url": self.webhook_url, "enabled": True,
        }] if self.webhook_url else [])
        active = []
        seen = set()
        for item in profiles:
            if not isinstance(item, dict) or not item.get("enabled"):
                continue
            url = str(item.get("url", "")).strip()
            parsed = parse_webhook_url(url)
            if not parsed or parsed in seen:
                continue
            seen.add(parsed)
            active.append({"id": str(item.get("id", "")), "url": url})
        return active

    def save(self) -> None:
        """Writes config atomically.

        Write-to-temp-then-replace, so an interrupted write (power loss,
        kill) can never leave a half-written config that fails to parse.
        """
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_PATH)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Validation used by the wizard and the startup path
    # ------------------------------------------------------------------
    def missing_required(self) -> list[str]:
        """Human-readable list of what still blocks a real run."""
        problems = []
        if not self.active_webhooks():
            problems.append("No valid, enabled Discord webhook set.")
        if len(self.active_webhooks()) > 5:
            problems.append("Enable no more than five Discord webhooks at once.")
        if not self.display_name.strip():
            problems.append("No display name set.")
        return problems
