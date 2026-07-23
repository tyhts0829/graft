# Grafix

Grafix is a Python-based creative coding framework for line-based geometry:

- Generate primitives (`G`)
- Chain effects (`E`)
- Real-time interactive rendering (`run`)
- Export plotter-ready G-code
- Export visuals (SVG / PNG / MP4)

<img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/top_movie.gif" width="1200" alt="Grafix demo" />
<img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/penplot_movie.gif" width="1200" alt="Penplotting" />

<img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/penplot1.JPG" width="800" alt="pen plotter art example" />

## Installation

```bash
pip install grafix
```

## Requirements

- Python >= 3.11
- macOS-first (tested on macOS / Apple Silicon).
- Optional external tools:
  - `resvg` for PNG export (`P` key / headless PNG export)
  - `ffmpeg` for MP4 recording (`V` key)

macOS (Homebrew):

```bash
brew install resvg ffmpeg
```

## Quick start

```python
from grafix import E, G, run

CANVAS_SIZE = (148, 210)  # A5 [mm]


def draw(t: float):
    # Coordinates are in canvas units: (0,0)=top-left, +x=right, +y=down.
    # Keyword arguments are discovered at runtime and show up in the Parameter GUI.
    geometry = G.polyhedron()
    effect = E.fill().subdivide().displace().rotate(rotation=(t * 6, t * 5, t * 4))
    return effect(geometry)


if __name__ == "__main__":
    run(draw, canvas_size=CANVAS_SIZE, render_scale=5.0)
```

To run a sketch file with transactional live reload:

```bash
python -m grafix run sketch.py --watch
python -m grafix run sketch.py --midi-port none  # exact token to disable MIDI
```

Grafix polls the source files without an extra watcher dependency. It snapshots the changed
`draw(t)` module and its local relative-import helpers, builds operations and presets in an
isolated candidate authoring catalog, and only then swaps the callable, catalog, and worker
generation. A syntax/load error keeps the last-good code, frame, parameters, catalog, and
worker alive; the Inspector shows the traceback with Retry/Open. Use relative imports for
sketch-local helpers (for example, `from .shapes import make_shape`) so the whole reachable
source generation can be watched and isolated.

## Core API

- `G`: primitive Geometry factories (`G.polygon(...)`, `G.grid(...)`, ...)
- `E`: Effect chain builders (`E.fill(...).rotate(...)`)
- `L`: wrap Geometry into Layers (color / thickness) for multi-pen / multi-pass workflows
- `P` / `@preset`: reusable components
- `cc`: MIDI CC(`cc[1]` -> 0..1) to control parameters with physical controllers
- `run(draw)`: interactive rendering + Parameter GUI
- `ResourceBudget`: per-operation vertex/line/byte limits checked before large allocations
- `RuntimeLimits` / `RuntimeLimitProfiles`: headless and interactive resource profiles

Use `from grafix import ...` as the canonical public import. The root and `grafix.api`
facades resolve implementation groups lazily: importing the DSL does not initialize the
interactive runner, render/export stack, parameter storage, or config discovery. A
core-only import such as `import grafix.core.geometry` also leaves those outer
capabilities unloaded.

`G.select` and `E.select` expose the registered operations as a Parameter GUI choice while
keeping target-specific base arguments separate:

```python
source = G.select(
    target="circle",
    params_by_target={
        "circle": {"radius": 30.0},
        "rect": {"width": 60.0, "height": 40.0},
    },
)
rotated = E.select(
    target="rotate",
    params_by_target={"rotate": {"rotation": (0.0, 0.0, 30.0)}},
)(source)

# A unary selector can also be inserted after a fixed effect.
moved = E.rotate(rotation=(0.0, 0.0, 15.0)).select(
    target="translate",
    params_by_target={"translate": {"delta": (10.0, 0.0, 0.0)}},
)(source)

mask = G.circle(radius=20.0)
difference = E.select(
    target="boolean",
    n_inputs=2,
    params_by_target={"boolean": {"mode": "difference"}},
)(source, mask)
```

`E.select` defaults to unary effects (`n_inputs=1`). Set `n_inputs` to the required fixed
arity to select only effects with that input count; a multi-input selector must be the
first step in an effect chain. Custom operations must be imported and registered before
the selector call that should list them. Omitting `target` makes the Parameter GUI choice
persistent (initially `circle` for `G.select` and `rotate` for `E.select`). Supplying
`target` explicitly follows the normal explicit-parameter policy: code is authoritative
on a normal startup, while recovery data can intentionally preserve an override.

`run()` evaluates `draw(t)` in one background worker by default (`n_worker=1`) so the
window stays responsive. Use `n_worker=0` only when synchronous evaluation is required,
or increase the worker count for CPU-heavy `draw(t)` functions. Background evaluation
uses multiprocessing `spawn`, so keep `draw` at module scope and call `run()` behind an
`if __name__ == "__main__":` guard. A background evaluation that exceeds
`evaluation_timeout=5.0` seconds is cancelled by restarting its worker while the last
successful frame stays visible; pass `evaluation_timeout=None` to disable this deadline.
Temporary user-code/effect errors keep the last successful frame visible and appear in
the Parameter GUI monitor bar; fixing the error lets the next successful frame recover
without restarting the application.

The Parameter GUI shows each value's effective `CODE` / `UI` / `MIDI LIVE` /
`MIDI FROZEN` source. Search and structured filters find rows by label, operation,
source, or MIDI CC; favorites, collapsible groups, and the Help pane keep large scenes
navigable. Parameter edits support coalesced Undo/Redo, named variations, deterministic
randomize/lock/morph, and debounced atomic autosave, so it is safe to explore alternatives
and return to an earlier state. Use `Cmd/Ctrl+Z` to undo and `Cmd/Ctrl+Shift+Z` (or
`Ctrl+Y`) to redo while the Parameter GUI is focused.

Closing the Inspector hides it instead of stopping the artwork; `Cmd/Ctrl+I` shows it
again. Preview/Inspector placement, Inspector visibility, and UI scale are saved per
sketch and clamped to the available screens on the next launch.

Use `RuntimeLimits` for headless rendering and `RuntimeLimitProfiles` for interactive
preview/final limits:

```python
from grafix import ResourceBudget, RuntimeLimitProfiles, RuntimeLimits, run

budget = ResourceBudget(
    max_output_vertices=2_000_000,
    max_output_lines=200_000,
    max_output_bytes=256 * 1024 * 1024,
)
limits = RuntimeLimits(per_operation=budget, scene=budget)
run(
    draw,
    runtime_limit_profiles=RuntimeLimitProfiles(preview=limits, final=limits),
)
```

`G`, `E`, `L`, and `P` accept `key=str|int` as a stable semantic identity when a
parameter group must survive moving its call within the same source file. For repeated
structures, add `instance_key=i` to give each loop/comprehension instance its own group,
or use `shared=True` to intentionally share one semantic group. `instance_key` and
`shared=True` are mutually exclusive. Without these options, Grafix derives a cached
project-relative call-site identity automatically.

## Export & shortcuts

When the draw window is focused:

- `S`: save SVG
- `P`: save PNG (requires `resvg`; its intermediate SVG is private and temporary)
- `V`: start/stop MP4 recording (requires `ffmpeg`)
- `G`: save G-code
- `Shift+G`: save G-code per layer (when your sketch returns multiple Layers)
- `Space`: play/pause the preview timeline
- `Home`: reset preview time to zero
- `Left` / `Right`: step backward/forward by one frame (and pause)
- `[` / `]`: halve/double preview speed (0.125x to 8x)

Outputs are written under `paths.output_dir` (default: `data/output`), under per-kind subdirectories (`svg/`, `png/`, `gcode/`, ...).
Interactive captures never silently overwrite an existing artifact: Grafix reserves an
unused numbered filename and writes a sibling `*.capture.json` manifest containing the
Grafix/source/git/config/parameter snapshot provenance, frame time/quality, output size,
format, and actual artifact paths. Provenance is fixed with the frame in the main process;
the capture worker does not re-read Git, config, or source state.
PNG and G-code shortcuts enqueue an immutable frame snapshot on one bounded background
worker, so a slow export does not stop the preview loop. After the first frame, each
shortcut is bound to the frame visible at keypress and is immediately admitted or rejected
against both request-count and aggregate geometry-byte limits. Accepted jobs keep FIFO
order, repeated captures of the same immutable snapshot share the retained geometry, and
rejections are shown explicitly instead of replacing or silently dropping an older request.
A small intent queue exists only before the first frame is available.

The byte limit is a conservative process-wide estimate: Grafix accounts for the parent
geometry, multiprocessing serialization, and the worker copy. It is a backpressure budget,
not an exact operating-system RSS measurement. Closing the app finalizes an active video
before draining other exports; video finalization and export drain share a bounded deadline,
and unfinished exports are reported as cancelled when that deadline is reached. Recording
uses an explicit pause-on-error policy: a failed scene is not replaced by a duplicated
last-good video frame, and its fixed-FPS clock does not advance. The recording manifest
reports written/dropped/duplicated/error counts and the stop/abort reason.

## Examples

<!-- BEGIN:README_EXAMPLES_GRN -->
<table>
  <tr>
    <td><img src="docs/readme/grn/1.png" width="320" alt="grn 1" /></td>
    <td><img src="docs/readme/grn/2.png" width="320" alt="grn 2" /></td>
    <td><img src="docs/readme/grn/3.png" width="320" alt="grn 3" /></td>
  </tr>
  <tr>
    <td><img src="docs/readme/grn/4.png" width="320" alt="grn 4" /></td>
    <td><img src="docs/readme/grn/5.png" width="320" alt="grn 5" /></td>
    <td><img src="docs/readme/grn/6.png" width="320" alt="grn 6" /></td>
  </tr>
  <tr>
    <td><img src="docs/readme/grn/7.png" width="320" alt="grn 7" /></td>
    <td><img src="docs/readme/grn/8.png" width="320" alt="grn 8" /></td>
    <td><img src="docs/readme/grn/9.png" width="320" alt="grn 9" /></td>
  </tr>
  <tr>
    <td><img src="docs/readme/grn/10.png" width="320" alt="grn 10" /></td>
    <td><img src="docs/readme/grn/11.png" width="320" alt="grn 11" /></td>
    <td><img src="docs/readme/grn/12.png" width="320" alt="grn 12" /></td>
  </tr>
  <tr>
    <td><img src="docs/readme/grn/13.png" width="320" alt="grn 13" /></td>
    <td><img src="docs/readme/grn/14.png" width="320" alt="grn 14" /></td>
    <td><img src="docs/readme/grn/15.png" width="320" alt="grn 15" /></td>
  </tr>
  <tr>
    <td><img src="docs/readme/grn/16.png" width="320" alt="grn 16" /></td>
    <td><img src="docs/readme/grn/17.png" width="320" alt="grn 17" /></td>
    <td><img src="docs/readme/grn/18.png" width="320" alt="grn 18" /></td>
  </tr>
</table>
<!-- END:README_EXAMPLES_GRN -->

## Extending

You can declare your own primitives and effects via decorators:

```python
import numpy as np

from grafix import effect, primitive

prim_meta = {
    "r": {
        "kind": "float",
        "ui_min": 1.0,
        "ui_max": 100.0,
        "description": "生成する形状の基準半径。",
    }
}
eff_meta = {
    "amount": {
        "kind": "float",
        "ui_min": 0.0,
        "ui_max": 2.0,
        "description": "入力形状へ適用する変形量。",
    }
}

@primitive(meta=prim_meta)
def user_prim(*, r=10.0) -> tuple[np.ndarray, np.ndarray]:
    coords = ...  # exact ndarray, C-order, float32, finite, shape (N, 3)
    offsets = ...  # exact ndarray, C-order, int32, shape (M+1,)
    return coords, offsets


@effect(meta=eff_meta)
def user_eff(g: tuple[np.ndarray, np.ndarray], *, amount=1.0) -> tuple[np.ndarray, np.ndarray]:
    coords, offsets = g
    coords_out = ...
    return coords_out, offsets
```

Notes:

- Built-in primitives/effects must provide `meta=...` (enforced).
- User-defined primitives/effects use one exact `(coords, offsets)` contract: C-contiguous
  `float32 (N,3)` finite coordinates and C-contiguous `int32 (M+1,)` offsets. Grafix rejects
  other dtypes/layouts instead of converting them.
- For user-defined ops, `meta` is optional. If omitted, parameters are not shown in the Parameter GUI.
- For user-defined ops, each `description` is also optional, but adding one makes the
  argument's purpose available to Parameter GUI Help and generated stubs.
- Importing a normal user module records immutable declarations. `run()` and
  `RenderSession` take one operation/preset snapshot at construction, so import custom
  modules before creating the session that should use them.
- `G.catalog()` / `E.catalog()` and `describe(name)` return immutable `OperationInfo`
  values for inspection. They do not expose evaluator implementations or session-owned
  resources.
- `overwrite=True` replaces only the named declaration for future snapshots. Existing
  sessions and Geometry DAGs keep their exact operation version; Grafix never silently
  redirects an old DAG to a newer evaluator.
- The default `cache_policy="content"` is for deterministic operations whose result is
  described by code, defaults, closure values, and arguments. If an operation intentionally
  reads dynamic process state that cannot be fingerprinted, declare
  `cache_policy="none", version="your-stable-version"`; the explicit version is required
  and the resulting DAG bypasses content caches.

## Presets (reusable components)

Use `@preset` to register a component, and call it via `P.<name>(...)`:

```python
from grafix import G, P, preset

meta = {
    "n_rows": {
        "kind": "int",
        "ui_min": 1,
        "ui_max": 20,
        "description": "グリッドの縦方向の分割数。",
    },
    "n_cols": {
        "kind": "int",
        "ui_min": 1,
        "ui_max": 20,
        "description": "グリッドの横方向の分割数。",
    },
}

@preset(meta=meta)
def grid_system_frame(
    *,
    n_rows: int = 5,
    n_cols: int = 8,
):
    return G.grid(
        nx=n_cols,
        ny=n_rows,
        center=(150.0, 150.0, 0.0),
        scale=180.0,
    )


P.grid_system_frame()
P(name="Main grid", key="main").grid_system_frame(n_rows=6)
```

A preset is a scene component: it must return a `Geometry`, a `Layer`, or nested
`list` / `tuple` containers of those values (`SceneItem`). Every preset also accepts the automatically
added `activate` argument. When `activate=False`, Grafix skips the function body and
returns an empty `Geometry` that can be passed through the normal scene pipeline.
Labels and parameter identity use only the namespace form
`P(name=..., key=..., instance_key=..., shared=...).<name>(...)`; preset function
signatures do not own these wrapper-reserved names. Direct `P.<name>(...)` calls accept
the automatically added `activate` argument, but not the four identity arguments.

For IDE completion of `P.<name>(...)`, regenerate stubs after adding/changing presets:

```bash
python -m grafix stub
```

Preset lookup is snapshot-based. A normally imported `@preset` is available to future
sessions (and to `P` outside a draw call). Presets from `paths.preset_module_dirs` are
loaded only while `run()`, `RenderSession`, or a corresponding CLI command builds its
session catalog. Calling `P` outside such a session does not implicitly scan config
directories. Candidate import failure or a duplicate name aborts that candidate without
changing another session or the process-level authoring declarations.

## Configuration (`config.yaml`)

A `config.yaml` lets you locate external fonts and choose where Grafix writes runtime outputs (`.svg`, `.png`, `.mp4`, `.gcode`).

Grafix starts from the packaged defaults (`grafix/resource/default_config.yaml`) and then overlays user config(s).

Load order (later wins):

1. packaged defaults
2. discovered config (0 or 1 file; first found wins)
3. explicit config path (if provided)

Config search (first found wins):

- `./.grafix/config.yaml` (project-local)
- `~/.config/grafix/config.yaml` (per-user)

You can also pass an explicit config path:

- `run(..., config_path="path/to/config.yaml")`
- `python -m grafix export --config path/to/config.yaml`

Validate or inspect effective values and their source before launching:

```bash
python -m grafix config validate .grafix/config.yaml
python -m grafix config show .grafix/config.yaml
```

The config path is positional; `config validate/show` does not provide a `--config`
alias.

Unknown keys and invalid values are rejected with a nearest-key hint. Interactive runs
remain recoverable: an invalid user config falls back to the packaged defaults and emits
an explicit Inspector diagnostic with the source and traceback. The validation CLI stays
strict and exits non-zero instead of applying that fallback.

`grafix.runtime_config_loader` owns YAML/package-resource I/O and CWD/HOME discovery.
Each application entry point resolves a new immutable `RuntimeConfig`; there is no
process-wide mutable config path or config cache. `run()` and `RenderSession` resolve it
once at construction and pass the same value to application subsystems. Geometry
evaluators receive only `EvaluationConfig` (currently `font_dirs`), so changing window,
output, GUI, or MIDI settings does not invalidate geometry caches. Two sessions with
different configs can coexist, and closing either session does not change the other.
Pass either `config_path=` or an already loaded `config=`, never both.

Lower-level output-path helpers never discover config implicitly. Resolve one immutable
value at the application boundary and pass it explicitly:

```python
from grafix.export.output_paths import default_param_store_path
from grafix.runtime_config_loader import load_runtime_config

config = load_runtime_config(".grafix/config.yaml")
parameter_path = default_param_store_path(draw, config=config)
```

Paths support `~` and environment variables like `$HOME`. Relative paths in a user
config are resolved from that config file's directory. Therefore paths in
`./.grafix/config.yaml` normally start with `../` when they point into the project root.

To create a project-local config, prefer the no-clobber project initializer:

```bash
python -m grafix init .
```

The packaged defaults are interpreted relative to the process working directory, so do
not copy them verbatim under `.grafix/`. A minimal project-local path overlay looks like:

```yaml
version: 1
paths:
  output_dir: "../data/output"
  sketch_dir: "../sketch"
  preset_module_dirs:
    - "../sketch/presets"
  font_dirs:
    - "../data/input/font"
```

Overlay is recursive for mapping values. For example, overriding only
`export.gcode.travel_feed` keeps the packaged defaults under `export.png` and the other
G-code fields.

G-code export treats the input polyline order as a semantic boundary.
`optimize_travel` may reorder or reverse only the clipping fragments produced from one
source polyline; it never reorders different source polylines. Likewise,
`bridge_draw_distance` never adds a drawn bridge across a source-polyline boundary.

To load user definitions into each session catalog from a directory:

```yaml
paths:
  preset_module_dirs:
    - "../sketch/presets"
```

Python source modules under these directories are loaded into a session-local candidate
namespace. They may declare presets, primitives, and effects and may use package-relative
imports. The candidate is published only after all entry modules load successfully; it is
not a live global registry and it is not retained in `sys.modules` as the canonical module
name.

Useful project/operation CLI entry points:

```bash
python -m grafix init my-project       # no-clobber scaffold
python -m grafix doctor                # GL/resvg/ffmpeg/MIDI/font/output checks
python -m grafix examples list
python -m grafix list
python -m grafix describe primitive circle
python -m grafix stub                  # project-local G/E/P typing
```

## Text-to-Physical art (WIP)

I'm experimenting with a fully autonomous LLM loop that creates Grafix sketches end-to-end from a single prompt.

It iterates through:

- ideate
- implement
- render
- critique
- improve

No human intervention, just continuous iteration and unexpected visual evolution.
The image below was generated by the LLM in this closed loop.

<img src="https://raw.githubusercontent.com/tyhts0829/grafix/main/docs/readme/agent_generated_art.png" width="1200" alt="LLM-generated sketches (work in progress)" />

### Headless export (batch rendering)

The loop uses `python -m grafix export` to render `draw(t)` without opening any window.
The default parameter source is `code`, so headless output never reads a hidden ParamStore
unless you explicitly select `saved`, `recovery`, or a JSON path:

```bash
python -m grafix export --callable sketch.main:draw --t 0.0 --canvas 300 300
python -m grafix export --callable sketch.main:draw --format svg --out art.svg
python -m grafix export --callable sketch.main:draw --format gcode --out plot.gcode
python -m grafix export --callable sketch.main:draw --t 0.0 1.0 2.0 --format png --canvas 300 300 --out-dir data/output
python -m grafix export --callable sketch.main:draw --parameter-source saved --out saved-state.png
python -m grafix export --callable sketch.main:draw --parameter-source data/params.json --out explicit-state.svg
```

With an explicit config file:

```bash
python -m grafix export --config path/to/config.yaml --callable sketch.main:draw --t 0.0 --canvas 300 300
```

Existing artifacts are not overwritten by default. The CLI prints the actual numbered
artifact path and its `*.capture.json` manifest; pass `--overwrite` only when replacing
that generation is intentional.

Render saved named variations as no-clobber thumbnails plus an SVG contact sheet and a
structured partial-failure summary:

```bash
python -m grafix variations \
  --callable sketch.main:draw \
  --parameter-source saved \
  --out-dir data/output/variation-batches
```

Each thumbnail label includes the variation name and seed. A failed variation does not
discard successful siblings; the CLI reports the failed item and exits non-zero.

The direct API separates one final-quality render from capture. `line_thickness=0.001`
means 0.1% of the canvas short side:

```python
from grafix import RenderOptions, export, render

frame = render(
    draw,
    0.0,
    options=RenderOptions(canvas_size=(300, 300), line_thickness=0.001),
    parameter_source="code",
)
result = export(frame, "data/output/art.svg")
print(result.path, result.manifest_path)
```

`render()` creates and closes a session for one frame. For multiple times, keep one
`RenderSession` open so the immutable config/catalog snapshot, bounded geometry cache, and
font resources are reused and then closed deterministically:

```python
from grafix import RenderOptions, RenderSession, export

with RenderSession(
    draw,
    options=RenderOptions(canvas_size=(300, 300)),
    parameter_source="code",
    config_path=".grafix/config.yaml",
) as session:
    for index, t in enumerate((0.0, 1.0, 2.0)):
        frame = session.render(t)
        export(frame, f"data/output/frame-{index}.svg")
```

`RenderSession.render()` always evaluates at final quality and performs no file I/O;
`export()` owns encoding, private staging, no-clobber publication, and the sibling capture
manifest. The same immutable `Frame` may therefore be exported to multiple formats.

`RenderSession` owns its evaluation resources and cache store and injects them into a borrowing
`RealizeSession`. At the lower-level API, each omitted `resources` or `cache_store` dependency is
owned and closed by `RealizeSession`; explicitly supplied dependencies remain caller-owned.
`RenderSession` does not expose those closeable child owners. Public properties may provide
immutable metadata or session views such as `options`, `param_store`, `config`,
`runtime_limits`, and `metadata`, but never a child resource with its own `close()` capability.

## Troubleshooting

- `resvg が見つかりません`: install `resvg` and ensure it is on `PATH` (macOS: `brew install resvg`)
- `ffmpeg が見つかりません`: install `ffmpeg` (macOS: `brew install ffmpeg`)

## Development

```bash
# run without installation
PYTHONPATH=src python sketch/main.py

# tests / lint / typecheck
PYTHONPATH=src pytest -q
ruff check src/grafix tests
mypy src/grafix

# short deterministic measurements (fresh process per case)
PYTHONPATH=src python -m grafix benchmark run --suite system --profile smoke
# long measurements are explicit; hosted CI does not use wall time as a hard gate
PYTHONPATH=src python -m grafix benchmark run --suite all --profile long
# generate the offline HTML report from schema v4 run JSON
PYTHONPATH=src python -m grafix benchmark report
```

The CLI is the normal benchmark entry point. Harness extensions use the canonical
`grafix.devtools.benchmarks.definition`, `.catalog`, `.metrics`, and `.executor` modules;
`.runner` intentionally exports only `run_case_isolated`. See `docs/developer_guide.md` before
adding a workload provider.

See: `architecture.md`, `docs/developer_guide.md`, and
`docs/migration_2026-07-23.md` for the current ownership/import migration.
