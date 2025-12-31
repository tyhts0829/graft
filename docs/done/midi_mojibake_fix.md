# MIDI ポート名の文字化け修正（チェックリスト）

## ゴール

`src/grafix/interactive/midi/midi_controller.py` のポート一覧表示で、日本語名が `„Éù...` 等に文字化けせず表示される。

## 対応方針

- 取得したポート名 `str` に対して、UTF-8 のバイト列を MacRoman として誤解釈した可能性が高いので、表示前に `name.encode("mac_roman").decode("utf-8")` を試す。
- 変換に失敗した場合は元の文字列を使う（例外で落とさない）。

## チェックリスト

- [x] 現象の対象箇所を確認（入力/出力ポート名表示）
- [x] `mac_roman -> utf-8` の復元関数を追加（失敗時はそのまま）
- [x] 復元関数をポート名一覧表示に適用
- [ ] 実機で表示が `USB MIDI装置`, `OXI E16 ポート1` 等になることを確認
- [ ] `ruff check src/grafix/interactive/midi/midi_controller.py` を実行
- [ ] （必要なら）pytest を対象限定で実行

## 事前確認したいこと

- [x] この復元を「表示のみに適用」する（内部でポートを開くときのキーには使わない）で良いか；はい

## メモ

- `ruff` コマンドが環境に無く、`ruff check ...` は未実行。
- 実機での表示確認は `python src/grafix/interactive/midi/midi_controller.py` で確認する。
