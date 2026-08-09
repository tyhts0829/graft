from grafix import E, G, L, primitive, run

# A5
CANVAS_WIDTH = 148
CANVAS_HEIGHT = 210


def draw(t: float):
    g = G.delaunay()
    return g


if __name__ == "__main__":
    run(
        draw,
        render_scale=3.5,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
    )
