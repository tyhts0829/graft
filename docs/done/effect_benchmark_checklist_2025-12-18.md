# effect_benchmark 実装チェックリスト（2025-12-18）

目的

- `effect_benchmark.py` を実行すると `src/grafix/core/effects/` 配下の effect を網羅してベンチを回す。
- 「点数が大きい/線が多い/閉曲線」など特徴の違う入力ジオメトリ（=ケース）ごとに、複数回実行の平均時間を計測する。
- 結果を `data/` 配下へ JSON 永続化し、同じ内容を一覧できる HTML レポートも生成する（どの effect が遅いかが一目で分かる）。

前提/方針

- 依存追加なし（標準ライブラリ + 既存依存のみ）。
- `interactive/runtime/perf.py` は「フレーム計測」用途なので触らない（今回のベンチは開発用ツールとして `tools/` に分離）。
- 対象 effect は原則 `src/grafix/core/effects/*.py`（`from_previous_project/` は除外）。
- Numba 系 effect は初回 JIT が混ざるため、ウォームアップと本計測を分ける。
- shapely 等のオプショナル依存が無い場合は、その effect を「skipped」としてレポートに残す（全体は止めない）。

配置案（新規）

- `tools/benchmarks/effect_benchmark.py`（CLI: 計測 + JSON + HTML）
- `tools/benchmarks/cases.py`（入力ケース生成）
- `tools/benchmarks/report.py`（HTML 生成）
- 出力: `data/output/benchmarks/<run_id>/results.json` と `report.html`

CLI の最低仕様（案）

- `python tools/benchmarks/effect_benchmark.py`
- オプション（最小）:
  - `--out data/output/benchmarks`（出力ルート）
  - `--repeats 30`（本計測回数）
  - `--warmup 3`（ウォームアップ回数）
  - `--seed 0`（ケース生成の乱数）
  - `--only scale,rotate,...` / `--skip offset,partition,...`（任意）
  - `--cases small,line_many,ring_big,...`（任意）

JSON 形式（叩き台）

- `meta`: 実行日時、python/platform、repeats/warmup、seed、（可能なら）git sha
- `cases[]`: case_id、説明、頂点数、ポリライン数、閉曲線か、など
- `effects[]`:
  - `name`（op 名）
  - `module`（分かる範囲で）
  - `params`（ベンチで使った引数。no-op 回避のためデフォルトから上書きがあれば明示）
  - `results`: case_id -> { status, mean_ms, stdev_ms, min_ms, max_ms, n, error? }

HTML レポート（叩き台）

- 先頭: 実行メタ + リンク（results.json）
- 「ケース別ランキング」: case ごとに mean_ms 降順の表
- 横棒グラフ: case ごとに mean_ms 降順のランキングを表示
- skipped/error も表に残す（理由を表示）

入力ケース（案）

- 小: 2 点の線分（1 polyline）
- 多点: 1 polyline の頂点数が大きいケース（例: 10k 点の折れ線）
- 多線: polyline 本数が多いケース（例: 2 点線分を 5k 本）
- 閉曲線: 大きめの閉ポリライン（例: 2k 辺の正多角形リング）
- 平面リング（shapely 系向け）: 複数リング（外周 + 穴）を少数含むケース（必要なら）

effect パラメータの扱い（案）

- 原則 `effect_registry.get_defaults(name)` を使う。
- 明確に no-op になりやすい effect はベンチ用に上書きする（例）:
  - `translate`: `delta=(10.0, 5.0, 0.0)`
  - `affine`: `delta=(10.0, 5.0, 0.0), rotation=(5.0, 0.0, 0.0), scale=(1.1, 1.1, 1.0)`
  - `offset/partition` など shapely 必須系は「依存が無ければ skipped」

チェックリスト

- [x] どの「ケース」を採用するか確定（サイズと個数も）
- [x] 「総合スコア」を何にするか確定（結論: 総合ランキング無し）
- [x] effect の「no-op 回避パラメータ」方針を確定（上書き対象の一覧）
- [x] `tools/benchmarks/` を新設し、`effect_benchmark.py` の骨組みを作成
- [x] `src/grafix/core/effects/` から effect を列挙・import して `effect_registry` を埋める
- [x] 入力ケース生成（再現性のため seed 固定）を実装
- [x] ベンチ本体（warmup + repeats、例外/ImportError は status に落とす）を実装
- [x] `results.json` を `data/output/benchmarks/<run_id>/` に保存
- [x] `report.html` を同ディレクトリに生成（ケース別ランキング + 横棒）
- [x] 手元確認: 1 回実行して JSON/HTML が生成され、一覧が読めることを確認
- [ ] （任意）README か `tools/benchmarks/README.md` に使い方を短く追記

決定事項（ユーザー回答）

- 総合ランキング: 不要（ケース別に横棒グラフでランキングが見えれば十分）
- ケースの規模感: 重くても正確さ優先（1 回あたり ~1 秒程度まで許容）
- shapely 未インストール: skipped で継続して良い

実行例

- `python -m tools.benchmarks.effect_benchmark`
- `python -m tools.benchmarks.effect_benchmark --cases ring_big --repeats 30 --warmup 3`
- `python -m tools.benchmarks.effect_benchmark --only offset,partition`（shapely 無しなら skipped）

気づき（後回しでOK）

- `grafix.api` は `grafix.api.primitives` の import で失敗し得るため（`circle.py` 不在）、本ベンチは `grafix.api` を import せず `grafix.core.effects` を直接 import する実装にした。
