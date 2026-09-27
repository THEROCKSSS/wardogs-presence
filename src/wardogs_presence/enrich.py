"""Optional API enrichment — decorates the OCR'd join code with directory data.

OFF by default and stays off unless the user turns it on, because the app must
be fully useful with the game alone. When enabled and reachable it adds server
name, region, population, map and mode; when disabled, unreachable, stale, or
the code is simply not in the directory, it returns ``None`` and the message
is published exactly as the OCR path produced it.

Source: https://api.wardogservers.com (``GET /v1/snapshot``).

The field contract here was read from the provider's live OpenAPI specification
and confirmed against a real snapshot — *not* assumed. That mattered: the first
version of this adapter matched on the wrong field (``id``, which is always a
UUID) and would have embedded raw ``map``/``mode`` dicts into the message.
Verified facts about the real payload:

  * Top level is ``{data: [...], meta: {...}}``.
  * ``meta`` carries ``fetchedAt``, ``snapshotId``, ``refreshSeconds``,
    ``stale``, ``requestedRegions``, ``observedRegions``.
  * Each record: ``id`` (live instance UUID — *always* a UUID, changes when the
    server restarts), ``serverId`` (the **in-game join code when available**: a
    decimal string with leading zeros preserved), ``name``, ``nativeName``,
    ``type`` (official|community), ``region``, ``players``, ``maxPlayers``,
    ``reservedPlayers``, ``passwordProtected``, ``serverNumber``, ``map``
    (object: variant/base/path), ``mode`` (object: experience/gameMode/...),
    ``rulesets``.
  * For official servers ``name`` is literally ``"Official #N"``; for community
    servers it is the decoded server name.
  * **``serverId`` is a UUID for community servers whose join code the provider
    has not resolved**, and a 6-digit decimal for those it has. An OCR read can
    never equal a UUID, so those records are skipped rather than indexed.
  * The game displays the id with a separator (``469-618``) while the provider
    stores bare digits (``469618``); both sides are normalized before compare.

Contract requirements respected here:
  * public reads need no API key, but **browser-origin requests are restricted
    to the provider's own domain** — so this is called from the app process,
    never from a page.
  * honour ``meta.refreshSeconds`` — never poll faster than the provider says.
  * reuse ``ETag`` via ``If-None-Match`` and treat ``304`` as "unchanged".
  * surface ``meta.stale`` rather than presenting stale data as live.
  * credit the provider publicly.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import DATA_DIR

log = logging.getLogger("wardogs.enrich")

SNAPSHOT_PATH = "/v1/snapshot"
CACHE_PATH = DATA_DIR / "servers-cache.json"
REQUEST_TIMEOUT = 20
# Never poll faster than this, whatever the provider's refreshSeconds says —
# a floor that protects against a mis-parsed or absent value.
MIN_REFRESH_SECONDS = 60
# The provider currently reports 180; this is the fallback when it says nothing.
DEFAULT_REFRESH_SECONDS = 180

CREDIT = "data: wardogservers.com"

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-")


def normalize_join_code(code: str | None) -> str | None:
    """Normalizes a join code to bare digits for comparison.

    Bridges the two spellings of the same value: the game shows ``469-618``,
    the provider stores ``469618``. Returns None for anything containing no
    digits — which correctly rejects the UUID-shaped ``serverId`` values that
    community servers carry when no join code has been resolved.
    """
    if not code:
        return None
    digits = re.sub(r"[^0-9]", "", str(code))
    return digits or None


def looks_like_join_code(code: str | None) -> bool:
    """True only for a plausible 5-7 digit join code.

    Explicitly excludes UUIDs, which would otherwise compare as digit soup and
    could never legitimately match a reading from the game's UI."""
    if not code:
        return False
    if _UUID_RE.match(str(code).strip()):
        return False
    digits = normalize_join_code(code)
    return bool(digits) and 5 <= len(digits) <= 7


@dataclass
class ServerInfo:
    """What the directory knows about one server. Every field may be None —
    absent data stays absent rather than being invented."""

    name: str | None = None
    region: str | None = None
    players: int | None = None
    max_players: int | None = None
    map_name: str | None = None
    mode: str | None = None
    password_protected: bool | None = None
    server_type: str | None = None
    stale: bool = False

    def format_line(self) -> str:
        """One compact block for the embed.

        ``LIVE DATA`` / ``STALE DATA`` is a hard label, per the standing rule
        that provider-sourced data is never presented as if it had been
        observed directly."""
        parts: list[str] = []
        if self.name:
            parts.append(self.name)
        if self.region:
            parts.append(self.region)
        if self.map_name:
            parts.append(self.map_name)
        if self.mode:
            parts.append(self.mode)
        if self.players is not None and self.max_players is not None:
            parts.append(f"{self.players}/{self.max_players} players")
        elif self.players is not None:
            parts.append(f"{self.players} players")
        if self.password_protected:
            parts.append("password")

        label = "STALE DATA" if self.stale else "LIVE DATA"
        if not parts:
            return f"{label} · {CREDIT}"
        return f"{label} · {' · '.join(parts)}\n{CREDIT}"


def _text(value) -> str | None:
    """Coerces a payload value to display text, or None.

    The provider nests ``map`` and ``mode`` as objects; naively taking those
    fields would put a Python dict into the Discord message. Only strings and
    simple scalars survive here."""
    if value is None or isinstance(value, (dict, list, tuple)):
        return None
    text = str(value).strip()
    return text or None


class DirectoryClient:
    """Fetches and caches the server snapshot. One instance per app run."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "WardogsPresence (1.0)"})
        self._etag: str | None = None
        self._servers: dict[str, dict] = {}
        self._stale = False
        self._refresh_seconds = DEFAULT_REFRESH_SECONDS
        self._last_fetch = 0.0
        self._load_cache()

    # ------------------------------------------------------------------
    def _load_cache(self) -> None:
        """Restores the last good snapshot so a restart is useful immediately
        instead of waiting on a network round trip."""
        if not CACHE_PATH.exists():
            return
        try:
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        self._etag = payload.get("etag")
        servers = payload.get("servers")
        self._servers = servers if isinstance(servers, dict) else {}
        self._last_fetch = float(payload.get("fetched_at") or 0)
        if self._servers:
            log.info("Loaded %d cached servers.", len(self._servers))

    def _save_cache(self) -> None:
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(
                json.dumps(
                    {
                        "etag": self._etag,
                        "servers": self._servers,
                        "fetched_at": self._last_fetch,
                    }
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not write the server cache: %s", exc)

    # ------------------------------------------------------------------
    def _due(self) -> bool:
        interval = max(self._refresh_seconds, MIN_REFRESH_SECONDS)
        return (time.time() - self._last_fetch) >= interval

    def refresh(self, force: bool = False) -> bool:
        """Fetches the snapshot if due. Returns True when the local view is
        usable afterwards (freshly fetched, 304, or a previously cached copy).

        Never raises: an unreachable provider must not break the app.
        """
        if not force and not self._due():
            return bool(self._servers)

        url = f"{self.base_url}{SNAPSHOT_PATH}"
        headers = {}
        if self._etag:
            # Conditional request: an unchanged directory costs a 304, not a
            # full re-download.
            headers["If-None-Match"] = self._etag

        try:
            resp = self.session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            log.warning("Server directory unreachable: %s", exc)
            self._stale = bool(self._servers)
            return bool(self._servers)

        if resp.status_code == 304:
            log.info("Server directory unchanged (304).")
            self._last_fetch = time.time()
            self._stale = False
            self._save_cache()
            return True

        if resp.status_code != 200:
            log.warning("Server directory returned %s.", resp.status_code)
            self._stale = bool(self._servers)
            return bool(self._servers)

        try:
            payload = resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            log.warning("Server directory returned unparseable JSON.")
            self._stale = bool(self._servers)
            return bool(self._servers)

        servers = self._extract_servers(payload)
        if servers is None:
            log.warning("Server directory had an unexpected shape; keeping OCR-only output.")
            self._stale = bool(self._servers)
            return bool(self._servers)

        self._servers = servers
        self._etag = resp.headers.get("ETag", self._etag)
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if isinstance(meta, dict):
            try:
                self._refresh_seconds = int(meta.get("refreshSeconds") or DEFAULT_REFRESH_SECONDS)
            except (TypeError, ValueError):
                self._refresh_seconds = DEFAULT_REFRESH_SECONDS
            # A provider that says its data is stale is believed.
            self._stale = bool(meta.get("stale", False))
        else:
            self._stale = False

        self._last_fetch = time.time()
        self._save_cache()
        log.info("Server directory refreshed: %d matchable servers.", len(self._servers))
        return True

    @staticmethod
    def _extract_servers(payload) -> dict[str, dict] | None:
        """Normalizes the snapshot into ``{normalized_join_code: record}``.

        Only records whose ``serverId`` is genuinely a join code are indexed.
        Community servers whose code the provider has not resolved carry a UUID
        there; an OCR read can never equal a UUID, so indexing them would only
        create false lookup paths.
        """
        if not isinstance(payload, dict):
            return None
        raw = payload.get("data")
        if raw is None:
            raw = payload.get("servers")
        if not isinstance(raw, list):
            return None

        out: dict[str, dict] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("serverId")
            if not looks_like_join_code(raw_id):
                continue
            code = normalize_join_code(raw_id)
            if code:
                out[code] = entry
        return out or None

    # ------------------------------------------------------------------
    def lookup(self, join_code: str | None) -> ServerInfo | None:
        """Returns directory info for a join code, or None when unknown.

        None is the honest answer for "not in the directory" — the caller then
        publishes the OCR-only message rather than inventing fields.
        """
        code = normalize_join_code(join_code)
        if not code or not self._servers:
            return None
        record = self._servers.get(code)
        if not isinstance(record, dict):
            return None

        def as_int(value):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        # `map` and `mode` are objects; pull the human-facing string out of
        # each rather than stringifying the whole thing.
        map_obj = record.get("map")
        map_name = None
        if isinstance(map_obj, dict):
            map_name = _text(map_obj.get("variant")) or _text(map_obj.get("base"))

        mode_obj = record.get("mode")
        mode_name = None
        if isinstance(mode_obj, dict):
            # `experience` is the friendly label (e.g. "Bakurani_KOTH_01");
            # `gameMode` is an internal id, used only as a fallback.
            mode_name = _text(mode_obj.get("experience")) or _text(mode_obj.get("gameMode"))

        password = record.get("passwordProtected")

        return ServerInfo(
            name=_text(record.get("name")) or _text(record.get("nativeName")),
            region=_text(record.get("region")),
            players=as_int(record.get("players")),
            max_players=as_int(record.get("maxPlayers")),
            map_name=map_name,
            mode=mode_name,
            password_protected=password if isinstance(password, bool) else None,
            server_type=_text(record.get("type")),
            stale=self._stale,
        )


def extract_join_code(status: str | None) -> str | None:
    """Pulls the join code out of a parsed status like
    ``"East #145 · ID 469-618"``.

    Returns the normalized (bare-digit) code, or None for queued /
    not-in-game / None statuses, which have nothing to look up.
    """
    if not status or "ID " not in status:
        return None
    tail = status.rsplit("ID ", 1)[-1].strip()
    token = tail.split()[0] if tail else ""
    return normalize_join_code(token)
