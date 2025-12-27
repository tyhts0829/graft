from grafix import E, G, L, run

# A4
CANVAS_WIDTH = 210
CANVAS_HEIGHT = 297

# TEXTS
TITLE = "GRAFIX"
SUBTITLE = "line-based generative geometry,  python-native creative coding framework, modulate everything via effects pipeline"
FUNC_TEXT = "polyhedron()       fill()       displace()"
DESCRIPTION = """
    This framework approaches visual design with an audio mindset.
    A minimal, line-based geometry engine keeps the representation intentionally simple,
    treating constraints as a source of creativity rather than a limitation.
    Instead of hiding structure and styling decisions inside a black-box renderer,
    grafix keeps them close to your code: you build multi-layer sketches
    where each layer can carry its own color and line weight,echoing pen changes in a plotter.
    Effects are composed as method-chained processors,
    forming an effect chain that feels closer to a synth and pedalboard than a monolithic graphics API.
    MIDI control and LFO-driven modulation keep parameters in constant motion,
    making geometry something you can “play” rather than merely render.
    From real-time OpenGL preview to pen-plotter-ready G-code,
    grafix offers a continuous path from experimental patch to physical output,
    with new Shapes and Effects defined as lightweight Python decorators.
    The aim is not just to produce images, but to compose line-based scores that unfold in time,on screen and on paper.
"""
FOOTER = "PRIMITIVES | EFFECTS | LAYERS | MIDI | MODULATION | PARAMETER GUI | REAL-TIME RENDERING | PEN PLOTTING"


def draw(t: float):
    title = G(name="title").text(text=TITLE)
    title_effed = E(name="title_eff").fill()(title)
    subtitle = G(name="subtitle").text(text=SUBTITLE)
    subtitle_effed = E(name="subtitle_eff").fill()(subtitle)
    line = G.line()

    poly = G(name="poly").polyhedron()
    poly_affine = E(name="poly_affine").affine()
    poly_fill = E(name="poly_fill").translate().fill()
    poly_displace = E(name="poly_displace").translate().subdivide().displace()

    poly_affined = poly_affine(poly)
    poly_filled = poly_fill(poly_affined)
    poly_displaced = poly_displace(poly_filled)

    func_text = G(name="func_text").text(text=FUNC_TEXT)
    func_text_effed = E(name="func_text_eff").fill()(func_text)

    description = G(name="description").text(text=DESCRIPTION)
    description_effed = E(name="description_eff").fill()(description)

    footer_square = G.polygon()
    footer_squareed = E(name="fill_footer").fill().repeat()(footer_square)

    grid = G.grid()
    circle = G.text(font="Cappadocia.otf", text="o")
    filled_circle = E(name="fill_circle").fill()(circle)

    footer = G(name="footer").text(text=FOOTER)
    footer_effed = E(name="footer_eff").fill()(footer)

    return (
        (
            L(
                name="text",
                geometry_or_list=[
                    title_effed,
                    subtitle_effed,
                    func_text_effed,
                    description_effed,
                    footer_effed,
                ],
            ),
            L(
                name="shapes",
                geometry_or_list=[
                    poly_affine(poly_affined + poly_filled + poly_displaced),
                    line,
                ],
            ),
        ),
        L(
            name="grid_pattern",
            geometry_or_list=[grid, filled_circle],
        ),
        L(name="footer_pattern", geometry_or_list=footer),
    )


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0),
        line_thickness=0.001,
        line_color=(0.0, 0.0, 0.0),
        render_scale=3.5,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
        parameter_gui=True,
        # midi_port_name="TX-6 Bluetooth",
    )
