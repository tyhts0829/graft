# weave の relax 抽出: 新規 effect 実装チェックリスト（2025-12-28）

目的: `src/grafix/core/effects/weave.py` にある「弾性緩和（relax）」処理を、単独で再利用できる新規 effect として切り出す。

背景（現状）:

- `weave.py` 内の `elastic_relaxation_nb(positions, edges, fixed, iterations, step)` が、グラフ（ノード+エッジ）に対して簡易な Laplacian 的平滑化を行っている。
- `create_web_nb(...)` が `fixed = True`（元の閉曲線の頂点群）を固定し、候補線で増えた点だけを動かすことで「糸っぽさ」を整えている。
- これを単独 effect にすると、`weave` 以外の線分ネットワークにも同じ緩和を適用できる。

方針（提案）:

- 新規 effect 名は `relax`（`src/grafix/core/effects/relax.py`）とし、**独立した実装**として追加する（今回 `weave` は一切変更しない）。
- relax effect は **単一入力**（`n_inputs=1`）で動く仕様にする（multi-input effect はチェーン先頭にしか置けないため、`weave(...).relax(...)` を成立させる）。

非目的:

- 物理的に正しいスプリング（自然長/剛性）モデル化
- メッシュ/面情報の導入
- 互換ラッパー/シムの追加（破壊的変更 OK）

## 0) 事前に決める（あなたの確認が必要）

- [x] 新規 effect 名: `relax` で確定してよいか（別案: `elastic_relax`, `smooth_graph`）;OK
- [x] relax の対象:
  - A: **ポリラインごと**に緩和（交差点の共有は考えない、最も単純）
  - B: **全ポリラインを 1 つのグラフとして**緩和（同一点は共有ノードとして扱い、交差が崩れない）← 提案;こちらで
- [ ] 「固定点（fixed）」の決め方（入力 1 個しか使えない制約あり）:
  - 推奨: A + B（次数 `!=2` + 連結成分の min/max）。固定点が無い連結成分はこの緩和だと縮退しやすく、特に純サイクルは重心へ潰れるため。；これで
  - A: 次数 `!= 2`（端点/分岐）を固定し、次数 2 を動かす（純サイクルは固定点が無いので別扱いが必要）
  - B: 各連結成分の軸方向 min/max（最大 6 点）を固定して潰れを防ぐ（次数条件と併用可）
  - C: “純サイクル成分は relax しない” で割り切る（最も単純）
- [ ] 次元: 3D（`(x,y,z)`）で緩和してよいか（`weave` 出力は平面上なので 3D でも平面を保つ想定）；OK
- [x] `weave` 側の扱い:
  - A: `weave` から緩和を削除し、ユーザーが `weave(...).relax(...)` で再現する
  - B: `weave` は現状どおり緩和込み、別途 `relax` を追加；こちらで。今回 weave は一切いじらなくていい。

## 1) 受け入れ条件（完了の定義）

- [ ] `E.relax(...)` が effect として登録され、`grafix.api.__init__.pyi` のスタブにも反映される
- [ ] `weave` は一切変更されない（`git diff` 上、`src/grafix/core/effects/weave.py` に差分無し）
- [ ] 最小のユニットテストが追加される（緩和が効く/固定点が動かない/共有点が崩れない等）

## 2) API 仕様（案）

- effect: `relax(inputs, *, relaxation_iterations: int = 15, step: float = 0.125, fixed_mode: str = "degree_neq_2+extrema")`
- meta（案）:
  - `relaxation_iterations`: kind `"int"`（0–50）
  - `step`: kind `"float"`（0.0–0.5）
  - `fixed_mode`: kind `"choice"`（例: `"degree_neq_2" | "extrema" | "degree_neq_2+extrema" | "skip_cycles"`）

## 3) 実装タスク

- [ ] `src/grafix/core/effects/relax.py` を新規作成（effect 本体）
  - [ ] 入力 `RealizedGeometry` から「ノード共有」を復元する（方針 0-B の場合）
    - [ ] 共有判定は座標の完全一致（`(x,y,z)` のタプル）で同一点を同ノードに束ねる（過度に防御せず、まずは単純に）
    - [ ] エッジは各ポリラインの隣接頂点から生成し、重複エッジは除去
  - [ ] `fixed` マスクを `fixed_mode` に従って作る
  - [ ] `elastic_relaxation_nb` 相当の緩和ループを `relax.py` 内に実装し、座標を更新（`weave` と共通化しない）
  - [ ] 元のポリライン構造（頂点数/offsets）を保ったまま出力へ戻す
- [ ] `src/grafix/api/effects.py` に `from grafix.core.effects import relax as _effect_relax` を追加（登録用）
- [ ] スタブ更新: `tools/gen_g_stubs.py` の生成結果で `src/grafix/api/__init__.pyi` を更新

## 4) テスト（最小の安全柵）

- [ ] `tests/core/effects/test_relax.py`（新規）
  - [ ] 反復 0 のとき入力と一致する（早期 return の確認）
  - [ ] 端点固定の鎖（3 点）で中央が平均方向へ動く（数値は近似で十分）
  - [ ] 共有点を含む 2 本のポリラインで、共有点の出力座標が一致し続ける（方針 0-B の場合）
  - [ ] “固定点が存在しない成分” の挙動が仕様どおり（skip する等）
- [ ] `pytest -q tests/stubs/test_api_stub_sync.py` が通る（スタブ同期）

## 5) 実行コマンド（ローカル確認）

- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_relax.py`
- [ ] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`
- [ ] `ruff check src/grafix/core/effects/relax.py`
- [ ] `mypy src/grafix`

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] `fixed_mode` の choices を最小にするか（例: `"degree_neq_2+extrema"` と `"skip_cycles"` だけに絞るか）
