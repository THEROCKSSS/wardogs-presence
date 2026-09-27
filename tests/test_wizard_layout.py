"""Layout regression tests for the setup wizard.

These exist because a *visual* check caught a bug that no widget-existence
test could: the window opened at a fixed 1000x780 while its content needed
1095px, so Save / Cancel / Run OCR check sat below the bottom of a 1080p
screen with no way to reach them. The window was open, mapped, and had every
widget present — and was still unusable.

The lesson these encode: assert the window **fits the screen it will open on**,
and assert the action buttons land inside the visible area, not merely that
they exist.

Skipped automatically where no display is available (headless CI), since Tk
cannot create a window there.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

tk = pytest.importorskip("tkinter", reason="tkinter unavailable")

from wardogs_presence.config import Config  # noqa: E402
from wardogs_presence.wizard import SetupWizard  # noqa: E402


@pytest.fixture()
def wizard():
    """One Tk window per test.

    Retried once: creating and destroying Tk roots in quick succession
    occasionally raises a TclError that is not a real defect, and a flaky skip
    in a layout test would hide the very regression these guard against.
    """
    instance = None
    for attempt in range(2):
        try:
            instance = SetupWizard(Config())
            break
        except tk.TclError:
            if attempt == 1:
                pytest.skip("no display available for Tk")
    instance.root.withdraw()
    instance.root.update_idletasks()
    yield instance
    try:
        instance.root.destroy()
    except tk.TclError:
        pass


def _find_buttons(widget) -> list:
    """Walks the widget tree collecting every ttk Button."""
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == "TButton":
            found.append(child)
        found.extend(_find_buttons(child))
    return found


def test_wizard_content_fits_on_this_screen(wizard):
    """The regression: a fixed window height that its own content overflowed."""
    required = wizard.root.winfo_reqheight()
    screen = wizard.root.winfo_screenheight()
    assert required <= screen - 30, (
        f"wizard needs {required}px but the screen is only {screen}px — "
        "controls will be unreachable"
    )


def test_window_height_never_exceeds_the_screen(wizard):
    assert wizard.root.winfo_height() <= wizard.root.winfo_screenheight()


def test_all_action_buttons_exist(wizard):
    labels = set()
    for button in _find_buttons(wizard.root):
        try:
            text = button["text"]
        except tk.TclError:
            continue
        if isinstance(text, str):
            labels.add(text)
    for required in ("Save and finish", "Cancel", "Run OCR check"):
        assert required in labels, f"missing button: {required}"


def test_action_buttons_are_within_the_visible_area(wizard):
    """A button that exists but sits below the screen edge is not reachable."""
    screen_h = wizard.root.winfo_screenheight()
    wizard.root.deiconify()
    wizard.root.update_idletasks()
    wizard.root.update()
    for button in _find_buttons(wizard.root):
        try:
            text = button["text"]
        except tk.TclError:
            continue
        if text in ("Save and finish", "Cancel", "Run OCR check"):
            bottom = button.winfo_rooty() + button.winfo_height()
            assert bottom <= screen_h, f"{text!r} renders at y={bottom}, off a {screen_h}px screen"
    wizard.root.withdraw()


def test_preview_never_starves_the_fixed_rows(wizard):
    """The preview is the only elastic row; it must yield enough room that the
    fixed content still fits."""
    assert wizard._preview_h >= 200


def test_monitor_list_is_populated(wizard):
    values = wizard.monitor_combo["values"]
    assert values, "no monitors listed — the user could not pick the right screen"
    for label in values:
        assert "\u00d7" in str(label), f"monitor label lacks a resolution: {label!r}"


def test_region_defaults_are_present(wizard):
    """Every region starts with a usable default so a user who drags nothing
    still gets a working configuration."""
    for key in ("capture_region", "team_icon_region", "score_region"):
        value = getattr(wizard.cfg, key)
        parts = value.split(",")
        assert len(parts) == 4, f"{key} is not a 4-part region: {value!r}"
        assert all(0.0 <= float(p) <= 1.0 for p in parts), f"{key} out of range: {value!r}"
