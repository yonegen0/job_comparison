"""Local visual system shared by the job-comparison Tk views."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from enum import Enum
import os
import tkinter as tk
from tkinter import font as tkfont, ttk
from typing import Callable


STYLE_ROOT = "App.TFrame"
STYLE_HEADER = "App.Header.TFrame"
STYLE_FOOTER = "App.Footer.TFrame"
STYLE_CARD_BODY = "App.CardBody.TFrame"
STYLE_TITLE = "App.Title.TLabel"
STYLE_SUBTITLE = "App.Subtitle.TLabel"
STYLE_HEADER_CAPTION = "App.HeaderCaption.TLabel"
STYLE_SECTION = "App.Section.TLabel"
STYLE_CARD_CAPTION = "App.CardCaption.TLabel"
STYLE_METRIC_LABEL = "App.MetricLabel.TLabel"
STYLE_METRIC_VALUE = "App.MetricValue.TLabel"
STYLE_COMPACT_METRIC_VALUE = "App.CompactMetricValue.TLabel"
STYLE_CARD_SUCCESS = "App.Card.Success.TLabel"
STYLE_CARD_WARNING = "App.Card.Warning.TLabel"
STYLE_CARD_ERROR = "App.Card.Error.TLabel"
STYLE_FILE = "App.File.TLabel"
STYLE_PRIMARY_BUTTON = "App.Primary.TButton"
STYLE_BUTTON = "App.TButton"
STYLE_CHECK = "App.TCheckbutton"
STYLE_NOTEBOOK = "App.TNotebook"
STYLE_NOTEBOOK_TAB = "App.TNotebook.Tab"
STYLE_STATUS = "App.Status.TLabel"
STYLE_SUCCESS = "App.Success.TLabel"
STYLE_WARNING = "App.Warning.TLabel"
STYLE_ERROR = "App.Error.TLabel"
STYLE_TREE = "App.Treeview"
STYLE_HEADER_RULE = "App.HeaderRule.TFrame"
STYLE_SECTION_ACCENT = "App.SectionAccent.TFrame"
STYLE_METRIC_ACCENT = "App.MetricAccent.TFrame"
STYLE_FOOTER_RULE = "App.FooterRule.TFrame"

BASE_TK_SCALING = 96 / 72
CARD_RADIUS = 12
CARD_BORDER = 1
ANTIALIAS_SAMPLES = 4


class VisualMode(Enum):
    MODERN = "modern"
    HIGH_CONTRAST = "high_contrast"
    NATIVE_FALLBACK = "native_fallback"


@dataclass(frozen=True)
class VisualPalette:
    app_background: str
    header_background: str
    surface: str
    border: str
    text: str
    muted: str
    accent: str
    accent_strong: str
    accent_soft: str
    success: str
    warning: str
    error: str


MODERN_PALETTE = VisualPalette(
    app_background="#F3F6FA",
    header_background="#EFF6FF",
    surface="#FFFFFF",
    border="#D6DEE8",
    text="#172033",
    muted="#5B6575",
    accent="#2563EB",
    accent_strong="#1D4ED8",
    accent_soft="#DBEAFE",
    success="#15803D",
    warning="#B45309",
    error="#B42318",
)


@dataclass(frozen=True)
class RoundedArtwork:
    radius: int
    border: int
    cut: int
    full_image: tk.PhotoImage
    corners: tuple[tk.PhotoImage, tk.PhotoImage, tk.PhotoImage, tk.PhotoImage]


def detect_high_contrast() -> bool | None:
    """Return True/False on Windows and None when detection is unavailable."""
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        class HighContrast(ctypes.Structure):
            _fields_ = (
                ("cbSize", wintypes.UINT),
                ("dwFlags", wintypes.DWORD),
                ("lpszDefaultScheme", wintypes.LPWSTR),
            )

        settings = HighContrast()
        settings.cbSize = ctypes.sizeof(settings)
        succeeded = ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            0x0042,
            settings.cbSize,
            ctypes.byref(settings),
            0,
        )
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        return None
    if not succeeded:
        return None
    return bool(settings.dwFlags & 0x00000001)


class RoundedCard(tk.Canvas):
    """A background-only rounded surface containing native ttk controls."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        padding: int,
        mode: VisualMode,
        palette: VisualPalette,
        artwork: RoundedArtwork | None,
    ) -> None:
        super().__init__(
            master,
            borderwidth=0,
            highlightthickness=0,
            takefocus=0,
            background=palette.app_background,
        )
        self._total_padding = padding
        self._mode = mode
        self._palette = palette
        self._artwork = artwork
        self._inset = self._content_inset()
        self.body = ttk.Frame(
            self,
            padding=max(0, padding - self._inset),
            style=STYLE_CARD_BODY,
        )
        self._body_window = self.create_window(
            self._inset,
            self._inset,
            anchor="nw",
            window=self.body,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.body.bind("<Configure>", self._sync_requested_size, add="+")
        self._sync_requested_size()

    def set_visual(
        self,
        mode: VisualMode,
        palette: VisualPalette,
        artwork: RoundedArtwork | None,
    ) -> None:
        self._mode = mode
        self._palette = palette
        self._artwork = artwork
        self._inset = self._content_inset()
        self.configure(background=palette.app_background)
        self.body.configure(padding=max(0, self._total_padding - self._inset))
        self._sync_requested_size()
        self._redraw()

    def _content_inset(self) -> int:
        if self._mode is not VisualMode.MODERN or self._artwork is None:
            return 1
        return max(self._artwork.border + 2, min(self._artwork.radius // 3, 6))

    def _sync_requested_size(self, event: tk.Event | None = None) -> None:
        del event
        requested_width = self.body.winfo_reqwidth() + 2 * self._inset
        requested_height = self.body.winfo_reqheight() + 2 * self._inset
        if int(float(self.cget("width"))) != requested_width:
            self.configure(width=requested_width)
        if int(float(self.cget("height"))) != requested_height:
            self.configure(height=requested_height)

    def _redraw(self, event: tk.Event | None = None) -> None:
        del event
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        self.delete("card_background")
        artwork = self._artwork
        if (
            self._mode is VisualMode.MODERN
            and artwork is not None
            and width >= 2 * artwork.cut
            and height >= 2 * artwork.cut
        ):
            self._draw_rounded_background(width, height, artwork)
        else:
            self.create_rectangle(
                0,
                0,
                width - 1,
                height - 1,
                fill=self._palette.surface,
                outline=self._palette.border,
                width=1,
                tags="card_background",
            )
        self.coords(self._body_window, self._inset, self._inset)
        self.itemconfigure(
            self._body_window,
            width=max(1, width - 2 * self._inset),
            height=max(1, height - 2 * self._inset),
        )
        self.tag_lower("card_background")

    def _draw_rounded_background(
        self,
        width: int,
        height: int,
        artwork: RoundedArtwork,
    ) -> None:
        cut = artwork.cut
        border = artwork.border
        surface = self._palette.surface
        border_color = self._palette.border
        self.create_rectangle(
            cut, 0, width - cut, height, fill=surface, outline="", tags="card_background"
        )
        self.create_rectangle(
            0, cut, width, height - cut, fill=surface, outline="", tags="card_background"
        )
        for coordinates in (
            (cut, 0, width - cut, border),
            (cut, height - border, width - cut, height),
            (0, cut, border, height - cut),
            (width - border, cut, width, height - cut),
        ):
            self.create_rectangle(
                *coordinates,
                fill=border_color,
                outline="",
                tags="card_background",
            )
        positions = (
            (0, 0),
            (width - cut, 0),
            (0, height - cut),
            (width - cut, height - cut),
        )
        for image, position in zip(artwork.corners, positions, strict=True):
            self.create_image(
                *position,
                anchor="nw",
                image=image,
                tags="card_background",
            )


class VisualManager:
    """Configure and refresh the application-owned Tk visual system."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.root = root
        self.style = ttk.Style(root)
        self.on_change = on_change
        self.mode = VisualMode.NATIVE_FALLBACK
        self.palette = MODERN_PALETTE
        self.fonts: dict[str, tkfont.Font] = {}
        self.cards: list[RoundedCard] = []
        self._artworks: dict[tuple[int, int, VisualPalette], RoundedArtwork] = {}
        self._image_refs: dict[tuple[int, int, VisualPalette], tuple[tk.PhotoImage, ...]] = {}
        self.rounded_artwork: RoundedArtwork | None = None
        self.rounded_metrics = (0, 0, 0)
        self.rounded_artwork_ready = False
        self._vista_active = False
        self._signature: tuple[VisualMode, float] | None = None
        self._refresh_id: str | None = None
        self._base_primary_foreground = ""
        self._base_primary_map: list[tuple[str, str]] = []
        self._base_tab_foreground = ""
        self._base_tab_map: list[tuple[str, str]] = []
        self._configure()
        # PhotoImage finalizers must run while the owning Tcl interpreter is
        # still alive.  Clearing the references during root destruction also
        # keeps repeated GUI test/application lifecycles reliable on Windows.
        self.root.bind("<Destroy>", self._release_images, add="+")

    def create_card(self, parent: tk.Misc, *, padding: int) -> RoundedCard:
        card = RoundedCard(
            parent,
            padding=padding,
            mode=self.mode,
            palette=self.palette,
            artwork=self.rounded_artwork,
        )
        self.cards.append(card)
        return card

    def tk_scaling(self) -> float:
        try:
            scaling = float(self.root.tk.call("tk", "scaling"))
        except (tk.TclError, TypeError, ValueError):
            return BASE_TK_SCALING
        return scaling if scaling > 0 else BASE_TK_SCALING

    def scaled_px(self, value: int) -> int:
        return max(1, round(value * self.tk_scaling() / BASE_TK_SCALING))

    def schedule_refresh(self, event: tk.Event | None = None) -> None:
        del event
        if self._refresh_id is not None:
            return
        try:
            self._refresh_id = self.root.after_idle(self.refresh)
        except tk.TclError:
            self._refresh_id = None

    def _release_images(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        for card in self.cards:
            card._artwork = None
        self.rounded_artwork = None
        self._artworks.clear()
        self._image_refs.clear()

    def refresh(self) -> None:
        self._refresh_id = None
        mode, palette = self._resolve_mode()
        signature = (mode, self.tk_scaling())
        if signature == self._signature:
            return
        self._apply(mode, palette)
        self._signature = signature
        if self.on_change:
            self.on_change()

    def tone_color(self, tone: str) -> str:
        if self.mode is not VisualMode.MODERN:
            return self.palette.text
        return {
            "success": self.palette.success,
            "warning": self.palette.warning,
            "error": self.palette.error,
        }.get(tone, self.palette.text)

    def _configure(self) -> None:
        try:
            if "vista" in self.style.theme_names():
                self.style.theme_use("vista")
                self._vista_active = self.style.theme_use() == "vista"
        except tk.TclError:
            self._vista_active = False

        self._base_primary_foreground = str(self.style.lookup("TButton", "foreground"))
        self._base_primary_map = list(self.style.map("TButton", "foreground"))
        self._base_tab_foreground = str(self.style.lookup("TNotebook.Tab", "foreground"))
        self._base_tab_map = list(self.style.map("TNotebook.Tab", "foreground"))

        default_font = tkfont.nametofont("TkDefaultFont")
        family = str(default_font.cget("family"))
        self.fonts = {
            "title": tkfont.Font(self.root, family=family, size=22, weight="bold"),
            "dialog_title": tkfont.Font(self.root, family=family, size=15, weight="bold"),
            "section": tkfont.Font(self.root, family=family, size=11, weight="bold"),
            "metric": tkfont.Font(self.root, family=family, size=16, weight="bold"),
            "body": tkfont.Font(self.root, family=family, size=10),
            "body_bold": tkfont.Font(self.root, family=family, size=10, weight="bold"),
            "caption": tkfont.Font(self.root, family=family, size=9),
        }
        self._define_styles()
        mode, palette = self._resolve_mode()
        self._apply(mode, palette)
        self._signature = (mode, self.tk_scaling())

    def _define_styles(self) -> None:
        for name in (STYLE_ROOT, STYLE_HEADER, STYLE_FOOTER, STYLE_CARD_BODY):
            self.style.configure(name)
        self.style.configure(STYLE_TITLE, font=self.fonts["title"])
        self.style.configure(STYLE_SUBTITLE, font=self.fonts["body"])
        self.style.configure(STYLE_HEADER_CAPTION, font=self.fonts["caption"])
        self.style.configure(STYLE_SECTION, font=self.fonts["section"])
        self.style.configure(STYLE_CARD_CAPTION, font=self.fonts["caption"])
        self.style.configure(STYLE_METRIC_LABEL, font=self.fonts["caption"])
        self.style.configure(STYLE_METRIC_VALUE, font=self.fonts["metric"])
        self.style.configure(STYLE_COMPACT_METRIC_VALUE, font=self.fonts["body_bold"])
        for name in (STYLE_CARD_SUCCESS, STYLE_CARD_WARNING, STYLE_CARD_ERROR):
            self.style.configure(name, font=self.fonts["body_bold"])
        self.style.configure(STYLE_FILE, font=self.fonts["body_bold"])
        self.style.configure(
            STYLE_PRIMARY_BUTTON,
            font=self.fonts["body_bold"],
            padding=(8, 2),
        )
        self.style.configure(STYLE_BUTTON, font=self.fonts["body"], padding=(8, 2))
        self.style.configure(STYLE_CHECK, font=self.fonts["body"])
        self.style.configure(STYLE_NOTEBOOK, tabmargins=(0, 0, 0, 0))
        self.style.configure(
            STYLE_NOTEBOOK_TAB,
            font=self.fonts["body_bold"],
            padding=(18, 8),
        )
        for name in (STYLE_STATUS, STYLE_SUCCESS, STYLE_WARNING, STYLE_ERROR):
            self.style.configure(name, font=self.fonts["body"])
        self.style.configure(
            STYLE_TREE,
            font=self.fonts["body"],
            rowheight=self.scaled_px(24),
        )
        self.style.configure(
            f"{STYLE_TREE}.Heading",
            font=self.fonts["body_bold"],
        )
        for name in (
            STYLE_HEADER_RULE,
            STYLE_SECTION_ACCENT,
            STYLE_METRIC_ACCENT,
            STYLE_FOOTER_RULE,
        ):
            self.style.configure(name)

    def _resolve_mode(self) -> tuple[VisualMode, VisualPalette]:
        high_contrast = detect_high_contrast()
        if high_contrast is True:
            system = self._system_palette()
            if system is not None:
                return VisualMode.HIGH_CONTRAST, system
            return VisualMode.NATIVE_FALLBACK, self._native_palette()
        if high_contrast is False and self._vista_active:
            return VisualMode.MODERN, MODERN_PALETTE
        return VisualMode.NATIVE_FALLBACK, self._native_palette()

    def _system_palette(self) -> VisualPalette | None:
        values = {
            "app_background": "SystemButtonFace",
            "header_background": "SystemButtonFace",
            "surface": "SystemWindow",
            "border": "SystemWindowText",
            "text": "SystemWindowText",
            "muted": "SystemGrayText",
            "accent": "SystemHighlight",
            "accent_strong": "SystemHighlight",
            "accent_soft": "SystemWindow",
            "success": "SystemHighlight",
            "warning": "SystemHighlight",
            "error": "SystemHighlight",
        }
        try:
            for color in set(values.values()):
                self.root.winfo_rgb(color)
        except tk.TclError:
            return None
        return VisualPalette(**values)

    def _native_palette(self) -> VisualPalette:
        background = str(self.style.lookup("TFrame", "background")) or str(
            self.root.cget("background")
        )
        text = str(self.style.lookup("TLabel", "foreground")) or self._base_primary_foreground
        text = text or "SystemWindowText"
        return VisualPalette(
            app_background=background,
            header_background=background,
            surface=background,
            border=background,
            text=text,
            muted=text,
            accent=text,
            accent_strong=text,
            accent_soft=background,
            success=text,
            warning=text,
            error=text,
        )

    def _apply(self, mode: VisualMode, palette: VisualPalette) -> None:
        self.mode = mode
        self.palette = palette
        self.root.configure(background=palette.app_background)
        for name in (STYLE_ROOT, STYLE_FOOTER):
            self.style.configure(name, background=palette.app_background)
        self.style.configure(STYLE_HEADER, background=palette.header_background)
        self.style.configure(STYLE_CARD_BODY, background=palette.surface)
        for name in (STYLE_TITLE, STYLE_SUBTITLE):
            self.style.configure(name, background=palette.header_background, foreground=palette.text)
        self.style.configure(
            STYLE_HEADER_CAPTION,
            background=palette.header_background,
            foreground=palette.muted,
        )
        self.style.configure(STYLE_SECTION, background=palette.surface, foreground=palette.text)
        self.style.configure(
            STYLE_CARD_CAPTION,
            background=palette.surface,
            foreground=palette.muted,
        )
        self.style.configure(
            STYLE_METRIC_LABEL,
            background=palette.surface,
            foreground=palette.muted,
        )
        for name in (STYLE_METRIC_VALUE, STYLE_FILE):
            self.style.configure(name, background=palette.surface, foreground=palette.text)
        self.style.configure(
            STYLE_COMPACT_METRIC_VALUE,
            background=palette.surface,
            foreground=palette.text,
        )
        self.style.configure(STYLE_CARD_SUCCESS, background=palette.surface, foreground=palette.success)
        self.style.configure(STYLE_CARD_WARNING, background=palette.surface, foreground=palette.warning)
        self.style.configure(STYLE_CARD_ERROR, background=palette.surface, foreground=palette.error)
        self.style.configure(STYLE_CHECK, background=palette.surface)
        self.style.configure(STYLE_NOTEBOOK, background=palette.app_background)
        self.style.configure(STYLE_NOTEBOOK_TAB, foreground=palette.text)
        self.style.configure(STYLE_STATUS, background=palette.app_background, foreground=palette.text)
        self.style.configure(STYLE_SUCCESS, background=palette.app_background, foreground=palette.success)
        self.style.configure(STYLE_WARNING, background=palette.app_background, foreground=palette.warning)
        self.style.configure(STYLE_ERROR, background=palette.app_background, foreground=palette.error)
        self.style.configure(
            STYLE_TREE,
            background=palette.surface,
            fieldbackground=palette.surface,
            foreground=palette.text,
            rowheight=self.scaled_px(24),
        )
        self.style.configure(STYLE_HEADER_RULE, background=palette.accent)
        self.style.configure(STYLE_SECTION_ACCENT, background=palette.accent)
        self.style.configure(STYLE_METRIC_ACCENT, background=palette.accent)
        self.style.configure(STYLE_FOOTER_RULE, background=palette.border)

        if mode is VisualMode.MODERN:
            disabled = str(self.style.lookup("TButton", "foreground", ("disabled",)))
            self.style.configure(STYLE_PRIMARY_BUTTON, foreground=palette.accent)
            self.style.map(
                STYLE_PRIMARY_BUTTON,
                foreground=[
                    ("disabled", disabled),
                    ("pressed", palette.accent_strong),
                    ("active", palette.accent_strong),
                ],
            )
            self.style.map(
                STYLE_NOTEBOOK_TAB,
                foreground=[("disabled", disabled), ("selected", palette.accent)],
            )
            self.rounded_artwork = self._ensure_artwork()
        else:
            self.style.configure(STYLE_PRIMARY_BUTTON, foreground=self._base_primary_foreground)
            self.style.map(STYLE_PRIMARY_BUTTON, foreground=self._base_primary_map)
            self.style.configure(STYLE_NOTEBOOK_TAB, foreground=self._base_tab_foreground)
            self.style.map(STYLE_NOTEBOOK_TAB, foreground=self._base_tab_map)
            self.rounded_artwork = None

        active_cards: list[RoundedCard] = []
        for card in self.cards:
            if card.winfo_exists():
                card.set_visual(mode, palette, self.rounded_artwork)
                active_cards.append(card)
        self.cards = active_cards

    def _ensure_artwork(self) -> RoundedArtwork | None:
        radius = self.scaled_px(CARD_RADIUS)
        border = self.scaled_px(CARD_BORDER)
        side = 2 * (radius + border) + 3
        self.rounded_metrics = (radius, border, side)
        key = (radius, border, self.palette)
        if key in self._artworks:
            self.rounded_artwork_ready = True
            return self._artworks[key]
        try:
            full = self._render_image(radius, border, side)
            cut = radius + border
            regions = (
                (0, 0, cut, cut),
                (side - cut, 0, side, cut),
                (0, side - cut, cut, side),
                (side - cut, side - cut, side, side),
            )
            corners = tuple(self._copy_region(full, region) for region in regions)
        except tk.TclError:
            self.rounded_artwork_ready = False
            return None
        artwork = RoundedArtwork(radius, border, cut, full, corners)
        self._artworks[key] = artwork
        self._image_refs[key] = (full, *corners)
        self.rounded_artwork_ready = True
        return artwork

    def _copy_region(
        self,
        source: tk.PhotoImage,
        region: tuple[int, int, int, int],
    ) -> tk.PhotoImage:
        left, top, right, bottom = region
        image = tk.PhotoImage(master=self.root, width=right - left, height=bottom - top)
        image.tk.call(str(image), "copy", str(source), "-from", left, top, right, bottom)
        return image

    def _render_image(self, radius: int, border: int, side: int) -> tk.PhotoImage:
        colors = {
            "outside": _hex_to_rgb(self.palette.app_background),
            "border": _hex_to_rgb(self.palette.border),
            "inside": _hex_to_rgb(self.palette.surface),
        }
        rows: list[tuple[str, ...]] = []
        for y in range(side):
            row: list[str] = []
            for x in range(side):
                totals = [0, 0, 0]
                for sample_y in range(ANTIALIAS_SAMPLES):
                    point_y = y + (sample_y + 0.5) / ANTIALIAS_SAMPLES
                    for sample_x in range(ANTIALIAS_SAMPLES):
                        point_x = x + (sample_x + 0.5) / ANTIALIAS_SAMPLES
                        if _inside_rounded_square(point_x, point_y, side, border, radius - border):
                            color = colors["inside"]
                        elif _inside_rounded_square(point_x, point_y, side, 0, radius):
                            color = colors["border"]
                        else:
                            color = colors["outside"]
                        for channel in range(3):
                            totals[channel] += color[channel]
                divisor = ANTIALIAS_SAMPLES**2
                row.append(
                    "#{:02X}{:02X}{:02X}".format(
                        *(round(total / divisor) for total in totals)
                    )
                )
            rows.append(tuple(row))
        image = tk.PhotoImage(master=self.root, width=side, height=side)
        image.put(tuple(rows))
        return image


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    normalized = color.removeprefix("#")
    if len(normalized) != 6:
        raise ValueError(f"expected a six-digit RGB color, got {color!r}")
    return tuple(int(normalized[index : index + 2], 16) for index in (0, 2, 4))


def _inside_rounded_square(
    x: float,
    y: float,
    side: int,
    inset: int,
    radius: int,
) -> bool:
    left = float(inset)
    top = float(inset)
    right = float(side - inset)
    bottom = float(side - inset)
    if x < left or y < top or x >= right or y >= bottom:
        return False
    effective_radius = max(0.0, min(float(radius), (right - left) / 2))
    if effective_radius == 0:
        return True
    center_x = min(max(x, left + effective_radius), right - effective_radius)
    center_y = min(max(y, top + effective_radius), bottom - effective_radius)
    return (x - center_x) ** 2 + (y - center_y) ** 2 <= effective_radius**2


__all__ = [
    "MODERN_PALETTE",
    "RoundedCard",
    "STYLE_BUTTON",
    "STYLE_CARD_BODY",
    "STYLE_CARD_CAPTION",
    "STYLE_CARD_ERROR",
    "STYLE_CARD_SUCCESS",
    "STYLE_CARD_WARNING",
    "STYLE_CHECK",
    "STYLE_COMPACT_METRIC_VALUE",
    "STYLE_ERROR",
    "STYLE_FILE",
    "STYLE_FOOTER",
    "STYLE_FOOTER_RULE",
    "STYLE_HEADER",
    "STYLE_HEADER_CAPTION",
    "STYLE_HEADER_RULE",
    "STYLE_METRIC_ACCENT",
    "STYLE_METRIC_LABEL",
    "STYLE_METRIC_VALUE",
    "STYLE_NOTEBOOK",
    "STYLE_NOTEBOOK_TAB",
    "STYLE_PRIMARY_BUTTON",
    "STYLE_ROOT",
    "STYLE_SECTION",
    "STYLE_SECTION_ACCENT",
    "STYLE_STATUS",
    "STYLE_SUBTITLE",
    "STYLE_SUCCESS",
    "STYLE_TITLE",
    "STYLE_TREE",
    "STYLE_WARNING",
    "VisualManager",
    "VisualMode",
    "VisualPalette",
    "detect_high_contrast",
]
