# どこで: `src/grafix/interactive/parameter_gui/gui.py`。
# 何を: DPI/Retina 対応フォントスケール実装を整理する。
# なぜ: 挙動は維持しつつ、紆余曲折で増えた冗長さ/命名のズレを解消して読みやすくするため。

## 前提（現状の挙動）

- 外部モニタ基準の `base_px` を固定し、Retina 等では `window.scale`（backing scale）でフォント生成 px を増やす。
- 具体的には `font_px = base_px * window.scale` でフォントアトラスを作り直す（スケール変化時のみ）。
- `io.font_global_scale` は 1.0 固定（座標系が backing pixel 側になり得るため）。

## ゴール

- いまの「外部でちょうど良い / Retina で小さすぎない」挙動は変えない。
- コードを最小の部品に整理し、命名を実態に合わせる（fb_scale ではなく backing/DPI scale）。
- 初期化時の二重処理など、明らかな無駄を削る。

## 改善チェックリスト（案）

- [x] 命名整理:
  - [x] `_compute_window_fb_scale` → backing scale を表す名前へ変更
  - [x] `self._font_fb_scale` → backing scale を表す名前へ変更
  - [x] `_sync_custom_font_for_window` → 役割が伝わる名前へ変更
- [x] 初期化の冗長さ削減:
  - [x] フォント同期内で `refresh_font_texture()` を呼ぶ設計に寄せ、`__init__` 側の「常に refresh」重複をなくす
  - [x] フォントファイルが無い場合の `refresh_font_texture()` 呼び出し責務を 1 箇所に集約する
- [x] スケール算出のシンプル化:
  - [x] `window.scale` を最優先にし、fallback 分岐を読みやすくする（過度な防御はしない）
  - [x] `round(..., 3)` / 許容誤差の扱いを「必要十分」に調整する（不要なら削除）
- [ ] 手動確認:
  - [ ] 外部モニタ / 内蔵 Retina / 移動（Retina↔外部）でサイズが破綻しない
- [x] 最低限の検証:
  - [x] `python -m py_compile src/grafix/interactive/parameter_gui/gui.py`

## 相談（事前確認）

- [x] 今の「毎フレーム scale を見て、変化したら再生成」のままで良い？（追加のイベントハンドラ等は入れない方針）→ OK
