# dash effect パラメータの Sequence 対応チェックリスト（2025-12-29）

目的: `src/grafix/core/effects/dash.py` の `dash_length` / `gap_length` / `offset` を `float` だけでなく `Sequence[float]` も受け取れるようにし、シーケンス指定時は値をサイクルして適用できるようにする（`fill` の `angle` と同系の UX）。

背景:

- `dash.py` のカーネル（`_count_line` / `_fill_line`）は既に `dash_lengths: np.ndarray` / `gap_lengths: np.ndarray` を受け取り、内部で index を回してサイクル適用している。
- しかし public API（`dash(..., dash_length: float, gap_length: float, offset: float, ...)`）が float 固定のため、外から配列指定できない。
- `fill` では `angle: float | Sequence[float]` を `_as_float_cycle` で正規化し、`i % len(seq)` でサイクル適用している。

方針（今回の決定案）:

- `dash_length` / `gap_length` を `float | Sequence[float]` に拡張し、`np.ndarray` も受けられるように正規化して `np.float64` 配列に落とす。
- `dash_length` / `gap_length` のサイクルは「1 本のポリライン内でダッシュごとに進む」（既存カーネルの挙動そのまま）。
- `offset` は `float | Sequence[float]` とし、「入力ポリラインごとに `offset_seq[li % len(offset_seq)]` を適用」する（`fill` の angle と同様の粒度）。
- `offset_jitter` は現状どおり float のまま（非目的）。
- effects ディレクトリ規約に従い、他 effect モジュール（例: `fill.py`）には依存しない。必要なら `src/grafix/core/effects/util.py` か `dash.py` 内に小さく閉じた正規化関数を置く。

非目的:

- `offset_jitter` の Sequence 対応
- parameter_gui の UI で Sequence を編集できるようにすること（`fill` 同様、コードからの指定を想定）
- dash アルゴリズム自体の最適化/刷新（今回の範囲は API + 入力正規化 + テスト）

## 0) 事前に決める（あなたの確認が必要）

- [x] サイクル粒度を以下で進めてよい
  - [x] `dash_length` / `gap_length`: 1 本のポリライン内でダッシュごとにサイクル（既存カーネルに準拠）
  - [x] `offset`: 入力ポリラインごとにサイクル（`li` 単位、`fill` の angle と同様）
- [x] `offset_jitter` の適用順は以下でよい（既存踏襲）
  - [x] `base_offset = offset_seq[li % len(offset_seq)]` を決める
  - [x] `base_offset + jitter` を作り、負値は 0 にクランプしてカーネルへ渡す
- [x] 空シーケンスの扱いは `fill` と同じく `ValueError` でよい（例: `dash_length=[]`）
- [x] 負値の扱いは既存どおり「無効 → no-op（入力を返す）」でよい
  - [x] `dash_length` / `gap_length` に 1 つでも負値が含まれる場合は no-op
  - [x] `offset` は負値なら 0 にクランプ（シーケンス指定でも要素ごとに同様）

## 1) 受け入れ条件（完了の定義）

- [x] `dash(dash_length=float, gap_length=float, offset=float, ...)` の既存テストがそのまま通る（既存互換）
- [x] `dash_length: Sequence[float]` を渡すと、ダッシュ長がサイクルして出力が変わる（単一線で確認）
- [x] `gap_length: Sequence[float]` を渡すと、ギャップ長がサイクルして出力が変わる（単一線で確認）
- [x] `offset: Sequence[float]` を渡すと、ポリラインごとに位相がサイクルして出力が変わる（複数線で確認）
- [x] docstring の型/説明が更新される（Sequence 指定時のサイクル粒度と、GUI 非対象の注記）
- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_dash.py`
- [x] `python -m tools.gen_g_stubs` 後にスタブ同期テストが通る（`tests/stubs/test_api_stub_sync.py`）
- [x] `mypy src/grafix`（任意だが推奨）
- [ ] `ruff check .`（環境に ruff がある場合のみ）

## 2) 変更箇所（ファイル単位）

- [x] `src/grafix/core/effects/dash.py`
  - [x] `dash_length` / `gap_length` / `offset` の型を `float | Sequence[float]` に更新
  - [x] `fill._as_float_cycle` 相当の正規化（`np.ndarray` / Sequence / scalar）を追加（`dash.py` 内）
  - [x] `dash_lengths` / `gap_lengths` を `np.float64` 配列へ正規化してカーネルへ渡す
  - [x] `offset` のシーケンスをポリラインごとに選択して `line_offset_arr` を作る
  - [x] docstring 更新（Sequence の意図/サイクル粒度/空シーケンス不可など）
- [x] `tests/core/effects/test_dash.py`
  - [x] `dash_length` / `gap_length` のサイクルを検証するテストを追加
  - [x] `offset` のポリラインごとのサイクルを検証するテストを追加（2 本以上の入力ジオメトリ）
- [x] `src/grafix/api/__init__.pyi`（自動生成）
  - [x] `E.dash(..., dash_length: float | Sequence[float], ...)` が反映されることを確認（手編集しない）

## 3) 手順（実装順）

- [x] 事前確認: `git status --porcelain` で依頼範囲外の差分/未追跡を把握（触らない）
- [x] 仕様合意: 上の「0) 事前に決める」をあなたと合意する
- [x] `dash.py` に正規化処理を追加し、`dash_length` / `gap_length` / `offset` を Sequence 対応
- [x] docstring 更新（Sequence の意味を明文化）
- [x] core テスト追加（`tests/core/effects/test_dash.py`）
- [x] スタブ再生成: `python -m tools.gen_g_stubs`
- [x] 最小確認: 追加したテスト + stub 同期テストを対象に `pytest -q` を実行
- [ ] 任意: `mypy` / `ruff`

## 4) 実行コマンド（ローカル確認）

- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_dash.py`
- [x] `python -m tools.gen_g_stubs`
- [x] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`
- [x] `mypy src/grafix`
- [ ] `ruff check .`（ruff 未導入なら未実施でよい）

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [x] `dash_length` と `gap_length` の長さが異なる場合（例: 2 要素と 3 要素）、組み合わせは「それぞれ独立にサイクル」になる（既存カーネルの `di/gi` 仕様）。この仕様で問題ないか確認したい。；はい
