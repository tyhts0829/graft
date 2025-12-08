import sys

sys.path.append("src")

from api import E, G, run
from core.realize import realize


def draw(t: float) -> None:
    c = G.circle(r=1.0, segments=16)
    s = E.scale(s=1.0 + 0.5 * t)
    scaled_c = s(c)
    return scaled_c


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0, 1.0),
        line_thickness=0.005,
        line_color=(0.0, 0.0, 0.0, 1.0),
        render_scale=6.0,
        canvas_size=(800, 800),
    )
