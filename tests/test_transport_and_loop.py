"""Tests for the webhook transport and the polling state machine.

Everything here runs offline. The transport is exercised against a stub that
records calls and returns scripted responses, so the *logic* under test
(recreate-on-404, rate-limit backoff, identity change, debounce, heartbeat,
score throttle) is verified without touching Discord.

The one thing these tests deliberately cannot prove is that a real webhook
behaves as documented — that is what the live gate script does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wardogs_presence import loop as loop_mod  # noqa: E402
from wardogs_presence import screen  # noqa: E402
from wardogs_presence.config import Config  # noqa: E402
from wardogs_presence.discord_webhook import SendResult, build_body, sanitize_username  # noqa: E402

NAME = "Tester"


# === Payload shape =========================================================
def test_build_body_matches_discord_message_contract():
    body = build_body(
        description="🔵 │ (East) #1",
        embed_title=f"Current Wardogs Server — {NAME}",
        in_game=True,
        timestamp_iso="2026-09-22T12:00:00+00:00",
        username=NAME,
        avatar_url="",
    )
    assert body["content"] == ""
    embed = body["embeds"][0]
    assert embed["title"] == f"Current Wardogs Server — {NAME}"
    assert embed["description"] == "🔵 │ (East) #1"
    assert embed["color"] == 0x57F287  # green while in a game
    assert embed["footer"]["text"] == "Last updated"
    assert body["username"] == NAME
    assert "avatar_url" not in body  # omitted when unset, not sent as empty


def test_build_body_not_in_game_uses_grey():
    body = build_body("Tester is not in a game", "t", False, "2026-09-22T12:00:00+00:00", NAME)
    assert body["embeds"][0]["color"] == 0x99AAB5


def test_build_body_includes_avatar_when_set():
    body = build_body("x", "t", True, "2026-09-22T12:00:00+00:00", NAME, "https://example.com/a.png")
    assert body["avatar_url"] == "https://example.com/a.png"


def test_build_body_sanitizes_username():
    body = build_body("x", "t", True, "2026-09-22T12:00:00+00:00", "@everyone")
    assert body["username"] == sanitize_username("@everyone")
    assert "@everyone" not in body["username"]


# === Description assembly (never blank) ====================================
def test_description_never_empty_for_any_status_shape():
    cases = [
        "East #1 \u00b7 ID 100-200",
        f"{NAME} is not in a game",
        "Queued (position 3 of 10)",
        "Valkyra · East #1 \u00b7 ID 100-200",
    ]
    for status in cases:
        described = loop_mod.build_description(status, NAME)
        assert described.strip(), status
        assert "None" not in described, status


def test_description_strips_team_but_keeps_icon():
    described = loop_mod.build_description("Valkyra · East #1 \u00b7 ID 100-200", NAME)
    assert "Valkyra" not in described  # the name is redundant; the icon says it
    assert "East #1" in described


def test_description_adds_scores_on_their_own_block():
    described = loop_mod.build_description("East #1 \u00b7 ID 100-200", NAME, scores=(5, 13, 95))
    assert "Score:" in described
    assert "5" in described and "13" in described and "95" in described


def test_description_includes_enrichment_when_supplied():
    described = loop_mod.build_description("East #1 \u00b7 ID 100-200", NAME, enrichment="LIVE DATA · 42/100 players")
    assert "LIVE DATA" in described


def test_description_not_in_game_is_plain():
    status = f"{NAME} is not in a game"
    assert loop_mod.build_description(status, NAME) == status


def test_round_down_never_goes_forward():
    from datetime import datetime, timezone

    dt = datetime(2026, 9, 22, 13, 38, 45, tzinfo=timezone.utc)
    rounded = loop_mod.round_down_to_5_minutes(dt)
    assert rounded <= dt
    assert (rounded.minute, rounded.second) == (35, 0)


# === Fake transport ========================================================
class FakeTransport:
    """Records every call and replays scripted results."""

    def __init__(self, script=None):
        self.calls: list[tuple[str, dict]] = []
        self.script = list(script or [])
        self._next_id = 1000

    def _result(self, kind: str, message_id: str | None):
        if self.script:
            return self.script.pop(0)
        if kind == "post":
            self._next_id += 1
            return SendResult(ok=True, message_id=str(self._next_id))
        return SendResult(ok=True, message_id=message_id)

    def post(self, body):
        self.calls.append(("post", body))
        return self._result("post", None)

    def patch(self, message_id, body):
        self.calls.append(("patch", body))
        return self._result("patch", message_id)

    def delete(self, message_id):
        self.calls.append(("delete", {}))
        return True


def _cfg(tmp_path, monkeypatch, **overrides) -> Config:
    """A config whose state file is isolated per test."""
    monkeypatch.setattr(loop_mod, "STATE_PATH", tmp_path / "state.json")
    cfg = Config(
        webhook_url="https://discord.com/api/webhooks/123456789/tok",
        display_name=NAME,
        **overrides,
    )
    return cfg


def _loop(cfg, transport):
    presence = loop_mod.PresenceLoop(cfg, dry_run=False)
    presence.transport = transport
    return presence


# === Edit-in-place semantics ===============================================
def test_first_publish_posts_and_later_updates_patch(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    transport = FakeTransport()
    presence = _loop(cfg, transport)

    presence._apply("East #1 \u00b7 ID 100-200")
    assert transport.calls[0][0] == "post"
    assert presence.message_id == "1001"

    presence._apply("East #2 \u00b7 ID 100-200")
    assert transport.calls[1][0] == "patch"  # edited, not reposted
    assert len([c for c in transport.calls if c[0] == "post"]) == 1


def test_state_survives_restart_without_duplicate_message(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    transport = FakeTransport()

    first = _loop(cfg, transport)
    first._apply("East #1 \u00b7 ID 100-200")
    posted_id = first.message_id

    # Simulate a restart: a fresh loop reading the same state file.
    second = _loop(cfg, FakeTransport())
    second.restore()
    assert second.message_id == posted_id
    second._apply("East #2 \u00b7 ID 100-200")
    assert second.transport.calls[0][0] == "patch"


def test_missing_message_is_recreated_not_retried_forever(tmp_path, monkeypatch):
    """A 404 on edit means the message is gone; the loop must post a fresh one
    instead of looping on an id that can never work."""
    cfg = _cfg(tmp_path, monkeypatch)
    transport = FakeTransport(
        script=[
            SendResult(ok=False, message_id="999", recreate=True, error="message not found"),
            SendResult(ok=True, message_id="2000"),
        ]
    )
    presence = _loop(cfg, transport)
    presence.message_id = "999"

    presence._apply("East #1 \u00b7 ID 100-200")

    kinds = [c[0] for c in transport.calls]
    assert kinds == ["patch", "post"]  # tried the edit, then recovered
    assert presence.message_id == "2000"


def test_rate_limit_sets_cooldown_and_skips_further_publishes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    transport = FakeTransport(script=[SendResult(ok=False, retry_after=30.0)])
    presence = _loop(cfg, transport)

    presence._apply("East #1 \u00b7 ID 100-200")
    assert presence.cooldown_until > 0
    assert presence.last_status is None  # nothing was claimed as published

    before = len(transport.calls)
    presence._apply("East #2 \u00b7 ID 100-200")
    assert len(transport.calls) == before  # still cooling down, no call made


def test_failed_publish_does_not_claim_success(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    transport = FakeTransport(script=[SendResult(ok=False, error="HTTP 500")])
    presence = _loop(cfg, transport)
    presence._apply("East #1 \u00b7 ID 100-200")
    assert presence.last_status is None
    assert presence.message_id is None


# === Identity handling =====================================================
def test_name_change_recreates_the_message(tmp_path, monkeypatch):
    """Discord fixes a webhook username when the message is created, so an
    identity change cannot be applied by editing — the message is re-created."""
    cfg = _cfg(tmp_path, monkeypatch)
    transport = FakeTransport()

    first = _loop(cfg, transport)
    first._apply("East #1 \u00b7 ID 100-200")
    assert first.message_id

    cfg.display_name = "Someone Else"
    second = _loop(cfg, FakeTransport())
    second.restore()
    assert second.message_id is None  # dropped, so the next apply posts fresh


def test_same_identity_keeps_the_message(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    first = _loop(cfg, FakeTransport())
    first._apply("East #1 \u00b7 ID 100-200")

    second = _loop(cfg, FakeTransport())
    second.restore()
    assert second.message_id == first.message_id


def test_posted_body_carries_the_configured_identity(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, avatar_url="https://example.com/a.png")
    transport = FakeTransport()
    _loop(cfg, transport)._apply("East #1 \u00b7 ID 100-200")
    body = transport.calls[0][1]
    assert body["username"] == NAME
    assert body["avatar_url"] == "https://example.com/a.png"


# === Debounce / heartbeat / score throttle =================================
def _stub_capture(monkeypatch, statuses):
    """Feeds a scripted sequence of (status, team, scores) tuples to the loop."""
    state = {"i": 0}

    def fake(cfg):
        i = min(state["i"], len(statuses) - 1)
        state["i"] += 1
        status, team, scores = statuses[i]
        return "raw", status, team, scores

    monkeypatch.setattr(loop_mod.screen, "capture_and_parse", fake)
    monkeypatch.setattr(loop_mod.screen, "is_game_running", lambda *_: True)


def test_debounce_requires_two_identical_reads(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    transport = FakeTransport()
    presence = _loop(cfg, transport)

    server = "East #1 \u00b7 ID 100-200"
    _stub_capture(monkeypatch, [(server, None, None)])

    presence.tick()  # first sighting — not yet trusted
    assert transport.calls == []
    presence.tick()  # second identical read — publish
    assert len(transport.calls) == 1


def test_single_flaky_read_does_not_publish(tmp_path, monkeypatch):
    """One odd reading followed by a different one must not publish the odd one."""
    cfg = _cfg(tmp_path, monkeypatch)
    transport = FakeTransport()
    presence = _loop(cfg, transport)

    good = "East #1 \u00b7 ID 100-200"
    flaky = "East #9 \u00b7 ID 909-909"
    _stub_capture(monkeypatch, [(good, None, None), (flaky, None, None), (good, None, None)])

    for _ in range(3):
        presence.tick()

    published = [c for c in transport.calls if c[0] == "post"]
    assert published == []  # neither reading was ever seen twice running


def test_heartbeat_republishes_without_creating_a_second_message(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, heartbeat_interval_seconds=0.0)
    transport = FakeTransport()
    presence = _loop(cfg, transport)

    _stub_capture(monkeypatch, [("East #1 \u00b7 ID 100-200", None, None)])
    presence.tick()
    presence.tick()  # publish #1 (post)
    assert presence.message_id is not None

    calls_after_first = len(transport.calls)
    presence.tick()  # heartbeat fires (interval 0) -> patch, not post
    assert len(transport.calls) > calls_after_first
    assert len([c for c in transport.calls if c[0] == "post"]) == 1


def test_game_closing_publishes_immediately_without_debounce(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    transport = FakeTransport()
    presence = _loop(cfg, transport)
    presence.last_status = "East #1 \u00b7 ID 100-200"
    presence.game_was_running = True

    monkeypatch.setattr(loop_mod.screen, "is_game_running", lambda *_: False)
    presence.tick()

    assert len(transport.calls) == 1  # certain signal, so no second read needed
    assert transport.calls[0][1]["embeds"][0]["description"] == f"{NAME} is not in a game"


def test_score_only_change_is_throttled(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, score_update_interval_seconds=9999.0)
    transport = FakeTransport()
    presence = _loop(cfg, transport)

    server = "East #1 \u00b7 ID 100-200"
    presence.last_status = server
    presence.last_applied_scores = (1, 2, 3)
    presence.last_known_scores = (4, 5, 6)
    presence.last_applied_at = loop_mod.time.time()

    monkeypatch.setattr(loop_mod.screen, "is_game_running", lambda *_: True)
    _stub_capture(monkeypatch, [(server, None, None)])
    presence.tick()
    presence.tick()

    assert transport.calls == []  # throttle window has not elapsed


def test_dry_run_never_touches_the_transport(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, heartbeat_interval_seconds=0.0)
    presence = loop_mod.PresenceLoop(cfg, dry_run=True)
    assert presence.transport is None

    seen = []
    presence.on_status = seen.append
    presence._apply("East #1 \u00b7 ID 100-200")
    assert seen == ["East #1 \u00b7 ID 100-200"]


def test_quitting_the_game_clears_stale_team(tmp_path, monkeypatch):
    """A team must never leak from one match into the next."""
    cfg = _cfg(tmp_path, monkeypatch)
    presence = _loop(cfg, FakeTransport())

    not_in_game = screen.not_in_game_text(NAME)
    _stub_capture(monkeypatch, [(not_in_game, None, None)])
    presence.last_known_team = "Valkyra"
    presence.tick()
    presence.tick()

    assert presence.last_known_team is None


def test_ocr_does_not_run_when_the_game_is_absent(tmp_path, monkeypatch):
    """SC-2.11: the expensive screen read is gated on the game process.

    Without this gate the app would capture and OCR the whole desktop every
    poll forever, for the entire time it sits in the tray with no game running
    — noticeable CPU and battery drain for no result.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    presence = _loop(cfg, FakeTransport())

    calls = []
    monkeypatch.setattr(loop_mod.screen, "is_game_running", lambda *_: False)
    monkeypatch.setattr(
        loop_mod.screen,
        "capture_and_parse",
        lambda *_a, **_k: calls.append(1) or (None, None, None, None),
    )

    for _ in range(5):
        presence.tick()

    assert calls == [], "OCR ran even though no game process was present"


def test_ocr_runs_when_the_game_is_present(tmp_path, monkeypatch):
    """The gate must not be stuck closed — the converse of the test above."""
    cfg = _cfg(tmp_path, monkeypatch)
    presence = _loop(cfg, FakeTransport())

    calls = []
    monkeypatch.setattr(loop_mod.screen, "is_game_running", lambda *_: True)
    monkeypatch.setattr(
        loop_mod.screen,
        "capture_and_parse",
        lambda *_a, **_k: calls.append(1) or (None, "East #1 \u00b7 ID 100-200", None, None),
    )

    presence.tick()

    assert calls, "OCR was skipped even though the game was running"


def test_two_enabled_webhooks_get_independent_messages_and_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(loop_mod, "STATE_PATH", tmp_path / "state.json")
    transports = {}

    def make_transport(url):
        transport = FakeTransport()
        transports[url] = transport
        return transport

    monkeypatch.setattr(loop_mod, "WebhookTransport", make_transport)
    first_url = "https://discord.com/api/webhooks/111111111/alpha"
    second_url = "https://discord.com/api/webhooks/222222222/beta"
    cfg = Config(display_name=NAME, webhook_profiles=[
        {"id": "squad", "label": "Squad", "url": first_url, "enabled": True},
        {"id": "friends", "label": "Friends", "url": second_url, "enabled": True},
    ])
    presence = loop_mod.PresenceLoop(cfg)
    presence._apply("East #1 · ID 100-200")
    assert [call[0] for call in transports[first_url].calls] == ["post"]
    assert [call[0] for call in transports[second_url].calls] == ["post"]
    assert presence.profile_state["squad"]["message_id"]
    assert presence.profile_state["friends"]["message_id"]

    # Advance the per-destination safety interval before the next status.
    for target in presence.profile_state.values():
        target["last_sent_at"] -= 5
    presence._apply("East #2 · ID 100-200")
    assert [call[0] for call in transports[first_url].calls] == ["post", "patch"]
    assert [call[0] for call in transports[second_url].calls] == ["post", "patch"]


def test_rate_limited_webhook_does_not_repeat_healthy_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(loop_mod, "STATE_PATH", tmp_path / "state.json")
    good = FakeTransport()
    limited = FakeTransport(script=[SendResult(ok=False, retry_after=30.0)])
    monkeypatch.setattr(loop_mod, "WebhookTransport", lambda url: good if "111" in url else limited)
    cfg = Config(display_name=NAME, webhook_profiles=[
        {"id": "good", "url": "https://discord.com/api/webhooks/111111111/alpha", "enabled": True},
        {"id": "limited", "url": "https://discord.com/api/webhooks/222222222/beta", "enabled": True},
    ])
    presence = loop_mod.PresenceLoop(cfg)
    presence._apply("East #1 · ID 100-200")
    assert len(good.calls) == len(limited.calls) == 1
    assert presence.profile_state["limited"]["cooldown_until"] > 0
    presence._apply("East #1 · ID 100-200")
    assert len(good.calls) == len(limited.calls) == 1


def test_legacy_webhook_is_migrated_to_a_profile(tmp_path, monkeypatch):
    from wardogs_presence import config as config_mod

    path = tmp_path / "config.json"
    path.write_text('{"display_name":"Tester","webhook_url":"https://discord.com/api/webhooks/123456789/old"}')
    monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
    cfg = Config.load()
    assert cfg.webhook_profiles[0]["id"] == "original"
    assert cfg.active_webhooks()[0]["url"].endswith("/old")
