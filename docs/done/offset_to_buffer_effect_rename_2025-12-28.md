# offset -> buffer エフェクト命名変更チェックリスト（2025-12-28）

目的: `E.offset(...)` で提供している輪郭オフセット effect を、実装実態（Shapely の `buffer`）に合わせて `E.buffer(...)` として提供する（破壊的変更）。

背景:

- 現在の `offset` effect は Shapely の `LineString(...).buffer(...)` を使用しており、概念としては「buffer」。
- `offset` という名前は、dash/repeat など他文脈の `offset`（位相/終点オフセット）と混同しやすい。

方針（今回の決定）:

- effect 名（op 名）を `offset` から `buffer` へ変更する（互換ラッパー/シムは作らない）。
- パラメータ名は原則そのまま（`join/distance/keep_original`）とし、`segments_per_circle` は Shapely に合わせて `quad_segs` に寄せる。
- `tools/gen_g_stubs.py` によるスタブは再生成し、同期を保つ。

非目的:

- dash/repeat など他の `offset` パラメータ名の変更
- Shapely の buffer パラメータや挙動変更（内側オフセット対応、cap_style 対応など）
- 旧 `offset` 名の互換 API（`E.offset` や alias モジュール）提供
- 既存の保存済みパラメータ/設定ファイルの自動移行（必要なら別タスク）

## 0) 事前に決める（あなたの確認が必要）

- [x] 新 effect 名: `buffer` で確定
- [x] 旧名 `offset` は完全に削除する（互換なし）
- [x] 実装ファイルも `src/grafix/core/effects/buffer.py` へ rename する（スタブ生成が `grafix.core.effects.<name>` を前提としているため）
- [x] テスト/ベンチ等のファイル名も合わせて rename する（`test_buffer.py` など）
- [x] GUI/パラメータ永続化で `op="offset"` を含む既存データは壊れてよい（移行しない）
- [x] `segments_per_circle` は `quad_segs` に rename する（破壊的変更）

## 1) 受け入れ条件（完了の定義）

- [x] `E.buffer(...)` が未登録エラーにならず、realize まで到達する
- [x] `E.offset(...)` は未登録として失敗する（意図した破壊的変更）
- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_buffer.py`
- [x] `python -m tools.gen_g_stubs` 後にスタブ同期テストが通る（既存の `tests/stubs/`）
- [ ] `ruff check .`（環境に ruff 未導入なら省略）
- [x] `mypy src/grafix`
- [ ] `python -m tools.benchmarks.effect_benchmark --only buffer` が動く（shapely 未導入なら skipped でも良い）

## 2) 変更箇所（ファイル単位）

- [x] `src/grafix/core/effects/offset.py` -> `src/grafix/core/effects/buffer.py`
  - [x] `offset_meta` 等の識別子を `buffer_meta` へ整理
  - [x] `@effect(meta=...)` の登録関数名を `buffer` に変更（op 名が `buffer` になる）
  - [x] `segments_per_circle` を `quad_segs` に rename
  - [x] docstring の文言を `buffer` に合わせる（内容は変えない）
- [x] `src/grafix/api/effects.py`
  - [x] レジストリ登録用 import を `from grafix.core.effects import buffer as _effect_buffer` に変更
- [x] `tests/core/effects/test_offset.py` -> `tests/core/effects/test_buffer.py`
  - [x] `E.offset` を `E.buffer` に更新
  - [x] `segments_per_circle` を `quad_segs` に更新
  - [x] docstring/テスト関数名を `buffer` に合わせる
- [x] `tools/benchmarks/effect_benchmark.py`
  - [x] overrides のキーを `"offset"` -> `"buffer"` に変更
  - [x] overrides の引数名を `"segments_per_circle"` -> `"quad_segs"` に変更
- [x] `src/grafix/api/__init__.pyi`（自動生成）
  - [x] 手編集せず `python -m tools.gen_g_stubs` で `offset` メソッドを `buffer` に更新

## 3) 手順（実装順）

- [x] 事前確認: `git status --porcelain` で依頼範囲外の差分/未追跡を把握（触らない）
- [x] 参照箇所の棚卸し: `rg -n "\\boffset\\b" src tests tools` で「effect 名としての offset」を洗い出す
- [x] コア effect を rename: `offset.py` -> `buffer.py`、関数名/識別子を変更
- [x] API/ツール/テストを rename: `E.buffer` に置換、bench overrides 更新、テストファイル rename
- [x] スタブ再生成: `python -m tools.gen_g_stubs`
- [x] 最小確認: `PYTHONPATH=src pytest -q tests/core/effects/test_buffer.py`
- [ ] 静的チェック: `ruff check .`（環境に ruff 未導入なら省略）
- [x] 静的チェック: `mypy src/grafix`
- [ ] 追加確認（任意）: ベンチ/GUI で `buffer` が一覧に出ること

## 4) 実行コマンド（ローカル確認）

- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_buffer.py`
- [ ] `ruff check .`（環境に ruff 未導入なら省略）
- [x] `mypy src/grafix`
- [x] `python -m tools.gen_g_stubs`
- [ ] `python -m tools.benchmarks.effect_benchmark --only buffer`（shapely なしなら skipped を確認）

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [x] `segments_per_circle` を `quad_segs` に寄せるか（今回は非目的）;はい
- [x] `buffer` の「内側距離（負値）」対応や `cap_style` 対応（別タスク）; 今回はなし
- [x] 既存の保存済みパラメータ/プリセットの一括置換スクリプトが必要か（別タスク）;不要
