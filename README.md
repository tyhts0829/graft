![](docs/readme/readme.png)

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
    run(draw, fps=60.0, canvas_size=(800, 800), render_scale=2.0, parameter_gui=True)
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

## Configuration (config.yaml) (optional)

Grafix can read a YAML config file to locate external assets (fonts) and to choose where it writes runtime outputs.

Config discovery (highest priority first):

- `run(..., config_path="path/to/config.yaml")`
- `./.grafix/config.yaml` (project-local)
- `~/.config/grafix/config.yaml` (per-user)

Paths support `~` and environment variables like `$HOME`.

Create a project-local config:

```bash
mkdir -p .grafix
$EDITOR .grafix/config.yaml
```

### Example

```yaml
# ./.grafix/config.yaml
font_dirs:
  - "~/Fonts"
output_dir: "./out"
```

### Keys

- `font_dirs` (list of paths): searched for `G.text(font=...)` and the Parameter GUI font picker.
- `output_dir` (path): base directory for auto-saved outputs (default: `data/output`).
  - Parameter GUI state: `{output_dir}/param_store/{script}.json`
  - Interactive saves: `{output_dir}/svg`, `{output_dir}/png`, `{output_dir}/video`
  - MIDI snapshots: `{output_dir}/midi`

All runtime outputs are written under `output_dir`.

Tip: Parameter persistence stores the selected `font` value. If you move a sketch to another machine and the font is not available, set `font_dirs` (or reset state by deleting the corresponding `{output_dir}/param_store/*.json` file).

### Environment variables (optional)

- `GRAFIX_FONT_DIRS` (paths separated by your OS path separator)
- `GRAFIX_OUTPUT_DIR`

Environment variables are used only when the corresponding key is not set in `config.yaml`.

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
- `grafix.api.run()` lets you render any shapes, effects, and layers that a user-defined `draw(t)` function returns on each frame.
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
- pyclipper
- moderngl
- pyglet
- imgui
- fontPens
- fontTools
- PyYAML
- mido
- python-rtmidi
- psutil

Dev (optional):

- pytest
- ruff
- mypy
