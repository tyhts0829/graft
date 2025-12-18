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
- 「総合ランキング」: 各 effect のスコア（例: case 別 mean_ms の幾何平均 or 最大値）で降順
- 「ケース別ランキング」: case ごとに mean_ms 降順の表
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

- [ ] どの「ケース」を採用するか確定（サイズと個数も）
- [ ] 「総合スコア」を何にするか確定（幾何平均 / 最大 / ケース別のみ など）
- [ ] effect の「no-op 回避パラメータ」方針を確定（上書き対象の一覧）
- [ ] `tools/benchmarks/` を新設し、`effect_benchmark.py` の骨組みを作成
- [ ] `src/grafix/core/effects/` から effect を列挙・import して `effect_registry` を埋める
- [ ] 入力ケース生成（再現性のため seed 固定）を実装
- [ ] ベンチ本体（warmup + repeats、例外/ImportError は status に落とす）を実装
- [ ] `results.json` を `data/output/benchmarks/<run_id>/` に保存
- [ ] `report.html` を同ディレクトリに生成（ランキング + ケース別表）
- [ ] 手元確認: 1 回実行して JSON/HTML が生成され、一覧が読めることを確認
- [ ] （任意）README か `tools/benchmarks/README.md` に使い方を短く追記

事前確認したいこと

- 総合ランキングの「スコア」は何を優先しますか？（例: 幾何平均 / 最大ケース / 特定ケース重視）；総合ランキング要らない。個別に横棒グラフでランキング見れればそれでいい。
- ケースの規模感はどれくらいが良いですか？（例: “1 回が 10〜100ms くらい” / “できるだけ軽く” など）；重くても正確な方が良い。1sec くらいまでなら許容。
- shapely 未インストール環境を想定して skipped 設計にして良いですか？（止めたいなら方針変更します）；はい。
