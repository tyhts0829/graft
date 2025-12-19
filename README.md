# Grafix

Grafix is a lightweight toolkit for building line-based geometries, applying chained effects, and viewing the result in real time.

Shapes and effects are registered through the public API (`grafix.api`), allowing sketches to be composed by combining `G.<shape>()` calls with pipelines built from `E.<effect>()`.

## Examples

```python
from grafix.api import E, G, run


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
python sketch/perf_sketch.py
```

## Extending (custom primitives / effects)

You can register your own primitives and effects via decorators:

```python
from grafix.api import effect, primitive


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

- `grafix.api.G` lets you generate primitive shapes such as `sphere`, `polyhedron`, `grid`, and more.
- `grafix.api.E` lets you modulate and transform shapes such as `affine`, `fill`, `repeat`, and more.
- `grafix.api.L` lets you define layers so you can manage colors, stroke widths, and other styling attributes per layer.
- `grafix.api.run` lets you render any shapes, effects, and layers that a user-defined `draw(t)` function returns on each frame.
- `Parameter GUI` lets you tweak parameters live while the sketch is running.
- `grafix.api.Export` provides a headless export entrypoint (SVG implemented; image/G-code are stubs).

## Not implemented yet

- MIDI/CC input, LFOs, keyboard shortcuts, screenshot/video recording
- PNG/G-code actual file generation (export stubs currently raise `NotImplementedError`)

## Dependencies

Core (default):

- numpy
- numba
- shapely
- moderngl
- pyglet
- imgui
- fontPens
- fontTools
- PyYAML
- mido
- python-rtmidi

Dev (optional):

- pytest
- ruff
- mypy
