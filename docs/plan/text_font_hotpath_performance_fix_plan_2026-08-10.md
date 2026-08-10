# Text font hot-path 性能修正計画（2026-08-10）

- 状態: **実装完了（focused / headless性能検証済み、実GUI再計測・full pytest未実施）**
- 計画作成時 branch: `main`
- 計画作成時 HEAD: `dd8b663`
- 実装承認: 2026-08-10
- 実装開始時 HEAD: `dd8b663`
- 対象: `G.text()` の font path 解決と Parameter GUI の font picker
- 発端: `sketch/work/260809.py` が約10 fps、`sketch/readme/grn/18.py` が約50 fpsとなる差の調査

2026-08-10に本計画の承認を得てから、production codeとtestの実装を開始した。

## 1. 目的

静的な `G.text()` を含むpreviewで、font数に比例するfilesystem全走査が毎フレーム発生しないようにする。
同時に、Parameter GUIのfont pickerを閉じている通常状態ではfont一覧を列挙しないようにする。

修正後も次の既存契約を維持する。

- Geometry cache hitより前にexternal dependency fingerprintを確定する。
- 同一pathのfont fileが置換された場合、同じsessionの次回lookupで新しいbytes、fingerprint、geometryを返す。
- より優先度の高いfontの出現、削除、探索rootの出現を次回lookupで観測する。
- `resolve_font_path()` と `list_font_choices()` の単体APIは、呼び出し時点のfilesystemを反映するstateless APIのままとする。
- font resource/cacheはsession ownerに所属させ、process-global cacheを導入しない。
- 既存ParamStoreのbasename指定を自動書換えせず、そのまま読み込めるようにする。

## 2. 作業ツリー境界

計画作成時点で、benchmark report、performance memo、`sketch/work/260809.py`、
`sketch/work/260810.py`などに別作業の差分がある。それらは本修正の対象外であり、restore、移動、削除、
再生成、stageを行わない。

実装開始時に改めて`git status --short`を確認し、以下の「予定変更ファイル」以外へ差分を広げない。
特に、調査対象だった次のファイルはfixture化や一時回避のために変更しない。

- `sketch/work/260809.py`
- `sketch/readme/grn/18.py`
- `data/output/param_store/work/260809.json`
- `.grafix/config.yaml`

## 3. 調査結果

### 3.1 保存済みfont指定

`data/output/param_store/work/260809.json`には、`G.text()`の次のoverrideが保存されている。

```text
font = "Kannada MN.ttc"
text = "a"
scale = 155.4
```

実体は探索root直下ではなく、次のnested pathにある。

```text
data/input/font/Supplemental/Kannada MN.ttc
```

`data/input/font`には対象拡張子のfontが545件あり、package同梱分と合わせた探索候補は558件である。

### 3.2 Preview側の全走査

現行`FontResources.resolve()`は、asset lease cacheを確認する前に毎回`resolve_font_path()`を呼ぶ。
basenameが探索root直下で見つからないと、`_list_font_files()`が全候補を再帰globし、各pathへ
`resolve()` / `is_file()`を実行する。

external dependency preflightはGeometry cache lookupより先にあるため、Geometry cacheがhitしても
この走査は残る。

### 3.3 Inspector側の全走査

現行`widget_font_picker()`はfont comboが閉じていても毎GUIフレーム`list_font_choices()`を呼ぶ。
これも同じ558候補を全列挙する。

`260809.py`はraw `G.text()`のfont rowがGUIへ露出するためこの経路を通る。一方、`18.py`のtextは
`@preset`内部でparameter recordingがmuteされ、font rowがGUIへ露出しない。

### 3.4 実測

```text
260809 saved state, headless steady:
  SceneRunner                 22.901 ms
  FontResources.resolve      22.436 ms
  user draw                    0.146 ms

18 saved state, headless steady:
  SceneRunner                  2.690 ms
  font resolve 3 calls total   0.283 ms
  user draw                    1.928 ms
```

実OpenGLのA/Bでは、現在の`260809.py`はPreview 27.45 ms、Inspector 27.89 ms、約15.85 fpsだった。
負荷差を含めるとユーザー観測の約10 fpsと整合する。

一方、CPU Geometry cacheはsteady 100/100 hit、GPU index buildは初回のみだった。したがって
Geometry/GPU cacheの破損ではなく、cache判定外にあるfont探索が原因である。

## 4. 採用設計

### 4.1 External dependency preflightの順序は変えない

`RealizeSession`でGeometry cacheを先に返す案は採用しない。font fileが置換された後も古いgeometryを
返してしまい、external dependency contractを壊すためである。

preflightは毎lookup実行したまま、preflight内部のfont tree探索をO(font files)から、通常時は
O(search directories + font symlinks + memory match)へ下げる。

### 4.2 Owner-local `FontPathResolver`を導入する

`src/grafix/core/font_resolver.py`へ、`FontResources`が所有する`FontPathResolver`を追加する。
process-global cache、module-level `lru_cache`、TTLは使わない。

resolverは固定configに対する最新1件のfont tree snapshotだけを保持する。実運用では一つの
`EvaluationResources`に一つの`EvaluationConfig`が固定されるため、configごとの無制限mapは持たない。
別configが渡された場合はsnapshotを置換する。

snapshotは次を保持する。

- `config.font_dirs`とpackage同梱font dirsからなる探索root列。
- 存在しないrootを含む、rootおよび再帰subdirectoryのmembership identity。
  - missing sentinel
  - `st_dev`
  - `st_ino`
  - `st_mtime_ns`
  - `st_ctime_ns`
- 現行の探索優先順で並べたfont index。
  - canonical path
  - search-root-relative POSIX path
  - 正規化済みfilename
  - 正規化済みstem
- font候補symlink本体と、その外部targetの出現・消失を検知するidentity。

lookupは次の順で行う。

1. 空文字なら既定fontを解決する。
2. explicit pathを毎回直接確認する。
3. 各search rootの`root / raw`を、現行優先順のまま毎回直接確認する。
4. direct lookupで見つからない場合だけtree snapshotを使う。
5. 全探索directoryとfont候補symlinkのidentityが不変なら、filesystemを再帰列挙せず
   memory上のname/stemを検索する。
6. directoryの出現、消失、identity変更が一つでもあればsnapshotを一度再構築する。
7. 解決pathに対する既存の`path.stat()`、asset LRU、content digest判定を実行する。

scan開始前後でdirectory identityが変化した場合、そのscan結果をwarm snapshotとして保持しない。
競合中の不完全なindexを後続frameへ固定しないためである。

この方式で次を維持する。

- 同一pathの内容差替えは既存`FontFileStat`とdigestで検知する。
- 既存directoryへのfont追加・削除・renameはdirectory identity変更で検知する。
- 新規nested directoryは親directoryのidentity変更で検知する。
- missing search rootの出現はmissing sentinelとの差で検知する。
- root差替えはdevice/inode差で検知する。
- font候補symlinkの付替え、dangling targetの出現、外部targetの消失をsymlink identityで検知する。
- config dirs、package dirs、root内安定順、canonical path先勝ちの優先順位を変えない。

### 4.3 Stateless convenience APIは維持する

公開convenienceである`resolve_font_path()`は、一時resolverまたは同じpure scannerへ委譲し、
呼び出し間でsnapshotを保持しない。`list_font_choices()`も呼び出し時点のfilesystemを列挙する。

これにより、既存の次の契約testを削除・弱体化しない。

- partial matchが次lookupでpreferred fontの出現・消失を観測する。
- `list_font_choices()`が前回呼出し後に追加されたfontを観測する。
- process-global font cacheが存在しない。

### 4.4 Nested fontをroot-relative valueで保存する

font scannerはcanonical pathだけでなく、それを発見したsearch rootからのrelative pathも保持する。
`list_font_choices()`はnested fontをbasenameへ潰さず、次のようなPOSIX文字列を返す。

```text
Supplemental/Kannada MN.ttc
```

root直下のfontは従来どおりbasenameとなる。これにより、GUIで今後選択したnested fontは
`resolve_font_path()`の`root / raw` direct branchで解決でき、evaluation sessionの初回tree scanも不要になる。

重複規則を次のように固定する。

- 異なるrelative pathを持つ同basename fontは別候補として保持する。
- 複数search rootに同一relative valueがある場合、resolverで到達可能な先頭rootだけを候補にする。
- symlink等で同じcanonical pathを再発見した場合も、探索順で先に見つかった候補だけを保持する。
- GUI表示stemが重複する場合だけrelative pathを併記し、ユーザーが区別できるようにする。

既存ParamStoreの`Kannada MN.ttc`は自動migrationしない。自動書換えは描画開始時のstore mutationとなり、
同basenameが複数ある場合に選択を推測するためである。既存値はowner-local resolverで初回だけtree scanし、
以後はsnapshotから高速に解決する。ユーザーがGUIで明示的に選び直した時だけrelative valueへ更新する。

既存basenameとrelative候補のbasenameが一意に一致する場合は、popup内のselected表示だけ対応付ける。
曖昧な場合は自動選択・自動書換えをしない。

### 4.5 Font pickerの一覧はGUI sessionが所有する

`WidgetSessionState`へfont choice snapshotを追加し、全font rowで共有する。

- comboが閉じているframeでは`list_font_choices()`を呼ばない。
- session内で初めてcomboを開いた時だけ一覧を構築する。
- comboを開いたままの後続frameと別font rowでは同じsnapshotを再利用する。
- popup内に`Refresh fonts`を設け、押した時だけsnapshotを破棄してその場で一度再構築する。
- GUI sessionの`clear()` / `close()`でsnapshotを破棄する。
- 別GUI session間では共有しない。

runtime configとfont dirsはParameterGUI lifetime中に固定されるため、catalog置換だけではfont choice snapshotを
破棄しない。filesystemへのfont追加・削除は明示的な`Refresh fonts`で反映する。coreのstateless APIと
evaluation resolverのhot-reload契約はこのGUI snapshot契約とは分離する。

## 5. 実装アクション

### Phase 0: Baselineと作業境界

- [x] `260809.py`と`18.py`のheadless/実GUI差を計測した。
- [x] Geometry cache、GPU index cache、MIDI、worker数、render scaleが主因でないことを切り分けた。
- [x] 保存済みnested fontとfont pickerの二重走査を特定した。
- [x] 実装開始時のHEADと作業ツリー差分を再記録する。
- [x] `/tmp`の再現probeを再実行できることを確認する。

### Phase 1: Font tree scanner / resolver

- [x] recursive scannerの出力を、canonical path、relative value、正規化name/stem、directory identityを持つ
  immutable snapshotへまとめる。
- [x] 現行のroot優先順、root内安定順、canonical dedupeを一つのscanner実装へ集約する。
- [x] 最新snapshot 1件だけを所有する`FontPathResolver`を実装する。
- [x] direct path / `root / raw` lookupではtree indexを構築しない。
- [x] directory identityが不変なら再帰filesystem scanを行わず、memory indexを検索する。
- [x] font候補symlinkと外部targetのidentityが不変なら同じsnapshotを再利用する。
- [x] directory identity変更時だけindexを再構築する。
- [x] scan中にdirectoryが変化したsnapshotはcacheしない。
- [x] `resolve_font_path()` / `list_font_choices()`のstateless contractを維持する。

### Phase 2: Evaluation resourceへの所有権統合

- [x] `FontResources`に`FontPathResolver`を一つ所有させる。
- [x] `FontResources.resolve()`のpath解決をowner resolverへ委譲する。
- [x] path解決後の`path.stat()`、asset key、content digest、lease、renderer cacheは変更しない。
- [x] `clear()` / `close()`でresolver snapshotも確実に破棄する。
- [x] configが変わった場合は最新snapshotを置換し、別configの結果を混在させない。
- [x] existing lock/lifecycleと同じowner境界でthread safetyを保つ。

### Phase 3: Font choice valueとGUI cache

- [x] nested font choiceをsearch-root-relative POSIX valueにする。
- [x] 重複relative value、重複basename、canonical path重複の規則を実装する。
- [x] 重複stemだけをrelative path付きlabelで区別するpure helperを追加する。
- [x] 既存basename値を一意なbasename候補へselected表示だけ対応付ける。
- [x] `WidgetSessionState`にsession-owned font choicesを追加する。
- [x] closed comboでは一覧を構築しない。
- [x] first openで一度だけ構築し、held-open frameと別font rowで再利用する。
- [x] `Refresh fonts`で明示的にinvalidate/rebuildする。
- [x] GUI close/session clearでchoice snapshotを解放する。

### Phase 4: Documentation

- [x] `font_resolver.py`、`font_resources.py`、`session_state.py`、`widgets.py`のdocstringへownerと
  invalidation契約を記載する。
- [x] `docs/migration_2026-08-10.md`を新規作成し、nested fontのGUI保存値がbasenameから
  root-relative pathになること、既存basenameが有効で自動migrationされないこと、
  `Refresh fonts`の使い方を記載する。
- [x] architecture文書は、external dependency fingerprintやowner境界自体を変えないため更新しない。

## 6. Test計画

wall-clockをpytest assertionにしない。性能回帰は、60静止frameにおけるscan/build/cacheの決定的な
call countで固定する。実時間は手動probeで観測する。

### 6.1 Core resolver

- [x] nested font choiceが`Supplemental/Kannada MN.ttc`になる。
- [x] そのrelative valueはrecursive scannerを呼ばずdirect branchで解決する。
- [x] root直下fontはbasename valueを維持する。
- [x] 同basename・異relative pathの候補を失わず、同relative valueのshadow候補は公開しない。
- [x] search keyへrelative pathを含め、directory名でもfilterできる。
- [x] stateless `resolve_font_path()`がpreferred fontの追加・削除を次lookupで観測する既存testを維持する。
- [x] stateless `list_font_choices()`が次callでfilesystem追加を観測する既存testを維持する。

### 6.2 FontResources / external dependency

- [x] nested basenameを同じ`FontResources`で60回resolveし、recursive tree scanが初回1回だけである。
- [x] warm 59回が同一leaseを返し、asset file openも追加発生しない。
- [x] 別ownerではsnapshotを共有せず、各ownerが初回scanを持つ。
- [x] `clear()`後の次lookupでscanを再実行する。
- [x] nested preferred fontの追加・削除をdirectory identity変更から次lookupで観測する。
- [x] 新規nested directoryとmissing search rootの出現を次lookupで観測する。
- [x] font候補symlinkの外部target消失とdangling target出現を次lookupで観測する。
- [x] same-path font置換で新lease、external dependency key、geometry outputへ切り替わる。
- [x] config A/Bの探索結果を混同しない。
- [x] process-global resolver/choice cacheが存在しないarchitecture testを維持する。

### 6.3 Geometry cacheとの統合回帰

- [x] nested fontを使う`G.text()`を同じ`RealizeSession`で60 frame評価する。
- [x] Geometry cacheはmiss 1 / hit 59、font tree scanは1回となる。
- [x] preflightは毎frame実行され、external dependency lease/fingerprintは同一である。
- [x] font置換時はGeometry cache hitを誤って返さず、新しいkey/outputへ切り替わる。

### 6.4 Parameter GUI

- [x] comboを閉じたまま60 frame、複数font rowを描画しても`list_font_choices()` callは0回である。
- [x] 同じsessionでcomboを60 frame開いたまま描画しても一覧buildは1回である。
- [x] 同じsessionの別font rowでもsnapshotを共有する。
- [x] `Refresh fonts`を押した場合だけbuild countが1増え、新規候補を表示する。
- [x] 別GUI sessionではcacheを共有しない。
- [x] `clear()` / close後の再openは新しい一覧を構築する。
- [x] nested choice clickがroot-relative valueを返す。
- [x] 重複stem labelとunique legacy basename selected表示を検証する。

### 6.5 Focused / broader検証

- [x] `PYTHONPATH=src pytest -q tests/core/test_font_resolver.py`
- [x] `PYTHONPATH=src pytest -q tests/core/test_font_resources.py`
- [x] `PYTHONPATH=src pytest -q tests/core/test_font_evaluation.py`
- [x] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui/test_parameter_gui_font_filter.py`
- [x] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui/test_parameter_gui_session_state.py`
- [x] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui/test_parameter_gui_lifecycle.py`
- [x] `PYTHONPATH=src pytest -q tests/api/test_render_session.py tests/interactive/runtime/test_scene_runner_mp_draw.py`
- [x] 対象ファイルへ`ruff check`を実行する。
- [x] `mypy src/grafix`を実行し、既存課題と今回差分を分けて報告する。
- [ ] focused検証成功後に、ユーザー承認があればfull pytestを実行する。

### 6.6 手動performance確認

- [x] 保存済み`260809`を変更せず、headless 100 steady framesでfont tree scanが初回1回だけであることを確認する。
- [x] 同条件のfont resolve中央値が、現行約22 msから1 ms未満へ下がることを診断値として確認する。
- [ ] 実GUIでfont comboを閉じた状態のInspectorから20〜30 ms級のfont一覧走査が消えることを確認する。
- [ ] `GRAFIX_PERF=1`でPreview/Inspector各区間を記録し、`18.py`と比較する。
- [x] FPSそのものはhardware、vsync、window状態に依存するためCI hard gateにしない。

実GUIの再計測は2回試みたが、現在の実行環境ではdisplayを列挙できず、pygletの`screen[0]`参照で
`IndexError`となった。このためproduction実GUIのafter値は未取得であり、上記2項目を未完了のまま残す。

### 6.7 検証結果

- focused core / GUI / runtime: 136 tests passed
- Parameter GUI全体: 339 tests passed
- `ruff check`（対象ファイル）: passed
- `git diff --check`: passed
- `mypy src/grafix`: 今回変更行外の既存4 errors
  - `src/grafix/core/font_resources.py:329`の`TTFont`属性
  - `src/grafix/core/effects/boolean.py:145,178,182`の`PyPolyNode`型名
- 保存済み`260809`のheadless steady 100 frames:
  - `FontResources.resolve`中央値: 22.436 ms → 0.091 ms
  - `SceneRunner`中央値: 22.901 ms → 0.365 ms
  - Geometry cache: first miss 1、以後104 hits
- full pytest: ユーザー承認を求める長時間検証のため未実施

初版ではdevtools benchmark providerを追加しない。今回のO(font files) per-frame再発は上記の決定的な
scan/build call count testで検出でき、wall-clock thresholdを導入せずに済むためである。長期の実時間推移を
benchmark reportへ載せる必要が出た場合は、別計画でself-sampling caseを追加する。

## 7. 受け入れ基準

次をすべて満たした時だけ実装完了とする。

1. 現在の`260809` ParamStoreを編集せず、static text 60 frameのrecursive font tree scanが1回である。
2. Geometry cacheはmiss 1 / hit 59で、external preflightのcorrectnessは維持される。
3. closed font picker 60 frameのfont choice列挙が0回である。
4. open font pickerはsessionごとに1回だけ一覧を構築し、明示Refreshでのみ再構築する。
5. nested fontの新規GUI選択はroot-relative POSIX valueとして保存される。
6. same-path置換、preferred候補の出現・削除、nested directory追加、missing root出現を次lookupで観測する。
7. stateless resolver/list API、探索優先順、process-global cache禁止、resource close契約を維持する。
8. `260809.py`、`18.py`、既存ParamStore、設定ファイル、依頼外差分を変更しない。
9. focused tests、targeted lint、type checkの結果を記録し、未完了項目を明示する。

## 8. 非ゴール

- full-frame redraw skip、pause中worker task coalescing、dirty-frame architectureは本計画へ含めない。
- `draw(t)`の呼出し回数やMP task submit方針は変更しない。
- external dependency preflightをGeometry cache lookup後へ移動しない。
- font部分一致検索を廃止しない。
- filesystem watcher、background thread、TTL cacheを導入しない。
- ParamStore schemaをbumpしない。
- 既存basename font値を起動時に自動migrationしない。
- `data/input/font`を移動、flatten、削減しない。
- sketch固有のfont指定やrender scaleを性能対策として書換えない。

## 9. 予定変更ファイル

Production:

- `src/grafix/core/font_resolver.py`
- `src/grafix/core/font_resources.py`
- `src/grafix/interactive/parameter_gui/session_state.py`
- `src/grafix/interactive/parameter_gui/widgets.py`

Tests:

- `tests/core/test_font_resolver.py`
- `tests/core/test_font_resources.py`
- `tests/core/test_font_evaluation.py`
- `tests/interactive/parameter_gui/test_parameter_gui_font_filter.py`
- `tests/interactive/parameter_gui/test_parameter_gui_session_state.py`
- 必要な場合のみ、既存lifecycle testのowner解放assertを補強する。

Documentation:

- `docs/migration_2026-08-10.md`（新規）
- 本計画書

stub、公開signature、ParamStore codec、benchmark report、sketch、font assetには変更を加えない。

## 10. 実装後の報告形式

実装中はPhaseごとに本計画のcheckboxを更新する。完了報告では次を分けて示す。

- 完了したproduction変更
- 維持したhot-reload / cache correctness契約
- focused test、lint、mypyの結果
- manual headless / GUI測定のbefore/after
- full pytestを実施したか否か
- 未完了項目と、依頼外差分へ触れていないこと
