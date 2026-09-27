"""Tests for the optional API enrichment adapter.

Offline. The live contract was verified separately against
api.wardogservers.com (see HANDOFF.md); these tests lock the *logic* so a
regression cannot silently reintroduce the bugs the live probe caught:

  * matching on ``id`` (always a UUID) instead of ``serverId``
  * embedding the raw ``map``/``mode`` dicts into the message
  * failing to bridge the game's ``469-618`` and the provider's ``469618``
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wardogs_presence.enrich import (  # noqa: E402
    DirectoryClient,
    ServerInfo,
    extract_join_code,
    looks_like_join_code,
    normalize_join_code,
)

# A record shaped exactly like the provider's real payload.
REAL_RECORD = {
    "id": "006f08c7-f62d-4b81-af9f-799015a67d70",
    "serverId": "669357",
    "serverIdUpdatedAt": "2026-09-23T05:07:37.444Z",
    "name": "Official #145",
    "nativeName": "0a971e9de915",
    "type": "official",
    "region": "asia-east",
    "players": 89,
    "maxPlayers": 100,
    "reservedPlayers": 0,
    "passwordProtected": False,
    "serverNumber": 145,
    "map": {"variant": "Bakurani", "base": "Europe", "path": "/Game/Maps/..."},
    "mode": {"experience": "Bakurani_KOTH_01", "gameMode": "GM_Session_C", "modifiers": []},
    "rulesets": ["standard"],
}

# A community server whose join code the provider has NOT resolved.
UUID_RECORD = {
    "id": "001c89b8-287d-46a8-a081-fc3a60c3a6ff",
    "serverId": "383dc9aa-3ffe-4b99-8821-9c57ad059faa",
    "name": "STARDAWGS NAE",
    "type": "community",
    "region": "na-central",
    "players": 0,
    "maxPlayers": 100,
    "map": {"variant": "Bakurani", "base": "Kavkazi"},
    "mode": {"experience": "Bakurani_KOTH_01", "gameMode": "GM_Session_C"},
}


# === join-code normalization ==============================================
def test_normalize_strips_the_games_separator():
    """The game shows 469-618; the provider stores 469618."""
    assert normalize_join_code("469-618") == "469618"
    assert normalize_join_code("469618") == "469618"


def test_normalize_rejects_non_digit_input():
    assert normalize_join_code("") is None
    assert normalize_join_code(None) is None
    assert normalize_join_code("abc") is None


def test_uuid_is_not_a_join_code():
    """Regression: the first adapter version listed `id` as a candidate and so
    matched the live-instance UUID. That can never equal an OCR read."""
    assert looks_like_join_code("383dc9aa-3ffe-4b99-8821-9c57ad059faa") is False
    assert looks_like_join_code("669357") is True
    assert looks_like_join_code(None) is False


def test_extract_join_code_from_parsed_status():
    assert extract_join_code("East #145 \u00b7 ID 469-618") == "469618"
    assert extract_join_code("Valkyra · East #1 \u00b7 ID 669357") == "669357"


def test_extract_join_code_returns_none_when_nothing_to_look_up():
    assert extract_join_code(None) is None
    assert extract_join_code("Queued for East #1 (position 3 of 10)") is None
    assert extract_join_code("Tester is not in a game") is None


# === indexing =============================================================
def test_indexing_keys_on_server_id_not_id():
    payload = {"data": [REAL_RECORD, UUID_RECORD]}
    index = DirectoryClient._extract_servers(payload)
    assert index is not None
    assert "669357" in index  # indexed by the join code
    # The UUID's own value must not appear as a key.
    assert "383dc9aa-3ffe-4b99-8821-9c57ad059faa" not in index
    assert "006f08c7-f62d-4b81-af9f-799015a67d70" not in index


def test_unresolved_community_records_are_skipped_not_indexed():
    """They can never match, so indexing them only creates false paths."""
    index = DirectoryClient._extract_servers({"data": [UUID_RECORD]})
    assert index is None


def test_indexing_tolerates_the_alternate_data_key():
    assert DirectoryClient._extract_servers({"servers": [REAL_RECORD]}) is not None


@pytest.mark.parametrize("bad", [None, [], "nope", {"data": "not-a-list"}, {"data": []}])
def test_indexing_returns_none_on_unusable_payloads(bad):
    assert DirectoryClient._extract_servers(bad) is None


# === lookup ===============================================================
def _client_with(records) -> DirectoryClient:
    client = DirectoryClient("https://example.invalid")
    client._servers = DirectoryClient._extract_servers({"data": records}) or {}
    return client


def test_lookup_returns_real_fields():
    info = _client_with([REAL_RECORD]).lookup("669357")
    assert info is not None
    assert info.name == "Official #145"
    assert info.region == "asia-east"
    assert info.players == 89
    assert info.max_players == 100
    assert info.server_type == "official"


def test_lookup_extracts_strings_from_the_nested_objects():
    """Regression: `map` and `mode` are objects. Stringifying them would put a
    Python dict in the Discord message."""
    info = _client_with([REAL_RECORD]).lookup("669357")
    assert info is not None
    assert info.map_name == "Bakurani"  # not "{'variant': ...}"
    assert info.mode == "Bakurani_KOTH_01"
    assert "{" not in info.map_name and "{" not in info.mode


def test_lookup_accepts_the_games_hyphenated_spelling():
    assert _client_with([REAL_RECORD]).lookup("669-357") is not None


def test_lookup_unknown_code_returns_none():
    client = _client_with([REAL_RECORD])
    assert client.lookup("999999") is None
    assert client.lookup("") is None
    assert client.lookup(None) is None


def test_lookup_by_uuid_returns_none():
    assert _client_with([REAL_RECORD]).lookup("383dc9aa-3ffe-4b99-8821-9c57ad059faa") is None


# === presentation =========================================================
def test_format_line_labelled_live_and_credits_the_provider():
    line = _client_with([REAL_RECORD]).lookup("669357").format_line()
    assert line.startswith("LIVE DATA")
    assert "wardogservers.com" in line
    assert "Official #145" in line


def test_format_line_labels_stale_data_as_stale():
    info = ServerInfo(name="X", players=1, max_players=2, stale=True)
    assert info.format_line().startswith("STALE DATA")


def test_format_line_never_emits_a_raw_dict():
    line = _client_with([REAL_RECORD]).lookup("669357").format_line()
    assert "{" not in line and "}" not in line and "None" not in line


def test_format_line_survives_a_record_with_no_usable_fields():
    line = ServerInfo(stale=False).format_line()
    assert line.strip()
    assert line.startswith("LIVE DATA")
    assert "wardogservers.com" in line


def test_format_line_omits_absent_fields_without_placeholders():
    info = ServerInfo(name="Solo", stale=False)
    line = info.format_line()
    assert "players" not in line  # never invented
    assert "None" not in line
