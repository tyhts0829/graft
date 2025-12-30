# どこで: `src/grafix/core/parameters/merge_ops.py`。
# 何を: `merge_frame_params()` / `_apply_explicit_override_follow_policy()` の挙動が追えるよう、処理意図・不変条件・分岐理由を細かくコメントする。
# なぜ: 「explicit/override の追従ルール」「フレーム観測→永続ストア反映」の設計意図がコードだけだと読み取りづらいため。

## 方針

- **挙動は一切変えない**（コメント追加のみ）。
- 「この行は何をしているか」よりも、**なぜここで必要か／何と整合しているか**を優先して補足する。
- 1〜2 行の要約コメント + まとまり単位のブロックコメントで読めるようにする（行ごとの冗長化は避ける）。

## 追記したい説明ポイント（候補）

- `records` を走査している間に更新しているものの意味:
  - `runtime.observed_groups` / `display_order_by_group` / `next_display_order`
  - `explicit_by_key_this_frame`
  - `runtime.last_effective_by_key`（今回追加したキャッシュの位置づけ）
- `store._ensure_state(... initial_override=(not explicit))` の意図（初期 override ポリシー）
- `meta` を「初出時に確定して保持」する理由（GUI 編集との関係）
- effect chain の step 記録の意図（GUI 表示順／折りたたみ単位との関係）
- `reconcile_loaded_groups_for_runtime()` をこのタイミングで呼ぶ理由（loaded/observed の整合）
- `_apply_explicit_override_follow_policy()` のルール:
  - `explicit` が変化したときだけ見る
  - `override` が「既定値のまま」なら追従して反転する
  - 既にユーザーが切り替えた override は尊重する（追従しない）
  - `prev_explicit is None`（旧 JSON）では触らず記録のみ、の理由

## チェックリスト

- [x] 事前確認: この方針でコメント追加してよい
- [x] `merge_frame_params()` にブロックコメントを追加（runtime/ordinals/state/meta/effects の役割）
- [x] `_apply_explicit_override_follow_policy()` にブロックコメントを追加（追従条件と例外）
- [x] テスト: `PYTHONPATH=src pytest -q tests/core/parameters/test_resolver.py tests/core/parameters/test_parameter_updates.py`
