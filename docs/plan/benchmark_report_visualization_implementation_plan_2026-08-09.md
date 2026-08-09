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

- [ ] 最新 run、source、suite/profile/mode、case 数を表示する。
- [ ] ok / error / timeout / contract-failure、warning、hard/soft contract の集計をカード表示する。
- [ ] 比較可能な baseline がない場合は、理由を明示して差分 chart を空表示にしない。

### 3.2 Regression / improvement

- [ ] 最新 run と、case set・environment・measurement settings が一致する直前 run を選ぶ。
- [ ] case compatibility key が一致する結果だけを比較する。
- [ ] `head / base - 1` の差分率を、0% 中心の横棒 chart で表示する。
- [ ] 遅化を赤、改善を緑、変化なしを中立色にする。
- [ ] 悪化上位と改善上位を優先し、category filter を付ける。
- [ ] tooltip に case、category、base/head 中央値、MAD、差分率を表示する。
- [ ] 差分を統計的有意差とは表現せず、noise を含む観測値であることを明記する。

### 3.3 Case history

- [ ] case selector で 1 case を選ぶ時系列 chart を表示する。
- [ ] median の折れ線と MAD band を重ねる。
- [ ] absolute time と最初の互換 run 比を切り替えられるようにする。
- [ ] environment・case・measurement settings が互換な run だけを同じ系列として結ぶ。
- [ ] 欠測、error、非互換 run を連続線で接続しない。

### 3.4 Latest timing overview

- [ ] 最新 run の計測済み case を、中央値の長い順に表示する。
- [ ] 広い値域を扱える対数軸の横棒 chart にする。
- [ ] category selector と case tooltip を付ける。
- [ ] self-sampling、status、contract-failure を視覚的に区別する。
- [ ] 異なる workload 間の絶対時間は最適化優先度を直接意味しない旨を表示する。

### 3.5 Contract guardrails

- [ ] 数値として比較可能な soft contract を actual / limit の bullet chart で表示する。
- [ ] comparator の向きを反映し、pass/fail と余裕を誤解なく示す。
- [ ] boolean、文字列、または chart 化できない contract は既存の詳細表示へ残す。

### 3.6 Static overview

- [ ] summary、最大の遅化・改善、主要 guardrail を `overview.svg` にまとめる。
- [ ] interactive selector がなくても最新状態を理解できる内容に限定する。
- [ ] report と同じ view model と chart 定義を使い、値や色の意味を二重実装しない。

### 3.7 Scaling

- [ ] 現行の scaling 表は維持する。
- [ ] 初版では case ID から系列や x 軸を推測して曲線を作らない。
- [ ] 本格的な scaling chart は、schema に family / x parameter / unit を追加する別計画とする。

## 4. 実装構造

- [ ] `LoadedRuns` から immutable な report/chart view model を構築する純粋関数を追加する。
- [ ] baseline 選択と measurement 互換性判定を一箇所に集約し、表と chart で共有する。
- [ ] Altair spec 構築と export を `report.py` から分離した内部 module に置く。
- [ ] chart data は JSON-compatible な有限値だけに正規化し、raw benchmark JSON を browser へ渡さない。
- [ ] Altair の selection/filter は表示対象の切替だけに使い、比較計算をさせない。
- [ ] `Chart.to_html(inline=True)` 相当で Vega / Vega-Lite / Vega-Embed を HTML 内へ同梱する。
- [ ] `vl-convert-python` で同じ spec から `overview.svg` を生成する。
- [ ] 同梱フォントを登録し、macOS / CI 間の文字 layout 差を抑える。
- [ ] chart がなくても warning と詳細表を生成できる構造を維持する。
- [ ] `benchmark report --out ...` の CLI と入力 directory 契約は変更しない。
- [ ] CLI は `report.html`、`overview.svg`、`warnings.json` の出力 path を表示する。

## 5. テスト

- [ ] baseline が同一 environment・mode・measurement・case set に限定されることをテストする。
- [ ] incompatible run、欠測、error、contract-failure、0値、単一点系列をテストする。
- [ ] 差分の符号、並び順、上位選択、対数 scale 用 view model をテストする。
- [ ] contract comparator ごとの pass/fail と表示方向をテストする。
- [ ] Altair spec を `to_dict(validate=True)` で検証し、encoding、scale、selection、tooltip を検査する。
- [ ] `vl-convert-python` で SVG を生成し、`xml.etree.ElementTree` で parse する smoke test を追加する。
- [ ] offline HTML に外部 CDN URL がなく、必要な runtime と benchmark data が埋め込まれることを検査する。
- [ ] HTML / JSON escaping と不正な非有限値が出力されないことをテストする。
- [ ] 既存の warning、contract、詳細表、CLI exit code のテストを維持する。
- [ ] 画像 pixel snapshot は使わない。

## 6. ドキュメントと確認

- [ ] `docs/memo/performance.md` に `benchmark-report` extra の導入方法を追記する。
- [ ] chart の意味、base 選択、比較可能性、MAD、差分率の読み方を追記する。
- [ ] CI の benchmark job に `benchmark-report` extra を追加する。
- [ ] smoke と複数の互換 run から report を生成する。
- [ ] ブラウザで desktop 幅と狭い幅、selector、tooltip を目視確認する。
- [ ] `overview.svg` を画像として目視確認する。
- [ ] report が network request なしで表示できることを確認する。
- [ ] 対象テスト、Ruff、mypy を実行する。

## 7. 完了条件

- 最新 run の成否と主要 guardrail を report 冒頭で把握できる。
- 互換な base/head がある場合、最大の遅化・改善を視覚的に判別できる。
- case を選び、互換な時系列と MAD を追える。
- chart と詳細表が同じ互換性判定・値を使う。
- `report.html` は単一 file、offline、CDN 不要で操作できる。
- `overview.svg` は browser runtime なしで最新状態を説明できる。
- 通常の Grafix runtime と benchmark 計測へ plotting dependency を強制しない。
- 互換性がない値を同じ比較または系列として描かない。

