"""The watch loop: poll the screen, decide, publish.

The debounce rules, the heartbeat, the score throttle and the state-machine
transitions are ported from ``msmcpeake/wardogs-discord-status`` (MIT) — they
encode real observations about how flaky OCR behaves, so they are preserved
rather than simplified.

What changed for this project: the transport is a webhook, and identity is
fixed per message, so a display-name change re-creates the message (see
``_apply``).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import screen
from .config import STATE_PATH
from .discord_webhook import (
    TEAM_EMOJIS,
    TEAM_EMOJIS_UNICODE,
    STATUS_EMOJI,
    STATUS_EMOJI_UNICODE,
    SCORE_TEAMS,
    WebhookTransport,
    build_body,
)
from .enrich import DirectoryClient, ServerInfo, extract_join_code

log = logging.getLogger("wardogs.loop")


# === State persistence =====================================================
def load_state() -> dict:
    """Returns ``{status, message_id, updated_at, identity}``.

    Tolerant by design: a corrupt state file must degrade to "post a fresh
    message", never to a crash."""
    default = {"status": None, "message_id": None, "updated_at": None, "identity": None, "profiles": {}}
    if not STATE_PATH.exists():
        return default
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    if not isinstance(data, dict):
        return default
    return {**default, **{k: data.get(k) for k in default}}


def save_state(status, message_id, identity) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "message_id": message_id,
        "identity": identity,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def save_profile_state(profiles: dict) -> None:
    """Persist independent message IDs without putting webhook tokens in state."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"profiles": profiles}, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _identity_token(cfg) -> str:
    """The identity a message was created with. Discord fixes the webhook
    username at creation, so a change here invalidates the existing message."""
    return f"{cfg.display_name}|{cfg.avatar_url}"


# === Presentation ==========================================================
def round_down_to_5_minutes(dt: datetime) -> datetime:
    """Cosmetic: floors the displayed timestamp to the 5-minute mark at or
    before it, because round numbers read cleaner. Always rounds *down* —
    rounding up could show a timestamp from the future. Heartbeat timing uses
    the real, unrounded time."""
    discard = timedelta(minutes=dt.minute % 5, seconds=dt.second, microseconds=dt.microsecond)
    return dt - discard


def icon_for(text: str, use_custom_emoji: bool) -> str:
    """Picks the faction icon from the status text's team prefix."""
    prefix = f"{text.split(' · ', 1)[0]} · " if " · " in text else ""
    team = prefix[:-3] if prefix else None
    if team in TEAM_EMOJIS:
        return TEAM_EMOJIS[team] if use_custom_emoji else TEAM_EMOJIS_UNICODE[team]
    return STATUS_EMOJI if use_custom_emoji else STATUS_EMOJI_UNICODE


def strip_team_prefix(text: str) -> str:
    """The team drives *which icon* is shown but is not repeated in the text —
    the coloured icon already says which team it is."""
    for team in TEAM_EMOJIS:
        prefix = f"{team} · "
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def format_scores(scores, use_custom_emoji: bool) -> str:
    """Mirrors the game's own blue – red – green order."""
    table = TEAM_EMOJIS if use_custom_emoji else TEAM_EMOJIS_UNICODE
    return " – ".join(f"{table[team]} {score}" for team, score in zip(SCORE_TEAMS, scores))


def build_description(
    text: str,
    display_name: str,
    scores=None,
    enrichment: str | None = None,
    use_custom_emoji: bool = False,
) -> str:
    """Assembles the embed's description.

    Every branch produces real content — there is no path where the channel
    shows a blank or ``None``. ``enrichment`` is a pre-formatted optional line
    from the API adapter; when absent the message is simply shorter.
    """
    not_in_game = screen.not_in_game_text(display_name)
    if text == not_in_game:
        description = text
    else:
        icon = icon_for(text, use_custom_emoji)
        description = f"{icon} │ {strip_team_prefix(text)}"

    if enrichment:
        description += f"\n{enrichment}"

    if scores:
        description += f"\n\nScore: {format_scores(scores, use_custom_emoji)}"
    return description


# === The loop ==============================================================
class PresenceLoop:
    """Owns the polling state machine. One instance per running app.

    ``on_status`` is an optional callback (used by the tray to update its
    tooltip) invoked whenever the displayed status changes.
    """

    def __init__(self, cfg, dry_run: bool = False, on_status=None):
        self.cfg = cfg
        self.dry_run = dry_run
        self.on_status = on_status
        self.transport = None if dry_run or cfg.webhook_profiles else WebhookTransport(cfg.webhook_url)
        self.profile_transports = {}
        self.profile_state = {}
        if not dry_run and cfg.webhook_profiles:
            self.transport = None
            for target in cfg.active_webhooks():
                self.profile_transports[target["id"]] = WebhookTransport(target["url"])
                self.profile_state[target["id"]] = {
                    "message_id": None, "status": None, "scores": None,
                    "identity": _identity_token(cfg), "cooldown_until": 0.0,
                    "last_sent_at": 0.0,
                }

        # Runtime state, mirroring upstream's semantics.
        self.last_status = None
        self.message_id = None
        self.last_applied_at = time.time()
        self.last_applied_scores = None
        self.cooldown_until = 0.0

        self.pending_status = None
        self.pending_count = 0
        self.last_known_server_status = None
        self.last_known_team = None
        self.last_known_scores = None
        self.pending_scores = None
        self.pending_scores_count = 0
        self.game_was_running = False

        # Optional directory enrichment. Constructed only when enabled, so a
        # user who never turns it on pays nothing — no client, no cache read,
        # no network session.
        self.directory: DirectoryClient | None = None
        if not dry_run and cfg.enrich_from_api:
            self.directory = DirectoryClient(cfg.api_base_url)

    # ------------------------------------------------------------------
    def restore(self) -> None:
        state = load_state()
        if self.profile_transports:
            saved_profiles = state.get("profiles") or {}
            for profile_id, current in self.profile_state.items():
                saved = saved_profiles.get(profile_id, {})
                if saved.get("identity") == _identity_token(self.cfg):
                    current["message_id"] = saved.get("message_id")
                    current["status"] = saved.get("status")
                    current["scores"] = saved.get("scores")
            return
        self.last_status = state["status"]
        self.message_id = state["message_id"]
        updated_at = state["updated_at"]
        if updated_at:
            try:
                self.last_applied_at = datetime.fromisoformat(updated_at).timestamp()
            except ValueError:
                self.last_applied_at = time.time()
        # If the stored message was created under a different identity, the
        # webhook username on it cannot be changed by an edit — drop the id so
        # the next apply re-creates the message with the new identity.
        if self.message_id and state.get("identity") not in (None, _identity_token(self.cfg)):
            log.info("Display identity changed; the message will be re-created.")
            self.message_id = None

    def _enrichment_line(self, status: str) -> str | None:
        """Returns a formatted directory line for ``status``, or None.

        Every failure path returns None rather than raising: the app must
        publish the OCR-derived status whether or not the optional directory is
        reachable. A provider outage must never blank the message or turn it
        into an error."""
        if self.directory is None or not screen.is_in_match(status, self.cfg.display_name):
            return None
        code = extract_join_code(status)
        if not code:
            return None
        try:
            self.directory.refresh()
            info: ServerInfo | None = self.directory.lookup(code)
        except Exception:  # noqa: BLE001 — enrichment is best-effort by design
            log.exception("Directory lookup failed; publishing OCR-only output")
            return None
        return info.format_line() if info else None

    # ------------------------------------------------------------------
    def _apply(self, status, scores=None) -> None:
        """Publishes one status (create on first use, edit thereafter)."""
        if self.profile_transports:
            self._apply_profiles(status, scores)
            return
        if time.time() < self.cooldown_until:
            return  # still backing off from a rate limit

        use_custom_emoji = False
        description = build_description(
            status,
            self.cfg.display_name,
            scores,
            self._enrichment_line(status),
            use_custom_emoji,
        )

        if self.dry_run:
            log.info("[dry-run] would publish: %s (scores %s)", description, scores)
            self.last_status = status
            self.last_applied_at = time.time()
            self.last_applied_scores = scores
            if self.on_status:
                self.on_status(status)
            return

        body = build_body(
            description=description,
            embed_title=f"Current Wardogs Server — {self.cfg.display_name}",
            in_game=screen.is_in_match(status, self.cfg.display_name),
            timestamp_iso=round_down_to_5_minutes(datetime.now(timezone.utc)).isoformat(),
            username=self.cfg.display_name,
            avatar_url=self.cfg.avatar_url,
        )

        if self.message_id:
            result = self.transport.patch(self.message_id, body)
            if result.recreate:
                self.message_id = None
                result = self.transport.post(body)
        else:
            result = self.transport.post(body)

        if result.ok:
            self.message_id = result.message_id
            self.last_status = status
            self.last_applied_at = time.time()
            self.last_applied_scores = scores
            save_state(status, self.message_id, _identity_token(self.cfg))
            log.info("Published: %s", status)
            if self.on_status:
                self.on_status(status)
        elif result.retry_after:
            self.cooldown_until = time.time() + result.retry_after
            log.warning("Rate limited; retrying in %.1fs", result.retry_after)
        else:
            log.error("Publish failed: %s", result.error)

    def _apply_profiles(self, status, scores=None) -> None:
        """Fan one OCR result out to enabled destinations with per-hook backoff.

        The screen is read once. A rate limit or outage on one destination
        never prevents another from receiving the update. Successful targets
        are skipped on retries so a failing hook cannot spam healthy ones.
        """
        now = time.time()
        description = build_description(
            status, self.cfg.display_name, scores, self._enrichment_line(status), False
        )
        body = build_body(
            description=description,
            embed_title=f"Current Wardogs Server — {self.cfg.display_name}",
            in_game=screen.is_in_match(status, self.cfg.display_name),
            timestamp_iso=round_down_to_5_minutes(datetime.now(timezone.utc)).isoformat(),
            username=self.cfg.display_name,
            avatar_url=self.cfg.avatar_url,
        )
        any_success = False
        for profile_id, transport in self.profile_transports.items():
            target = self.profile_state[profile_id]
            if now < target["cooldown_until"]:
                continue
            if now - target["last_sent_at"] < 4.0:
                continue
            if target["status"] == status and target["scores"] == scores:
                if now - target["last_sent_at"] < self.cfg.heartbeat_interval_seconds:
                    continue
            target["last_sent_at"] = now
            if target["message_id"]:
                result = transport.patch(target["message_id"], body)
                if result.recreate:
                    target["message_id"] = None
                    result = transport.post(body)
            else:
                result = transport.post(body)
            if result.ok:
                target.update(message_id=result.message_id, status=status, scores=scores)
                any_success = True
                log.info("Published to profile %s: %s", profile_id, status)
            elif result.retry_after:
                target["cooldown_until"] = time.time() + max(1.0, result.retry_after)
                log.warning("Profile %s rate limited; waiting %.1fs", profile_id, result.retry_after)
            else:
                target["cooldown_until"] = time.time() + 15.0
                log.error("Publish to profile %s failed: %s", profile_id, result.error)
        if any_success:
            self.last_status = status
            self.last_applied_at = time.time()
            self.last_applied_scores = scores
            save_profile_state({
                key: {k: target[k] for k in ("message_id", "status", "scores", "identity")}
                for key, target in self.profile_state.items()
            })
            if self.on_status:
                self.on_status(status)

    # ------------------------------------------------------------------
    def tick(self) -> None:
        """One poll. Called in a loop by ``run``."""
        running = screen.is_game_running(self.cfg.game_process_substring)

        if running:
            _, server_status, team, scores = screen.capture_and_parse(self.cfg)

            if server_status is not None:
                if server_status != self.last_known_server_status:
                    # New server (or left the match): the previous match's
                    # scores are stale.
                    self.last_known_scores = None
                    self.pending_scores = None
                    self.pending_scores_count = 0
                self.last_known_server_status = server_status
                if server_status == screen.not_in_game_text(self.cfg.display_name):
                    self.last_known_team = None  # never carry a stale team forward

            if team is not None:
                self.last_known_team = team

            if scores is not None:
                if scores == self.pending_scores:
                    self.pending_scores_count += 1
                else:
                    self.pending_scores = scores
                    self.pending_scores_count = 1
                if self.pending_scores_count >= 2:
                    self.last_known_scores = scores

            candidate = screen.compose_status(
                self.last_known_server_status, self.last_known_team, self.cfg.display_name
            )

            # Debounce: a reading must appear twice in a row before it is
            # trusted, so one flaky OCR pass cannot trigger an update.
            if candidate == self.pending_status:
                self.pending_count += 1
            else:
                self.pending_status = candidate
                self.pending_count = 1

            profiles_behind = self.profile_transports and any(
                target["status"] != candidate for target in self.profile_state.values()
            )
            if candidate and self.pending_count >= 2 and (candidate != self.last_status or profiles_behind):
                scores_for_apply = self.last_known_scores if screen.is_in_match(
                    candidate, self.cfg.display_name
                ) else None
                self._apply(candidate, scores_for_apply)
            elif (
                screen.is_in_match(self.last_status, self.cfg.display_name)
                and self.last_known_scores != self.last_applied_scores
                and time.time() - self.last_applied_at >= self.cfg.score_update_interval_seconds
            ):
                # Score-only change: throttled, because scores tick up
                # constantly. Server and team changes are never delayed by this.
                self._apply(self.last_status, self.last_known_scores)
        else:
            self.pending_status = None
            self.pending_count = 0
            self.last_known_server_status = None
            self.last_known_team = None
            self.last_known_scores = None
            self.pending_scores = None
            self.pending_scores_count = 0
            if self.game_was_running and self.last_status != screen.not_in_game_text(self.cfg.display_name):
                # The game closing is a certain signal, not a flaky read, so
                # it is published immediately rather than debounced.
                self._apply(screen.not_in_game_text(self.cfg.display_name))

        self.game_was_running = running

        # Heartbeat: nothing changed, but refresh the timestamp so a long
        # stretch on one server does not look stale.
        if (
            self.last_status is not None
            and time.time() - self.last_applied_at >= self.cfg.heartbeat_interval_seconds
        ):
            scores_for_apply = self.last_known_scores if screen.is_in_match(
                self.last_status, self.cfg.display_name
            ) else None
            self._apply(self.last_status, scores_for_apply)

    # ------------------------------------------------------------------
    def run(self, stop_event: threading.Event) -> None:
        self.restore()
        log.info(
            "Watching for process matching '%s'. Last known status: %s",
            self.cfg.game_process_substring,
            self.last_status,
        )
        if self.on_status:
            self.on_status(self.last_status)

        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Unexpected error in poll; continuing")
            if stop_event.wait(self.cfg.poll_interval_seconds):
                break
        log.info("Stopped.")
