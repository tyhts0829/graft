# displace の amplitude z 成分が見た目に寄与しない件（原因と改善計画）

## 症状

- `main.py` の `E(...).displace()` で `amplitude_mm` の z 成分だけを増やしても、プレビューの線形状が変わらない。

## 期待

- `amplitude_mm=(0, 0, z)` の z を上げると、プレビュー上の形状が変形して見える。

## 原因（実装から特定）

結論: **displace 自体は z を変位しているが、レンダラが z を捨てている**。さらに **`main.py` の effect 順序により z 変位が xy に混ざる機会が無い**。

- `src/effects/displace.py` は z 成分の変位を計算し、`result[i, 2]` に反映している（例: L679）。
  - つまり「ジオメトリの z が動いていない」のではなく、「z を動かしても見た目（xy 投影）が変わらない」。
- `src/render/shader.py` のシェーダが z を描画に使っていない。
  - 頂点シェーダで `gl_Position = projection * vec4(in_vert.xy, 0.0, 1.0);` として `in_vert.z` を常に 0 扱い（L13）。
  - ジオメトリシェーダも `vec4(..., 0.0, 1.0)` で z を 0 に固定して出力（L29–35）。
- `main.py` のチェーンが `affine(rotation=...)` → `...` → `displace()` になっている（L16–21）。
  - 現状プレビューは実質「xy の正射影」なので、**最後に z だけ動かしても画面の x/y が変わらず**、z 成分が“見た目”に寄与しない。
  - 逆に、`displace()` の後で回転（あるいは 3D 投影）が入れば、z 変位が x/y に混ざり見た目が変わる。

## 改善方針（選択肢）

### 案A: `main.py` の例だけ直して「z が効く」状態にする（最小変更）

- `displace()` を回転より前へ移動する、または `displace()` の後に `affine(rotation=...)` を置く。
- 2D レンダラのままでも、回転で z が x/y に混ざるため、z 振幅が見た目に反映される。

### 案B: レンダラを 3D 対応して z を捨てない（根本）

- `src/render/shader.py` の頂点/ジオメトリシェーダで z を保持する。
- `src/render/utils.py` の投影行列を 3D 前提で整理し、必要なら view（カメラ）と perspective を導入する。
- 目的が「z 変位で見た目が変わる」なら、perspective か view 回転が必要。

### 案C: 仕様を 2D に寄せて混乱を消す（破壊的変更でもOK）

- `displace` の `amplitude_mm` を vec2 にする / z 成分を廃止する / GUI から z を隠す。
- 併せて「プレビューは xy 投影で z は表示されない」を明記する。

## 改善計画（チェックリスト）

- [ ] 期待仕様を決める（案A/B/C）
- [ ] 選択した方針で修正する
  - [ ] 案A: `main.py` の effect 順序を変更
  - [ ] 案B: `src/render/shader.py` の z 取り扱い修正 + 投影/カメラの整理
  - [ ] 案C: `src/effects/displace.py` の引数型/`displace_meta`/GUI 表示を更新
- [ ] 回帰確認
  - [ ] `pytest -q tests/test_displace.py`
  - [ ] `main.py` を起動し、`amplitude_mm` の z だけを変えて見た目が変わることを確認（案A/B）
- [ ] ドキュメント更新（選択した仕様に合わせて）

