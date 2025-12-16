# src_core_export_interactive_refactor_plan.md

どこで: `src/` 配下のパッケージ構造と import 依存（`core` / `export` / `interactive`）。
何を: 「ヘッドレス一括出力」と「対話プレビュー」を衝突させないための、依存方向を固定するリファクタ計画。
なぜ: 形だけのディレクトリ整理ではなく、責務境界とデータの流れがそのまま依存方向になる状態にして、将来の機能追加（SVG/PNG/G-code/録画/GUI）でも崩れない土台を作るため。

## 0. ゴール（成功条件）

- `src.core` は import 時に `pyglet` / `moderngl` / `imgui` を要求しない（ヘッドレスで import 可能）。
- `src.export` は import 時に `pyglet` / `moderngl` / `imgui` を要求しない（ヘッドレスで export 可能）。
- `src.interactive` だけが window/GL/GUI 依存を持つ（依存が一箇所へ隔離される）。
- 依存方向を「テスト」で機械的に検査し、破ったら即座に落ちる。
- 既存テストは必要なら変更してよいが、依存境界と主要動作を担保するテストは維持/追加する（意図を明文化する）。
- SVG/画像/G-code/動画の “実出力” は未実装でもよい（現段階では API の骨格＝空実装/スタブを用意する）。

## 1. 現状整理（依存の “混ざり” ポイント）

現状の `src/` は概ねレイヤ分割できているが、次の点が “本質的な境界” を曖昧にしている。

1. **`src/render` に「ドメイン（Layer/Scene）」と「GL 実装」が同居**

   - `src/render/layer.py` と `src/render/scene.py` は「シーン表現」であり、export/interactive どちらでも共通に使う概念。
   - 一方で `src/render/draw_renderer.py` は `moderngl` / `pyglet` を直 import しており、明確に interactive。

2. **パイプラインが GL 前提（index 生成）を含み、export と共有できない**

   - `src/render/frame_pipeline.py:render_scene()` が `build_line_indices()` を呼び、描画 API が GL 寄りになっている。
   - `build_line_indices` が `LineMesh.PRIMITIVE_RESTART_INDEX` に依存していて、純粋関数が GPU 実装に引っ張られている。

3. **API から `src.render.*` を参照しており、概念的に “render 依存” に見える**
   - 例: `src/api/layers.py` が `src/render/layer.Layer` を import。
   - 実際には `Layer` はドメイン概念なので、置き場所が原因で依存関係の意味が歪む。

## 2. 目指す設計（依存方向 = データの流れ）

**中心方針**: 「core はデータと計算だけ」「export/interactive は core の結果を “外へ出すアダプタ”」に固定する。

- `draw(t)` は _Geometry/Layer/シーン_ を返す（レシピ）。
- core が _scene 正規化 → style 解決 → realize_ を行い、**“描画/出力に必要な最終形”**（Realized + style）を返す。
- export はそれを SVG/PNG/G-code へ変換する。
- interactive はそれを GL で表示する（GL 専用の index 生成は interactive 側へ隔離）。

## 3. ターゲット構造（案）

`src/` 直下に 3 ディレクトリを作り、既存の責務を吸収する（`src/api` はファサードとして残す想定）。

```
src/
  api/                    # ファサード（公開導線）
    __init__.py
    primitives.py / effects.py / layers.py
    run.py                 # interactive は遅延 import
    export.py              # ヘッドレス export の入口（Export クラス）
  core/
    geometry.py / realized_geometry.py / realize.py / *_registry.py
    layer.py              # Layer / style 解決（ドメイン概念）
    scene.py              # SceneItem / normalize_scene（ドメイン概念）
    pipeline.py           # scene を “実体 + style” へ落とす（GL 非依存）
    parameters/           # 旧 src/parameters
    primitives/           # 旧 src/primitives
    effects/              # 旧 src/effects
  export/                 # 出力 API（当面はスタブ）
    __init__.py
    svg.py                # SVG 出力（当面は空実装）
    image.py              # 画像出力（当面は空実装）
    gcode.py              # G-code 出力（当面は空実装）
  interactive/
    __init__.py
    runtime/              # 旧 src/app/runtime
    parameter_gui/        # 旧 src/app/parameter_gui
    gl/                   # 旧 src/render の GL 実装（DrawRenderer, Shader, LineMesh, index 生成）
```

補足:

- `src/api` は「ユーザーが触る入口」として残し、内部実装は `core/export/interactive` へ委譲する（公開面を薄く保つ）。
- `core` 配下へ `parameters/primitives/effects` を移すのは、「core の外に“ドメイン核の一部”が散らばっている」状態を解消し、依存検査を単純にするため。

## 4. 依存ルール（機械的に検査する前提）

- `src.core`:
  - ✅ 許可: `src.core.*`、（必要な）数値計算依存（numpy/numba 等）、標準ライブラリ
  - ❌ 禁止: `src.export.*` / `src.interactive.*`、`pyglet` / `moderngl` / `imgui` の import
- `src.export`:
  - ✅ 許可: `src.core.*`、標準ライブラリ（I/O）、（必要なら）svg/png の軽量依存
  - ❌ 禁止: `src.interactive.*`、`pyglet` / `moderngl` / `imgui`
- `src.interactive`:
  - ✅ 許可: `src.core.*` / `src.export.*`、`pyglet` / `moderngl` / `imgui`（ショートカット保存で利用する想定）
- `src.api`（ファサード）:
  - ✅ 許可: `src.core/export/interactive` への参照（ただし **interactive は遅延 import**）

## 5. コアの “境界面” を先に作る（スマート化の要点）

ディレクトリ移動より先に、**core が提供する最小の境界 API** を決める。

### 5.1 core/pipeline の契約（提案）

- `core.pipeline.realize_scene(draw, t, defaults) -> list[RealizedLayer]`
  - `RealizedLayer` は `RealizedGeometry + (color, thickness) + 元 Layer 情報` を持つ薄い dataclass。
  - ここで:
    - `normalize_scene(draw(t))`
    - `resolve_layer_style(...)`
    - ParamStore があれば “Layer style override” を反映（現 `frame_pipeline` の責務）
    - `realize(geometry)` までを完了
  - **GL 専用の index 生成は含めない**（export と共有できるようにする）

### 5.2 interactive 側の最小責務

- `interactive.gl` が `RealizedLayer` を受けて
  - offsets から GL 用 index を生成
  - `DrawRenderer.render_layer(realized, indices, color, thickness)` を呼ぶ

### 5.3 export 側の最小責務（最初は SVG）

- Phase 5 では、`export.svg/image/gcode` の API（関数/クラスのシグネチャ）だけ確定し、処理は空実装/スタブでよい。
- 将来の実装では、`RealizedLayer` から polyline 群へ分解して SVG/PNG/G-code を生成する（ただし本計画の非ゴール）。

## 6. 段階的リファクタ手順（チェックリスト）

### Phase 1: core へ「ドメイン概念」を集約（混ざりを解消）

- [x] `src/render/layer.py` を `src/core/layer.py` へ移動（Layer/LayerStyleDefaults/resolve_layer_style）。
- [x] `src/render/scene.py` を `src/core/scene.py` へ移動（SceneItem/normalize_scene）。
- [x] 参照箇所（`src/api/layers.py`, `src/api/run.py`, tests など）の import を更新。
- [x] “render という名前にドメイン概念が入っている” 状態を解消（以降 `render` という語を interactive/export の実装側にのみ残す）。

### Phase 2: core.pipeline を確立（GL 非依存の最終形を返す）

- [x] `src/render/frame_pipeline.py` を `src/core/pipeline.py` へ再設計して移動（GL index 生成を削除）。
- [x] `tests/core/test_pipeline.py` を core.pipeline の契約に合わせて更新（renderer スタブではなく “返り値” を検証する形へ）。
- [x] `build_line_indices` と `PRIMITIVE_RESTART_INDEX` 依存を interactive 側へ押し込む。

### Phase 3: interactive を `src/interactive` へ移設（依存を隔離）

- [x] `src/app/*` を `src/interactive/*` へ移動（runtime/parameter_gui を保持）。
- [x] `src/render/*` のうち GL 実装（DrawRenderer/LineMesh/Shader/utils/index_buffer 等）を `src/interactive/gl/*` へ移動。
- [x] `src/api/run.py` から参照している import を新パスへ更新（公開 API は維持、ただし内部参照は更新）。

### Phase 4: core の中核（parameters/primitives/effects）を core 配下へ集約

- [x] `src/parameters` → `src/core/parameters` へ移動し、全 import を更新。
- [x] `src/primitives` → `src/core/primitives`、`src/effects` → `src/core/effects` へ移動し、登録 import（`src/api/primitives.py`, `src/api/effects.py`）を更新。
- [x] （必要なら）`src/core/effects/AGENTS.md` を新設し、現 `src/effects/AGENTS.md` のルール（相互依存禁止等）を維持する。

### Phase 5: export API の骨格（スタブを先に置く）

- [x] `src/export/svg.py` / `src/export/image.py` / `src/export/gcode.py` を追加し、公開したい関数/クラスのシグネチャだけ先に固定する（中身は空実装でよい）。
- [x] “ヘッドレス export” の入口を `src/api/export.py` に用意する（`Export(draw, t, fmt, path, ...)` の形）。
- [ ] interactive 実行時の保存（画像/svg/g-code/動画）は Keyboard Shortcut 経由を基本とし、ユーザーが `from api import Export` しなくても保存できる導線にする。
- [x] export が `pyglet/moderngl/imgui` を import しないことをテストで保証する。

### Phase 6: 依存方向の自動検査（壊れない仕組み）

- [x] `tests/test_dependency_boundaries.py` を追加し、`src/core` が `src/interactive` / `src/export` を import していないことを AST で検査。
- [ ] `import src.core` / `import src.export` が `pyglet/moderngl/imgui` を引かないことを smoke テスト化（必要なら `importlib` + `sys.modules` で確認）。

### Phase 7: ドキュメント更新

- [ ] `architecture.md` を新構造に合わせて更新（依存図、実行フロー、責務境界）。
- [ ] `README.md` の import パス例が実態とズレていれば修正（必要最小限）。

## 7. 合意事項（2025-12-16）

- [x] `src/api` は “第 4 の層（ファサード）” として残す（3 ディレクトリ＋ api）。
- [x] `core` は `core/layer.py` + `core/scene.py` に分ける。
- [x] export の入口は A を採用（ただし関数ではなく `Export` を公開導線にする）。
  - `from api import Export`（例: `Export(draw, t, "svg", "data/outputs/svg/some_user_art.svg")`）
  - CLI は将来タスク（この計画では API 入口まで）
- [x] SVG/画像/G-code/動画の出力処理は未実装でよい（当面は空実装/スタブ）。

## 8. 非ゴール（この計画ではやらない）

- 画質/AA/録画などの高度なレンダリング品質改善（export の “成立” を優先）。
- SVG/PNG/G-code/動画の具体的なファイル生成（Phase 5 では空実装/スタブまで）。
- 依存管理（pyproject/extras）を整える作業（必要になった段階で別計画に切り出す）。

## 9. 追加: 後片付け（テスト配置/旧ディレクトリ削除）

- [x] `src/app`, `src/render`, `src/parameters` が不要なことを確認し、残骸を削除する（tracked/untracked 両方）。
- [x] `tests/` 配下を新レイヤ構造に合わせて整理する（`tests/core`, `tests/interactive` へ集約）。
- [x] `pytest -q` を通し、結果をこの計画に記録する（177 passed, 2.97s）。
