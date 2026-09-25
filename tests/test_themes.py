"""ROLO-0015: themes and accent colours -- the palettes, not the widgets.

GTK-free. Checks each palette's promises directly: field colours stay distinguishable under
colourblindness simulations, text is readable on every surface it sits on, and any accent the
desktop hands over is made readable rather than trusted. Run with: pytest tests/
"""

import itertools
import math
import re

import pytest

import rolodex

# Machado, Oliveira & Fernandes (2009), severity 1.0, applied to linear RGB.
CVD = {
    "protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
                   (-0.003882, -0.048116, 1.051998)),
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
                     (-0.011820, 0.042940, 0.968881)),
    "tritanopia": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602),
                   (0.004733, 0.691367, 0.303900)),
}
# CIE76 distance every pair of field colours keeps, under each vision type. The palettes were
# tuned to at least 22; the margin keeps a small later nudge from failing on rounding.
MIN_DELTA_E = 20


def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _unlin(c):
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _simulate(color, matrix):
    lin = [_lin(c) for c in rolodex._hex_rgb(color)]
    if matrix is None:
        return rolodex._hex_rgb(color)
    return tuple(_unlin(sum(m * v for m, v in zip(row, lin))) for row in matrix)


def _lab(rgb):
    r, g, b = (_lin(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    return (116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z)))


@pytest.mark.parametrize("palette", list(rolodex.PALETTES))
@pytest.mark.parametrize("vision", [None, *CVD])
def test_field_colours_stay_apart_under_colourblindness(palette, vision):
    fields = rolodex.PALETTES[palette]["fields"]
    matrix = CVD.get(vision)
    seen = {k: _lab(_simulate(v, matrix)) for k, v in fields.items()}
    close = [(a, b, round(math.dist(seen[a], seen[b]), 1))
             for a, b in itertools.combinations(seen, 2)
             if math.dist(seen[a], seen[b]) < MIN_DELTA_E]
    assert close == []


@pytest.mark.parametrize("palette", list(rolodex.PALETTES))
def test_field_colours_are_visible_on_every_surface(palette):
    """WCAG 1.4.11: a non-text cue needs 3:1 against what it sits on."""
    p = rolodex.PALETTES[palette]
    low = [(k, s) for k, v in p["fields"].items() for s in p["surfaces"]
           if rolodex.contrast_ratio(v, s) < 3]
    assert low == []


@pytest.mark.parametrize("palette", list(rolodex.PALETTES))
def test_text_colours_are_readable_on_every_surface(palette):
    """WCAG 1.4.3: 4.5:1 for text. The Dark theme's timestamps and category headings sat at
    about 2.3:1 before ROLO-0015."""
    p = rolodex.PALETTES[palette]
    low = [(k, s) for k, v in p["text"].items() for s in p["surfaces"]
           if rolodex.contrast_ratio(v, s) < 4.5]
    assert low == []


# Every preset, plus colours a desktop could plausibly hand over, including the extremes.
ACCENTS = [*rolodex.ACCENT_PRESETS.values(), "#ffff00", "#e01b24", "#000000", "#ffffff",
           "#00ffff", "#1a1a40"]


@pytest.mark.parametrize("palette", list(rolodex.PALETTES))
@pytest.mark.parametrize("accent", ACCENTS)
def test_any_accent_is_made_readable(palette, accent):
    p = rolodex.PALETTES[palette]
    tokens = rolodex.accent_tokens(accent, p)
    assert all(rolodex.contrast_ratio(tokens["accent_text"], s) >= 4.5 for s in p["surfaces"])
    assert rolodex.contrast_ratio(tokens["accent_bg"], "#ffffff") >= 4.5


def test_every_colour_name_the_stylesheet_uses_is_defined_by_every_palette():
    """A name CUSTOM_CSS uses and a palette forgets does not fail loudly: GTK drops the rule,
    and the widget quietly falls back to libadwaita's default."""
    for name in rolodex.PALETTES:
        css = rolodex.theme_css(name, rolodex.FALLBACK_ACCENT)
        defined = set(re.findall(r"@define-color (rolo_\w+)", css))
        used = set(re.findall(r"@(rolo_\w+)", css.replace("@define-color ", "")))
        assert used - defined == set(), name


def test_every_palette_defines_the_same_names():
    shapes = {name: (set(p["colors"]), set(p["text"]), set(p["fields"]))
              for name, p in rolodex.PALETTES.items()}
    assert len({repr(sorted(map(sorted, s))) for s in shapes.values()}) == 1


def test_field_colour_keys_match_the_field_categories():
    for p in rolodex.PALETTES.values():
        assert set(p["fields"]) == {name for name, _ in rolodex.FIELD_CATEGORIES} | {"other"}


def test_css_custom_properties_are_left_out_for_old_gtk():
    """GTK before 4.16 cannot parse `--name: value`."""
    css = rolodex.theme_css("dark", rolodex.FALLBACK_ACCENT, css_vars=False)
    assert ":root" not in css and "--accent" not in css
    assert "--accent-bg-color" in rolodex.theme_css("dark", rolodex.FALLBACK_ACCENT)


def test_high_contrast_rules_come_after_the_shared_stylesheet():
    """Its thicker borders only win at equal weight if they are loaded last."""
    css = rolodex.theme_css("high-contrast", rolodex.FALLBACK_ACCENT)
    assert css.index(rolodex.CUSTOM_CSS) < css.index(rolodex.PALETTES["high-contrast"]["extra_css"])


@pytest.mark.parametrize("theme,system_dark,expected", [
    ("auto", True, "dark"), ("auto", False, "light"),
    ("dark", False, "dark"), ("light", True, "light"),
    ("high-contrast", False, "high-contrast"), ("high-contrast", True, "high-contrast"),
    ("neon", False, "dark"),
])
def test_resolve_palette(theme, system_dark, expected):
    assert rolodex.resolve_palette(theme, system_dark) == expected


@pytest.mark.parametrize("stored", [None, 5, "neon", "", ["light"]])
def test_a_bad_theme_in_the_config_falls_back_to_automatic(stored):
    conf = {} if stored is None else {rolodex.THEME_KEY: stored}
    assert rolodex.config_choice(conf, rolodex.THEME_KEY, rolodex.THEMES,
                                 rolodex.DEFAULT_THEME) == "auto"


def test_a_valid_theme_in_the_config_is_kept():
    conf = {rolodex.THEME_KEY: "high-contrast"}
    assert rolodex.config_choice(conf, rolodex.THEME_KEY, rolodex.THEMES,
                                 rolodex.DEFAULT_THEME) == "high-contrast"
