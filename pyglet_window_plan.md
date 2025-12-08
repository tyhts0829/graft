# pyglet_window_plan.md

どこで: リポジトリ直下 `main.py` / `src/api` / `src/render`。
何を: pyglet + ModernGL で仕様 13.6 の最小ライン描画をウィンドウ表示するまでの実装計画。
なぜ: `spec.md` と `main.py` を実行して実際にプレビューできる動線を確立するため。

## 0. ゴールとスコープ

- `main.py` を実行すると pyglet ウィンドウが開き、`draw(t)` が生成する Geometry が実際に描画される状態にする。
- `spec.md` 13.6 の最小パイプライン（RealizedGeometry → indices → GPU 転送 → シェーダ描画）を満たす。
- 最小限のプレビュー機能に絞り、Parameter GUI やキャッシュ容量管理は別タスクとする。

## 1. 現状と前提

- `run` が未実装のため `main.py` は描画まで到達しない。
- `line_mesh.py` / `shader.py` / `utils.py` に最小の描画部品はあるが、ウィンドウ・イベントループは無い。
- 依存に `pyglet` / `moderngl` が明示されておらず、導入には事前承認が必要（AGENTS 安全方針）。
- Geometry/realize 周りは既存実装をそのまま利用する想定。

## 2. 設計スケッチ（最小フロー）

- pyglet で GL4.1 Core のダブルバッファウィンドウを開く（MSAA はあれば利用）。
- pyglet の GL コンテキストから `moderngl.create_context()` で ModernGL コンテキストを取得。
- 起動時に `Shader.create_shader` と `LineMesh` を初期化。
- フレームループ:
  - `t` を起動からの経過秒として pyglet の `clock.get_time()` で取得。
  - `draw(t)` を呼び、Geometry を得て `realize` で `RealizedGeometry` を取得。
  - `offsets` から GL_LINES 用のインデックス配列を生成（ポリライン間は PRIMITIVE_RESTART を挿入）。
  - `mesh.upload(coords, indices)` → `projection` と `color` を設定し `vao.render`。
  - 厚みはジオメトリシェーダの `uniform float line_thickness` へそのまま渡し、法線方向に `thickness/2` オフセットした 4 頂点矩形へ展開（miter/cap なし）。
  - 各レイヤー描画直前に `layer.thickness` があればそれを、なければ基準値を `line_thickness` に適用。
  - `ctx.clear` で背景色を設定し、描画後は pyglet のダブルバッファを `flip`。
- 終了時に `mesh.release()`、ModernGL コンテキストやウィンドウを破棄。

## 3. 実装タスクチェックリスト

- [ ] 依存確認: `pyglet`, `moderngl` のインストール有無を確認し、必要なら追加の承認を得る。
- [ ] API 整備: `src/api/run.py`（または同等）にランナー関数を実装し、`api.__init__` に `run` を公開する。
- [ ] ウィンドウ初期化: pyglet で GL4.1 Core / ダブルバッファ / 可変サイズウィンドウを作成し、リサイズ時にビューポート更新を入れる。
- [ ] コンテキスト準備: pyglet ウィンドウから ModernGL コンテキストを生成し、`Shader` と `LineMesh` を初期化。
- [ ] インデックス生成ヘルパ: `RealizedGeometry.offsets` から `uint32` インデックス + `PRIMITIVE_RESTART` を作る関数を追加。
- [ ] フレームループ: `clock.schedule_interval` で更新しつつ `draw(t)` → `realize` → `upload` → `render` の流れを `on_draw` に実装。
- [ ] スタイル適用: `run` の引数として背景色・線色・線幅・キャンバス寸法を受け、`projection` と `line_thickness` 設定に反映（`line_thickness` は clip 空間ベースで最終線幅）。
- [ ] 終了処理: `on_close` で `mesh.release()` を呼び、コンテキスト/ウィンドウを安全に破棄。

## 4. 動作確認項目

- [ ] `python main.py` でウィンドウが開き、円が回転/スケールして描画されることを目視確認。
- [ ] リサイズしても描画が継続し、線幅や投影が破綻しない。
- [ ] ウィンドウを閉じても例外なく終了し、GPU リソースが解放される。

## 4.1 厚みの振る舞い補足
- 厚みはクリップ空間ベースなので、キャンバスサイズが変わると見た目の mm 厚みも比例して変化する。`render_scale` を上げるとピクセル上の線幅は同じ mm 厚みのまま高精細化。
- G-code / PNG エクスポートは中心線ジオメトリをそのまま使うため、太さ指定はプレビュー描画専用。

## 5. 承認・オープン事項

- 依存追加（`pyglet`, `moderngl`, 必要なら `pyglet-moderngl` 拡張）の可否。
- 対象 GL バージョン（現状シェーダが `#version 410` 前提）。macOS 4.1 Core 固定で進めてよいか。
- 本タスクではキャッシュ上限や Parameter GUI を外してよいか（後続タスクで扱う前提）。；はい。
