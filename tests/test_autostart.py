"""Autostart registration tests.

Written after a live check disproved the original approach: ``schtasks
/SC ONLOGON`` is refused with "Access is denied" on a non-elevated account
(verified by hand — ``/SC ONCE`` succeeded on the same account, so it is the
logon trigger specifically that needs elevation). Every friend runs
non-elevated, so the shipped build would have failed to register autostart for
all of them, silently.

These tests pin the replacement: a launcher in the per-user Startup folder,
which needs no elevation. The filesystem is redirected to a tmp_path so the
suite never touches the real Startup folder.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wardogs_presence import tray  # noqa: E402


@pytest.fixture()
def fake_startup(tmp_path, monkeypatch):
    """Points the Startup folder at a temp dir for the duration of a test."""
    target = tmp_path / "Startup"
    target.mkdir()
    monkeypatch.setattr(tray, "_startup_dir", lambda: target)
    yield target


def test_register_creates_a_launcher(fake_startup):
    ok, message = tray.register_autostart()
    assert ok, message
    launcher = fake_startup / f"{tray.TASK_NAME}.cmd"
    assert launcher.is_file()


def test_launcher_uses_explicit_crlf(fake_startup):
    """A double-translated CRLF ("\\r\\r\\n") confuses cmd.exe's line
    handling — the file must contain clean CRLF terminators."""
    tray.register_autostart()
    raw = (fake_startup / f"{tray.TASK_NAME}.cmd").read_bytes()
    assert b"\r\r\n" not in raw
    assert b"\r\n" in raw


def test_launcher_has_no_bare_lf(fake_startup):
    """Every newline in a .cmd should be part of a CRLF pair."""
    tray.register_autostart()
    raw = (fake_startup / f"{tray.TASK_NAME}.cmd").read_bytes()
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0


def test_launcher_starts_detached(fake_startup):
    """`start ""` keeps a console window from lingering at login — the app is a
    tray application."""
    tray.register_autostart()
    text = (fake_startup / f"{tray.TASK_NAME}.cmd").read_text()
    assert text.startswith("@echo off")
    assert 'start ""' in text


def test_launcher_never_depends_on_an_unquoted_python(fake_startup, monkeypatch):
    """The packaged build must not need a Python on PATH, and any path with a
    space must be quoted or Windows cannot launch it."""
    monkeypatch.setattr(tray, "_launch_target", lambda: [r"C:\Program Files\App\App.exe"])
    tray.register_autostart()
    text = (fake_startup / f"{tray.TASK_NAME}.cmd").read_text()
    assert '"C:\\Program Files\\App\\App.exe"' in text, text


def test_register_is_idempotent(fake_startup):
    first_ok, _ = tray.register_autostart()
    second_ok, _ = tray.register_autostart()
    assert first_ok and second_ok
    entries = list(fake_startup.glob("*.cmd"))
    assert len(entries) == 1, f"duplicate autostart entries: {entries}"


def test_unregister_removes_the_launcher(fake_startup):
    tray.register_autostart()
    ok, message = tray.unregister_autostart()
    assert ok, message
    assert not (fake_startup / f"{tray.TASK_NAME}.cmd").is_file()


def test_unregister_without_registration_is_not_an_error(fake_startup):
    ok, _ = tray.unregister_autostart()
    assert ok, "unregistering when nothing is registered should be a no-op"


def test_frozen_build_targets_its_own_executable(monkeypatch):
    """SC-7.5: a packaged install must point at its own exe."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Games\WardogsPresence\WardogsPresence.exe")
    target = tray._launch_target()
    assert target == [r"C:\Games\WardogsPresence\WardogsPresence.exe"]
    assert "-m" not in target
    assert not any(".venv" in part for part in target)


def test_source_run_targets_this_interpreter(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\venv\Scripts\python.exe")
    target = tray._launch_target()
    assert target[0] == r"C:\venv\Scripts\python.exe"
    assert "wardogs_presence.cli" in target


def test_task_name_does_not_collide_with_upstream():
    """Upstream ships a `WardogsDiscordStatus` task. Ours must be distinct so
    installing both cannot hijack one another."""
    assert tray.TASK_NAME != "WardogsDiscordStatus"
