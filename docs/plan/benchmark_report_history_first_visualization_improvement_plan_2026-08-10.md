# Benchmark report history-first visualization improvement plan

## 1. 目的

benchmark report の主語を「最新 run と直前 run の差」から、
**「同じ条件で測定された case の性能が、過去から現在までどう推移したか」**へ変更する。

利用者が report を開いて最初に答えられる問いを、次の 4 点に限定する。

1. どの期間に、何回、何 case を測定したか。
2. 選択した case の median が時系列でどう動いたか。
3. その動きは run 内のばらつきである MAD と比べてどの程度か。
4. どの点が同条件で接続でき、どの点が環境・case 定義・計測条件の違いで別系列なのか。

前回比を自動的に採点する regression / improvement chart は report から削除する。
明示的に 2 run を比較したい用途には、既存の
`python -m grafix benchmark compare BASE.json HEAD.json` をそのまま使う。

## 2. 現状の問題

- report の hero、最初の chart、静的 SVG、詳細表が baseline / head / delta を中心に構成され、
  長期推移が補助情報に見える。
- history は最新 run に存在する case だけを対象とするため、過去にしかない case や旧条件の系列が
  report から消える。
- case を含まない別 suite の run、別環境の run、別設定の run が途中にあるだけで、
  前後の互換な点まで別 segment になり、線で接続されない。
- absolute time と「最初の正値からの相対値」が混在し、何を基準に読めばよいか分かりにくい。
- MAD band が信頼区間に見えやすく、2 点だけでも回帰・改善を断定できるように見える。
- report directory に履歴を蓄積すると、古い hard contract failure のために report CLI が
  将来も継続して exit 1 になる。
- CI は一時 directory に 1 run だけ生成するため、現状の artifact は長期履歴を持たない。

## 3. 可視化コンセプト

### 3.1 report は採点表ではなく観測記録にする

- 横軸を UTC の実測日時、縦軸を 1 iteration 当たりの median milliseconds とする。
- default は常に実時間の絶対値とし、初回比、前回比、総合スコアを主表示にしない。
- 上へ動くほど遅く、下へ動くほど速いことは軸で読ませる。
- 自動的な「回帰」「改善」「有意差」「原因」の判定は行わない。
- source commit は系列の分離条件ではなく、変更点を読むための tooltip 情報とする。

### 3.2 異なる仕事量を一つの数値へ集約しない

- case ごとに workload と時間尺度が異なるため、全 case の milliseconds を同一軸に重ねない。
- 幾何平均などの単一性能スコアを作らない。
- 全 case の俯瞰には、case ごとに独立した y 軸を持つ small multiples を使う。
- 色で改善・悪化を採点しない。青は選択中の互換系列、黄は互換性境界、灰は欠測・非選択、
  赤は実際の failure だけに使う。

### 3.3 ばらつきを過剰解釈させない

- 各 run は median の点と線で示す。
- `median ± MAD` は連続した面ではなく、各点の縦 whisker で示す。
- subtitle と tooltip に「MAD は同一 run 内 sample のばらつきであり、信頼区間ではない」と明記する。
- 互換な成功点が 1 点だけの系列には `1 compatible observation; trend unavailable` と表示する。

## 4. 履歴のデータ契約

### 4.1 対象データ

- `<out>/runs/*.json` にある、検証済み schema v4 run を対象とする。
- schema v4 は必要な日時、source、environment、case、measurement、stats、contract 情報を
  すでに保持しているため、schema bump と物理 migration は行わない。
- `legacy/` の schema v2 / schema 不明データは、厳格な environment / case compatibility key、
  checksum、計測情報が不足しているため今回の対象外とする。
- legacy 値を推測で schema v4 に変換したり、v4 系列へ接続したりしない。
- raw samples は browser へ渡さず、median、MAD、p95/p99、sample 数、status などの
  report 用統計だけを view model に保持する。

### 4.2 case 単位の compatibility cohort

full run 全体の case set 一致ではなく、各 case result を次の key で cohort 化する。

```text
case id
+ case compatibility key
+ environment compatibility key
+ run mode
+ effective measurement settings
```

effective measurement settings は既存 compare の意味と揃える。

- 通常 case: `samples`、`warmup`、`target_ns`、`disable_gc`、`timeout`
- self-sampling case: `disable_gc`、`timeout`

次の情報は cohort key に含めない。

- suite / profile / run 全体の case set
- source commit / dirty state
- run id / 作成日時

これにより、`all` と `smoke` のように run 全体の構成が違っても、共有する 1 case の定義、
environment、実効計測条件が同じなら同じ系列へ接続できる。

### 4.3 点と線の接続規則

- 同じ cohort の `status == ok` の点だけを時系列の線で接続する。
- 別 suite の run に選択 case が含まれない場合は「欠測」として coverage へ表示するが、
  同じ cohort の前後の線は切らない。
- 別 environment、別 case 定義、別 measurement settings の点は捨てず、別 cohort として保持する。
- error、timeout、resource-limit、skipped など同 cohort の非成功結果は failure event として表示し、
  その前後を別 segment にする。
- stats を持つ contract-failure は測定点を赤い marker で残すが、成功点と連続線では結ばない。
- checksum が変わった場合は両側の点を残し、`output changed` と注記して segment を分ける。
- 無関係な別 cohort や case 欠測によって、選択 cohort の segment を増やさない。

### 4.4 時系列順

- `created_at` を ISO 8601 として parse し、UTC へ正規化する。
- chart は `(UTC timestamp, run_id)` で安定 sort する。
- parse 不能な日時の run は黙って破棄せず warning と詳細表へ残し、temporal chart からだけ除外する。
- model 構築前に各 run を `case_id -> result` へ index し、全 result 数に対して線形に cohort 化する。

## 5. 画面構成

### 5.1 History scope と latest health

report 冒頭を baseline 説明から履歴の coverage へ置き換える。

- 履歴の開始日時と終了日時
- valid run 数
- unique case 数
- compatibility cohort 数
- 2 点以上の成功観測を持つ cohort 数
- latest run の status、hard/soft contract、warning
- latest warning と historical warning を分けた件数

latest health は現在状態の補助情報として残すが、過去との自動比較結果は表示しない。

### 5.2 Performance history

ページ先頭の主 chart とする。

- category filter と、全履歴 case を含む case selector を置く。
- selector の表示名は `category / label · case_id` とし、最新 run にない case も
  `historical` と明示して選択可能にする。
- default case は「latest run に存在し、latest を含む cohort の成功観測数が最も多い case」とし、
  同数なら case id で決定する。
- cohort は `current/past`、成功観測数、OS/CPU/Python、mode、主要 measurement settings を示す
  人が読める label の独立 row panel として並べる。hash は tooltip の診断情報に留める。
- current cohort を先頭にし、past cohort と同じ y 軸へ重ねない。case と連動しない無効な cohort 選択を
  生まないため、cohort dropdown より全系列を俯瞰できる row facet を採用する。
- x 軸は UTC datetime、y 軸は median milliseconds の linear scale とする。
- median line / point、MAD whisker、latest point の値 label を表示する。
- tooltip に日時、run id、commit、dirty、median、MAD、p95/p99、sample 数、status、
  cohort label、mode、measurement settings、checksum change を表示する。
- absolute milliseconds だけを初版に実装し、relative-to-first toggle は削除する。

### 5.3 Run coverage strip

主 chart の直下に、選択した case / cohort に対する全 run の扱いを 1 行で表示する。

- 青: 選択 case の current cohort に採用した成功点
- 黄: 同じ case だが別 cohort
- 灰: case 欠測
- 赤: 選択 cohort の failure / contract-failure
- tooltip: run、日時、分類、除外または分離の理由

これにより、点がない理由と、どこで比較条件が変わったかを性能線へ混ぜずに説明する。

### 5.4 Case trend overview

全体を探索する副 chart として small multiples を置く。

- 各 panel は case ごとの current cohort を描き、y 軸を独立させる。
- category filter を付ける。
- default は成功観測数の多い順、次に case id 順で最大 12 case を表示する。
- `12 / N cases shown` のように表示上限を明記し、主 chart の selector から全 case へ到達可能にする。
- panel には case label、観測数、最初と最新の日時、最新 median を表示する。
- 変化量の大きさで case をランキングしない。
- 1 点しかない panel は線を描かず、観測数を明記する。

### 5.5 Guardrail history と詳細情報

- latest guardrail の pass/fail だけでなく、同じ contract 定義の `actual / limit` 推移を表示する。
- threshold を `1.0` とし、pass 側は comparator で読む。contract id、comparator、limit、severity、reason が
  変わった場合は別系列にする（現行 schema の contract には unit field がない）。
- latest timing、warnings、contracts、runs、scaling table は補助情報として後段へ移し、
  HTML では折りたためる構成にする。
- detailed runs table から `compatible Δ` 列を削除する。
- historical warning は audit 用に保持し、latest warning と混ぜて現在の異常に見せない。

### 5.6 Static overview

`overview.svg` も差分棒 chart ではなく履歴概要に置き換える。

- header: 期間、run 数、unique case 数、latest health
- latest run に存在する current cohort のうち、成功観測数が多い順の最大 6 case
- case ごとに独立 y 軸を持つ median + MAD whisker の small multiple
- 表示中の case 数と選定規則
- latest の guardrail failure と warning の短い footer

静的 SVG では selector を使えないため、`6 / N cases shown` を明記する。
regression / improvement / base-head delta は載せない。

## 6. report model の変更

- [x] `ReportViewModel` から report 専用の baseline、comparison、delta を削除する。
- [x] `DeltaView`、`RunComparisonView`、`select_baseline()` と report 内の比較 lookup を削除する。
- [x] history coverage、case、cohort、point、failure event、guardrail history の immutable view を追加する。
- [x] `measurement_signature(run, result)` と `trend_cohort_key(run, result)` を純粋関数として一箇所に定義する。
- [x] 全 valid v4 run / case を最新 run 基準で捨てず、1 pass で cohort 化する。
- [x] checksum 変化と同 cohort failure だけから segment を構築する。
- [x] datetime の parse、UTC 正規化、安定 sort、invalid timestamp warning を実装する。
- [x] chart record を有限の JSON-compatible 値に限定し、raw samples を含めない。
- [x] latest summary と historical coverage / warning summary を分離する。

候補となる内部 view は次の責務に分ける。名称は実装時に簡潔さを優先して確定する。

- `HistorySummaryView`
- `TrendCaseView`
- `TrendCohortView`
- `TrendPointView`
- `CoveragePointView`
- `GuardrailTrendView`

report 内部 API は破壊的に置き換え、旧 baseline model の互換 shim は作らない。

## 7. chart / HTML / CLI の変更

- [x] chart 順を history scope、Performance history、coverage strip、case overview、guardrail history、
  latest details の順にする。
- [x] regression chart と relative-to-first chart を削除する。
- [x] Performance history を median line / point + MAD whisker の layered Altair chart で実装する。
- [x] cohort を跨ぐ線、failure を跨ぐ線、checksum 変更を跨ぐ線が生成されない spec にする。
- [x] coverage strip と small multiples を同じ view model から生成する。
- [x] Vega-Lite の scale resolution を明示し、small multiple 間で y scale を共有しない。
- [x] `overview.svg` を history-first な composition へ置き換える。
- [x] hero の baseline 文、regression section、詳細表の `compatible Δ` を削除する。
- [x] latest / historical warning の表示領域を分ける。
- [x] `report.html`、`overview.svg`、`warnings.json` の path と offline 契約を維持する。
- [x] Altair / Vega runtime の inline bundle と lazy import を維持し、通常 runtime と benchmark 計測へ
  plotting dependency を強制しない。
- [x] `benchmark report` の終了 code を、過去全体ではなく最新の valid run に対して判定する。

終了 code は次の契約とする。

- `0`: report 生成成功かつ最新 valid run に hard contract failure なし
- `1`: report 生成成功かつ最新 valid run に hard contract failure あり
- `2`: 有効 run なし、依存不足、CLI 引数、または出力生成の fatal error

古い failure と壊れた historical input の情報は HTML と `warnings.json` へ残すが、
最新 run が正常な report を永続的に exit 1 にはしない。

## 8. 履歴の保存運用

- `benchmark report --out X` は `X/runs/*.json` に実在する履歴だけを可視化する。
- local では毎回同じ `--out` を使い、run id を一意にして履歴を蓄積する運用を文書化する。
- interactive HTML の model では valid v4 history を黙って打ち切らない。表示上限を持つ small multiples と
  static SVG には、表示中件数 / 全件数を必ず表示する。
- coverage は observed result と run timeline を別 record に正規化し、browser 側の lookup で欠測を導出する。
  sparse archive で case × run の直積を埋め込まない。dense archive は入力 result 数に対して線形に増えるため、
  silent cap より履歴の完全性を優先する。
- CI の `${{ runner.temp }}` は workflow ごとに消えるため、現状の 1-run artifact を
  「長期履歴」とは説明しない。
- CI artifact の workflow 間永続化は、正本の保存先、retention、branch / machine の分離、
  trust boundary を決める別計画とする。cache を履歴の正本として暗黙利用しない。
- この改善では、履歴らしい画像を作るためだけに同じ commit の benchmark を重複実行しない。
  複数 run の契約は synthetic fixture と local report で検証する。

## 9. テスト移行

### 9.1 model

- [x] 0 run、1 run、複数 run の empty / one-point / trend 状態をテストする。
- [x] 最新 run にない過去 case も model と selector に残ることをテストする。
- [x] full suite と smoke suite の共有 case が、case 単位で互換なら同じ cohort になることをテストする。
- [x] environment、mode、case compatibility key、通常 case の各 measurement setting が
  cohort を分けることをテストする。
- [x] self-sampling の outer samples / warmup / target 差は cohort を分けず、
  disable_gc / timeout 差は分けることをテストする。
- [x] 別 cohort run が途中にあっても、同 cohort の成功点が接続されることをテストする。
- [x] case 欠測では線が切れず、同 cohort failure と checksum 変更では切れることをテストする。
- [x] contract-failure の測定点を残し、成功線へ接続しないことをテストする。
- [x] offset 付き日時、同時刻の run id tie-break、逆順 input、invalid timestamp warning をテストする。
- [x] median 0、MAD 0、下限 0 clamp、p95/p99 欠測、単一点をテストする。
- [x] 250 run × 50 case の synthetic archive で、全 result が欠落なく線形構造へ集約され、
  silent cap されないことをテストする。wall-clock 閾値は設けない。
- [x] report の旧 baseline / delta model tests を削除し、明示的 2-run 比較の tests は
  `compare` module 側で維持する。

### 9.2 chart / artifact

- [x] 全 Altair spec を `to_dict(validate=True)` で検証する。
- [x] temporal x、absolute median y、MAD whisker、cohort / segment detail、failure layer、tooltip を検査する。
- [x] history が最初の chart で、regression / delta field / baseline 文が spec と HTML にないことを検査する。
- [x] small multiples の y scale が independent で、表示件数が明記されることを検査する。
- [x] coverage 色の意味と分離理由が view model / tooltip に存在することを検査する。
- [x] static SVG を XML parse し、複数日時の history と finite 値を含むことを検査する。
- [x] external `src` / `href` がない offline HTML、inline runtime 1 回、script breakout、
  Unicode line separator、JSON escaping の既存 tests を維持する。
- [x] HTML / spec に raw samples が埋め込まれないことを検査する。
- [x] warnings、contracts、scaling、詳細表、3 artifact path の既存契約を回帰 test する。
- [x] report exit code が「旧 failure + 最新 success」で 0、「最新 failure」で 1 になることをテストする。

## 10. ドキュメント更新

- [x] `docs/memo/performance.md` の strict baseline / delta 説明を history-first の読み方へ置き換える。
- [x] x=UTC datetime、y=median ms、whisker=MAD、line=同一 cohort の意味を記載する。
- [x] MAD は信頼区間ではなく、2 点だけで trend を断定しないことを記載する。
- [x] 同じ `--out` を継続利用し、同じ machine / settings で run を蓄積する例を追加する。
- [x] `benchmark report` は時系列探索、`benchmark compare` は指定した 2 run の厳格比較という
  役割分担を記載する。
- [x] schema v4 のみを対象とし、legacy を接続しない理由を記載する。
- [x] CI artifact は保存済み run の範囲だけを示し、workflow 間履歴を現在は保持しないことを明記する。

## 11. 実装順序

### Phase 1: model の置換

- [x] 既存 tests を history-first の契約へ書き換える。
- [x] datetime / measurement signature / cohort / segment の純粋関数を実装する。
- [x] 全 run / case を一度だけ走査する history model を実装する。
- [x] latest summary、historical summary、coverage、guardrail history を追加する。
- [x] baseline / delta model と report 内参照を削除する。

### Phase 2: interactive report

- [x] Performance history、coverage strip、case overview、guardrail history を実装する。
- [x] hero、section order、detailed table を history-first に変更する。
- [x] empty、one-point、multiple cohort、failure の説明表示を実装する。

### Phase 3: static artifact と CLI

- [x] history-first `overview.svg` を実装する。
- [x] latest-based exit code と warning 分離を実装する。
- [x] offline / atomic write / lazy dependency の既存契約を回帰確認する。

### Phase 4: 検証と文書

- [x] synthetic history tests、既存 benchmark tests、Ruff、mypy を実行する。
- [x] 5 点以上の互換 run、途中の無関係 run、別 cohort、failure、checksum 変更を含む fixture で
  report を生成する。
- [x] desktop 幅と 390 px 幅で selector、tooltip、coverage、長い case label、empty state を目視する。
- [x] `overview.svg` を画像として確認し、異なる panel の y scale と MAD 表示を照合する。
- [x] 文書を更新し、完了した項目へチェックを入れる。

## 12. 非目標

- 統計的有意差、change-point、回帰原因の自動判定
- 前回比ランキング、速度の赤緑採点、単一総合性能スコア
- 異なる cohort や workload の値を一本の線または一つの y 軸へ接続すること
- rolling average / smoothing
- raw sample 分布の browser 描画
- case id から scaling family / x parameter を推測すること
- GitHub、PR、外部 network から metadata を取得すること
- legacy benchmark を推測で schema v4 へ移行すること
- CI の長期履歴 store をこの変更だけで新設すること

## 13. 完了条件

- report の最初の問いと主 chart が「過去の性能推移」になり、base/head/delta を結論に使わない。
- 全 valid schema v4 measured result が 1 つの case cohort に保持され、最新 run にない case も選べる。
- 互換な点は別 suite / 無関係 run を挟んでも接続され、非互換な点は別系列として見える。
- 欠測、failure、checksum change、互換性境界の違いが表示上区別される。
- absolute median と MAD の意味を、表を読まずに理解できる。
- 1 点しかない系列を trend に見せず、MAD を信頼区間と表現しない。
- report 内の regression chart、baseline hero、relative-to-first、`compatible Δ` 列が削除される。
- explicit `benchmark compare`、benchmark run/list、schema v4 JSON は変更なく利用できる。
- `report.html` は自己完結・offline、`overview.svg` は JavaScript 不要で履歴を説明できる。
- 通常の Grafix runtime と benchmark 計測へ plotting dependency を強制しない。
- 過去の hard failure が残っていても、最新 run が正常なら report CLI は 0 で終了する。
- 対象 tests、Ruff、mypy、desktop / narrow browser、SVG 目視確認が完了する。

## 14. 承認ゲート

この文書の方針を確認後に実装を開始する。承認前は source、tests、CI、既存 docs を変更しない。

## 15. 実装結果（2026-08-10）

全実装項目と検証項目を完了した。

- report の主表示を UTC の performance history、cohort 別の独立 panel、MAD whisker、run coverage へ置換した。
- Category / Case の連動 control、failure-only の説明、checksum 境界、guardrail history を実装した。
- sparse archive は observed result と run timeline の lookup に正規化し、case × run の直積を除去した。
- duplicate run ID、invalid timestamp、latest / historical warning、latest-based exit code を明示的に扱う。
- 実データ 4 run / 162 case で offline HTML と SVG を生成し、desktop と 390 px、Chrome の SVG 描画を確認した。
- `tests/devtools/benchmarks`: 228 passed。対象 Ruff、mypy、`git diff --check` も通過した。

意図的に残した制約は次の通り。

- dense 200 run × 162 case（32,400 result）では chart spec が約 19.65 MiB、生成が約 10 秒になる。
  入力 result 数に対して線形であり、履歴を黙って切り捨てない契約を優先する。
- cohort が非常に多い case は縦長になる。狭い画面では chart を横スクロールして読む場合がある。
- cohort の短い表示 label は CPU / Python / mode / measurement を優先し、OS は canonical
  environment compatibility の内部情報として保持する。
