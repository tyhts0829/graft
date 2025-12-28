from grafix import E, G, run


def draw(t):
    g = G.polygon()
    e = E.fill()
    return e(g)


if __name__ == "__main__":
    run(draw)
