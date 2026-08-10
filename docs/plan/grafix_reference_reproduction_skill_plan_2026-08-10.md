# Grafix 参照画像再現 Skill 新設計画（2026-08-10）

作成日: 2026-08-10
ステータス: 完了（2026-08-10 承認・実装済み）

## 背景

- 参照画像を忠実に再現する単一 Sketch の依頼が、発散探索用の `grafix-art-loop` に暗黙ルーティングされ、不要な3候補作成へ進んでいる。
- 参照画像再現は、複数案から winner を選ぶ処理ではなく、1本の Sketch を `render -> 参照比較 -> 同じファイルを修正` して収束させる処理として分離する必要がある。
- `python -m grafix export` は Sketch 側の `run(..., background_color=...)` を headless PNG に引き継がないため、紙色を含めた再現では `render()` / `save()` を用いる専用の小さな render helper が必要になる。

## 目的

- 参照画像の再現・模写・トレース依頼へ暗黙発動する、リポジトリスコープの `grafix-reference-reproduction` Skill を新設する。
- A5縦、線色数と Layer 数の一致、全線幅 `0.001`、fill 調整値の冒頭集約を毎回守らせる。
- 組み込み `G` / `E` を優先しながら、単一 Sketch を画像比較で収束調整する。
- 最終 PNG を画像確認し、チャットへ絶対パスの Markdown 画像として表示させる。
- `grafix-art-loop` の暗黙発動を可逆的に一時抑制する。

## 配置と対象ファイル

- 新規 Skill: `.agents/skills/grafix-reference-reproduction/`
  - `SKILL.md`
  - `agents/openai.yaml`
  - `scripts/render_sketch.py`
- Sketch import 用: `sketch/agent_art/__init__.py`
- 一時抑制対象: `.agents/skills/grafix-art-loop/agents/openai.yaml`
- 本計画: `docs/plan/grafix_reference_reproduction_skill_plan_2026-08-10.md`

## Skill の適用境界

### 発動させる依頼

- 与えられた画像を Grafix Sketch として忠実に再現する。
- 参照画像を模写、トレース、facsimile 化する。
- 構図、余白、配色、線密度、文字、重なりを画像どおりに近づける。

### 発動させない依頼

- 新規作品の自由な発散生成。
- 複数案、バリエーション、contact sheet、候補比較、winner 選定。
- 参照画像から着想だけを得る創作。
- 既存 Sketch の一般的な修正で、参照画像への一致が目的ではないもの。

frontmatter の `description` に正例と除外条件の両方を記載し、`agents/openai.yaml` では新 Skill の暗黙発動を有効にする。

## Sketch 固定契約

- 作成するコードは `sketch/agent_art/<unique_id>.py` の1本だけとする。
- canvas は A5 縦の `CANVAS_SIZE = (148, 210)` に固定する。
- `LINE_THICKNESS = 0.001` を import 直後の調整ブロックへ置く。
  - すべての `L(...).layer(..., thickness=LINE_THICKNESS)` で同じ値を使う。
  - headless render と `run()` の既定線幅にも同じ値を使う。
  - 再現の都合で Layer ごとの太さを変えない。
- 参照画像の意図的な線色1色につき、ちょうど1 Layer を作る。
  - 背景色、紙色、アンチエイリアス、圧縮ノイズは線色数に含めない。
  - 同色の Geometry は `L.layer()` へ list / tuple でまとめる。
  - 濃淡は可能な限り同色 Layer 内の hatch 密度・間隔で表現する。
  - 同色内の重なりは `E.clip` / `E.boolean` など Geometry 側で解決し、同色 Layer を増やさない。
- import 直後へ `BACKGROUND_COLOR`、palette、主要 layout 値、seed、すべての fill 調整値を集約する。
  - 単一設定なら `FILL_DENSITY` / `FILL_MIN_SPACING` を使う。
  - 複数設定が必要なら、意味名を持つ `FILL_DENSITIES` / `FILL_MIN_SPACINGS` mapping に全値を置く。
  - すべての `E.fill` の `density` / `min_spacing` はこの調整ブロックを参照し、数値を inline で直書きしない。
  - 公開 API の正しい引数名は `min_spacing` とする。

## 再現ワークフロー

1. 参照画像を画像として確認し、縦横比、余白、主要形状、線色 palette、文字、反復、重なり順を分解する。
2. 参照画像と A5 の比率が異なる場合は、明示指示がなければ無言で stretch せず、等比 fit と crop / offset を調整ブロックで制御する。
3. 実装前に live registry の `list` / `describe` で利用可能な primitive / effect を必要な範囲だけ確認する。
4. `G.rect`、`G.circle`、`G.ellipse`、`G.polyline`、`G.spline`、`G.bezier`、`G.text` と、`E.fill`、`E.affine`、`E.translate`、`E.rotate`、`E.scale`、`E.clip`、`E.boolean`、`E.repeat` などの組み込みを優先する。
5. custom `@primitive` / `@effect` や手書き NumPy は、組み込みだけでは忠実度または可読性を保てない箇所に限定する。`RealizedGeometry` は直接 import しない。
6. 1本の Sketch を render して画像確認し、参照との差を具体化して同じファイルだけを修正・再 render する。別 concept や候補は作らない。
7. 作業 PNG を `sketch/agent_art/<unique_id>.png` に保存し、確定後に `data/output/png/codex_generated/<unique_id>.png` へ複製する。
8. 最終 PNG を再度画像確認し、最終回答で Sketch / PNG の絶対パスを報告し、次の形式で実画像を表示する。

```md
![再現結果](/absolute/path/to/data/output/png/codex_generated/<unique_id>.png)
```

## Render helper

`scripts/render_sketch.py` は module と output path を受け取り、公開 API の `render()` / `save()` で PNG を生成する。

- Sketch の `draw`、`CANVAS_SIZE`、`BACKGROUND_COLOR`、`LINE_THICKNESS` を読み込む。
- `RenderOptions` へ canvas、背景色、既定線幅を明示する。
- `parameter_source="code"` と `overwrite=True` を用いる。
- render 後の Frame に対して次を検証する。
  - canvas が `(148, 210)`。
  - Layer が1件以上ある。
  - 全 Layer の最終線幅が `0.001`。
  - Layer 数と一意な線色数が一致する。
- 保存結果と実在する PNG path を出力する。

## `grafix-art-loop` の一時抑制

- `.agents/skills/grafix-art-loop/agents/openai.yaml` に次を追加する。

```yaml
policy:
  allow_implicit_invocation: false
```

- 暗黙発動だけを止め、明示的な `$grafix-art-loop` 呼び出しは残す。
- 一時措置として可逆性を保つため、今回は `grafix-art-loop` の workflow 本文や候補数既定値は変更しない。

## 実装タスク

- [x] 既存の Skill 規約、参照画像再現例、Grafix API、headless export 挙動を調査する。
- [x] `skill-creator` の `init_skill.py` を使い、`grafix-reference-reproduction` を `.agents/skills/` に初期化する。
- [x] frontmatter に正例・除外条件を持つ簡潔な `SKILL.md` を実装する。
- [x] `agents/openai.yaml` を生成し、新 Skill の表示情報と暗黙発動方針を設定する。
- [x] 固定契約を検証して背景色込みで PNG 保存する `scripts/render_sketch.py` を実装する。
- [x] `sketch/agent_art/__init__.py` を追加し、collision-safe な module 名で Sketch を import できるようにする。
- [x] `grafix-art-loop` の `allow_implicit_invocation` を `false` にする。
- [x] `quick_validate.py` で新 Skill と既存 art-loop Skill を検証する。
- [x] render helper を `/tmp` の最小2色 Sketch で smoke test し、A5、2色=2 Layer、全線幅 `0.001`、PNG生成を確認する。
- [x] 同色重複 Layer または誤った線幅を持つ入力を helper が拒否することを対象限定テストで確認する。
- [x] fresh subagent に正例・負例の routing prompt matrix を渡し、新 Skill の境界を独立確認する。
- [x] 対象ファイルだけの lint、差分、未完了項目を確認し、本計画のチェック状態を更新する。

## 受け入れ条件

- `grafix-reference-reproduction` が Skill として validation を通る。
- 参照画像の忠実再現依頼だけに適合し、複数案・art loop を明示的に除外している。
- Skill が `sketch/agent_art/` に単一 Sketch だけを作るよう指示している。
- A5縦、線色数=Layer数、全線幅 `0.001`、冒頭の fill 定数参照を明文化している。
- 組み込み `G` / `E` を優先し、custom operation を必要時だけ許可している。
- 背景色を含む PNG を deterministic に render / save できる。
- 最終 PNG の画像確認とチャット内表示が完了条件に含まれている。
- `grafix-art-loop` は暗黙発動せず、明示呼び出しのみ可能になっている。
- 依頼外の既存差分 `sketch/work/260810_measurement_poster.py` に触れていない。

## 非対象

- Grafix 本体 API の変更。
- `grafix-art-loop` の削除、候補生成 workflow の改修、明示呼び出しの禁止。
- 参照画像が与えられていない今回の Skill 作成タスク内で、最終作品を新規制作すること。
- 依存追加、git commit、git push。
