![](docs/readme/readme.png)

Grafix is a Python-based creative coding framework compatible with pen plotter.

## Installation

```bash
pip install grafix
```

macOS-first. Tested on macOS (Apple Silicon). Other platforms are not officially supported yet.

## Quick start

```python
from grafix import E, G, run


def draw(t: float):
    poly = G.polygon()
    effect = E.fill().displace()
    return effect(poly)


if __name__ == "__main__":
    run(draw, canvas_size=(800, 800), render_scale=2.0)
```

## Core API

- `G` lets you generate primitives such as `sphere`, `polyhedron`, `grid`, and more.
- `E` lets you modulate primitives such as `affine`, `fill`, `repeat`, and more.
- `run` lets you render a user-defined `draw(t)` function on each frame.

## Optional features

- `L` lets you define layers (stroke color, thickness, etc.).
- `cc` lets you map MIDI CC messages to any parameter.
- `Export` provides a headless export entrypoint (SVG implemented; PNG/MP4/G-code are stubs).
- `Parameter GUI` lets you tweak parameters live while the sketch is running.
- Keyboard shortcuts let you export output quickly:
  - `P` saves a `.png` image
  - `S` saves a `.svg` file
  - `V` records an `.mp4` video
  - `G` saves a `.gcode` file for pen plotters

## Configuration

A `config.yaml` lets you locate external fonts and choose where Grafix writes runtime outputs (`.svg`, `.png`, `.mp4`, `.gcode`).

Grafix starts from the packaged defaults (`grafix/resource/default_config.yaml`) and then overlays user configs.

Config overlay order (later wins):

- packaged defaults: `grafix/resource/default_config.yaml`
- `./.grafix/config.yaml` (project-local)
- `~/.config/grafix/config.yaml` (per-user)
- `run(..., config_path="path/to/config.yaml")`

Paths support `~` and environment variables like `$HOME`.

To create a project-local config (starting from the packaged defaults):

```bash
mkdir -p .grafix
python -c "from importlib.resources import files; print(files('grafix').joinpath('resource','default_config.yaml').read_text())" > .grafix/config.yaml
$EDITOR .grafix/config.yaml
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

## Not implemented yet

- MIDI/CC input, LFOs, keyboard shortcuts, screenshot/video recording
- PNG/G-code actual file generation (export stubs currently raise `NotImplementedError`)

## Development

Dev tools (optional):

```bash
pip install -e ".[dev]"
```

Run a sketch:

```bash
python sketch/readme.py
```

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

External:

- resvg (svg to png)
- ffmpeg (video encoding)

Dev (optional):

- pytest
- ruff
- mypy
