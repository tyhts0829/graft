# Grafix

Grafix is a macOS-first Python toolkit for creating line-based artwork, tuning it
interactively, and exporting it for screens or pen plotters.

**Status:** Beta · **Python:** 3.11+ · **Tested on:** macOS / Apple Silicon

- Build geometry with primitives such as circles, grids, text, and polyhedra.
- Transform it with chainable effects.
- Adjust parameters in a real-time Inspector.
- Export SVG, PNG, MP4, and plotter-ready G-code.

<img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/penplot_movie.gif" width="900" alt="Grafix preview and parameter Inspector" />
<img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/top_movie.gif" width="900" alt="Grafix preview and parameter Inspector" />

## Installation

Create a virtual environment and install Grafix:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install grafix
```

PNG export requires `resvg`, and MP4 recording requires `ffmpeg`. Neither is needed
for the first preview or for SVG and G-code export.

## Quick start

Save this as `sketch.py`:

```python
from grafix import E, G, SQUARE, run


def draw(t: float):
    shape = G.polygon(
        n_sides=6,
        center=(150.0, 150.0, 0.0),
        scale=170.0,
    )
    # density=28 means about 28 lines per 100 scene units.
    effects = E.fill(angle=45.0, density=28.0).rotate(
        rotation=(0.0, 0.0, t * 20.0),
    )
    return effects(shape)


if __name__ == "__main__":
    run(draw, canvas_size=SQUARE)
```

Run it:

```bash
python sketch.py
```

A hatched hexagon appears in the preview. The Inspector exposes the primitive and
effect arguments for live adjustment. Coordinates use `(0, 0)` at the top-left,
with positive X to the right and positive Y downward.

## How it works

Most sketches follow one small pipeline:

```text
G creates geometry → E transforms it → draw(t) returns it → run(draw) previews it
```

| API             | Purpose                                            |
| --------------- | -------------------------------------------------- |
| `G`             | Create primitive geometry.                         |
| `E`             | Build a chain of effects.                          |
| `L`             | Add color and line thickness for multi-layer work. |
| `P` / `@preset` | Define and reuse scene components.                 |
| `run(draw)`     | Open the preview and Inspector.                    |

Discover available primitives and effects from the CLI:

```bash
python -m grafix list
python -m grafix describe effect fill
```

## Canvas sizes

Grafix provides common canvas sizes as `(width, height)` integer tuples. Short A-series
names are portrait; the `_LANDSCAPE` variants swap width and height.

| Canvas | Portrait constant and value | Landscape constant and value |
| ------ | --------------------------- | ---------------------------- |
| Square | `SQUARE = (300, 300)`       | —                            |
| ISO A2 | `A2 = (420, 594)`           | `A2_LANDSCAPE = (594, 420)`  |
| ISO A3 | `A3 = (297, 420)`           | `A3_LANDSCAPE = (420, 297)`  |
| ISO A4 | `A4 = (210, 297)`           | `A4_LANDSCAPE = (297, 210)`  |
| ISO A5 | `A5 = (148, 210)`           | `A5_LANDSCAPE = (210, 148)`  |
| ISO A6 | `A6 = (105, 148)`           | `A6_LANDSCAPE = (148, 105)`  |

The A-series values are the nominal ISO 216 dimensions in millimetres, but `canvas_size`
remains a logical coordinate tuple for preview, SVG, PNG, and video. Only G-code interprets
the same numbers as millimetres. `SQUARE` is a convenient 300 × 300 logical canvas, not a
paper standard. Omitting `canvas_size` keeps the existing `(800, 800)` default.

## Export

With the preview focused:

| Key       | Output                      | Extra tool |
| --------- | --------------------------- | ---------- |
| `S`       | SVG                         | None       |
| `P`       | PNG                         | `resvg`    |
| `V`       | Start or stop MP4 recording | `ffmpeg`   |
| `G`       | G-code                      | None       |
| `Shift+G` | One G-code file per layer   | None       |

Outputs are written below `data/output/` by default. Existing files are not silently
overwritten; Grafix chooses an unused numbered filename.

Install the optional tools on macOS with:

```bash
brew install resvg ffmpeg
```

Render the Quick start sketch without opening a window:

```bash
python -m grafix export \
  --callable sketch:draw \
  --canvas 300 300 \
  --format svg \
  --out art.svg
```

The CLI accepts numeric dimensions, so `--canvas 300 300` is the command-line equivalent
of the Python `SQUARE` constant.

## Examples

<table>
  <tr>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/1.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/1.png" width="280" alt="Polygon repeat and displacement study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/2.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/2.png" width="280" alt="Layered text and fill study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/3.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/3.png" width="280" alt="Polyhedron line study" /></a></td>
  </tr>
  <tr>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/4.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/4.png" width="280" alt="Rotated and extruded sphere study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/5.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/5.png" width="280" alt="Paired filled-shape study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/6.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/6.png" width="280" alt="Repeated filled polygon study" /></a></td>
  </tr>
  <tr>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/7.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/7.png" width="280" alt="Radial curve study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/8.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/8.png" width="280" alt="Affine grid study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/9.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/9.png" width="280" alt="Gradient triangle study" /></a></td>
  </tr>
  <tr>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/10.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/10.png" width="280" alt="Repeated displaced line study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/11.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/11.png" width="280" alt="Isocontour polygon study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/12.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/12.png" width="280" alt="Reaction-diffusion text study" /></a></td>
  </tr>
  <tr>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/13.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/13.png" width="280" alt="Pixelated text study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/14.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/14.png" width="280" alt="Handwritten text study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/15.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/15.png" width="280" alt="Quantized asemic writing study" /></a></td>
  </tr>
  <tr>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/16.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/16.png" width="280" alt="Layered interference study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/17.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/17.png" width="280" alt="Wireframe cube study" /></a></td>
    <td><a href="https://github.com/tyhts0829/grafix/blob/main/sketch/readme/grn/18.py"><img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/grn/18.png" width="280" alt="Lissajous texture study" /></a></td>
  </tr>
</table>

Click a study to open its source. Grafix also includes small, copyable examples:

```bash
python -m grafix examples list
python -m grafix examples copy basic_shapes --output basic_shapes.py
python basic_shapes.py
```

## Projects and advanced use

Create a minimal project containing a sketch and `.grafix/config.yaml`:

```bash
python -m grafix init my-project
cd my-project
python sketch/main.py
```

Inspect the effective configuration with `python -m grafix config show`. Grafix also
supports live reload, MIDI control, reusable presets, custom primitives and effects,
named variations, and batch rendering. Run `python -m grafix --help` to find the
corresponding commands.

## Troubleshooting

Start with Grafix's built-in environment check:

```bash
python -m grafix doctor
```

If PNG or MP4 export fails, confirm that `resvg` or `ffmpeg` is installed and available
on `PATH`. If a preview is blank, verify that the geometry's center and scale place it
inside the canvas.

## Development and documentation

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check src/grafix tests
mypy src/grafix
```

- [Architecture](https://github.com/tyhts0829/grafix/blob/main/architecture.md)
- [Developer Guide](https://github.com/tyhts0829/grafix/blob/main/docs/developer_guide.md)
- [Glossary](https://github.com/tyhts0829/grafix/blob/main/docs/glossary.md)
- [Source](https://github.com/tyhts0829/grafix)
- [MIT License](https://github.com/tyhts0829/grafix/blob/main/LICENSE)
