# どこで: `docs/parameter_gui_ui_scale_checklist.md`。
# 何を: Parameter GUI の「UI 全体倍率（ズーム）」を実装から指定できるようにするチェックリスト。
# なぜ: Retina/DPI とは別に、好みの見やすさで UI を拡大できるようにするため。

## ゴール

- `run(..., parameter_gui_ui_scale=1.25)` 等で Parameter GUI の文字/余白/ウィジェットが全体的に拡大する。
- ウィンドウサイズは固定のまま（コンテンツ密度が変わるだけ）。
- 既定は現状互換（倍率 1.0）。

## 実装方針（案）

- ImGui の `io.font_global_scale` と `style.scale_all_sizes()` を使い、`ParameterGUI` 生成時に 1 回だけ倍率を適用する。
  - 文字だけではなくパディング等も一緒に拡大して「全体ズーム」に近づける。
- API は `src/api/run.py` から渡せるようにし、`ParameterGUIWindowSystem` → `ParameterGUI` へ伝搬する。

## チェックリスト

- [ ] 仕様確定: 倍率の引数名（例: `parameter_gui_ui_scale`）と型（`float`）を決める
- [ ] `src/app/parameter_gui/gui.py`: `ParameterGUI.__init__` に `ui_scale: float = 1.0` を追加し、init で `io.font_global_scale` と `style.scale_all_sizes(ui_scale)` を適用
- [ ] `src/app/runtime/parameter_gui_system.py`: `ParameterGUIWindowSystem` に `ui_scale` を受け取り、`ParameterGUI(..., ui_scale=ui_scale)` を渡す
- [ ] `src/api/run.py`: `run()` に `parameter_gui_ui_scale` を追加し、`ParameterGUIWindowSystem(..., ui_scale=parameter_gui_ui_scale)` を渡す
- [ ] 手動確認: `tests/manual/` に倍率違いを確認できるスモーク（新規 or 既存更新）を用意し、`1.0 / 1.25 / 1.5` で視認確認
- [ ] ドキュメント: `src/api/run.py` の docstring に新引数を追記（使い方の一行だけ）

## 既知のトレードオフ

- `font_global_scale` はフォントテクスチャを拡大するだけなので、環境によっては少し滲む可能性がある。
  - より綺麗にするには「倍率に合わせたフォントサイズで atlas を作る」方式が必要だが、今回はシンプル優先で見送る。

## 相談したい点（事前確認）

- [ ] 倍率は `run()` 引数で出す（推奨）で良い？ それとも内部定数で固定したい？
- [ ] 文字だけ拡大（fontのみ）か、余白/ウィジェットも含めた全体ズーム（styleも）どちらが良い？
- [ ] 想定する既定倍率は `1.0` で OK？

