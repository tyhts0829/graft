from __future__ import annotations

from grafix.api import G
from grafix.core.realize import realize


def test_text_composite_glyph_google_sans_f_is_not_empty() -> None:
    realized = realize(G.text(text="f", font="GoogleSans-Regular.ttf", scale=10.0))
    assert realized.coords.shape[0] > 0


def test_text_composite_glyph_google_sans_eacute_is_not_empty() -> None:
    realized = realize(G.text(text="é", font="GoogleSans-Regular.ttf", scale=10.0))
    assert realized.coords.shape[0] > 0


def test_text_simple_glyph_google_sans_i_is_not_empty() -> None:
    realized = realize(G.text(text="i", font="GoogleSans-Regular.ttf", scale=10.0))
    assert realized.coords.shape[0] > 0

