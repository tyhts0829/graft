# Benchmark report visualization implementation plan

## 1. 目的

schema v4 の benchmark 結果を、表を読まなくても「成功したか」「何が遅くなったか」
「性能がどう推移したか」「guardrail に余裕があるか」を把握できる report にする。

現行の厳格な schema 検証、environment / case / measurement 互換性、warning、contract、
詳細表は維持する。可視化が性能値の意味を歪めないことを優先する。

## 2. 採用方針

### 2.1 可視化 stack

- Python を benchmark JSON の読込、schema 検証、互換性判定、比較、chart view model の
  唯一の正とする。
- chart 定義には Altair 6、offline HTML と静的画像変換には `vl-convert-python` を使う。
- Altair へ渡すデータは Python で確定済みの小さな inline record に限定し、Pandas を
  追加しない。
- 人が探索する主成果物は、JavaScript runtime を内包した自己完結 `report.html` とする。
- CI、レビュー、埋込み向けに、同じ chart 定義から静的 `overview.svg` も生成する。
- 既存の warning、contract、詳細表は report 内に残す。

### 2.2 依存の配置

- 通常の Grafix runtime へ plotting stack を強制しない。
- 次の optional extra を追加する。

```toml
[project.optional-dependencies]
benchmark-report = [
  "altair>=6,<7",
  "vl-convert-python>=1.9,<2",
]
```

- benchmark の計測自体は追加依存なしで動作させる。
- enhanced report の生成環境と CI は `.[benchmark-report]` を明示して構築する。

### 2.3 初版では採用しないもの

- Chart.js / Plotly / Matplotlib
- npm / Node build
- CDN または report 表示時の network access
- JavaScript による schema 解釈、互換性判定、base/head 選択
- 手書き SVG chart geometry
- case ID から scaling family や x 軸を推測する処理
- pixel snapshot を正とするテスト

Altair / Vega-Lite の宣言的 spec を使い、表示上の selection、filter、tooltip だけを
同梱 JavaScript runtime に担当させる。性能値の意味と比較可否は Python 側で確定する。

## 3. 初版の表示

### 3.1 Summary

- [x] 最新 run、source、suite/profile/mode、case 数を表示する。
- [x] ok / error / timeout / contract-failure、warning、hard/soft contract の集計をカード表示する。
- [x] 比較可能な baseline がない場合は、理由を明示して差分 chart を空表示にしない。

### 3.2 Regression / improvement

- [x] 最新 run と、case set・environment・measurement settings が一致する直前 run を選ぶ。
- [x] case compatibility key が一致する結果だけを比較する。
- [x] `head / base - 1` の差分率を、0% 中心の横棒 chart で表示する。
- [x] 遅化を赤、改善を緑、変化なしを中立色にする。
- [x] 悪化上位と改善上位を優先し、category filter を付ける。
- [x] tooltip に case、category、base/head 中央値、MAD、差分率を表示する。
- [x] 差分を統計的有意差とは表現せず、noise を含む観測値であることを明記する。

### 3.3 Case history

- [x] case selector で 1 case を選ぶ時系列 chart を表示する。
- [x] median の折れ線と MAD band を重ねる。
- [x] absolute time と最初の互換 run 比を切り替えられるようにする。
- [x] environment・case・measurement settings が互換な run だけを同じ系列として結ぶ。
- [x] 欠測、error、非互換 run を連続線で接続しない。

### 3.4 Latest timing overview

- [x] 最新 run の計測済み case を、中央値の長い順に表示する。
- [x] 広い値域を扱える対数軸の横棒 chart にする。
- [x] category selector と case tooltip を付ける。
- [x] self-sampling、status、contract-failure を視覚的に区別する。
- [x] 異なる workload 間の絶対時間は最適化優先度を直接意味しない旨を表示する。

### 3.5 Contract guardrails

- [x] 数値として比較可能な soft contract を actual / limit の bullet chart で表示する。
- [x] comparator の向きを反映し、pass/fail と余裕を誤解なく示す。
- [x] boolean、文字列、または chart 化できない contract は既存の詳細表示へ残す。

### 3.6 Static overview

- [x] summary、最大の遅化・改善、主要 guardrail を `overview.svg` にまとめる。
- [x] interactive selector がなくても最新状態を理解できる内容に限定する。
- [x] report と同じ view model と chart 定義を使い、値や色の意味を二重実装しない。

### 3.7 Scaling

- [x] 現行の scaling 表は維持する。
- [x] 初版では case ID から系列や x 軸を推測して曲線を作らない。
- [x] 本格的な scaling chart は、schema に family / x parameter / unit を追加する別計画とする。

## 4. 実装構造

- [x] `LoadedRuns` から immutable な report/chart view model を構築する純粋関数を追加する。
- [x] baseline 選択と measurement 互換性判定を一箇所に集約し、表と chart で共有する。
- [x] Altair spec 構築と export を `report.py` から分離した内部 module に置く。
- [x] chart data は JSON-compatible な有限値だけに正規化し、raw benchmark JSON を browser へ渡さない。
- [x] Altair の selection/filter は表示対象の切替だけに使い、比較計算をさせない。
- [x] `Chart.to_html(inline=True)` 相当で Vega / Vega-Lite / Vega-Embed を HTML 内へ同梱する。
- [x] `vl-convert-python` で同じ spec から `overview.svg` を生成する。
- [x] 同梱フォントを登録し、macOS / CI 間の文字 layout 差を抑える。
- [x] chart がなくても warning と詳細表を生成できる構造を維持する。
- [x] `benchmark report --out ...` の CLI と入力 directory 契約は変更しない。
- [x] CLI は `report.html`、`overview.svg`、`warnings.json` の出力 path を表示する。

## 5. テスト

- [x] baseline が同一 environment・mode・measurement・case set に限定されることをテストする。
- [x] incompatible run、欠測、error、contract-failure、0値、単一点系列をテストする。
- [x] 差分の符号、並び順、上位選択、対数 scale 用 view model をテストする。
- [x] contract comparator ごとの pass/fail と表示方向をテストする。
- [x] Altair spec を `to_dict(validate=True)` で検証し、encoding、scale、selection、tooltip を検査する。
- [x] `vl-convert-python` で SVG を生成し、`xml.etree.ElementTree` で parse する smoke test を追加する。
- [x] offline HTML に外部 CDN URL がなく、必要な runtime と benchmark data が埋め込まれることを検査する。
- [x] HTML / JSON escaping と不正な非有限値が出力されないことをテストする。
- [x] 既存の warning、contract、詳細表、CLI exit code のテストを維持する。
- [x] 画像 pixel snapshot は使わない。

## 6. ドキュメントと確認

- [x] `docs/memo/performance.md` に `benchmark-report` extra の導入方法を追記する。
- [x] chart の意味、base 選択、比較可能性、MAD、差分率の読み方を追記する。
- [x] CI の benchmark job に `benchmark-report` extra を追加する。
- [x] smoke と複数の互換 run から report を生成する。
- [x] ブラウザで desktop 幅と狭い幅、selector、tooltip を目視確認する。
- [x] `overview.svg` を画像として目視確認する。
- [x] report が network request なしで表示できることを確認する。
- [x] 対象テスト、Ruff、mypy を実行する。

## 7. 完了条件

- 最新 run の成否と主要 guardrail を report 冒頭で把握できる。
- 互換な base/head がある場合、最大の遅化・改善を視覚的に判別できる。
- case を選び、互換な時系列と MAD を追える。
- chart と詳細表が同じ互換性判定・値を使う。
- `report.html` は単一 file、offline、CDN 不要で操作できる。
- `overview.svg` は browser runtime なしで最新状態を説明できる。
- 通常の Grafix runtime と benchmark 計測へ plotting dependency を強制しない。
- 互換性がない値を同じ比較または系列として描かない。


## 8. 実施結果

- smoke を同一条件で再実行し、`codex-smoke-20260809` を baseline、
  `codex-report-smoke-20260809` を head とする 6 case の差分を確認した。
- `report.html` を desktop / 390 px 幅で表示し、category、case、absolute / relative の
  selector と chart tooltip data を確認した。
- browser からの request は report 本体と favicon のみで、chart runtime / data の外部取得は
  発生しないことを確認した。
- `overview.svg` を画像として確認し、regression / improvement と soft guardrail の色・値を
  HTML report と照合した。
- benchmark test は 206 passed、全 test suite は 4257 passed、Ruff は pass。
- 変更した benchmark report 4 module 単体（`--follow-imports=skip`）の mypy は pass。
  全 `src/grafix` の mypy は依頼外の
  `font_resources.py` / `effects/boolean.py` にある既存の外部 stub error 4件で非0となる。
