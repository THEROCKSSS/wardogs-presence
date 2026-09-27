"""Setup wizard.

Everything a new user has to configure lives here, so nobody edits a file by
hand: paste the webhook URL, type a name, pick the monitor, drag the three
capture boxes over a live screenshot, and confirm with real OCR output.

This replaces upstream's separate ``region_preview.py`` step *and* its
``.env`` editing step. The drag-to-set-region interaction is ported from
upstream (MIT); the surrounding flow is new.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from . import APP_TITLE, __version__
from .config import (
    DEFAULT_CAPTURE_REGION,
    DEFAULT_MONITOR_INDEX,
    DEFAULT_SCORE_REGION,
    DEFAULT_TEAM_ICON_REGION,
    Config,
)
from .discord_webhook import validate_webhook_url

log = logging.getLogger("wardogs.wizard")

# (config attribute, label, outline colour, fill colour, default)
REGION_SPECS = [
    ("capture_region", "OCR capture region", "#c98f00", "#ffd000", DEFAULT_CAPTURE_REGION),
    ("team_icon_region", "Faction icon region", "#d1002c", "#ff3b3b", DEFAULT_TEAM_ICON_REGION),
    ("score_region", "Scoreboard region", "#0078b8", "#3bd1ff", DEFAULT_SCORE_REGION),
]

MIN_DRAG_PX = 6

# The preview is sized to whatever vertical room is actually available on this
# machine, rather than a fixed guess. A fixed 900x520 canvas made the window's
# required height 1095px, which does not fit a 1080p screen — the Save button
# and the OCR output area were pushed off-screen with no way to reach them.
PREVIEW_MAX_W = 900
PREVIEW_MIN_H = 200


def _load_font(size: int):
    for name in ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class SetupWizard:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.saved = False
        self._shot: Image.Image | None = None
        self._preview_scale = 1.0
        self._drag_start: tuple[int, int] | None = None
        self._active_key = REGION_SPECS[0][0]

        self.root = tk.Tk()
        self.root.title(f"{APP_TITLE} — Setup {__version__}")

        # Size to the actual screen. The previous fixed 1000x780 window could
        # not fit its own content on a 1080p display, which silently pushed
        # Save/Cancel/OCR off-screen — unusable, and invisible to any test
        # that only asserted widgets exist.
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        win_w = min(1000, max(720, screen_w - 80))
        win_h = min(940, max(600, screen_h - 80))
        # Leave a little breathing room and never exceed the work area.
        win_h = min(win_h, screen_h - 60)
        self._win_w, self._win_h = win_w, win_h

        # Reserve the fixed-height rows, then give whatever is left to the
        # preview. Measured: the non-preview content is ~584px tall, so this
        # must leave at least that much or the preview pushes Save off-screen.
        reserved = 620
        self._preview_h = max(PREVIEW_MIN_H, min(520, win_h - reserved))

        self.root.geometry(f"{win_w}x{win_h}+{max(0, (screen_w - win_w) // 2)}+20")
        self.root.minsize(760, 620)

        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="WARDOGS Presence setup",
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Three steps: connect Discord, name yourself, and line up the capture "
                "boxes. Nothing here needs a terminal."
            ),
            wraplength=940,
        ).pack(anchor="w", pady=(2, 10))

        # --- Step 1: Discord -------------------------------------------------
        discord = ttk.LabelFrame(outer, text="1 · Discord webhook", padding=10)
        discord.pack(fill="x", pady=4)

        ttk.Label(
            discord,
            text="Channel → Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL",
            font=("Segoe UI", 9, "italic"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        ttk.Label(discord, text="Webhook URL").grid(row=1, column=0, sticky="w")
        self.webhook_var = tk.StringVar(value=self.cfg.webhook_url)
        ttk.Entry(discord, textvariable=self.webhook_var, width=78).grid(
            row=1, column=1, sticky="we", padx=6
        )
        ttk.Button(discord, text="Test", command=self._test_webhook).grid(row=1, column=2)
        self.webhook_status = ttk.Label(discord, text="", wraplength=880, foreground="#555")
        self.webhook_status.grid(row=2, column=1, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Label(discord, text="Display name").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.name_var = tk.StringVar(value=self.cfg.display_name)
        ttk.Entry(discord, textvariable=self.name_var, width=30).grid(
            row=3, column=1, sticky="w", padx=6, pady=(6, 0)
        )
        ttk.Label(discord, text="shown in the channel").grid(
            row=3, column=2, sticky="w", pady=(6, 0)
        )

        ttk.Label(discord, text="Avatar URL (optional)").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.avatar_var = tk.StringVar(value=self.cfg.avatar_url)
        ttk.Entry(discord, textvariable=self.avatar_var, width=78).grid(
            row=4, column=1, columnspan=2, sticky="we", padx=6, pady=(6, 0)
        )

        discord.columnconfigure(1, weight=1)

        # --- Step 2: monitor --------------------------------------------------
        monitor = ttk.LabelFrame(outer, text="2 · Monitor", padding=10)
        monitor.pack(fill="x", pady=4)

        self.monitor_var = tk.StringVar()
        self.monitor_combo = ttk.Combobox(
            monitor, textvariable=self.monitor_var, state="readonly", width=48
        )
        self.monitor_combo.grid(row=0, column=0, sticky="w")
        ttk.Button(monitor, text="Use this monitor", command=self._use_monitor).grid(
            row=0, column=1, padx=6
        )
        ttk.Button(monitor, text="Capture now", command=self._capture).grid(row=0, column=2)
        ttk.Button(monitor, text="Capture in 5s", command=self._capture_delayed).grid(
            row=0, column=3, padx=6
        )
        self.monitor_status = ttk.Label(monitor, text="", foreground="#555")
        self.monitor_status.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # --- Step 3: regions --------------------------------------------------
        regions = ttk.LabelFrame(outer, text="3 · Capture boxes", padding=10)
        regions.pack(fill="both", expand=True, pady=4)

        radio_row = ttk.Frame(regions)
        radio_row.pack(anchor="w")
        ttk.Label(radio_row, text="Drag a box for:").pack(side="left", padx=(0, 8))
        self.active_radio = tk.StringVar(value=self._active_key)
        for key, label, _outline, _fill, _default in REGION_SPECS:
            ttk.Radiobutton(
                radio_row,
                text=label,
                value=key,
                variable=self.active_radio,
                command=lambda k=key: self._set_active(k),
            ).pack(side="left", padx=4)
        ttk.Button(radio_row, text="Reset to default", command=self._reset_active).pack(
            side="left", padx=12
        )

        self.canvas = tk.Canvas(
            regions,
            width=PREVIEW_MAX_W,
            height=self._preview_h,
            background="#222",
            highlightthickness=1,
        )
        self.canvas.pack(fill="both", expand=True, pady=6)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        ttk.Label(
            regions,
            text=(
                "Tip: in-game, open Settings → Interface and set Faction to Always On, "
                "or the faction icon will not be on screen to read."
            ),
            wraplength=940,
            font=("Segoe UI", 9, "italic"),
        ).pack(anchor="w")

        # --- OCR check + finish ----------------------------------------------
        # Packed BEFORE the regions pane is given expand=True, so these can
        # never be pushed off-screen by a tall preview. This is the bug that
        # made Save/Cancel unreachable on a 1080p display.
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(6, 0), side="bottom")

        buttons = ttk.Frame(bottom)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Run OCR check", command=self._run_ocr_check).pack(side="left")
        ttk.Button(buttons, text="Save and finish", command=self._save).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=6)

        self.ocr_text = tk.Text(bottom, height=5, width=110, wrap="word")
        self.ocr_text.pack(fill="x", expand=True, pady=(8, 0))
        self.ocr_text.insert("1.0", "Press “Run OCR check” to see what the app reads from your screen.")
        self.ocr_text.configure(state="disabled")

        self._refresh_monitors()

    # ------------------------------------------------------------------
    # Discord
    # ------------------------------------------------------------------
    def _test_webhook(self) -> None:
        url = self.webhook_var.get().strip()
        self.webhook_status.configure(text="Checking…", foreground="#555")
        self.root.update_idletasks()

        ok, message = validate_webhook_url(url)
        self.webhook_status.configure(text=message, foreground="#0a7a0a" if ok else "#b00020")

    # ------------------------------------------------------------------
    # Monitors
    # ------------------------------------------------------------------
    def _refresh_monitors(self) -> None:
        from .screen import list_monitors

        try:
            self._monitors = list_monitors()
        except Exception as exc:  # noqa: BLE001 - a missing display is not fatal here
            self._monitors = []
            self.monitor_status.configure(text=f"Could not list monitors: {exc}", foreground="#b00020")
            return

        labels = [
            f"{m['index']} — {m['width']}×{m['height']}" + ("  (primary)" if m["primary"] else "")
            for m in self._monitors
        ]
        self.monitor_combo["values"] = labels
        selected = next(
            (i for i, m in enumerate(self._monitors) if m["index"] == self.cfg.monitor_index), 0
        )
        if labels:
            self.monitor_combo.current(selected)
        self.monitor_status.configure(text=f"{len(labels)} display(s) found.", foreground="#555")

    def _selected_monitor_index(self) -> int:
        position = self.monitor_combo.current()
        if 0 <= position < len(getattr(self, "_monitors", [])):
            return self._monitors[position]["index"]
        return self.cfg.monitor_index

    def _use_monitor(self) -> None:
        self.cfg.monitor_index = self._selected_monitor_index()
        self.monitor_status.configure(
            text=f"Monitor {self.cfg.monitor_index} selected (saved when you finish).", foreground="#0a7a0a"
        )

    # ------------------------------------------------------------------
    # Screenshot + regions
    # ------------------------------------------------------------------
    def _capture(self) -> None:
        from .screen import grab_region

        index = self._selected_monitor_index()
        try:
            # Capture the whole monitor by grabbing the full 0-1 box.
            self._shot = grab_region("0,0,1,1", index)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"Could not take a screenshot:\n{exc}")
            return
        self._render()

    def _capture_delayed(self, seconds: int = 5) -> None:
        self.monitor_status.configure(
            text=f"Capturing in {seconds}s — switch to Wardogs now…", foreground="#555"
        )

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                self._capture()
                self.monitor_status.configure(text="Captured.", foreground="#0a7a0a")
                return
            self.monitor_status.configure(
                text=f"Capturing in {remaining}s — switch to Wardogs now…", foreground="#555"
            )
            self.root.after(1000, countdown, remaining - 1)

        countdown(seconds)

    def _render(self) -> None:
        if self._shot is None:
            return
        shot = self._shot
        available_h = self._preview_h if hasattr(self, "_preview_h") else PREVIEW_MIN_H
        scale = min(PREVIEW_MAX_W / shot.width, available_h / shot.height, 1.0)
        preview = shot.resize((int(shot.width * scale), int(shot.height * scale)), Image.LANCZOS)
        self._preview_scale = scale

        canvas_img = preview.copy()
        draw = ImageDraw.Draw(canvas_img, "RGBA")
        font = _load_font(14)

        for key, label, outline, fill, _default in REGION_SPECS:
            region = getattr(self.cfg, key)
            try:
                left, top, right, bottom = (float(x) for x in region.split(","))
            except ValueError:
                continue
            x0, y0 = left * preview.width, top * preview.height
            x1, y1 = right * preview.width, bottom * preview.height
            draw.rectangle([x0, y0, x1, y1], outline=outline, width=2, fill=fill + "40")
            draw.text((x0 + 4, max(0, y0 - 16)), label, fill=outline, font=font)

        self._tk_image = ImageTk.PhotoImage(canvas_img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_image)
        self.canvas.configure(
            width=canvas_img.width, height=canvas_img.height, scrollregion=(0, 0, canvas_img.width, canvas_img.height)
        )

    def _set_active(self, key: str) -> None:
        self._active_key = key

    def _reset_active(self) -> None:
        default = next(
            (d for k, _l, _o, _f, d in REGION_SPECS if k == self._active_key), None
        )
        if default:
            setattr(self.cfg, self._active_key, default)
            self._render()

    def _on_press(self, event) -> None:
        self._drag_start = (event.x, event.y)

    def _on_drag(self, event) -> None:
        if self._drag_start is None or self._shot is None:
            return
        x0, y0 = self._drag_start
        self.canvas.delete("rubber")
        self.canvas.create_rectangle(x0, y0, event.x, event.y, outline="#00ff88", width=2, tags="rubber")

    def _on_release(self, event) -> None:
        if self._drag_start is None or self._shot is None:
            return
        x0, y0 = self._drag_start
        x1, y1 = event.x, event.y
        self._drag_start = None
        self.canvas.delete("rubber")

        if abs(x1 - x0) < MIN_DRAG_PX or abs(y1 - y0) < MIN_DRAG_PX:
            return  # a click, not a box

        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        left = max(0.0, min(x0, x1) / width)
        top = max(0.0, min(y0, y1) / height)
        right = min(1.0, max(x0, x1) / width)
        bottom = min(1.0, max(y0, y1) / height)
        if left >= right or top >= bottom:
            return

        setattr(self.cfg, self._active_key, f"{left:.4f},{top:.4f},{right:.4f},{bottom:.4f}")
        self._render()

    # ------------------------------------------------------------------
    # OCR check
    # ------------------------------------------------------------------
    def _run_ocr_check(self) -> None:
        from . import screen

        def work():
            path = screen.configure_tesseract(self.cfg.tesseract_cmd)
            if not path:
                self._set_ocr_text(
                    "Tesseract was not found.\n\nInstall it from the UB-Mannheim build, then retry."
                )
                return
            try:
                text, status, team, scores = screen.capture_and_parse(self.cfg)
            except Exception as exc:  # noqa: BLE001
                self._set_ocr_text(f"OCR failed: {exc}")
                return

            lines = [
                f"Raw OCR: {text.strip() or '(nothing read)'}",
                "",
                f"Server: {status or '(not found — adjust the OCR capture region)'}",
                f"Faction: {team or '(not detected — adjust the faction icon region)'}",
                f"Scores: {' - '.join(map(str, scores)) if scores else '(not readable — adjust the scoreboard region)'}",
                "",
                f"Tesseract: {path}",
            ]
            self._set_ocr_text("\n".join(lines))

        threading.Thread(target=work, daemon=True).start()

    def _set_ocr_text(self, value: str) -> None:
        def apply():
            self.ocr_text.configure(state="normal")
            self.ocr_text.delete("1.0", "end")
            self.ocr_text.insert("1.0", value)
            self.ocr_text.configure(state="disabled")

        self.root.after(0, apply)

    # ------------------------------------------------------------------
    # Finish
    # ------------------------------------------------------------------
    def _save(self) -> None:
        self.cfg.webhook_url = self.webhook_var.get().strip()
        self.cfg.display_name = self.name_var.get().strip()
        self.cfg.avatar_url = self.avatar_var.get().strip()
        self.cfg.monitor_index = self._selected_monitor_index()

        problems = self.cfg.missing_required()
        if problems:
            messagebox.showerror(APP_TITLE, "\n".join(problems))
            return

        ok, message = validate_webhook_url(self.cfg.webhook_url)
        if not ok:
            if not messagebox.askyesno(
                APP_TITLE,
                f"The webhook could not be verified:\n\n{message}\n\nSave anyway?",
            ):
                return

        self.cfg.setup_complete = True
        try:
            self.cfg.save()
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not save settings:\n{exc}")
            return

        self.saved = True
        messagebox.showinfo(
            APP_TITLE,
            "Settings saved.\n\nStart the app normally to begin watching. "
            "Restart it if it was already running.",
        )
        self.root.destroy()

    def _cancel(self) -> None:
        self.root.destroy()

    def run(self) -> Config | None:
        self.root.mainloop()
        return self.cfg if self.saved else None


def run_wizard(cfg: Config) -> Config | None:
    """Opens the wizard. Returns the saved config, or None if cancelled."""
    try:
        return SetupWizard(cfg).run()
    except tk.TclError as exc:
        log.error("Could not open the setup window: %s", exc)
        print(f"Could not open the setup window: {exc}")
        return None
