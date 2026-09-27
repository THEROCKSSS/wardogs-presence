"""Tests for the OCR parsing and transport logic.

These run with no screen, no game, and no Discord — every input is literal
text or a synthetic image, so they are honest unit tests rather than a
disguised integration test.

The parsing fixtures mirror the real pause-menu layout documented upstream.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wardogs_presence import screen  # noqa: E402
from wardogs_presence.discord_webhook import (  # noqa: E402
    parse_webhook_url,
    sanitize_username,
    validate_webhook_url,
)

NAME = "Tester"


# === parse_server ==========================================================
# Fixtures use the real pause-menu layout: the region is WRAPPED IN PARENTHESES
# ((East) #145). That is not cosmetic — REGION_RE requires the parens, and a
# bare "East #145" therefore cannot parse. Upstream relies on the same shape.
def test_parse_server_full_reading():
    text = "CURRENT SERVER: (East) #145\nSERVER ID 469-618"
    assert screen.parse_server(text) == "East #145 \u00b7 ID 469-618"


def test_parse_server_requires_all_three_fields():
    # No SERVER ID line -> incomplete read, must not be treated as a status.
    assert screen.parse_server("CURRENT SERVER: (East) #145") is None
    # No region/# -> also incomplete.
    assert screen.parse_server("CURRENT SERVER: something\nSERVER ID 469-618") is None


def test_parse_server_normalises_ocr_digit_confusions():
    # OCR frequently reads 0 as O and 1 as I/l in the id field.
    text = "CURRENT SERVER: (Central) #7\nSERVER ID 5O9-79I"
    assert screen.parse_server(text) == "Central #7 \u00b7 ID 509-791"


def test_parse_server_handles_spaced_dash():
    text = "CURRENT SERVER: (West) #12\nSERVER ID 509 - 791"
    assert screen.parse_server(text) == "West #12 \u00b7 ID 509-791"


def test_parse_server_multiword_region():
    text = "CURRENT SERVER: (US East) #3\nSERVER ID 100-200"
    assert screen.parse_server(text) == "US East #3 \u00b7 ID 100-200"


# === parse_queue ===========================================================
def test_parse_queue_with_region_and_position():
    text = "IN SERVER QUEUE... POSITION 15 OF 15\nQueued for (Central) #1"
    result = screen.parse_queue(text)
    assert result is not None
    assert "position 15 of 15" in result


def test_parse_queue_excludes_countdown_timer():
    """The countdown ticks every second; including it would make almost every
    poll look like a status change.

    Regression test: upstream's REGION_RE matched the timer's own parentheses
    and read "(0:42)" as the region name, producing "Queued for 0:42 #7".
    A region is alphabetic, so the timer must never satisfy it."""
    text = (
        "IN SERVER QUEUE... POSITION 3 OF 10 (0:42)\n"
        "QUEUED FOR (Central) #7"
    )
    result = screen.parse_queue(text)
    assert result is not None
    assert "0:42" not in result
    assert "position 3 of 10" in result
    assert result == "Queued for Central #7 (position 3 of 10)"


def test_region_re_never_matches_a_countdown():
    """Direct guard on the regex itself, independent of parse_queue."""
    assert screen.REGION_RE.search("(0:42)") is None
    assert screen.REGION_RE.search("(1:59)") is None
    match = screen.REGION_RE.search("(Central) #7")
    assert match is not None and match.group(1) == "Central"


def test_parse_queue_falls_back_to_server_id():
    text = "IN SERVER QUEUE... POSITION 4 OF 9\nSERVER ID 311-496"
    result = screen.parse_queue(text)
    assert result is not None
    assert "311-496" in result


def test_parse_queue_absent_returns_none():
    assert screen.parse_queue("CURRENT SERVER: East #1") is None


# === determine_status ======================================================
def test_determine_status_prefers_server_over_queue():
    text = "CURRENT SERVER: (East) #145\nSERVER ID 469-618\nSERVER QUEUE POSITION 1 OF 2"
    assert screen.determine_status(text, NAME) == "East #145 \u00b7 ID 469-618"


def test_determine_status_menu_splash_is_not_in_game():
    assert screen.determine_status("PRESS ANY BUTTON TO START", NAME) == f"{NAME} is not in a game"


def test_determine_status_server_browser_is_not_in_game():
    assert screen.determine_status("DEPLOY   SERVER BROWSER", NAME) == f"{NAME} is not in a game"


def test_determine_status_unrecognised_returns_none():
    """None means 'nothing readable' — the caller must leave the current
    status alone rather than guessing. This is the in-match, menu-closed case."""
    assert screen.determine_status("just some gameplay hud text", NAME) is None


# === is_in_match / compose =================================================
def test_is_in_match_only_for_real_server_lines():
    assert screen.is_in_match("East #145 \u00b7 ID 469-618", NAME) is True
    assert screen.is_in_match(f"{NAME} is not in a game", NAME) is False
    assert screen.is_in_match("Queued (position 3 of 10)", NAME) is False
    assert screen.is_in_match(None, NAME) is False


def test_compose_status_adds_team_only_in_match():
    assert screen.compose_status("East #1 \u00b7 ID 100-200", "Valkyra", NAME) == (
        "Valkyra · East #1 \u00b7 ID 100-200"
    )
    # Queued and not-in-game never carry a team, even if one was known before.
    assert screen.compose_status("Queued (position 1 of 2)", "Valkyra", NAME) == (
        "Queued (position 1 of 2)"
    )
    assert screen.compose_status(f"{NAME} is not in a game", "Valkyra", NAME) == (
        f"{NAME} is not in a game"
    )


def test_compose_status_without_team_is_unchanged():
    assert screen.compose_status("East #1 \u00b7 ID 100-200", None, NAME) == "East #1 \u00b7 ID 100-200"


# === region parsing ========================================================
def test_parse_region_valid():
    assert screen.parse_region("0,0.65,1.0,1.0") == (0.0, 0.65, 1.0, 1.0)


@pytest.mark.parametrize("bad", ["", "1,2,3", "0.5,0.5,0.4,0.6", "a,b,c,d", "0,0,1,2"])
def test_parse_region_rejects_invalid(bad):
    with pytest.raises(ValueError):
        screen.parse_region(bad)


# === webhook URL ===========================================================
def test_parse_webhook_url_accepts_valid_forms():
    for url in (
        "https://discord.com/api/webhooks/123456789/abcDEF-_.123",
        "https://discord.com/api/webhooks/123456789/abcDEF-_.123/",
        "https://discordapp.com/api/webhooks/123456789/abcDEF-_.123",
        "https://discord.com/api/v10/webhooks/123456789/abcDEF-_.123",
    ):
        parsed = parse_webhook_url(url)
        assert parsed is not None, url
        assert parsed[0] == "123456789"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not a url",
        "https://discord.com/api/webhooks/",
        "https://example.com/api/webhooks/123/abc",
        "https://discord.com/channels/123/456",
    ],
)
def test_parse_webhook_url_rejects_invalid(bad):
    assert parse_webhook_url(bad) is None


def test_validate_webhook_url_rejects_bad_shape_without_network():
    """A malformed URL must fail on the shape check alone — no network call."""
    ok, message = validate_webhook_url("https://example.com/not-a-webhook")
    assert ok is False
    assert "webhook URL" in message


# === username sanitization =================================================
def test_sanitize_username_breaks_mentions():
    cleaned = sanitize_username("@everyone")
    assert "@everyone" not in cleaned
    assert "\u200b" in cleaned  # zero-width space inserted


def test_sanitize_username_strips_forbidden_characters():
    cleaned = sanitize_username("bad`name:with#chars")
    for ch in "`:#":
        assert ch not in cleaned


def test_sanitize_username_truncates_and_never_blank():
    assert len(sanitize_username("x" * 200)) <= 80
    assert sanitize_username("") == "Wardogs Player"
    assert sanitize_username("   ") == "Wardogs Player"


def test_sanitize_username_preserves_normal_names():
    assert sanitize_username("Ghost") == "Ghost"
    assert sanitize_username("  Owen  ") == "Owen"
