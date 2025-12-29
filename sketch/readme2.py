from grafix import E, G, L, run


def draw(t):
    square = G.polygon()
    text1 = G.text()
    text2 = G.text()
    text_fill = E(name="text_fill").fill()
    return L((square, text_fill(text1 + text2)))


if __name__ == "__main__":
    run(
        draw,
        canvas_size=(148, 210),
        render_scale=4,
    )
