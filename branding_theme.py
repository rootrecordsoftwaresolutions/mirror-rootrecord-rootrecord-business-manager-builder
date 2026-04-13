"""Derive UI colors from build/branding/Loading.* (same asset as the splash screen)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

LOADING_NAMES = (
    "Loading.png",
    "Loading.jpg",
    "Loading.jpeg",
    "Loading.bmp",
    "Loading.webp",
)


def iter_loading_image_paths() -> list[Path]:
    """Search order matches startup splash / PyInstaller branding folder."""
    out: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for name in LOADING_NAMES:
            out.append(Path(meipass) / "branding" / name)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        for name in LOADING_NAMES:
            out.append(exe_dir / "branding" / name)
    here = Path(__file__).resolve().parent
    for name in LOADING_NAMES:
        out.append(here / "build" / "branding" / name)
        out.append(here / "branding" / name)
    return out


def first_loading_image_path() -> Path | None:
    for p in iter_loading_image_paths():
        if p.is_file():
            return p
    return None


def _hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _parse_hex(h: str) -> tuple[int, int, int]:
    s = h.strip().lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _mix_rgb(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (x / 255.0 for x in rgb)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _saturation(rgb: tuple[int, int, int]) -> float:
    r, g, b = (x / 255.0 for x in rgb)
    mx = max(r, g, b)
    mn = min(r, g, b)
    if mx <= 1e-6:
        return 0.0
    return (mx - mn) / mx


@dataclass(frozen=True)
class BrandingPalette:
    """Colors for CTk chrome, typography, and dashboard charts (dark mode)."""

    bg: str
    sidebar: str
    panel: str
    nav_idle: str
    nav_active: str
    hover: str
    accent: str
    text_muted: str
    text_secondary: str
    text_heading: str
    help_icon_bg: str
    help_icon_fg: str
    border_subtle: str
    activation_bar: str
    billing_hero_sub: str
    billing_card_title: str
    billing_row_label: str
    billing_hint: str
    chart_bg: str
    chart_fg: str
    chart_edge: str
    splash_label: str

    def bg_rgb(self) -> tuple[int, int, int]:
        return _parse_hex(self.bg)


# Legacy defaults (before Loading.* or if extraction fails).
_DEFAULT_BG = "#070d14"
_DEFAULT_ACCENT = "#2ea7b8"
_LEGACY = BrandingPalette(
    bg=_DEFAULT_BG,
    sidebar="#0d1622",
    panel="#111c29",
    nav_idle="#142131",
    nav_active="#1f3448",
    hover="#28445f",
    accent=_DEFAULT_ACCENT,
    text_muted="#8b95a5",
    text_secondary="#a8b8cc",
    text_heading="#e8eef5",
    help_icon_bg="#5d636d",
    help_icon_fg="#f2f4f7",
    border_subtle="#2a3d52",
    activation_bar="#121c28",
    billing_hero_sub="#b8c5d6",
    billing_card_title="#c5d0e0",
    billing_row_label="#9aaaba",
    billing_hint="#7d8fa3",
    chart_bg="#0d1723",
    chart_fg="#d9e5f4",
    chart_edge="#2f445b",
    splash_label="#8fa4bc",
)


def _derive_palette_from_colors(sample_rgb: list[tuple[int, int, int]]) -> BrandingPalette:
    """Build a coherent dark UI from representative RGB samples (e.g. quantized palette)."""
    w = (255, 255, 255)
    if not sample_rgb:
        return _LEGACY

    bg_rgb = min(sample_rgb, key=_luminance)
    lum_floor = max(0.04, _luminance(bg_rgb))
    # Prefer saturated mid tones for accent (brand color on the art).
    pool = [c for c in sample_rgb if 0.08 < _luminance(c) < 0.93]
    if not pool:
        pool = list(sample_rgb)

    def accent_score(c: tuple[int, int, int]) -> float:
        sat = _saturation(c)
        lum = _luminance(c)
        return sat * (1.0 - abs(lum - 0.42))

    accent_rgb = max(pool, key=accent_score)
    if _saturation(accent_rgb) < 0.14:
        accent_rgb = _mix_rgb(bg_rgb, _parse_hex(_DEFAULT_ACCENT), 0.5)
    if _luminance(accent_rgb) < 0.12:
        accent_rgb = _mix_rgb(accent_rgb, w, 0.25)

    bg = _hex(bg_rgb)
    accent = _hex(accent_rgb)

    sidebar = _hex(_mix_rgb(bg_rgb, w, 0.05))
    panel = _hex(_mix_rgb(bg_rgb, w, 0.1))
    nav_idle = _hex(_mix_rgb(bg_rgb, w, 0.13))
    nav_active = _hex(_mix_rgb(_mix_rgb(bg_rgb, accent_rgb, 0.3), w, 0.1))
    hover = _hex(_mix_rgb(_parse_hex(nav_active), w, 0.12))

    text_heading = _hex(_mix_rgb(bg_rgb, w, 0.93))
    text_muted = _hex(_mix_rgb(_mix_rgb(bg_rgb, accent_rgb, 0.1), w, 0.52))
    text_secondary = _hex(_mix_rgb(_mix_rgb(bg_rgb, accent_rgb, 0.12), w, 0.48))

    help_icon_bg = _hex(_mix_rgb(_parse_hex(panel), w, 0.26))
    help_icon_fg = text_heading

    border_subtle = _hex(_mix_rgb(_parse_hex(nav_active), accent_rgb, 0.18))
    activation_bar = _hex(_mix_rgb(_parse_hex(panel), accent_rgb, 0.06))

    billing_hero_sub = _hex(_mix_rgb(_parse_hex(text_secondary), accent_rgb, 0.08))
    billing_card_title = _hex(_mix_rgb(_parse_hex(text_heading), accent_rgb, 0.06))
    billing_row_label = text_secondary
    billing_hint = _hex(_mix_rgb(_parse_hex(text_muted), accent_rgb, 0.15))

    chart_bg = _hex(_mix_rgb(bg_rgb, accent_rgb, 0.05))
    chart_fg = text_heading
    chart_edge = border_subtle
    splash_label = text_secondary

    return BrandingPalette(
        bg=bg,
        sidebar=sidebar,
        panel=panel,
        nav_idle=nav_idle,
        nav_active=nav_active,
        hover=hover,
        accent=accent,
        text_muted=text_muted,
        text_secondary=text_secondary,
        text_heading=text_heading,
        help_icon_bg=help_icon_bg,
        help_icon_fg=help_icon_fg,
        border_subtle=border_subtle,
        activation_bar=activation_bar,
        billing_hero_sub=billing_hero_sub,
        billing_card_title=billing_card_title,
        billing_row_label=billing_row_label,
        billing_hint=billing_hint,
        chart_bg=chart_bg,
        chart_fg=chart_fg,
        chart_edge=chart_edge,
        splash_label=splash_label,
    )


def load_palette_from_image(path: Path) -> BrandingPalette | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(path).convert("RGB")
        img = img.resize((96, 96), Image.Resampling.LANCZOS)
        q = img.quantize(colors=10).convert("RGB")
        raw = q.getcolors(maxcolors=96 * 96)
        if not raw:
            return None
        raw.sort(reverse=True, key=lambda t: t[0])
        sample = [t[1] for t in raw[:10]]
        return _derive_palette_from_colors(sample)
    except Exception:
        return None


_palette_cache: BrandingPalette | None = None


def get_branding_palette(*, force_reload: bool = False) -> BrandingPalette:
    """Load once: colors from Loading.* when present and readable, else legacy defaults."""
    global _palette_cache
    if _palette_cache is not None and not force_reload:
        return _palette_cache
    pal: BrandingPalette | None = None
    for pth in iter_loading_image_paths():
        if pth.is_file():
            pal = load_palette_from_image(pth)
            if pal is not None:
                break
    _palette_cache = pal if pal is not None else _LEGACY
    return _palette_cache


def reset_branding_palette_cache() -> None:
    """Test hook."""
    global _palette_cache
    _palette_cache = None
