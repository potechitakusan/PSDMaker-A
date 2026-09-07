# 指定線画への着色（任意の第2機能）

この工程は、利用者が着色対象の線画を指定した場合だけ実行する。完成イラストからPSDを分解する既存工程は変更しない。通常の分解から自動的に別ポーズや着色を始めない。利用者が指定していない線画をフォルダから探して着色しない。

必要な入力は、着色する線画と、同じキャラクターを編集優先工程でPSD化した参照job、またはPSDと隣接したcoloring_reference.json。実PSDのBaseレイヤーからRGBを検証する。元の画像の代表色を再推定したり、照明ガイドの色でBaseを置き換えたりしない。同じpalette_idのBaseが単色で統一されたPSDを対象とする。

持ち運べるJSONは第1機能のartist構築で自動出力される。既存jobは `export-coloring-reference --job work/<source>` で追加できる。形式は [COLORING_REFERENCE.md](COLORING_REFERENCE.md)。元jobなしで公開作例を使う準備例:

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-coloring --lineart examples/coloring/lineart.jpg --source-reference examples/output/coloring_reference.json --job work/coloring_example --threshold 192 --gap-close 5 --min-area 20
```

`--source-reference`と`--source-job`はどちらか一方を指定する。JSONのpartsにある意味名・階層・coloring_notesを読み、新線画の素材を目視対応させる。元のsource_bboxを新ポーズへコピーしない。JSONは領域割当を自動生成しない。JPGも入力できるが圧縮による線の変化を確認する。

## 入力を準備する

開始時刻を記録し、start_preview.batを用意してmonitorを登録する。線画と参照キャラクターを実画像で確認し、人物・衣装・素材の対応を判断する。別ポーズなので参照キャラクターの形へ線画を変形しない。線画がなければこの機能は開始しない。企画書などで明示された開発試験に限り、生成した線画を模擬入力として別jobへ保存し、その旨を記録する。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent monitor --psd output/<name>/output.psd --job work/<name>
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-coloring --lineart input/new_pose_lineart.png --source-job work/<source> --job work/<name> --threshold 192 --gap-close 5
```

- `--lineart`は必須で既定パスなし。参照jobと同じjobは拒否し、既存jobを上書きしない。
- thresholdは1〜254。白背景へ合成した輝度を二値化し、lineart_binary.pngと白を透明にしたlineart_rgba.pngを生成。入力そのものは変更しない。二値化による細線・アンチエイリアスの変化を必ず確認する。
- gap-closeは0〜64px。0は隙間補助なし。近接した線の端点と向きから仮の境界を補う。補助線は塗り分け用であり、完成PSDの線へ描き足さない。全ての切れ目を正しく閉じる保証はない。
- min-areaは1〜1000、既定12px。これ未満の微小領域は近隣へまとめる。目の小さな光など、必要な領域が消えていないか確認する。
- coloring_regions.jsonにはID・bbox・面積・Python算出の種座標・画像端への接触を保存。画像端に接しているだけで背景とは決めない。

単独の二値化・透明化も利用できる。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent binarize-lineart --input input/line.png --output output/line_binary.png
.\.venv\Scripts\python.exe -m anime_layer_agent binarize-lineart --input input/line.png --output output/line_rgba.png --transparent
```

## Astraが領域に意味と色を割り当てる

全coloring_regionsの画像を開き、source_palette.jsonのpalette_idと対応させる。生成されるsemantic_plan.jsonのpartsへ、意味ID・日本語名・パレット・領域番号・階層を記入する。カラー配列や座標列をLLMが生成する必要はない。

```json
{"semantic_id":"hair_front","display_name":"前髪","palette_id":"pink_hair",
 "regions":[10,19],"group_path":["キャラクター","髪"]}
```

IDは説明例。実画像で確認したIDのみを使う。座標から塗る場合はプレビューまたはPythonのseed_xyを利用する。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent fill-region --job work/<name> --x 120 --y 240 --to hair_front --palette-id pink_hair --display-name 前髪
```

fill-regionはその座標が属する閉領域を割り当て、flat_preview.pngを更新する。既に別パーツへ割り当てた領域は拒否する。再割当はsemantic_planの旧所有者から明示的に移す。線の上・範囲外・未知パレット・重複は拒否。backgroundにはpalette_id不要で、background_rgbの既定は白。

一部だけ大きな隙間がある場合は、その領域に限って補助を強める。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent split-color-region --job work/<name> --region 139 --gap-close 32
```

新IDはcoloring_split_139の画像とJSONへ保存し、対象外IDと入力の線画は保持する。計画はpartial_reviewへ戻り、照明計画を無効化する。新IDを割り当て、全素材・背景・白目・アクセントを確認してからclassification_sourceをastra_reviewedへ変更する。未割当を自動的に背景へ流さない。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent paint-flats --job work/<name>
```

全領域が一度だけ割り当てられたことを検証し、参照PSDのBase色で下塗りする。線画の黒い部分は最上段の透明線画レイヤー。元のPSDは変更しない。下塗りだけのPSDもbuild-coloredで保存できる。

## 任意の影・光

flat_preview.png全体を内蔵の画像生成AIへ1回渡し、元の輪郭・ポーズ・配色を維持した照明案を作る。画素ごとのLLM指示やパーツごとの大量生成は不要。CLIは画像生成APIを自動呼出ししない。照明案を別ファイルに保存し、実画像を開く。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-lighting --job work/<name> --image work/<name>/lighting_guide.png --max-shift 8
```

入力の線画は固定し、照明ガイド側をリサイズ・局所補正する。alignment_comparison.pngとlighting_alignment.jsonを確認する。エッジ距離には陰影境界も含むので、輪郭誤差や合格の自動判定として扱わない。確認後、semantic_planのlighting.reviewed=trueと具体的なnotesを記入する。

lighting.strengthは0〜2、temperatureはneutral/cool/warm。ガイドの明度だけを使い、元のパーツマスク内に一定照明色のShadow（Multiply）とHighlight（Screen）を作る。ガイドの色・線・形をPSDへ貼り付けない。元線周辺の暗さは近い同素材の内側で補間し、影への二重線混入を抑える。再描画差や細いパーツの陰影には限界があるため、目視確認と局所修正を行う。

Base・線画・割当を変えた場合は、下塗りから照明ガイドを作り直して確認する。入力やガイドのハッシュ不一致を拒否する。

## PSD保存と事後チェック

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent build-colored --job work/<name> --output output/<name>/output.psd
.\.venv\Scripts\python.exe -m anime_layer_agent post-review --job work/<name>
# 全画像を開きPOST_REVIEW_CHECKLIST.mdに従って所見・根拠を記録
.\.venv\Scripts\python.exe -m anime_layer_agent finish-review --job work/<name>
```

着色jobにはbuild-coloredを使う。build-compact/build/apply-repairを使わない。既定の描画上限は前景パーツNに対して3N+2、明示する場合はmax_pixel_layers。不要な透明レイヤーは作らず、描画枚数とフォルダ数を別記する。Baseの色統一許容値は0相当で、参照Base色をそのまま継承する。

評価対象はPythonが定義した着色結果からのPSD読み戻し（MAE≤0.5、最大3階調）。別ポーズの参照画像とのSSIMを品質点数にせず、AIガイドとの一致も完成品質の代用にしない。顔・衣装・塗り残し・色替え・線の保持・背景・照明をAstraが確認し、制約を記録する。数値合格だけでは完了しない。

jobと出力フォルダへPSD、プレビュー、解析結果、参照パレット、線画を保存。finish-review後は事後レビュー結果と時間ログを保存する。利用者の線画・PSD・計画・作業画像はGitへ追加しない。
