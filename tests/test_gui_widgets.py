"""Focused tests for :mod:`gui_widgets`.

These tests only exercise the reusable widgets: no network, no file access.  A
real Tk window is created per test so geometry behaviour is actually validated;
the tests skip only when Tk has no usable display at all.
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest
from datetime import date, datetime
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gui_widgets  # noqa: E402


DARK = dict(gui_widgets.DEFAULT_PALETTE)

LIGHT = {
    "bg": "#edf2f7", "panel": "#ffffff", "field": "#f7f9fb", "fg": "#17212b",
    "muted": "#5f6d7a", "grid": "#b8c6d3", "grid_soft": "#dce5ec",
    "good": "#137333", "bad": "#b3261e", "accent": "#1769aa", "warning": "#b45f06",
}

REQUIRED_PALETTE_KEYS = {
    "bg", "panel", "field", "fg", "muted", "grid", "grid_soft",
    "good", "bad", "accent", "warning",
}


def descendants(widget: tk.Misc) -> list[tk.Misc]:
    found: list[tk.Misc] = []
    for child in widget.winfo_children():
        found.append(child)
        found.extend(descendants(child))
    return found


def labels_in(widget: tk.Misc) -> list[ttk.Label]:
    return [child for child in descendants(widget) if isinstance(child, ttk.Label)]


class TkWidgetTestCase(unittest.TestCase):
    """Base class that owns a real Tk root and always destroys it."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - only without a display
            raise unittest.SkipTest(f"Tk display unavailable: {exc}") from exc
        self.addCleanup(self._destroy_root)
        self.root.geometry("320x240+40+40")
        self.root.update_idletasks()

    def _destroy_root(self):
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def settle(self):
        self.root.update()


class PaletteContractTests(unittest.TestCase):
    def test_default_palette_defines_every_required_key(self):
        self.assertEqual(REQUIRED_PALETTE_KEYS - set(gui_widgets.DEFAULT_PALETTE), set())

    def test_partial_palette_is_completed_from_defaults(self):
        resolved = gui_widgets.coerce_palette({"accent": "#ff00aa"})
        self.assertEqual(resolved["accent"], "#ff00aa")
        self.assertEqual(resolved["bg"], gui_widgets.DEFAULT_PALETTE["bg"])


class ToggleSwitchTests(TkWidgetTestCase):
    def make_switch(self, **kwargs) -> gui_widgets.ToggleSwitch:
        switch = gui_widgets.ToggleSwitch(self.root, **kwargs)
        switch.pack()
        self.settle()
        return switch

    def track_fill(self, switch):
        return switch.itemcget(switch.find_withtag("track")[0], "fill")

    def thumb_box(self, switch):
        return switch.coords(switch.find_withtag("thumb")[0])

    def test_default_state_is_off_with_useful_size(self):
        switch = self.make_switch()
        self.assertIs(switch.get(), False)
        self.assertEqual(int(switch.cget("width")), 52)
        self.assertEqual(int(switch.cget("height")), 28)
        self.assertEqual(self.track_fill(switch), DARK["grid"])

    def test_click_toggles_and_reports_new_state(self):
        seen = []
        switch = self.make_switch(command=seen.append)
        switch.event_generate("<Button-1>", x=5, y=5)
        self.settle()
        self.assertIs(switch.get(), True)
        self.assertEqual(seen, [True])
        switch.event_generate("<Button-1>", x=5, y=5)
        self.settle()
        self.assertIs(switch.get(), False)
        self.assertEqual(seen, [True, False])

    def test_space_and_return_toggle_when_focused(self):
        seen = []
        switch = self.make_switch(command=seen.append)
        switch.focus_force()
        self.settle()
        switch.event_generate("<space>")
        self.settle()
        switch.event_generate("<Return>")
        self.settle()
        self.assertIs(switch.get(), False)
        self.assertEqual(seen, [True, False])

    def test_set_is_silent_unless_invoked(self):
        seen = []
        switch = self.make_switch(command=seen.append)
        switch.set(True)
        self.assertIs(switch.get(), True)
        self.assertEqual(seen, [])
        switch.set(False, invoke=True)
        self.assertIs(switch.get(), False)
        self.assertEqual(seen, [False])

    def test_set_without_command_is_safe(self):
        switch = self.make_switch()
        switch.set(True, invoke=True)
        self.assertIs(switch.get(), True)

    def test_on_and_off_states_are_visually_distinct(self):
        switch = self.make_switch()
        off_track, off_thumb, off_box = self.track_fill(switch), switch.itemcget(
            switch.find_withtag("thumb")[0], "fill"), self.thumb_box(switch)
        switch.set(True)
        self.settle()
        self.assertNotEqual(self.track_fill(switch), off_track)
        self.assertNotEqual(switch.itemcget(switch.find_withtag("thumb")[0], "fill"), off_thumb)
        self.assertGreater(self.thumb_box(switch)[0], off_box[0])

    def test_set_palette_repaints_canvas_and_track(self):
        switch = self.make_switch()
        switch.set_palette(LIGHT)
        self.settle()
        self.assertEqual(switch.cget("background"), LIGHT["bg"])
        self.assertEqual(self.track_fill(switch), LIGHT["grid"])
        switch.set(True)
        self.assertEqual(self.track_fill(switch), LIGHT["accent"])


class ScrollableFrameTests(TkWidgetTestCase):
    def make_scrollable(self, **kwargs) -> gui_widgets.ScrollableFrame:
        frame = gui_widgets.ScrollableFrame(self.root, **kwargs)
        frame.pack(fill="both", expand=True)
        self.settle()
        return frame

    def fill_body(self, frame, count=40):
        for index in range(count):
            ttk.Label(frame.body, text=f"row {index}").pack(anchor="w")
        frame.refresh_scrollregion()
        self.settle()

    def test_body_is_an_exposed_ttk_frame(self):
        frame = self.make_scrollable()
        self.assertIsInstance(frame.body, ttk.Frame)
        self.assertIs(frame.body.master, frame.canvas)

    def test_clear_destroys_body_children(self):
        frame = self.make_scrollable()
        self.fill_body(frame, count=5)
        self.assertEqual(len(frame.body.winfo_children()), 5)
        frame.clear()
        self.settle()
        self.assertEqual(frame.body.winfo_children(), [])
        ttk.Label(frame.body, text="fresh").pack(anchor="w")
        frame.refresh_scrollregion()
        self.settle()
        self.assertEqual(len(frame.body.winfo_children()), 1)

    def test_refresh_scrollregion_grows_with_content(self):
        frame = self.make_scrollable()
        frame.refresh_scrollregion()
        empty_region = [int(value) for value in frame.canvas.cget("scrollregion").split()]
        self.fill_body(frame)
        filled_region = [int(value) for value in frame.canvas.cget("scrollregion").split()]
        self.assertGreater(filled_region[3] - filled_region[1], empty_region[3] - empty_region[1])
        self.assertGreater(filled_region[3], frame.canvas.winfo_height())

    def test_canvas_background_follows_palette(self):
        frame = self.make_scrollable()
        self.assertEqual(frame.canvas.cget("background"), DARK["bg"])
        frame.set_palette(LIGHT)
        self.assertEqual(frame.canvas.cget("background"), LIGHT["bg"])

    def test_inner_window_width_matches_canvas(self):
        frame = self.make_scrollable()
        self.fill_body(frame, count=3)
        self.settle()
        self.assertGreater(frame.canvas.winfo_width(), 1)
        self.assertAlmostEqual(frame.body.winfo_width(), frame.canvas.winfo_width(), delta=1)

    def test_wheel_scrolls_while_pointer_is_over_on_windows(self):
        frame = self.make_scrollable()
        self.fill_body(frame)
        frame.canvas.event_generate("<Enter>")
        self.settle()
        self.assertTrue(frame.wheel_bound)
        before = frame.canvas.yview()
        frame.body.event_generate("<MouseWheel>", delta=-120)
        self.settle()
        self.assertGreater(frame.canvas.yview()[0], before[0])
        frame._unbind_wheel()
        resting = frame.canvas.yview()
        frame.body.event_generate("<MouseWheel>", delta=-120)
        self.settle()
        self.assertFalse(frame.wheel_bound)
        self.assertEqual(frame.canvas.yview(), resting)

    def test_unbind_restores_a_pre_existing_wheel_binding(self):
        calls = []
        frame = self.make_scrollable()
        self.fill_body(frame)
        self.root.bind_all("<MouseWheel>", lambda _event: calls.append("app"), add="+")
        frame.canvas.event_generate("<Enter>")
        self.settle()
        frame._unbind_wheel()
        frame.body.event_generate("<MouseWheel>", delta=-120)
        self.settle()
        self.assertEqual(calls, ["app"])

    def test_wheel_is_never_hooked_off_windows(self):
        with patch.object(gui_widgets, "IS_WINDOWS", False):
            frame = self.make_scrollable()
            self.fill_body(frame)
            frame.canvas.event_generate("<Enter>")
            self.settle()
            self.assertFalse(frame.wheel_bound)
            before = frame.canvas.yview()
            frame.body.event_generate("<MouseWheel>", delta=-120)
            self.settle()
            self.assertEqual(frame.canvas.yview(), before)


class InlineCalendarTests(TkWidgetTestCase):
    def make_calendar(self, selected=date(2026, 9, 21), on_select=None, **kwargs):
        calendar = gui_widgets.InlineCalendar(self.root, selected, on_select, **kwargs)
        calendar.pack()
        self.settle()
        return calendar

    def test_month_view_shows_monday_first_weekday_header(self):
        calendar = self.make_calendar()
        header = [label.cget("text") for label in labels_in(calendar.grid_frame)]
        self.assertEqual(header, list(gui_widgets.WEEKDAY_LABELS))

    def test_month_view_lists_days_and_title(self):
        calendar = self.make_calendar()
        self.assertEqual(calendar.mode, "month")
        self.assertEqual(sorted(calendar.day_buttons), list(range(1, 31)))
        self.assertEqual(calendar.title_button.cget("text"), "2026 年 9 月")
        self.assertEqual(calendar.day_buttons[21].cget("style"),
                         gui_widgets.InlineCalendar.STYLE_SELECTED)

    def test_title_click_toggles_year_view_and_back(self):
        calendar = self.make_calendar()
        calendar.title_button.invoke()
        self.settle()
        self.assertEqual(calendar.mode, "year")
        self.assertEqual(len(calendar.month_buttons), 12)
        self.assertEqual([button.cget("text") for button in calendar.month_buttons[:3]],
                         ["1 月", "2 月", "3 月"])
        self.assertEqual(calendar.title_button.cget("text"), "2026 年")
        calendar.title_button.invoke()
        self.settle()
        self.assertEqual(calendar.mode, "month")
        self.assertEqual(len(calendar.day_buttons), 30)

    def test_year_view_lays_out_twelve_months_in_three_columns(self):
        calendar = self.make_calendar()
        calendar.show_year()
        self.settle()
        positions = [button.grid_info()["column"] for button in calendar.month_buttons]
        self.assertEqual(positions, [0, 1, 2] * 4)
        rows = [int(button.grid_info()["row"]) for button in calendar.month_buttons]
        self.assertEqual(rows, [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3])

    def test_selecting_month_returns_to_month_view(self):
        calendar = self.make_calendar()
        calendar.show_year()
        self.settle()
        calendar.month_buttons[1].invoke()
        self.settle()
        self.assertEqual(calendar.mode, "month")
        self.assertEqual((calendar.year, calendar.month), (2026, 2))
        self.assertEqual(sorted(calendar.day_buttons), list(range(1, 29)))

    def test_selecting_day_invokes_callback(self):
        picked = []
        calendar = self.make_calendar(on_select=picked.append)
        calendar.day_buttons[30].invoke()
        self.settle()
        self.assertEqual(picked, [date(2026, 9, 30)])
        self.assertEqual(calendar.selected, date(2026, 9, 30))
        self.assertEqual(calendar.day_buttons[30].cget("style"),
                         gui_widgets.InlineCalendar.STYLE_SELECTED)

    def test_previous_and_next_cross_year_boundary(self):
        calendar = self.make_calendar(selected=date(2026, 1, 15))
        calendar.previous()
        self.settle()
        self.assertEqual((calendar.year, calendar.month), (2025, 12))
        calendar.next()
        calendar.next()
        self.settle()
        self.assertEqual((calendar.year, calendar.month), (2026, 2))

    def test_previous_and_next_move_years_in_year_view(self):
        calendar = self.make_calendar()
        calendar.show_year()
        calendar.previous()
        self.assertEqual(calendar.year, 2025)
        calendar.next()
        calendar.next()
        self.settle()
        self.assertEqual(calendar.year, 2027)
        self.assertEqual(calendar.mode, "year")

    def test_set_date_updates_visible_month(self):
        calendar = self.make_calendar()
        calendar.set_date(date(2024, 2, 29))
        self.settle()
        self.assertEqual((calendar.year, calendar.month), (2024, 2))
        self.assertEqual(sorted(calendar.day_buttons), list(range(1, 30)))
        self.assertEqual(calendar.title_button.cget("text"), "2024 年 2 月")

    def test_datetime_selection_argument_is_accepted(self):
        calendar = self.make_calendar(selected=datetime(2026, 9, 21, 8, 30))
        self.assertEqual(calendar.selected, date(2026, 9, 21))

    def test_set_palette_updates_styles(self):
        calendar = self.make_calendar()
        calendar.set_palette(LIGHT)
        self.settle()
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup(gui_widgets.InlineCalendar.STYLE_SELECTED, "background"),
                         LIGHT["accent"])
        self.assertEqual(style.lookup(gui_widgets.InlineCalendar.STYLE_FRAME, "background"),
                         LIGHT["bg"])
        self.assertEqual(style.lookup(gui_widgets.InlineCalendar.STYLE_MUTED, "foreground"),
                         LIGHT["muted"])

    def test_widget_stays_inline_and_manages_no_own_visibility(self):
        calendar = gui_widgets.InlineCalendar(self.root, date(2026, 9, 21), None)
        self.root.update_idletasks()
        self.assertIsInstance(calendar, ttk.Frame)
        self.assertNotIsInstance(calendar, tk.Toplevel)
        self.assertEqual(calendar.winfo_manager(), "")
        self.assertEqual([child for child in descendants(calendar) if isinstance(child, tk.Toplevel)], [])
        self.assertEqual(len(self.root.winfo_children()), 1)


if __name__ == "__main__":
    unittest.main()
