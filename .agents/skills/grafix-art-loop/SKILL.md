---
name: grafix-art-loop
description: Grafixで意味的に異なる少数の作品候補を一度ずつ実装・レンダーし、画像比較でwinnerを選び、明確な欠点がある場合だけ最大1回修正する。Grafix作品の自動生成、art loop、複数案比較、LLMによるcreative codingを依頼されたときに使う。
---

# Grafix Art Loop

良いGrafix作品を少ないLLM処理で作る。登録skillはこれ一つとし、候補数 `n`（既定3）だけを主要パラメータにする。

## 出力契約

次のflat layoutだけを使う。

```text
sketch/agent_loop/runs/<run_id>/
├── run.json
├── candidates/
│   ├── v01/{sketch.py,out.png,stdout.txt,stderr.txt}
│   ├── v02/{sketch.py,out.png,stdout.txt,stderr.txt}
│   └── vNN/{sketch.py,out.png,stdout.txt,stderr.txt}
├── contact_sheet.png
└── final/{sketch.py,out.png,out.svg?}
```

`run.json`だけを管理JSONにする。top-levelへ `theme`、`canvas: {w, h}`、`candidate_count`、`winner`、`reason` を置き、各candidateへ次を置く。
Grafixが自動生成する `*.capture.json` はprovenance出力として許可し、role間管理JSONには数えない。

- concept card: `id`、`concept`、`composition`、`mark`、`palette`、`seed`
- execution: `status`（`pending|rendered|failed`）とexport実行ごとに増やす `attempts`

## Workflow

1. **初期化する。** `n`未指定時は3とし、次を実行する。
   `PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/gl5/bin/python .agents/skills/grafix-art-loop/scripts/init_run_dir.py --n <N>`
   表示された `run_dir` をこのrunの唯一の出力先にする。
2. **一括構想する。** themeとcanvasを反映した `n` 個のconcept cardを一度に作る。候補間で `composition` の意味が異なることだけを確認し、`run.json`へ保存する。
3. **各候補を一度作る。** 実装時だけ [Grafix Quick Guide](references/grafix_quick_guide.md) を読む。candidate makerへ渡す情報は、その候補のconcept card、quick guide、candidate dirだけにする。候補ごとに同じmakerを維持し、`sketch.py`を実装、export、`out.png`の画像確認まで行う。
4. **実行失敗だけを直す。** syntax/API/export失敗時は同じmakerが最大1回修正・再exportする。2回目も失敗なら `failed` とし、次候補へ進む。正常な初稿へ一律の美的refineをしない。
   exportごとにorchestratorがexit codeから `run.json` の `attempts` と `status` を更新し、makerに管理JSONを書かせない。
5. **一括表示する。** 次を一度実行する。
   `PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/gl5/bin/python .agents/skills/grafix-art-loop/scripts/make_contact_sheet.py --run-dir <run_dir>`
6. **短く選ぶ。** 成功候補が0ならworkflow失敗とする。成功候補が2件以上ならfresh judgeがcontact sheet（必要なら候補画像）だけを画像レベルで比較し、winnerと1〜3文の理由だけを返す。成功候補が1件ならそれを選ぶ。結果を `run.json`へ保存する。
7. **必要時だけwinnerを直す。** clipping、焦点不明、余白崩れ、線密度破綻のいずれかが画像上で明確な場合だけ、同じmakerへ短い具体指示を返し、winnerを最大1回patch・export・画像確認する。それ以外は変更しない。
8. **確定する。** 採用した `sketch.py` と `out.png` を `final/` へコピーし、最終PNGを画像確認する。その後 `data/output/png/codex_generated/` を作成し、最終PNGを `<run_id>.png` として複製する。SVGは明示要求時だけexportする。通常runではskill改善reportを作らず、明示的audit要求時またはworkflow失敗時だけ、最大3項目を `run.json.audit` に残す。

## Hard rules

1. 画像、コード、ログ、一時物を含むrun成果物は現在の `run_dir` 配下だけに置く。唯一の例外として、確定した最終PNGを `data/output/png/codex_generated/<run_id>.png` にも複製する。
2. Pythonは `/opt/anaconda3/envs/gl5/bin/python` だけを使う。
3. exportへ `PYTHONDONTWRITEBYTECODE=1` と `--overwrite` を付け、exit code 0と `out.png` の存在を確認する。
4. Layerの `thickness` を `0 < thickness <= 0.005` にする。
5. `RealizedGeometry`を直接importしない。custom `@primitive` / `@effect` は任意とする。
6. contact sheetまたは各候補画像を必ず画像として確認する。
7. 同一batchで構図familyを使い回さない。

## 完了条件

- 成功候補のコード、PNG、stdout、stderrと、短いwinner理由が揃っている。
- final PNGを画像確認済みで、finalコードから再exportできる。
- `data/output/png/codex_generated/<run_id>.png` に最終PNGの複製がある。
- 旧来の多段階層、長文critique、ledger、role別JSON、通常時skill reportを生成していない。
