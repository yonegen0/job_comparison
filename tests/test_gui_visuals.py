from __future__ import annotations

import tkinter as tk
from unittest.mock import patch

import job_comparison_app.gui.visuals as visuals
from job_comparison_app.gui.view import TkView


def _contrast_ratio(first: str, second: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    brighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (brighter + 0.05) / (darker + 0.05)


def _layout_elements(layout) -> set[str]:
    elements: set[str] = set()
    for element, options in layout:
        elements.add(element)
        elements.update(_layout_elements(options.get("children", [])))
    return elements


def test_modern_palette_styles_cards_and_focus_layouts(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        with patch.object(visuals, "detect_high_contrast", return_value=False):
            view = TkView(root)
        root.update_idletasks()

        assert view.visuals.mode is visuals.VisualMode.MODERN
        assert view.visuals.palette == visuals.MODERN_PALETTE
        assert view.visuals.rounded_metrics == (12, 1, 29)
        assert view.visuals.rounded_artwork is not None
        assert len(view.visuals.cards) == 35
        for color in (
            visuals.MODERN_PALETTE.text,
            visuals.MODERN_PALETTE.muted,
            visuals.MODERN_PALETTE.accent,
            visuals.MODERN_PALETTE.success,
            visuals.MODERN_PALETTE.warning,
            visuals.MODERN_PALETTE.error,
        ):
            assert _contrast_ratio(color, visuals.MODERN_PALETTE.surface) >= 4.5
        for card in view.visuals.cards:
            assert str(card.cget("takefocus")) == "0"
            card._redraw()
            count = len(card.find_withtag("card_background"))
            card._redraw()
            assert len(card.find_withtag("card_background")) == count
        assert "Button.focus" in _layout_elements(view.style.layout(visuals.STYLE_PRIMARY_BUTTON))
        assert "Notebook.focus" in _layout_elements(view.style.layout(visuals.STYLE_NOTEBOOK_TAB))
        assert "Entry.field" in _layout_elements(view.style.layout("TEntry"))
    finally:
        root.destroy()


def test_high_contrast_and_native_fallback_use_rectangular_system_visuals(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        with patch.object(visuals, "detect_high_contrast", return_value=True):
            view = TkView(root)
        assert view.visuals.mode is visuals.VisualMode.HIGH_CONTRAST
        assert view.visuals.rounded_artwork is None
        assert all(not value.startswith("#") for value in view.visuals.palette.__dict__.values())
        assert all(card._artwork is None for card in view.visuals.cards)
        assert view.pdf_tree.tag_configure("warning")["foreground"] == "SystemWindowText"
    finally:
        root.destroy()

    root = tk.Toplevel(tk_window.master)
    root.withdraw()
    try:
        with patch.object(visuals, "detect_high_contrast", return_value=None):
            view = TkView(root)
        assert view.visuals.mode is visuals.VisualMode.NATIVE_FALLBACK
        assert view.visuals.rounded_artwork is None
    finally:
        root.destroy()


def test_artwork_failure_keeps_modern_view_operational(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        with (
            patch.object(visuals, "detect_high_contrast", return_value=False),
            patch.object(visuals.VisualManager, "_copy_region", side_effect=tk.TclError),
        ):
            view = TkView(root)
        assert view.visuals.mode is visuals.VisualMode.MODERN
        assert view.visuals.rounded_artwork is None
        assert not view.visuals.rounded_artwork_ready
        assert all(card._artwork is None for card in view.visuals.cards)
    finally:
        root.destroy()


def test_dpi_radius_scales_and_artwork_cache_does_not_grow(tk_window) -> None:
    original = float(tk_window.tk.call("tk", "scaling"))
    for scaling, expected_radius in ((2.0, 18), (8 / 3, 24)):
        root = tk.Toplevel(tk_window.master)
        try:
            root.tk.call("tk", "scaling", scaling)
            with patch.object(visuals, "detect_high_contrast", return_value=False):
                manager = visuals.VisualManager(root)
                assert manager.rounded_metrics[0] == expected_radius
                count = len(manager._artworks)
                manager._signature = None
                manager.refresh()
                assert len(manager._artworks) == count
        finally:
            root.tk.call("tk", "scaling", original)
            root.destroy()
