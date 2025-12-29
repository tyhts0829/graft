from grafix import E, G, L, run


def draw(t):
    FONT = "Geist-medium"
    square = G.polygon()
    square_eff = E(name="square_eff").buffer().fill()
    square = square_eff(square)
    text1 = G.text(
        font=FONT,
        text="GRA",
        text_align="center",
        letter_spacing_em=0.574,
        center=(50, 36, 0),
        scale=15,
    )
    text2 = G.text(
        font=FONT,
        text="FIX",
        text_align="center",
        letter_spacing_em=0.800,
        center=(50, 56, 0),
        scale=15,
    )
    text_fill = E(name="text_fill").fill()
    text = text_fill(text1 + text2)
    line = G(name="line").line()
    line_eff = E(name="line_eff").affine().dash().buffer().fill()
    line = line_eff(line)
    return L((square, text, line))


if __name__ == "__main__":
    run(
        draw,
        canvas_size=(100, 100),
        render_scale=8,
    )
