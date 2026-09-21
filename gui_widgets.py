"""Reusable Tkinter widgets shared by the MAA Agent manager GUI.

The widgets in this module depend only on the standard library and on a palette
dictionary using the keys ``bg``, ``panel``, ``field``, ``fg``, ``muted``,
``grid``, ``grid_soft``, ``good``, ``bad``, ``accent`` and ``warning``.
``maa_agent_gui`` owns the real palettes; the module keeps a dark fallback so the
widgets stay usable on their own and remain cheap to unit test.
"""

from __future__ import annotations

import calendar
import os
import tkinter as tk
from datetime import date, datetime
from tkinter import ttk
from typing import Callable

IS_WINDOWS = os.name == "nt"

DEFAULT_PALETTE = {
    "bg": "#11161d", "panel": "#18212b", "field": "#202c38", "fg": "#f2f5f7",
    "muted": "#aab7c4", "grid": "#324353", "grid_soft": "#253441",
    "good": "#5bd38b", "bad": "#ff7b72", "accent": "#62a8ff",
    "warning": "#ffb86b",
}

WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")


def coerce_palette(palette: dict | None) -> dict:
    """Return a complete palette, falling back to :data:`DEFAULT_PALETTE`."""
    resolved = dict(DEFAULT_PALETTE)
    if palette:
        for key, value in palette.items():
            if value is not None:
                resolved[key] = value
    return resolved


def contrast_text(color: str) -> str:
    """Pick dark or light text for a ``#rrggbb`` background colour."""
    value = str(color).lstrip("#")
    if len(value) != 6:
        return "#ffffff"
    try:
        red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return "#ffffff"
    luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
    return "#111111" if luminance > 0.6 else "#ffffff"


def rounded_rectangle_points(x0: float, y0: float, x1: float, y1: float, radius: float) -> list[float]:
    """Return polygon points that render as a rounded rectangle when smoothed."""
    radius = max(0.0, min(float(radius), (x1 - x0) / 2, (y1 - y0) / 2))
    return [
        x0 + radius, y0, x1 - radius, y0, x1, y0, x1, y0 + radius,
        x1, y1 - radius, x1, y1, x1 - radius, y1, x0 + radius, y1,
        x0, y1, x0, y1 - radius, x0, y0 + radius, x0, y0,
    ]


class ToggleSwitch(tk.Canvas):
    """Two-state switch drawn on a canvas.

    Clicking the switch, pressing Space or pressing Return flips the state and
    calls ``command(new_state)``.  ``set(value, invoke=True)`` performs the same
    notification programmatically, while ``set(value)`` stays silent.
    """

    DEFAULT_WIDTH = 52
    DEFAULT_HEIGHT = 28
    FOCUS_RING_TAG = "focus_ring"

    def __init__(self, master: tk.Misc, value: bool = False,
                 command: Callable[[bool], None] | None = None,
                 palette: dict | None = None, **kwargs):
        self.palette = coerce_palette(palette)
        self.command = command
        self._value = bool(value)
        kwargs.setdefault("width", self.DEFAULT_WIDTH)
        kwargs.setdefault("height", self.DEFAULT_HEIGHT)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("takefocus", True)
        super().__init__(master, **kwargs)
        self.configure(background=self.palette["bg"])
        self.bind("<Button-1>", self._on_click)
        self.bind("<space>", self._on_key)
        self.bind("<Return>", self._on_key)
        self.bind("<FocusIn>", lambda _event: self.redraw())
        self.bind("<FocusOut>", lambda _event: self.redraw())
        self.bind("<Configure>", lambda _event: self.redraw())
        self.redraw()

    # -- public API ---------------------------------------------------------
    def get(self) -> bool:
        return self._value

    def set(self, value: bool, invoke: bool = False) -> bool:
        """Store ``value``; when ``invoke`` is true also notify ``command``."""
        self._value = bool(value)
        self.redraw()
        if invoke and self.command is not None:
            self.command(self._value)
        return self._value

    def toggle(self) -> bool:
        return self.set(not self._value, invoke=True)

    def set_palette(self, palette: dict) -> None:
        self.palette = coerce_palette(palette)
        self.redraw()

    # -- drawing ------------------------------------------------------------
    def redraw(self) -> None:
        width, height = self._canvas_size()
        self.delete("all")
        self.configure(background=self.palette["bg"])

        inset = 3.0
        x0, y0 = inset, inset
        x1, y1 = width - inset, height - inset
        radius = (y1 - y0) / 2
        track_fill = self.palette["accent"] if self._value else self.palette["grid"]

        if self._has_focus():
            self.create_polygon(
                rounded_rectangle_points(0.5, 0.5, width - 0.5, height - 0.5, radius + 2),
                smooth=True, splinesteps=18, fill="", outline=self.palette["accent"],
                width=1, tags=(self.FOCUS_RING_TAG,),
            )

        self.create_polygon(
            rounded_rectangle_points(x0, y0, x1, y1, radius),
            smooth=True, splinesteps=18, fill=track_fill, outline=track_fill,
            tags=("track", "on" if self._value else "off"),
        )

        padding = max(2.5, (y1 - y0) * 0.16)
        thumb_radius = max(2.0, (y1 - y0) / 2 - padding)
        center_y = (y0 + y1) / 2
        if self._value:
            center_x = x1 - padding - thumb_radius
            thumb_fill = self.palette["panel"]
            thumb_outline = self.palette["accent"]
        else:
            center_x = x0 + padding + thumb_radius
            thumb_fill = self.palette["muted"]
            thumb_outline = self.palette["grid"]
        self.create_oval(
            center_x - thumb_radius, center_y - thumb_radius,
            center_x + thumb_radius, center_y + thumb_radius,
            fill=thumb_fill, outline=thumb_outline, width=1, tags=("thumb",),
        )

    def _canvas_size(self) -> tuple[int, int]:
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1:
            width = self._option_int("width", self.DEFAULT_WIDTH)
        if height <= 1:
            height = self._option_int("height", self.DEFAULT_HEIGHT)
        return max(30, width), max(16, height)

    def _option_int(self, option: str, fallback: int) -> int:
        try:
            return int(float(self.cget(option)))
        except (tk.TclError, TypeError, ValueError):
            return fallback

    def _has_focus(self) -> bool:
        try:
            return self.focus_get() is self
        except (tk.TclError, KeyError):
            return False

    # -- events -------------------------------------------------------------
    def _on_click(self, _event=None) -> str:
        try:
            self.focus_set()
        except tk.TclError:
            pass
        self.toggle()
        return "break"

    def _on_key(self, event) -> str:
        if event.keysym in ("space", "Return"):
            self.toggle()
            return "break"
        return ""


class ScrollableFrame(ttk.Frame):
    """Vertically scrollable container whose contents live in ``.body``.

    The mouse wheel is hooked only on Windows and only while the pointer is over
    the widget; a wheel binding that existed before is restored on leave.
    """

    def __init__(self, master: tk.Misc, palette: dict | None = None,
                 on_view_change: Callable[[float, float], None] | None = None,
                 **kwargs):
        self.palette = coerce_palette(palette)
        self.on_view_change = on_view_change
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, background=self.palette["bg"], highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_yview_changed)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.body = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self._wheel_bound = False
        self._previous_wheel_script = ""
        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind("<Destroy>", self._on_destroy)
        if IS_WINDOWS:
            for widget in (self.canvas, self.body):
                widget.bind("<Enter>", self._on_pointer_enter, add="+")
                widget.bind("<Leave>", self._on_pointer_leave, add="+")

    # -- public API ---------------------------------------------------------
    def clear(self) -> None:
        """Destroy every widget placed in :attr:`body`."""
        for child in self.body.winfo_children():
            child.destroy()
        self.refresh_scrollregion()

    def refresh_scrollregion(self) -> None:
        self.canvas.update_idletasks()
        bbox = self.canvas.bbox("all")
        self.canvas.configure(scrollregion=bbox if bbox else (0, 0, 0, 0))

    def set_palette(self, palette: dict) -> None:
        self.palette = coerce_palette(palette)
        self.canvas.configure(background=self.palette["bg"])

    def _on_yview_changed(self, first: str, last: str) -> None:
        """Keep the scrollbar and an optional sticky-header observer in sync."""
        self.scrollbar.set(first, last)
        if self.on_view_change is None:
            return
        try:
            self.on_view_change(float(first), float(last))
        except (TypeError, ValueError, tk.TclError):
            pass

    # -- geometry -----------------------------------------------------------
    def _on_body_configure(self, event) -> None:
        if event.widget is self.body:
            self.refresh_scrollregion()

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._unbind_wheel()

    # -- wheel handling -----------------------------------------------------
    @property
    def wheel_bound(self) -> bool:
        return self._wheel_bound

    def _on_pointer_enter(self, _event=None) -> None:
        self._bind_wheel()

    def _on_pointer_leave(self, event) -> None:
        if event is not None and self._pointer_still_inside(event):
            return
        self._unbind_wheel()

    def _pointer_still_inside(self, event) -> bool:
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, KeyError, AttributeError):
            return False
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _bind_wheel(self) -> None:
        if not IS_WINDOWS or self._wheel_bound:
            return
        try:
            self._previous_wheel_script = str(self.tk.call("bind", "all", "<MouseWheel>"))
            self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        except tk.TclError:
            return
        self._wheel_bound = True

    def _unbind_wheel(self) -> None:
        if not self._wheel_bound:
            return
        try:
            self.tk.call("bind", "all", "<MouseWheel>", self._previous_wheel_script)
        except tk.TclError:
            pass
        self._wheel_bound = False

    def _content_overflows(self) -> bool:
        return self.body.winfo_reqheight() > self.canvas.winfo_height()

    def _on_mousewheel(self, event):
        if not self._wheel_bound or not self._content_overflows():
            return None
        delta = getattr(event, "delta", 0) or 0
        steps = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
        if steps:
            self.canvas.yview_scroll(steps, "units")
        return "break"


class InlineCalendar(ttk.Frame):
    """Inline month/year calendar; the caller decides where to place it."""

    STYLE_FRAME = "InlineCalendar.TFrame"
    STYLE_LABEL = "InlineCalendar.TLabel"
    STYLE_MUTED = "InlineCalendar.Muted.TLabel"
    STYLE_BUTTON = "InlineCalendar.TButton"
    STYLE_SELECTED = "InlineCalendar.Selected.TButton"
    STYLE_TITLE = "InlineCalendar.Title.TButton"

    def __init__(self, master: tk.Misc, selected: date,
                 on_select: Callable[[date], None] | None = None,
                 palette: dict | None = None, **kwargs):
        self.palette = coerce_palette(palette)
        self.selected = self._as_date(selected)
        self.on_select = on_select
        self.year = self.selected.year
        self.month = self.selected.month
        self.mode = "month"
        kwargs.setdefault("style", self.STYLE_FRAME)
        super().__init__(master, **kwargs)
        self._configure_styles()

        self.columnconfigure(0, weight=1)
        self.header = ttk.Frame(self, style=self.STYLE_FRAME)
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.columnconfigure(1, weight=1)
        self.prev_button = ttk.Button(self.header, text="‹", width=3, style=self.STYLE_BUTTON,
                                      command=self.previous)
        self.prev_button.grid(row=0, column=0, sticky="w")
        self.title_button = ttk.Button(self.header, text="", style=self.STYLE_TITLE,
                                       command=self.toggle_view)
        self.title_button.grid(row=0, column=1, sticky="ew", padx=4)
        self.next_button = ttk.Button(self.header, text="›", width=3, style=self.STYLE_BUTTON,
                                      command=self.next)
        self.next_button.grid(row=0, column=2, sticky="e")

        self.grid_frame = ttk.Frame(self, style=self.STYLE_FRAME)
        self.grid_frame.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self.day_buttons: dict[int, ttk.Button] = {}
        self.month_buttons: list[ttk.Button] = []
        self._draw()

    # -- public API ---------------------------------------------------------
    def set_date(self, selected: date) -> None:
        self.selected = self._as_date(selected)
        self.year = self.selected.year
        self.month = self.selected.month
        self._draw()

    def set_palette(self, palette: dict) -> None:
        self.palette = coerce_palette(palette)
        self._configure_styles()
        self._draw()

    def show_month(self) -> None:
        self.mode = "month"
        self._draw()

    def show_year(self) -> None:
        self.mode = "year"
        self._draw()

    def toggle_view(self) -> None:
        self.show_month() if self.mode == "year" else self.show_year()

    def previous(self) -> None:
        self._shift(-1)

    def next(self) -> None:
        self._shift(1)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _as_date(value: date) -> date:
        if isinstance(value, datetime):
            return value.date()
        return value

    def _shift(self, delta: int) -> None:
        if self.mode == "year":
            self.year += delta
        else:
            index = self.year * 12 + (self.month - 1) + delta
            self.year, zero_based_month = divmod(index, 12)
            self.month = zero_based_month + 1
        self._draw()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        palette = self.palette
        style.configure(self.STYLE_FRAME, background=palette["bg"])
        style.configure(self.STYLE_LABEL, background=palette["bg"], foreground=palette["fg"])
        style.configure(self.STYLE_MUTED, background=palette["bg"], foreground=palette["muted"])
        style.configure(self.STYLE_BUTTON, background=palette["field"], foreground=palette["fg"],
                        padding=(4, 2))
        style.map(self.STYLE_BUTTON,
                  background=[("active", palette["grid_soft"])],
                  foreground=[("active", palette["fg"])])
        style.configure(self.STYLE_SELECTED, background=palette["accent"],
                        foreground=contrast_text(palette["accent"]), padding=(4, 2))
        style.map(self.STYLE_SELECTED,
                  background=[("active", palette["accent"])],
                  foreground=[("active", contrast_text(palette["accent"]))])
        style.configure(self.STYLE_TITLE, background=palette["bg"], foreground=palette["accent"],
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(4, 2))
        style.map(self.STYLE_TITLE,
                  background=[("active", palette["grid_soft"])],
                  foreground=[("active", palette["accent"])])

    def _draw(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.day_buttons = {}
        self.month_buttons = []
        self._reset_grid_weights()
        if self.mode == "year":
            self.title_button.configure(text=f"{self.year} 年")
            self._draw_year()
        else:
            self.title_button.configure(text=f"{self.year} 年 {self.month} 月")
            self._draw_month()

    def _reset_grid_weights(self) -> None:
        for column in range(7):
            self.grid_frame.columnconfigure(column, weight=0, uniform="")
        for row in range(7):
            self.grid_frame.rowconfigure(row, weight=0, uniform="")

    def _draw_month(self) -> None:
        for column, label in enumerate(WEEKDAY_LABELS):
            ttk.Label(self.grid_frame, text=label, style=self.STYLE_MUTED, anchor="center").grid(
                row=0, column=column, sticky="nsew", padx=1, pady=1)
            self.grid_frame.columnconfigure(column, weight=1, uniform="day")
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(self.year, self.month)
        for row_index, week in enumerate(weeks, start=1):
            for column, day in enumerate(week):
                if not day:
                    continue
                chosen = (self.selected.year == self.year and self.selected.month == self.month
                          and self.selected.day == day)
                button = ttk.Button(
                    self.grid_frame, text=str(day), width=4,
                    style=self.STYLE_SELECTED if chosen else self.STYLE_BUTTON,
                    command=lambda value=day: self._pick_day(value),
                )
                button.grid(row=row_index, column=column, sticky="nsew", padx=1, pady=1)
                self.day_buttons[day] = button

    def _draw_year(self) -> None:
        for index in range(12):
            month = index + 1
            chosen = self.year == self.selected.year and month == self.selected.month
            button = ttk.Button(
                self.grid_frame, text=f"{month} 月", width=6,
                style=self.STYLE_SELECTED if chosen else self.STYLE_BUTTON,
                command=lambda value=month: self._pick_month(value),
            )
            button.grid(row=index // 3, column=index % 3, sticky="nsew", padx=2, pady=2)
            self.month_buttons.append(button)
        for column in range(3):
            self.grid_frame.columnconfigure(column, weight=1, uniform="month")
        for row in range(4):
            self.grid_frame.rowconfigure(row, weight=1)

    def _pick_day(self, day: int) -> None:
        value = date(self.year, self.month, day)
        self.selected = value
        self._draw()
        if self.on_select is not None:
            self.on_select(value)

    def _pick_month(self, month: int) -> None:
        self.month = month
        self.show_month()
