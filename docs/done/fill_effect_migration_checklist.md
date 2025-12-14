# どこで: `docs/fill_effect_migration_checklist.md`。

# 何を: `src/effects/fill.py` を現行コア（RealizedGeometry/effect_registry/ParamMeta）へ合わせて再実装するチェックリスト。

# なぜ: 旧プロジェクト依存（`engine.*` / `util.*` / 旧 registry）を排除し、`E.fill(...)` をこのリポジトリで利用可能にするため。

## ゴール

- `E.fill(...)` が登録済み effect として利用できる。
- `realize()` により、ハッチ線（必要なら境界線も）を含む `RealizedGeometry` が得られる。
- Parameter GUI 用に `ParamMeta` が定義され、デフォルト値が観測される。
- 最小限のユニットテストで挙動が固定される。

## 仕様（確定）

- 入力: `RealizedGeometry`（複数ポリラインを含み得る）。
- 平面性:
  - 全体がほぼ平面なら、平面基底へ射影して外周＋穴を even-odd でグルーピングして塗る。
  - 全体が非平面なら、各ポリラインごとに平面性を判定し、平面なら単独輪郭として塗る（穴の統合はしない）。
- 塗り対象:
  - 頂点数 >=3 のポリラインを輪郭として扱い、最終点→始点を結ぶ（明示的な閉ループは要求しない。旧仕様踏襲）。
- 座標系/ z:
  - ハッチ生成は平面 2D 座標で行い、生成線は元の平面へ戻す（z を含む 3D 形状を維持する）。
- 塗りルール: スキャンライン交点を集めて偶奇規則（even-odd）で区間化する。
- ハッチ方向: `angle`[deg] を基準に、`angle_sets` 本（180°を等分）でクロスハッチを生成する。
- 密度: `density`（旧仕様）。`round(density)` 本相当として間隔を算出し、2..MAX にクランプする。
- 生成範囲: `y in [min_y, max_y)`（max_y は含めない）。旧仕様踏襲。
- 自己交差/重なり輪郭: 旧仕様同様、even-odd の結果に従う（厳密定義はしない）。
- 出力: `remove_boundary=False` の場合は入力ポリラインも先頭に残し、塗り線を後段に追加する。

## 作業チェックリスト

- [x] 現状整理
  - [x] `src/effects/fill.py` が旧 import（`engine.*` / `util.*` / `.registry`）に依存している点を確認
  - [x] 現行 effect 実装の規約（`src/effects/scale.py`, `src/effects/rotate.py`）と `EffectRegistry` のシグネチャを確認
  - [x] `src/api/effects.py` に fill の import が無く、現状 `E.fill` が未登録である点を確認
- [x] 仕様確定
  - [x] 公開引数を決める（`angle_sets:int`, `angle:float[deg]`, `density:float`, `spacing_gradient:float`, `remove_boundary:bool`）
  - [x] 2D 前提と z の扱いを決める（平面へ射影して生成し、元平面へ戻す）
  - [x] 輪郭の扱いを決める（頂点数>=3 を輪郭扱い・最終点→始点で閉じる）
  - [x] 開いたポリラインの扱いを決める（輪郭扱い/非平面や頂点数不足は塗り無し）
- [x] 実装
  - [x] `src/effects/fill.py` を現行形式へ全面書き換え（`@effect(meta=...)` + `RealizedGeometry` 変換）
  - [x] スキャンライン交点 → 偶奇ペアリングの最小実装（角度 1 本）
  - [x] `angle_sets>1` のクロスハッチ（等分回転）
  - [x] `remove_boundary` の反映（境界を残す/除去）
  - [x] dtype/shape を `RealizedGeometry` 契約に合わせる（float32, (N,3) / offsets int32）
  - [x] 不要になった旧コード/依存を整理（旧 util/engine 依存の除去）
- [x] 影響範囲の更新
  - [x] `src/api/effects.py` に `from src.effects import fill as _effect_fill` を追加して登録されるようにする
  - [ ] （必要なら）README の "fill" 言及を実動作に合わせて更新
- [x] テスト
  - [x] `tests/test_fill.py` を追加
    - [x] 単純な正方形が指定 density で期待本数になる（angle=0）
    - [x] `remove_boundary=True` で境界が含まれない
    - [x] 外周+穴（2 輪郭）で穴領域に線が入らない（even-odd）
    - [x] 空入力が no-op で落ちない
- [x] 検証
  - [x] `pytest -q tests/test_fill.py` を実行
  - [x] `pytest -q tests/test_scale.py tests/test_rotate.py` を実行（影響確認）

## 追加で確認したい点（実装中に増えたら追記する）

- [x] spacing を `density` に戻す必要があるか（GUI の操作感）；ない。旧仕様から変えないで
- [x] 線の生成範囲を min..max のどちらを含むか（テストと仕様を合わせる）；旧仕様と同じでいいよ
- [x] 自己交差ポリゴン/重なり輪郭の扱いは未定（まずは未定義でよいか）；旧仕様と同じでいいよ
