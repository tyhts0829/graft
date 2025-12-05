# Graft

Graft is a lightweight toolkit for building line-based geometries, applying chained effects, and viewing the result in real time.

Shapes and effects are registered through the public API, allowing sketches to be composed by combining `G.<shape>()` calls with pipelines built from `E.<effect>()`.

## Examples

```python
from api import E, G, run


def draw(t: float):
    poly = G.sphere()
    effect = E.affine().displace()
    return effect(poly)


if __name__ == "__main__":
    run(draw, canvas_size="A5", render_scale=10)
```

## Features

- `api.G` lets you generate primitive shapes such as `sphere`, `polyhedron`, `grid`, and more.
- `api.E` lets you modulate and transform shapes such as `affine`, `fill`, `repeat`, and more.
- `api.L` lets you define layers so you can manage colors, stroke widths, and other styling attributes per layer.
- `api.run` lets you render any shapes, effects, and layers that a user-defined `draw(t)` function returns on each frame.
- `api.cc` lets you map MIDI controllers to parameters so sliders, knobs, and pads can drive your sketches.
- `api.lfo` lets you create tempo-synced oscillators for modulating any numeric parameter over time.
- `Parameter GUI` lets you tweak all shape and effect argument parameters live while the sketch is running.
- `Keyboard shortcuts` let you capture output while a sketch is running:
  - `P` lets you save a screenshot (`Shift+P` for high resolution).
  - `V` lets you record a video of the sketch (`Shift+V` for high resolution).
  - `G` lets you export per-layer G-code for pen plotters.

## Configurations

- Default settings: `configs/default.yaml`
- Local overrides: `config.yaml` at the repository root (merged when present)

## Dependencies

- Numpy
- Numba
- scipy
- shapely
- noise
- vnoise
- ModernGL
- pyglet
- mido
- DearPyGui
