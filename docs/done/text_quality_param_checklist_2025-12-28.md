# Text primitive の `tolerance` → `quality` 置換チェックリスト（2025-12-28）

目的: `src/grafix/core/primitives/text.py` の `tolerance`（小さいほど精緻）を、直感に合う `quality`（大きいほど精緻）へ置き換える。

背景:

- 現状の `tolerance` は「平坦化許容差（= 近似セグメント長）」で、値を小さくすると点が増えて精緻になる。
- UI スライダーやパラメータ名としては「値が大きいほど良い」が直感的なので、外向け引数を反転させたい。

方針（今回の決定）:

- 互換ラッパー/シムは作らない（破壊的変更 OK）。
- 外向け API は `quality`（0..1）に統一し、内部で `tolerance`（セグメント長）へ変換して既存のアルゴリズムを維持する。

## 0) 事前に決める（決定済み）

- [x] `quality` のレンジを `0.0..1.0` に固定する（UI もこの範囲）。
- [x] `quality -> tolerance` は指数補間（対数スケール）で反転させる。
  - `tolerance = tol_max * (tol_min / tol_max) ** quality`
  - `quality=0 -> tol_max`（粗い/速い）, `quality=1 -> tol_min`（精緻/重い）
- [x] `tol_min=0.001`, `tol_max=0.1`（em 基準の近似セグメント長）に固定する。
- [x] `quality` のデフォルト値は `0.5`（指数補間なら `tolerance≈0.01`）。
- [x] `quality` は `0..1` にクランプする（範囲外入力は丸める）。

## 1) 受け入れ条件（完了の定義）

- [x] `G.text(quality=...)` で、`quality` を上げるほど点数（`coords.shape[0]`）が増える。
- [x] 公開 API から `tolerance` 引数が無くなる（破壊的変更を許容）。
- [x] Param GUI で `quality` が `0..1` のスライダーとして表示される。
- [x] スタブ（`src/grafix/api/__init__.pyi`）が再生成され、スタブ同期テストが通る。

## 2) 実装タスク（コード変更）

- [x] `src/grafix/core/primitives/text.py` の `text_meta` を `tolerance` → `quality` に置換（`ui_min=0.0`, `ui_max=1.0`）。
- [x] `src/grafix/core/primitives/text.py` の `text()` 引数を `tolerance` → `quality` に置換し、docstring も更新する。
- [x] `src/grafix/core/primitives/text.py` 内部で `quality` を `0..1` にクランプし、`tolerance = 0.1 * (0.001 / 0.1) ** quality` で `seg_len_units` を決める。
- [x] `rg -n "tolerance" src/grafix` で公開 API 側の残存参照を消す（必要なら docs/sketch も更新）。

## 3) スタブ生成（公開 API の反映）

- [x] スタブ生成元の更新が不要なことを確認する（primitive の meta 更新で `G.text(..., quality=..., ...)` が生成される）。
- [x] `python -m tools.gen_g_stubs` を実行し、`src/grafix/api/__init__.pyi` を更新する（手編集しない）。

## 4) テスト（最小の安全柵）

- [x] `tests/core/test_text_primitive.py` に「`quality` を上げると点数が増える」テストを追加する。
- [x] `PYTHONPATH=src pytest -q tests/core/test_text_primitive.py tests/stubs/test_api_stub_sync.py` を通す。

## 5) 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] 将来 `quality` が他プリミティブにも増えそうなら、`flatten_quality` のようにドメインを明示するか検討する（今回はシンプル優先で `quality` のままでも良い）。
