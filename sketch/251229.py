from grafix import E, G, run

A5 = (148, 210)


def draw(t):
    t1 = G(name="t1").text()
    e_t1 = E(name="e_t1").fill()
    t2 = G(name="t2").text()
    e_t2 = E(name="e_t2").fill()
    t3 = G(name="t3").text()
    e_t3 = E(name="e_t3").fill()
    return e_t1(t1), e_t2(t2), e_t3(t3)


if __name__ == "__main__":
    run(
        draw,
        canvas_size=A5,
        render_scale=5,
    )
