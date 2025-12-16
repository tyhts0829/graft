# displace 再実装（旧実装ほぼコピー）計画

## 前提と現状確認（ここまでで実際に確認できたこと）

- 現行 `src/effects/displace.py` は、`amplitude_mm=(0,0,10)` のように z 成分だけを与えると **z 座標は変化する**が **x/y は変化しない**（純粋に z 方向へだけ変位する）。
  - これは旧 `src/effects/from_previous_project/done/displace.py` も同様で、`result[i, 2] = z + noise * az` という形で z へだけ加算している。
- そのため、**描画が xy 投影で、かつ displace の後段に回転等が無い**場合、z 成分だけ変位しても画面上の形状は変わらない。
  - 一方で `affine(scale の z)` や `polyhedron(scale の z)` が「見た目に効く」ケースは、回転が後段にあり z が x/y に混ざっている可能性が高い。

## ゴール（確認したい）

次のどちらを「直った」とみなすか、ここを揃えたいです。

- ゴールA: displace の z 振幅で **実体 coords の z が変われば OK**（見た目は 2D 投影なので変わらなくても仕様）。
- ゴールB: displace の z 振幅で **プレビューの見た目（xy）も変わることが必須**。

※ ゴールB だと「旧実装コピー」だけでは基本的に達成できず、(1) displace 後段に回転/投影を入れる、(2) レンダラを 3D 化する、(3) displace の意味を変えて z 振幅が x/y にも影響する、のどれかが必要になります。

## 実装方針（ユーザー要望: 旧実装のほぼコピー）

- `src/effects/from_previous_project/done/displace.py` の
  - `fade/lerp/grad/perlin_noise_3d/perlin_core/_apply_noise_to_coords`
  - パラメータのクランプ規則（min/max factor、GX/FGX など）
  を **できるだけそのまま** `src/effects/displace.py` に反映する。
- 既存の現行アーキテクチャ差分（`RealizedGeometry` / `@effect(meta=...)` / `ParamMeta`）に必要な薄い変換だけを残す。

## 変更チェックリスト

- [ ] 期待ゴール（A or B）を確定する（ここが最重要）
- [ ] `src/effects/displace.py` を旧実装ベースで置き換える（挙動差分が出ない形での整理を含む）
  - [ ] `_apply_noise_to_coords` の分岐構造を旧と同形にする
  - [ ] 定数（GX/FGX、MIN_GRADIENT_*、位相など）を旧と一致させる
  - [ ] Numba の設定（fastmath/cache）を旧と一致させる
- [ ] 既存テストの追加/更新
  - [ ] `tests/test_displace.py` に「z 振幅で z が変化する」テストを追加（ゴールA）
  - [ ] （ゴールB の場合のみ）「displace 後段の回転/投影により xy が変化する」統合テストを追加
- [ ] 対象限定でテスト実行: `pytest -q tests/test_displace.py`

## 事前確認したほうがいいこと

- [ ] あなたが意図しているのはゴールA/Bどちらですか？
  - もしゴールBなら、旧実装コピーに加えて「描画パイプライン側」の変更が必要です（順序変更 or 3D 化 or displace 意味変更）。

