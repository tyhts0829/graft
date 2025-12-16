# Graft

Graft is a lightweight toolkit for building line-based geometries, applying chained effects, and viewing the result in real time.

Shapes and effects are registered through the public API (`graft.api`), allowing sketches to be composed by combining `G.<shape>()` calls with pipelines built from `E.<effect>()`.

## Examples

```python
from graft.api import E, G, run


def draw(t: float):
    poly = G.sphere()
    effect = E.scale().rotate()
    return effect(poly)


if __name__ == "__main__":
    run(draw, canvas_size=(800, 800), render_scale=2.0, parameter_gui=True)
```

## Development

Editable install (recommended):

```bash
pip install -e .
```

Dev tools (optional):

```bash
pip install -e ".[dev]"
```

Run a sketch:

```bash
python sketch/main.py
```

## Extending (custom primitives / effects)

You can register your own primitives and effects via decorators:

```python
from graft.api import effect, primitive


@primitive
def user_prim(*, r=10.0):
    ...


@effect
def user_eff(inputs, *, amount=1.0):
    ...
```

Notes:
- Built-in primitives/effects must provide `meta=...` (enforced).
- For user-defined ops, `meta` is optional. If omitted, parameters are not shown in the Parameter GUI.

## Features (current)

- `graft.api.G` lets you generate primitive shapes such as `sphere`, `polyhedron`, `grid`, and more.
- `graft.api.E` lets you modulate and transform shapes such as `affine`, `fill`, `repeat`, and more.
- `graft.api.L` lets you define layers so you can manage colors, stroke widths, and other styling attributes per layer.
- `graft.api.run` lets you render any shapes, effects, and layers that a user-defined `draw(t)` function returns on each frame.
- `Parameter GUI` lets you tweak parameters live while the sketch is running.
- `graft.api.Export` provides a headless export entrypoint (SVG/image/G-code) as an API skeleton (currently unimplemented / stubs).

## Not implemented yet

- MIDI/CC input, LFOs, keyboard shortcuts, screenshot/video recording
- SVG/PNG/G-code actual file generation (export stubs currently raise `NotImplementedError`)

## Dependencies

Core (default):

- numpy
- numba
- shapely
- moderngl
- pyglet
- pyimgui

Dev (optional):

- pytest
- ruff
- mypy
