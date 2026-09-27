"""Discord transport over an incoming webhook.

This module is the *deliberate divergence* from upstream. Upstream
(``msmcpeake/wardogs-discord-status``, MIT) posts with a bot token against
``/channels/{id}/messages``; we post against a webhook instead.

Why the swap:
  * No Discord application, no OAuth2 invite, no per-channel permission
    overwrite. A friend pastes one URL.
  * Blast radius shrinks: a leaked webhook URL can only post to *one* channel.
    A leaked bot token can act across the whole guild.
  * Per-execution ``username``/``avatar_url`` give each install its own
    identity for free — which is the whole point of the multi-user design.

Contract notes verified against Discord's Webhook Resource documentation:
  * Execute with ``?wait=true`` to get the created message object back
    (without it, Discord returns 204 with no body and we could not learn the
    message id we need in order to edit in place).
  * Edit via ``PATCH /webhooks/{id}/{token}/messages/{message_id}``.
  * A webhook may only edit messages *it* created. That is exactly the
    isolation we rely on for multiple installs sharing one webhook.
  * ``username``/``avatar_url`` are accepted on execute. They are NOT part of
    the edit-message contract, so identity is fixed at creation time. Changing
    the display name therefore requires re-creating the message; see
    ``needs_recreate`` below.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

log = logging.getLogger("wardogs.webhook")

# Discord's own limit for a webhook username.
MAX_USERNAME_LENGTH = 80
REQUEST_TIMEOUT = 10

WEBHOOK_URL_RE = re.compile(
    r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/(?:v\d+/)?webhooks/"
    r"(?P<id>\d+)/(?P<token>[A-Za-z0-9_\-\.]+)/?$"
)

EMBED_COLOR_IN_GAME = 0x57F287  # Discord green
EMBED_COLOR_NOT_IN_GAME = 0x99AAB5  # Discord grey

# Faction emoji, ported from upstream. These are custom emoji that live in a
# specific guild; they only render for users who are in it. Kept as-is for
# parity with the original, with a plain-colour fallback for everyone else.
TEAM_EMOJIS = {
    "Lonestar": "<:blue:1548650924782653561>",
    "Valkyra": "<:red:1549381696560963677>",
    "Manticore": "<:green:1549381698234482870>",
}
STATUS_EMOJI = "<:blue:1548650924782653561>"

# Unicode equivalents, used when custom emoji cannot render.
TEAM_EMOJIS_UNICODE = {"Lonestar": "🔵", "Valkyra": "🔴", "Manticore": "🟢"}
STATUS_EMOJI_UNICODE = "🔵"
SCORE_TEAMS = ("Lonestar", "Valkyra", "Manticore")


@dataclass
class SendResult:
    """Outcome of one transport call. Never an exception for a normal failure
    path — the run loop needs to keep going through rate limits and outages."""

    ok: bool
    message_id: str | None = None
    retry_after: float | None = None
    # Set when the stored message id is no longer usable and the caller should
    # re-create rather than retry the edit.
    recreate: bool = False
    error: str = ""


def sanitize_username(name: str) -> str:
    """Makes a user-supplied name safe to send as a webhook username.

    Two concerns, both real:
      1. Discord rejects usernames containing ``@``/``#``/``:``/`` ``` ``.
      2. A name is user input and lands in a channel other people read, so
         mention-like sequences are neutralized rather than trusted.
    """
    cleaned = (name or "").strip()
    cleaned = cleaned.replace("@", "@\u200b")  # zero-width space breaks the mention
    cleaned = re.sub(r'[`:#]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:MAX_USERNAME_LENGTH].strip() or "Wardogs Player"


def parse_webhook_url(url: str) -> tuple[str, str] | None:
    """Returns ``(webhook_id, token)`` or ``None`` if the URL is not a valid
    Discord incoming-webhook URL."""
    if not url:
        return None
    match = WEBHOOK_URL_RE.match(url.strip())
    if not match:
        return None
    return match.group("id"), match.group("token")


def validate_webhook_url(url: str) -> tuple[bool, str]:
    """Checks the URL shape and then *actually calls Discord* to confirm it
    works. A regex alone cannot tell a live webhook from a deleted one, and
    the wizard should fail here rather than silently doing nothing later."""
    parsed = parse_webhook_url(url)
    if not parsed:
        return False, (
            "That does not look like a Discord webhook URL.\n"
            "It should start with https://discord.com/api/webhooks/ and be "
            "about three lines long."
        )
    webhook_id, token = parsed
    try:
        resp = requests.get(
            f"https://discord.com/api/v10/webhooks/{webhook_id}/{token}",
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, f"Could not reach Discord: {exc}"

    if resp.status_code == 200:
        data = resp.json()
        channel = data.get("channel_id", "unknown")
        name = data.get("name") or "(unnamed)"
        return True, f"Webhook works — posting to channel {channel} as “{name}”."
    if resp.status_code in (401, 403):
        return False, "Discord rejected that webhook token. Re-copy the URL from the channel settings."
    if resp.status_code == 404:
        return False, "That webhook no longer exists — it may have been deleted. Create a new one."
    return False, f"Discord returned {resp.status_code}: {resp.text[:200]}"


def build_body(
    description: str,
    embed_title: str,
    in_game: bool,
    timestamp_iso: str,
    username: str,
    avatar_url: str = "",
) -> dict:
    """Builds the message payload. ``username``/``avatar_url`` are only
    meaningful on execute (see module docstring)."""
    body: dict = {
        # Explicitly blank so editing a message created by an older version
        # cannot leave stale plain text above the embed.
        "content": "",
        "embeds": [
            {
                "title": embed_title,
                "description": description,
                "color": EMBED_COLOR_IN_GAME if in_game else EMBED_COLOR_NOT_IN_GAME,
                "timestamp": timestamp_iso,
                "footer": {"text": "Last updated"},
            }
        ],
    }
    if username:
        body["username"] = sanitize_username(username)
    if avatar_url:
        body["avatar_url"] = avatar_url
    return body


class WebhookTransport:
    """Stateless except for the message id it is given. Keeps the caller's
    loop simple: hand it a payload and the message id you last knew about."""

    def __init__(self, webhook_url: str):
        parsed = parse_webhook_url(webhook_url)
        if not parsed:
            raise ValueError("Invalid Discord webhook URL")
        self.webhook_id, self.token = parsed
        self.base = f"https://discord.com/api/v10/webhooks/{self.webhook_id}/{self.token}"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "WardogsPresence (1.0)"})

    # ------------------------------------------------------------------
    def post(self, body: dict) -> SendResult:
        """Creates the message. ``?wait=true`` is mandatory here — without it
        Discord answers 204 with an empty body and we would never learn the id
        needed for every subsequent edit."""
        try:
            resp = self.session.post(
                self.base, params={"wait": "true"}, json=body, timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            return SendResult(ok=False, error=f"network error: {exc}")
        return self._interpret(resp, want_id=True)

    def patch(self, message_id: str, body: dict) -> SendResult:
        """Edits an existing webhook message in place."""
        try:
            resp = self.session.patch(
                f"{self.base}/messages/{message_id}", json=body, timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            return SendResult(ok=False, message_id=message_id, error=f"network error: {exc}")
        return self._interpret(resp, want_id=True, message_id=message_id)

    def delete(self, message_id: str) -> bool:
        """Removes a message this webhook posted. Used only when the display
        name changes and the message must be re-created to carry the new
        identity."""
        try:
            resp = self.session.delete(
                f"{self.base}/messages/{message_id}", timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException:
            return False
        return resp.status_code in (200, 204, 404)

    # ------------------------------------------------------------------
    def _interpret(self, resp: requests.Response, want_id: bool, message_id: str | None = None) -> SendResult:
        if resp.status_code == 429:
            retry_after = 5.0
            try:
                retry_after = float(resp.json().get("retry_after", 5.0))
            except (ValueError, requests.exceptions.JSONDecodeError):
                pass
            log.warning("Rate limited by Discord; holding off %.1fs", retry_after)
            return SendResult(ok=False, message_id=message_id, retry_after=retry_after)

        if resp.ok:
            new_id = message_id
            if want_id:
                try:
                    new_id = str(resp.json()["id"])
                except (ValueError, KeyError, requests.exceptions.JSONDecodeError):
                    return SendResult(ok=False, message_id=message_id, error="no message id in response")
            return SendResult(ok=True, message_id=new_id)

        # A 404 on an edit means the message is gone (deleted by hand, or the
        # webhook was recreated). Recover by posting a fresh one rather than
        # looping forever on an id that can never work again.
        if resp.status_code == 404 and message_id:
            log.warning("Stored message id is gone; a new message will be created.")
            return SendResult(ok=False, message_id=None, recreate=True, error="message not found")

        return SendResult(
            ok=False,
            message_id=message_id,
            error=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )
