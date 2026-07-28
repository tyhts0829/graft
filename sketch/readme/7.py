from grafix import E, G, run

# A4
CANVAS_WIDTH = 210
CANVAS_HEIGHT = 297


def draw(t):
    g1 = G.grid()
    g2 = G.grid()
    e = E.subdivide()
    g1 = e(g1)
    g2 = e(g2)
    e1 = E.displace().rotate()
    e2 = E.displace()
    return e1(g1), e2(g2)


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0),
        line_thickness=0.001,
        line_color=(0.0, 0.0, 0.0),
        render_scale=3.5,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
        parameter_gui=True,
        # midi_port_name="Grid",
        # midi_mode="14bit",
        n_worker=4,
    )
