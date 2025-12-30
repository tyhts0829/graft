from grafix import E, G, L, cc, component, run

meta = {
    "center": {"kind": "vec3", "ui_min": 0.0, "ui_max": 100.0},
    "scale": {"kind": "float", "ui_min": 0.0, "ui_max": 4.0},
    "fill_density_coef": {"kind": "float", "ui_min": 0.0, "ui_max": 1.0},
}


@component(meta=meta)
def logo(center=(0, 0, 0), scale=1.0, fill_density_coef=1.0):
    square = G.polygon(
        n_sides=4,
        phase=45.0,
        center=(50.0, 50.0, 0.0),
        scale=100.0,
    )
    square_eff = (
        E(name="square_eff")
        .buffer(
            bypass=False,
            join="mitre",
            quad_segs=1,
            distance=-3.0,
            keep_original=True,
        )
        .fill(
            bypass=False,
            angle_sets=1,
            angle=45.0,
            density=300.0 * fill_density_coef,
            spacing_gradient=0.0,
            remove_boundary=False,
        )
    )
    square = square_eff(square)
    text1 = G.text(
        text="GRA",
        font="Geist-black",
        font_index=0,
        text_align="center",
        letter_spacing_em=0.5740000000000001,
        line_height=1.2,
        quality=0.5,
        center=(50.0, 37.5, 0.0),
        scale=15.0,
    )

    text2 = G.text(
        text="FIX",
        font="Geist-black",
        font_index=0,
        text_align="center",
        letter_spacing_em=0.8,
        line_height=1.2,
        quality=0.5,
        center=(50.0, 57.065, 0.0),
        scale=15.0,
    )

    text_fill = E(name="text_fill").fill(
        bypass=False,
        angle_sets=1,
        angle=45.0,
        density=135.0 * fill_density_coef,
        spacing_gradient=0.0,
        remove_boundary=False,
    )

    text = text_fill(text1 + text2)
    line = G(name="line").line(
        center=(47.283, 68.478, 0.0),
        length=49.153,
        angle=0.001,
    )

    line_eff = (
        E(name="line_eff")
        .affine(
            bypass=False,
            auto_center=True,
            pivot=(0.0, 0.0, 0.0),
            rotation=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            delta=(0.0, 0.0, 0.0),
        )
        .dash(
            bypass=False,
            dash_length=(20, 4),
            gap_length=9.322000000000001,
            offset=36.271,
            offset_jitter=0.0,
        )
        .buffer(
            bypass=False,
            join="round",
            quad_segs=12,
            distance=3.136,
            keep_original=False,
        )
        .fill(
            bypass=False,
            angle_sets=1,
            angle=45.0,
            density=35.0 * fill_density_coef,
            spacing_gradient=0.0,
            remove_boundary=False,
        )
    )

    line = line_eff(line)

    total_affine = E(name="total_affine").affine(
        delta=center, scale=(scale, scale, scale)
    )
    ret = total_affine(square + text + line)

    return ret


def draw(t):
    return logo(center=(0, 0, 0), scale=1)


if __name__ == "__main__":
    run(
        draw,
        canvas_size=(100, 100),
        render_scale=8,
    )
