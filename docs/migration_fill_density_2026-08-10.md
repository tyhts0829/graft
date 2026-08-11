# `E.fill` の絶対 density への移行（2026-08-10）

`E.fill()` の `density` は、入力 geometry の高さに対する相対的な本数スケールから、
100 scene units を固定基準とする nominal scanline 数へ変更する。

## 新しい計算

```text
N = clamp(round(density), 2, 1000)  # density > 0
nominal_spacing = 100 / N
```

`density=0` は従来どおり hatch を生成しない。標準的な2D plotでscene unitがmmなら、
`density=25` は100 mmあたり25本、nominal pitch 4 mmに相当する。

`spacing_gradient=0` の場合、geometryの大きさ、別の `fill()` 呼び出し、同一入力内のgroup、
hatch angleにかかわらず同じ垂直pitchを使う。大きいgeometryは同じpitchで広い範囲を覆うため、
小さいgeometryより多くの線を生成する。angle間の本数は投影幅に応じて変わり、同数には補正しない。

## 旧値を見た目に近づける目安

旧実装の特定のplanar scopeで使われていたlocal Y高さを `H_old` とすると、旧pitchへ近づける値は
次で見積もれる。

```text
old_N = clamp(round(old_density), 2, 1000)
new_density ~= 100 * old_N / H_old
```

旧scopeの高さが100なら変更不要。動的scale、複数group、nonplanar local faceでは旧基準高さが
評価時に変わるため、一つの値へ完全には変換できない。

有効本数上限1000を維持するため、最小nominal pitchは0.1 scene unitsとなる。旧実装で0.1未満の
pitchを使っていた作品は完全には再現できない。`min_spacing` は下限floorであり、pitchを0.1未満へ
縮める用途には使えない。

## effectの順序

- geometryをscaleしてから `fill` する: nominal pitchは変わらず、線の本数が増減する。
- `fill` してからscaleする: 生成済みhatchもscaleされ、最終pitchが変わる。
- fill後のnonuniform scale、warp、3D projectionも最終pitchを変え得る。

## 保存済みparameter

保存済みGUI / ParamStoreのdensity値は自動変換しない。旧基準高さが保存されておらず、安全な
一括変換ができないためである。既存storeを再読込すると見た目が変わり得る。比較renderを行う場合は
parameter persistenceを無効にし、コード上の値だけでbefore / afterを確認する。

legacy mode、bbox換算flag、compatibility wrapperは追加しない。

## `min_spacing` default

`min_spacing` の既定値は `0.05` scene unitsである。引数省略は `min_spacing=0.05` の明示と
同じ結果になり、下限floorを無効にする場合は `min_spacing=0.0` を明示する。
