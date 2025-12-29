from grafix import E, G, L, cc, component, run

# meta = {
#     "center": {"kind": "vec3", "ui_min": 0.0, "ui_max": 100.0},
#     "scale": {"kind": "float", "ui_min": 0.0, "ui_max": 4.0},
# }


# @component(meta=meta)
def logo(center=(0, 0, 0), scale=1.0):
    FONT = "Geist-black"
    square = G.polygon(
        n_sides=4,
        phase=45,
        center=(50, 50, 0),
        scale=100,
    )
    square_eff = E(name="square_eff").buffer().fill()
    square = square_eff(square)
    text1 = G.text(
        font=FONT,
        text="GRA",
        text_align="center",
        letter_spacing_em=0.574,
        center=(50, 36, 0),
        scale=15,
    )
    text2 = G.text(
        font=FONT,
        text="FIX",
        text_align="center",
        letter_spacing_em=0.800,
        center=(50, 56, 0),
        scale=15,
    )
    text_fill = E(name="text_fill").fill()
    text = text_fill(text1 + text2)
    line = G(name="line").line()
    line_eff = E(name="line_eff").affine().dash(dash_length=(16, 4)).buffer().fill()
    line = line_eff(line)

    total_affine = E(name="total_affine").affine(
        delta=center, scale=(scale, scale, scale)
    )
    ret = total_affine(square + text + line)
    return ret


def draw(t):
    return logo(center=(50, 50, 0), scale=1)


if __name__ == "__main__":
    run(
        draw,
        canvas_size=(100, 100),
        render_scale=8,
    )
