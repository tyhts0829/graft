# Grafix Art Loop contact sheet 出力先変更計画

## 目的

`make_contact_sheet.py` が生成する contact sheet の既定出力先を
`data/output/png/codex_generated/` に変更し、run ID から出力ファイル名を
一意に決める。

## 期待する出力

- run directory:
  `sketch/agent_loop/runs/run_20260724_175132_n3`
- contact sheet:
  `data/output/png/codex_generated/run_20260724_175132_n3_contact_sheet.png`

## アクション

- [x] `.agents/skills/grafix-art-loop/scripts/make_contact_sheet.py` の既定出力先を
      `data/output/png/codex_generated/` に変更する。
- [x] 既定ファイル名を `<run_dir.name>_contact_sheet.png` に変更する。
- [x] `--out` の説明と出力パス検証を新しい保存場所に合わせる。
- [x] `.agents/skills/grafix-art-loop/SKILL.md` の出力契約と保存先ルールを
      実装に合わせて更新する。
- [x] `--dry-run` で例示 run の出力パスが期待値と一致することを確認する。

## 検証結果

- 対象スクリプトの構文チェックに成功した。
- `run_20260724_175132_n3` に対する `--dry-run` の出力が
  `data/output/png/codex_generated/run_20260724_175132_n3_contact_sheet.png`
  になることを確認した。

## 非対象

- 既存の各 run directory にある `contact_sheet.png` の移動・削除
- final PNG の保存規則の変更
- contact sheet のレイアウトや画質の変更
