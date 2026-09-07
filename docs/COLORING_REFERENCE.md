# 配色・意味情報の引き継ぎ

`coloring_reference.json`（schema 1）は、第1機能のPSDを第2機能の配色参照として持ち運ぶためのファイル。PSDと同じフォルダに保存する。元のworkフォルダやsemantic_plan.jsonは、読み込み時には不要。

| 項目 | 内容 |
|---|---|
| schema / kind | `1` / `coloring_reference` |
| psd_file / psd_hash | 隣接PSDのファイル名とSHA-256。絶対パス・親フォルダ参照は禁止 |
| semantic_plan_hash | 書き出し元の意味計画のSHA-256。元計画の添付は不要 |
| size / bbox_convention | PSDの幅・高さ、元画像のbbox座標規約（右端・下端は含まない） |
| classification_source | 元計画の分類状態。事後レビューの合格証ではない |
| palettes | palette_idごとの実Base RGB（0〜255整数）とsource_parts |
| parts | semantic_id、display_name、palette_id、group_path、coloring_notes、base_layer_path、source_bbox |

意味名と階層はAgentの確認した計画から引き継ぐ。RGBとBaseレイヤー階層・bboxはPythonが実PSDから取得する。coloring_notesは任意の素材説明・注意点で、未指定は空文字。背景の色、旧領域番号、マスク、照明ガイド、ローカル絶対パスは転送しない。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent export-coloring-reference --job work/source
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-coloring --lineart input/new_pose.jpg --source-reference output/source/coloring_reference.json --job work/new_pose
```

読み込み時にPSDハッシュ・サイズ・実Base RGB・パーツとパレットの対応を検証する。着色の途中でPSDやJSONが変わった場合は再準備を求める。RGBだけを書き換えてPSDと矛盾させることはできない。JSONは署名済みデータではなく、自由記述の意味・素材が正しいかはAgentが画像と照合する。

Base名が重複して一意に特定できないPSD、単色でないBase、同じpalette_idの異なるBase色は書き出しを拒否する。新規制作ではdisplay_nameを一意にする。参照PSDだけを外部アプリで編集した場合、既存jobのハッシュは一致しなくなるため、そのまま再書き出しはできない。

共有palette_idは配色の共通性を示す。PSD内の複数Baseレイヤーがペイントアプリで自動連動する仕組みではない。別ポーズの領域は新たに抽出・分類し、元bboxを塗り座標へ転用しない。
