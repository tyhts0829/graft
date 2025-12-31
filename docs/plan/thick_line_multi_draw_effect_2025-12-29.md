# 複数描画で太線化する effect 追加チェックリスト（2025-12-29）

目的: 「線を複数回描画して見た目の線幅を太くする」ための新規 effect を追加する。

背景:

- ペンプロッタ等では物理ペン幅が固定で、太線を出したい場合に表現が詰まりがち。
- `buffer` は輪郭生成（形状変換）であり「線を濃く/太く見せる」用途とは違う。
- 同じ線を少しずつずらして複数回描くと、見た目上の太線（インクの重なり）を作れる。

方針（今回の決定案）:

- effect 名（関数名）は `bold` とし、`src/grafix/core/effects/bold.py` を追加する。
- 実装は「入力ジオメトリ全体を N 回複製し、各コピーに平行移動（オフセット）を与える」だけにする。
- オフセットは XY 平面内（z は維持）で生成し、同じ入力・同じパラメータなら決定的に同じ結果にする（seed）。
- effect 間依存は禁止（`bold.py` は単体完結）。共通ロジックが必要なら `src/grafix/core/effects/util.py` に置く。

非目的:

- 形状としての厳密な「線幅（stroke）」実装（join/cap、自己交差処理、面生成など）
- ペン幅・紙特性を考慮した物理シミュレーション
- `buffer` の代替や統合（用途が別）
- 互換ラッパー/シムの追加（破壊的でもシンプル優先）

## 0) 事前に決める（あなたの確認が必要）

- [x] effect 名を `bold` で確定する（候補: `thicken` / `multistroke` / `bold`）
- [x] パラメータを以下で確定してよい
  - `count: int`（デフォルト例: 5、1 以下は no-op）
  - `radius: float`（デフォルト例: 0.5、0 以下は no-op、XY の最大オフセット量 [mm] 相当）
  - `seed: int`（デフォルト例: 0、オフセット生成の決定性用）
- [x] `count` は「出力する総ストローク数（元の線を 1 本含む）」の意味でよい（`count=1` は完全 no-op）
- [x] オフセット分布は v1 では「一様円盤（半径 `radius`）」サンプリングでよい（`np.random.default_rng(seed)`）
- [x] 3D 入力でも、オフセットは常に XY で適用してよい（z は保持、任意平面への追従はやらない）

## 1) 受け入れ条件（完了の定義）

- [x] `bold([])` が空の `RealizedGeometry` を返す
- [x] `count<=1` または `radius<=0` で no-op（入力 `RealizedGeometry` をそのまま返す: `out is base`）
- [x] 出力の `coords/offsets` が `RealizedGeometry` の不変条件を満たす（dtype と writeable=False を含む）
- [x] `count>1` のとき `coords.shape == (N*count, 3)`、`offsets.size == (M*count + 1)` になる（N=頂点数、M=ポリライン数）
- [x] 同じ入力・同じパラメータ（seed 含む）で結果が決定的に一致する
- [x] `PYTHONPATH=src pytest -q tests/core/test_effect_bold.py`
- [x] `python -m tools.gen_g_stubs` 後にスタブ同期テストが通る（`tests/stubs/test_api_stub_sync.py`）
- [ ] `mypy src/grafix`（任意だが推奨）
- [ ] `ruff check .`（環境に ruff がある場合のみ。ruff 未導入なら未実施でよい）

## 2) 変更箇所（ファイル単位）

- [x] `src/grafix/core/effects/bold.py`
  - [x] `bold_meta`（`ParamMeta`）を定義
  - [x] `@effect(meta=bold_meta)` で `bold(...) -> RealizedGeometry` を実装
  - [x] docstring（NumPy スタイル、日本語）を追加
- [x] `src/grafix/api/effects.py`
  - [x] `from grafix.core.effects import bold as _effect_bold  # noqa: F401` を追加（登録のため）
- [x] `tests/core/test_effect_bold.py`
  - [x] 空入力
  - [x] no-op 条件（`count<=1` / `radius<=0`）
  - [x] 配列形状と offsets 構造
  - [x] 決定性（同 seed で一致）
- [x] `src/grafix/api/__init__.pyi`（自動生成）
  - [x] `E.bold(...)` が反映されることを確認（手編集せず再生成）

## 3) 手順（実装順）

- [x] 事前確認: `git status --porcelain` で依頼範囲外の差分/未追跡を把握（触らない）
- [x] 0. の仕様をあなたと合意（命名/パラメータ/分布/XY 固定）
- [x] `bold.py` を追加して最小実装（no-op/空入力/複製+平行移動）
- [x] `src/grafix/api/effects.py` に import を追加して effect 登録
- [x] core テスト追加（`tests/core/test_effect_bold.py`）
- [x] スタブ再生成: `python -m tools.gen_g_stubs`
- [x] 最小確認: 追加したテスト + スタブ同期テストを実行
- [ ] 任意: `mypy` / `ruff`

## 4) 実行コマンド（ローカル確認）

- [x] `PYTHONPATH=src pytest -q tests/core/test_effect_bold.py`
- [x] `python -m tools.gen_g_stubs`
- [x] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`
- [ ] `mypy src/grafix`
- [ ] `ruff check .`（ruff 未導入なら未実施でよい）

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [x] 「線の法線方向へオフセット」より「全体平行移動コピー」のほうが意図に合っているか（求める見た目の確認）
- [x] `radius` の UI レンジ（例: 0〜3mm / 0〜10mm）をどうするか（採用: 0〜1）
- [x] `count` の UI レンジ（例: 1〜50 / 1〜200）をどうするか（採用: 1〜10）
