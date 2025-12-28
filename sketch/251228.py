from grafix import E, G, run

A5 = (148, 210)


def draw(t):
    t = G.text()
    e = E.partition().drop().fill(angle=(30, 60, 90), density=(0, 600, 600, 600))
    return e(t)


if __name__ == "__main__":
    run(
        draw,
        canvas_size=A5,
        render_scale=4,
    )
