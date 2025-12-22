from grafix import E, G, L, run

# A4
CANVAS_WIDTH = 210
CANVAS_HEIGHT = 297

# TEXTS
TITLE = "GRAFIX"
SUBTITLE = "line-based generative geometry,  python-native creative coding framework, modulate everything via effects pipeline"
DESCRIPTION = """
    This framework approaches visual design with an audio mindset.
    A minimal, line-based geometry engine keeps the representation intentionally simple, treating constraints as a source of creativity rather than a limitation. Instead of hiding structure and styling decisions inside a black-box renderer, pyxidraw keeps them close to your code: you build multi-layer sketches where each layer can carry its own color and line weight, echoing pen changes in a plotter. Effects are composed as method-chained processors, forming an effect chain that feels closer to a synth and pedalboard than a monolithic graphics API. MIDI control and LFO-driven modulation keep parameters in constant motion, making geometry something you can “play” rather than merely render. From real-time OpenGL preview to pen-plotter-ready G-code, pyxidraw offers a continuous path from experimental patch to physical output, with new Shapes and Effects defined as lightweight Python decorators. The aim is not just to produce images, but to compose line-based scores that unfold in time, on screen and on paper.
    """


def draw(t: float):
    title = G.text()
    subtitle = G.text()
    func_text = G.text()
    description = G.text()
    return eff1(ply1)


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0),
        line_thickness=0.001,
        line_color=(0.0, 0.0, 0.0),
        render_scale=3.0,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
        parameter_gui=True,
    )
