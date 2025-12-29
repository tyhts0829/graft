from grafix import E, G, L, run


def draw(t):
    square = G.polygon()
    square_eff = E(name="square_eff").buffer().fill()
    text1 = G.text()
    text2 = G.text()
    text_fill = E(name="text_fill").fill()
    circle = G(name="circle").polygon()
    circle_eff = E(name="circle_eff").affine().repeat()
    return L((square_eff(square), text_fill(text1 + text2), circle_eff(circle)))


if __name__ == "__main__":
    run(
        draw,
        canvas_size=(148, 210),
        render_scale=4,
    )
