from grafix import E, G, run

# A4
CANVAS_WIDTH = 210
CANVAS_HEIGHT = 297


def draw(t):
    poly = G.polygon(phase=45)
    e = E.relax()
    return e(poly)


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0),
        line_thickness=0.001,
        line_color=(0.0, 0.0, 0.0),
        render_scale=3.5,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
        parameter_gui=True,
        midi_port_name="Grid",
        midi_mode="14bit",
    )
