# layer_plan.md

どこで: `docs/layer_plan.md`。対象コードは `src/render/layer.py`, `src/render/scene.py`, `src/api/api.py`, `src/api/run.py`, `tests/` 配下など。
何を: `spec.md` に定義された Layer モデルと Scene 正規化、スタイル既定値適用の実装計画をまとめる。
なぜ: Geometry と描画スタイルを分離し、ユーザー draw の戻り値を Layer 列へ揃える基盤を整えることで、描画/エクスポートを一貫化するため。

## 0. ゴールとスコープ
- `spec.md` 2.3, 6.1–6.2, 7.2 の要件に従い、Layer モデルと `L` ヘルパ、Scene 正規化、スタイル解決を段階的に導入する手順を明文化する。
- ランナー (`run`) が `Geometry | Layer | Sequence[...]` を受け取り `list[Layer]` に一本化できるようにする。
- RenderSettings から描画既定値を `Layer` の欠損スタイルへ適用する Contract を決める。
- GPU/キャッシュ層の実装は本計画の外とし、Layer 生成と正規化に直接必要な部分のみ扱う。

## 1. 現状整理（2025-12-08 時点）
- `src/api/run.py` は `draw(t) -> Geometry` を前提に `realize` → `build_line_indices` で即描画しており、複数 Geometry や Layer スタイルに未対応。
- `Layer` データクラスや `L` ヘルパは未実装。`render` 配下にも Layer 専用モジュールが存在しない。
- シーン正規化ヘルパやスタイル既定値適用の仕組みがなく、`line_color`/`line_thickness` はグローバル設定として run 内で直接使用している。
- テストは Geometry とキャッシュ周りのみで、Layer／シーンの挙動を保証するテストが無い。

## 2. 実装アーキテクチャ方針
- `src/render/layer.py` に `Layer`, `RenderDefaults`, `ResolvedLayer`（仮称）と `resolve_layer_style` を定義し、Renderer 以外の層でも共有する。
- `src/render/scene.py` へ `normalize_scene` とヘルパ（たとえば `_iter_layers`）を置き、ユーザー draw の戻り値を flatten して `list[Layer]` を返す純粋関数にする。
- `src/api/api.py` へ `LayerHelper`（`L`) を追加し、`L(...)` と `L.of([...], **style)` で Layer を生成できる公開 API を提供する。
- `src/api/run.py` は `draw` の戻り値型を `SceneOutput = Geometry | Layer | Sequence[Geometry | Layer]` に広げ、Scene 正規化＋スタイル解決を通してから realize/render へ渡す。
- 既定スタイルは `RenderSettings` から `RenderDefaults` を組み立てて適用する。`Layer.thickness <= 0` は早期に `ValueError` を投げて仕様違反を明示する。

## 3. タスク分解

### 3-1. Layer モデル (`src/render/layer.py`)
- [ ] `Layer` を `@dataclass(frozen=True, slots=True)` で定義（`geometry`, `color`, `thickness`, `name`）。RGBA は `tuple[float, float, float, float] | None`。
- [ ] `RenderDefaults` を定義し、`line_color` と `line_thickness`（必要なら `color_space` 拡張も視野）を保持する。
- [ ] `ResolvedLayer` を定義し、`Layer` + 解決済みスタイル（None を埋めた color/thickness）を保持。
- [ ] `resolve_layer_style(layer: Layer, defaults: RenderDefaults) -> ResolvedLayer` を実装し、`thickness is not None and thickness <= 0` の場合は `ValueError`。`None` は defaults で埋める。
- [ ] NumPy スタイル docstring と型ヒントを追加し、`spec.md 2.3` のルール（Layer が GeometryId に影響しないこと等）をコメントで明記。

### 3-2. Layer ヘルパ API (`src/api/api.py` or 新設モジュール)
- [ ] `LayerHelper` クラスを追加し、`__call__(geometry, *, color=None, thickness=None, name=None)` で `Layer` を返すようにする。
- [ ] `LayerHelper.of(geometries: Sequence[Geometry], **style) -> list[Layer]` を提供し、共通 style を適用した Layer 列を生成。
- [ ] `Geometry` 以外が渡された場合のバリデーション（TypeError）と `thickness <= 0` の即時拒否を行う。
- [ ] API ドキュメント文字列を `spec.md 1.1` の G/E/L 説明と整合させ、`__all__` に `L` を追加。`src/api/__init__.py` も更新。

### 3-3. Scene 正規化 (`src/render/scene.py`)
- [ ] `SceneItem = Geometry | Layer | Sequence[Geometry | Layer]` 型エイリアスを定義し、`normalize_scene(scene: SceneItem) -> list[Layer]` を実装。
- [ ] Sequence flatten 時に `str`, `bytes`, `Geometry` を誤って展開しないよう、`collections.abc.Sequence` + 明示的除外で実装。
- [ ] 入力 `Layer` はそのまま返し、`Geometry` は `Layer(geometry=g, color=None, thickness=None, name=None)` に包む。
- [ ] ネスト深い構造や空シーンにも対応し、順序を維持する実装にする。
- [ ] docstring で `spec.md 6.2` の正規化ルールとエラー条件（未知型で `TypeError`）を規定。

### 3-4. ランナー統合 (`src/api/run.py`)
- [ ] `draw` の型を `Callable[[float], SceneItem]` に拡張し、`normalize_scene` を通して `layers` を得る。
- [ ] `RenderSettings` から `RenderDefaults` を構築して `resolve_layer_style` を各 Layer に適用、描画順序を保持。
- [ ] 複数 Layer への対応として `realize`/`build_line_indices` をループ化。`geometry.id` をキーにキャッシュへつながる余地をコメントしておく（実装は別タスク）。
- [ ] `Layer` 名称はログ/デバッグ表示用に予約（まだ未使用でも docstring で目的を説明）。
- [ ] 暫定的に 1 Layer ずつレンダリングし、既存の `DrawRenderer.render` が単体 Geometry を前提にしているならラッパ関数（仮 `render_layer(layer, settings)`）で橋渡しする計画を記載。

### 3-5. テストと検証
- [ ] `tests/test_layer.py`（仮）を追加し、`Layer` バリデーションと `resolve_layer_style` の挙動をカバー。
- [ ] `tests/test_scene.py`（または既存ファイル）で `normalize_scene` の入力/出力パターン、順序維持、ネスト flatten、無効値エラーを確認。
- [ ] `tests/api/test_layer_helper.py` を追加し、`L(...)` と `L.of(...)` の API をスモークテスト。
- [ ] `run` の戻り値型拡張に関しては統合テストを後続に回しつつ、`DrawRenderer` をスタブ化したユニットテスト（`pytest`）で Scene 正規化→resolve までの経路を検証する案を作る。

## 4. リスク・未決事項
- RenderSettings の `line_color/line_thickness` をそのまま defaults に使うが、将来的にパレットや単位系（ワールド単位 vs 画面単位）が増える想定があるため、`RenderDefaults` にフィールド追加できる余白を持たせる。
- `normalize_scene` で巨大/無限再帰にならないよう、Python の Sequence と `Layer`/`Geometry` の型判定順序に注意（先に exact type 判定→Sequence 判定）。
- `L.of` がジェネレータ入力を受けた場合の 1 回消費など、API の落とし穴を docstring で明記する必要がある。
- ランナー側の複数 Layer 描画では、`DrawRenderer` が単一 `RealizedGeometry` に最適化されているため、暫定の for-loop で性能を許容しつつ後続タスクで RenderPrepCache へ接続する計画を別途立てる。

## 5. 進行管理チェックリスト
- [ ] Layer モデル (`Layer`, `RenderDefaults`, `ResolvedLayer`, `resolve_layer_style`).
- [ ] 公開 Layer ヘルパ (`L`, `LayerHelper`, docstring, export)。
- [ ] Scene 正規化 (`normalize_scene`, `SceneItem` 型, 入力バリデーション)。
- [ ] ランナー統合（draw 型拡張、スタイル適用、複数 Layer 描画ループ）。
- [ ] テスト整備（Layer/scene/helper/run スタブ）。
- [ ] ドキュメント更新（README や spec 補足が必要なら別タスク化して記載）。

