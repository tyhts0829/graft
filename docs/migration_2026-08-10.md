# 2026-08-10 Font path / picker hot-path修正への移行

`G.text()`のfont探索とParameter GUIのfont pickerを、静止frameでfont treeを毎回再帰走査しない
session-ownedな実装へ変更する。

## 1. Nested fontの保存値

Parameter GUIで新しくfontを選択した場合、探索root配下のnested fontはbasenameではなく、
探索rootからのrelative POSIX pathとして保存する。

```text
# 旧GUI選択値
Kannada MN.ttc

# 新GUI選択値
Supplemental/Kannada MN.ttc
```

探索root直下のfontは従来どおりbasenameとなる。relative pathは既存の`resolve_font_path()`と
`G.text(font=...)`がそのまま受け付ける。

同basenameのfontが異なるsubdirectoryにある場合は別候補として表示し、stemが重複する候補だけ
relative pathを併記する。複数の探索rootに同一relative pathがある場合は、従来の探索優先順で
最初のrootにあるfontを使う。

## 2. 既存ParamStore

既存ParamStoreに保存されたbasenameは引き続き有効であり、schema migrationや手動編集は不要である。
起動時に値を推測して自動書換えすることもしない。

既存basenameはsession-ownedなfont tree indexで初回だけ探索し、探索directoryが変化しない限り
後続frameでは同じindexを再利用する。GUIでfontを明示的に選び直した時だけ、新しいrelative valueが
通常のparameter editとして保存される。

## 3. Font treeのhot reload

evaluation ownerは、font treeのfile一覧ではなくdirectory membershipとfont候補symlinkのidentityを
毎lookup確認する。次の変更を観測した場合だけtree indexを再構築する。

- font fileの追加、削除、rename
- nested directoryの追加、削除
- 存在しなかったfont search rootの出現
- search root自体の差替え
- font候補symlinkの付替え、外部targetの出現・消失

選択済みfont fileの同一path上の内容差替えは、従来どおりfile statとcontent digestで次lookup時に
検知する。Geometry cacheより先にexternal dependencyを確認する順序は変更しない。

cacheは`FontResources` ownerに所属し、別sessionや別configへ共有しない。`clear()` / `close()`では
font asset、glyph resourceと一緒にtree indexも破棄する。

## 4. Font pickerの更新

font comboを閉じている通常frameではfont一覧を列挙しない。sessionで初めてcomboを開いた時にだけ
一覧を構築し、同じsessionのfont rowで共有する。

session開始後にfilesystemへ追加・削除したfontを一覧へ反映する場合は、combo内の
`Refresh fonts`を押す。評価側のfont tree hot reloadと、GUIの一覧snapshotは別のownerであるため、
評価結果の変更検知にこの操作は不要である。

GUIを閉じると一覧snapshotも破棄され、次のGUI sessionでは新しく構築する。

## 5. 変更しない契約

- `G.text()`の公開signatureとfont部分一致指定
- `resolve_font_path()` / `list_font_choices()`を直接呼ぶ場合のstatelessなfilesystem観測
- config dirs、package dirs、root内安定順による探索優先順位
- font asset fingerprintと`ResolvedFontLease`
- external dependency preflightとGeometry cache lookupの順序
- ParamStore schema

互換wrapper、process-global font cache、TTL、filesystem watcherは追加しない。
