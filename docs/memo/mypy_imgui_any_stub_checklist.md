# どこで: `docs/memo/mypy_imgui_any_stub_checklist.md`。
# 何を: mypy の `imgui.*` 警告をゼロにするため、ローカル stub で `imgui` を Any 扱いにする。
# なぜ: `imgui.get_io` などが型定義に存在せず、`attr-defined` が大量に出て開発体験が悪いため。

## ゴール

- `src/interactive/parameter_gui/gui.py` ほか、imgui 呼び出し行の mypy 警告を（まずは）ゼロにする。

## 非ゴール

- imgui の正確な型付け（必要箇所から段階的に行う）。
- 実行時挙動の変更。

## 実施チェックリスト

- [ ] 現状のエラーを対象限定で再現（例: `python -m mypy src/interactive/parameter_gui/gui.py`）
- [ ] mypy 用の stub 置き場として `typings/` を追加
- [ ] `typings/imgui/__init__.pyi` を追加（`__getattr__ -> Any` で全面 Any）
- [ ] `typings/imgui/integrations/__init__.pyi` を追加（必要なら）
- [ ] `typings/imgui/integrations/pyglet.pyi` を追加（必要なら）
- [ ] `mypy.ini` を追加して `mypy_path = typings` を設定
- [ ] 対象限定の mypy を再実行し、imgui 由来の警告が消えることを確認

## 事前確認したい

- [ ] stub の置き場は `typings/` + `mypy.ini` で進めて良い？（実行時 import を汚さないため）

---

状態: 未着手。
