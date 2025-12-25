# どこで: `src/grafix/interactive/parameter_gui/gui.py` / `src/grafix/interactive/parameter_gui/pyglet_backend.py`。
# 何を: Parameter GUI のウィンドウ横幅を backing scale に応じて自動調整する。
# なぜ: macOS + pyglet(dpi_scaling="platform") で `window.width` が backing pixel 基準になり、外部モニタ(scale=1)で横幅が不足して UI が見切れるため。

## 現状の観察

- Retina(scale=2) では「ちょうど良い」横幅に見える。
- 外部モニタ(scale=1) だと同じコードでも利用可能な横幅(px)が減り、テーブル等がウィンドウ内で見切れる。
- 高さはスクロールで逃がせるので固定で良い。

## 方針（採用）

- 「Retina での見た目」を基準に、**ターゲット framebuffer 幅(px)** を決めて維持する。
  - 具体的には `DEFAULT_WINDOW_WIDTH * 2` を “基準 framebuffer 幅” とする（典型的な Retina=2x）。
- 現在の `window.scale` から必要な requested width を逆算して `window.set_size()` する:
  - `requested_width = target_fb_width / window.scale`
  - 高さは `window.get_requested_size()` の高さをそのまま使う（変更しない）。
- モニタ移動で `window.scale` が変わったときだけ適用する（毎フレーム set_size はしない）。

## チェックリスト

- [x] 事前確認: 方針A（外部でウィンドウが広がってOK）で進める → OK
- [x] `pyglet_backend.py` に「ターゲット framebuffer 幅」の定数を追加する。
- [x] `ParameterGUI` に「横幅同期」メソッドを追加する（scale 変化時のみ）。
- [x] `draw_frame()` 内で `scale` 変化に追従する（高さは固定）。
- [ ] 手動確認: Retina↔外部で UI が見切れない（高さはそのまま）。
- [x] 最低限の検証: `python -m py_compile` が通る。
