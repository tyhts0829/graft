from grafix import E, G, run
from sketch.presets.logo import logo

A5 = (148, 210)


def draw(t):
    text1 = G.text(
        text="pen",
        font="Geist-Black.ttf",
        font_index=0,
        text_align="center",
        letter_spacing_em=0.031,
        line_height=1.2,
        quality=0.5,
        center=(73.228, 54.331, 0.0),
        scale=62.992000000000004,
    )

    text2 = G.text(
        text="\nplo",
        font="Geist-Black.ttf",
        font_index=0,
        text_align="center",
        letter_spacing_em=0.162,
        line_height=0.974,
        quality=0.5,
        center=(73.228, 54.331, 0.0),
        scale=62.992000000000004,
    )

    text3 = G.text(
        text="\n\nter",
        font="Geist-Black.ttf",
        font_index=0,
        text_align="center",
        letter_spacing_em=0.186,
        line_height=0.997,
        quality=0.5,
        center=(73.228, 54.331, 0.0),
        scale=62.992000000000004,
    )

    text = text1 + text2 + text3
    text_eff = E.affine(
        bypass=False,
        auto_center=True,
        pivot=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, 0.0),
        scale=(1.0, 1.074, 1.0),
        delta=(0.0, 0.0, 0.0),
    ).fill(
        bypass=False,
        angle_sets=1,
        angle=55.67,
        density=487.973,
        spacing_gradient=0.0,
        remove_boundary=False,
    )

    text = text_eff(text)
    square = G(name="square").polygon(phase=45, n_sides=4)
    square_eff = E(name="square_eff").affine()
    square = square_eff(square)
    clip = E.clip()
    text = clip(text, square)
    return text


if __name__ == "__main__":
    run(
        draw,
        canvas_size=A5,
        render_scale=4,
        midi_port_name="Grid",
        midi_mode="14bit",
    )
