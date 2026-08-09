from grafix import E, G, L, primitive, run

# A5
CANVAS_WIDTH = 148
CANVAS_HEIGHT = 210


def draw(t: float):
    g = G.delaunay()
    e = E.affine().select().select().select().select()
    g = e(g)
    return g


if __name__ == "__main__":
    run(
        draw,
        render_scale=6,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
    )
