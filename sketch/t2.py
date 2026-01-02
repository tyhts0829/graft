from grafix import E, G, run


def draw(t):
    grid = G.grid()
    grid_e = E.affine()
    grid = grid_e(grid)

    grid_e2 = E.affine().subdivide().displace().displace()
    grid2 = grid_e2(grid)

    grid = grid + grid2
    grid_e = E.rotate()
    return grid_e(grid)


if __name__ == "__main__":
    run(
        draw,
        canvas_size=(100, 100),
        render_scale=10,
        midi_port_name="OXI E16 „Éù„Éº„Éà3",
        # midi_port_name="Grid",
        midi_mode="14bit",
    )
