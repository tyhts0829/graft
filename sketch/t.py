from grafix import E, G, run


def flow_field_clipped(t: float, noise=(1.0, 1.0, 1.0), freqs=(0.1, 0.1, 0.1)):
    square = G(name="square").polygon(
        n_sides=4,
        phase=45.0,
        center=(50.0, 50.0, 0.0),
        scale=200.0,
    )

    square_e = E(name="square_e").affine().buffer()
    square = square_e(square)

    flow = G(name="flow").grid(
        nx=300,
        ny=0,
        center=(50.0, 50.0, 0.0),
        scale=150.0,
    )

    flow_e = (
        E(name="flow_e")
        .rotate()
        .subdivide()
        .displace(amplitude=noise, spatial_freq=freqs)
    )
    flow = flow_e(flow)

    clip = E(name="clip").clip()
    ret = clip(flow, square)

    aff = E(name="pos").affine()
    return aff(ret)


def draw(t):
    return flow_field_clipped(t)


if __name__ == "__main__":
    run(
        draw,
        canvas_size=(100, 100),
        render_scale=10,
        midi_port_name="Grid",
        midi_mode="14bit",
    )
