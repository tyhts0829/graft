# mirror3d 移植チェックリスト（2025-12-18）

目的: 旧プロジェクトの `src/grafix/core/effects/from_previous_project/mirror3d.py` を参考に、新プロジェクトの組み込み effect として `mirror3d` を実装する（基本は旧仕様踏襲）。

非目的:

- 互換ラッパー/シムは作らない
- 速度最適化（numba 化など）は後回し（必要になったら別タスク）
- `from_previous_project/` 側の既存ファイル削除・移動は行わない（参照として残す）

## 0) 事前に決める（あなたの確認が必要）

- [x] 新規ファイル配置: `src/grafix/core/effects/mirror3d.py`（effect 名は関数名 `mirror3d`）；OK
- [x] 公開 API（引数）方針:
  - A: 旧仕様をそのまま踏襲（`cx,cy,cz` を個別引数のまま、`axis`, `phi0`, `mode`, `group`, `use_reflection`, `mirror_equator`, `show_planes` 等）
  - B: 新プロジェクト寄りに整理（例: `center: vec3` 化、`source_side` 整理など）※破壊的；こちらで
- [x] `source_side` の扱い（旧コードでは実質未使用）:
  - A: 旧 API として残すが挙動は現状踏襲（=内部では使わない）
  - B: 削除する（シンプルだが旧仕様から逸れる）
  - C: “赤道ミラーのソース側選択”として実装し直す（仕様追加・要設計）；こちらで
- [x] `mode='polyhedral'` のソース領域:
  - A: 旧実装通り「正の八分体（x>=cx,y>=cy,z>=cz）」でクリップ；こちらで
  - B: 将来予定の「球面三角形（群の基本領域）」へ変更（大きめの設計/実装）
- [x] `mode='polyhedral'` の反射倍化:
  - A: 旧実装通り代表反射（y=0）で倍化；こちらで
  - B: 反射生成元から群を生成（旧ファイル末尾の未使用関数相当）※変更大
- [x] UI メタ情報（`ParamMeta` の `ui_min/ui_max`）:
  - `cx,cy,cz` のレンジを旧メタ（0..1000）に寄せるか、他 effect に合わせて ±500 などに寄せるか；他に合わせて

## 1) 仕様の把握（参照確認）

- [x] 旧 `mirror3d` の挙動を読み取り、主要仕様を整理する
  - `azimuth`: くさび（2 平面）へクリップ → 回転（n 回）+ 片側反射 → 必要なら赤道反射
  - `polyhedral`: 正の八分体へクリップ → 群の回転行列を列挙して複製 → 必要なら代表反射で倍化
  - `show_planes`: 対象面の“十字線”を追加して可視化
- [x] 新プロジェクトの effect 実装パターンを確認する（`RealizedGeometry`, `@effect(meta=...)`, `grafix/api/effects.py` での import 登録）

## 2) 新規 effect ファイル実装（中核）

- [x] `src/grafix/core/effects/mirror3d.py` を新規作成
- [x] 先頭 docstring は「効果が何か」の説明にする（effects/ 配下の規約に従い “どこで/なにを/なぜ” は書かない）
- [x] `mirror3d_meta: dict[str, ParamMeta]` を定義（`mode/group` は `kind=\"choice\"`）
- [x] `@effect(meta=mirror3d_meta)` で `mirror3d(...) -> RealizedGeometry` を実装
  - 入力が空なら空ジオメトリを返す（他 effect と同様）
  - `inputs[0]` を対象とする（基本 1 入力）
- [x] 旧実装の内部ヘルパ（`_unit`, 回転/反射, 半空間クリップ, 行列列挙, dedup, show_planes）を新プロジェクト向けに移植
  - `RealizedGeometry.coords/offsets` を直接使用する
  - 返り値の `coords=float32`, `offsets=int32` を守る
  - effects 同士の依存は禁止（`grafix.core.effects.util` の利用のみ許可、必要なら検討）

## 3) effect 登録（API から使えるように）

- [x] `src/grafix/api/effects.py` に `from grafix.core.effects import mirror3d as _effect_mirror3d  # noqa: F401` を追加
- [x] `E.mirror3d(...)` が `AttributeError` にならないことを確認（テストで呼び出し済み）

## 4) テスト追加（旧仕様の安全柵）

- [x] `tests/core/effects/test_mirror3d.py` を追加
- [x] `azimuth` の最小ケース（`n_azimuth=1`）で 2 倍になること
- [x] `azimuth` の `n_azimuth>=2` で `2*n_azimuth` 本になること（dedup 後の本数で確認）
- [x] `mirror_equator + source_side` で半空間選択が効くこと（ソース側が逆なら空になる）
- [x] `polyhedral` の `group='T'|'O'|'I'` で回転群の本数に一致すること
- [x] `polyhedral` の `use_reflection=True` で倍化すること
- [x] `show_planes=True` で出力本数が増えること（面の十字線が追加されること）

## 5) 品質ゲート（最小）

- [ ] `ruff check src/grafix/core/effects/mirror3d.py tests/core/effects/test_mirror3d.py`
  - この環境では ruff が未インストール（`ruff: command not found`）のため未実行
- [ ] `mypy src/grafix`（少なくとも新規ファイル周辺で型エラーが増えないこと）
  - 代替として `mypy src/grafix/core/effects/mirror3d.py` は実行済み

## 6) 動作確認（ローカル）

- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_mirror3d.py`

## 7) 仕上げ

- [ ] `parameter_spec.md` / `spec.md` 等に effect 一覧がある場合、`mirror3d` を追記（必要なら）
- [ ] “追加で確認すべき点” が出たら、このチェックリストへ追記してから実装を進める
