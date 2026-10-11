# 公開作例

| ファイル | 内容 |
|---|---|
| reference.jpg | 最新の第1機能PSDに対応する入力イラストの公開用JPG |
| output/output.psd | Base候補の目視選択を適用した第1機能の開発試験PSD。1024×1536、171描画＋48フォルダ |
| output/coloring_reference.json | 上記PSDと一組の14パレット・35前景パーツの情報。実Base・tones・選択理由を含む |
| output/preview.jpg | 第1機能PSDの完成色プレビュー |
| coloring/lineart.jpg | 第2機能の開発用生成線画を二値化した、PSD線レイヤーに対応するJPG |
| coloring/preview.jpg | 新しいBaseを継承したimagegen方式の実PSDプレビュー |
| coloring/output.psd | 第2機能のimagegen方式。1024×1536、99描画＋48フォルダ |
| coloring_normals/output.psd | 第2機能の法線マップ方式の比較例。1024×1536、104描画＋48フォルダ |
| coloring_normals/preview.jpg | 法線マップ方式の実PSDプレビュー |

両方式は `output/output.psd` と隣接JSONからBaseを継承しています。白布・髪は通常明部候補、肌・黒布は中間色候補を選び、下塗りが影色へ沈む問題を軽減しました。

今回は既存の手を振る線画と目視済み素材区分を使う、同ポーズのBase・照明比較試験です。参照PSDの最終素材マスクを継承しました。新ポーズの領域抽出・意味分類を改めて試験した結果ではありません。

imagegen方式は、新しい下塗りと参照PSDの完成色をCodex組込みimagegenへ渡して生成したガイドの明度を使います。`color_model: source_tones`、`tone_mapping: source_levels`で素材ごとの明暗と色を参照へ合わせました。ガイドの線や色をPSDへ直接貼っていません。白布の中間明度差は、無調整の+6.78から+0.21 L*へ縮まりました。

法線マップ方式は同一格子の既存推定法線を再利用し、既定の左上手前（光源1）から照明を計算しました。`source_tones` と `source_ramp` で素材別に明暗幅・色を合わせています。保存PSDはこの光源で固定されたBase/Shadow/Highlightです。法線配列は公開例に含めていません。50光源の切替は法線を登録した作業jobとプレビュー画面で利用できます。

両PSDの35BaseのRGB・alpha・bbox、入力二値線の完全保持、影/光の素材外画素0を検証しました。全色替え・素材・背景・明暗比較画像を目視し、事後レビューは制約付き完了。Python着色目標からのPSD読み戻しMAEはimagegen方式0.05824、法線方式0.07232、最大チャンネル差は両方1です。元絵の印象や影形状の再現点数ではありません。

制約: 法線方式は鼻の点状影、髪の艶、瞳の反射が元絵と異なるため、主例にはimagegen方式を採用しました。imagegen方式にも花・虹彩の色差や微細な照明位置差が残ります。うさぎの開いた耳や髪先の背景抜け、入力線の微小ジャギー、線主体の眉・黒目を継承しています。大きな色替えでは影・光の色も調整が必要です。Photoshop／CLIP STUDIO PAINTでの実アプリ確認は行っていません。

プレビューJPGは実PSDから作成し、EXIF/XMP・コメントを付けずに保存しています。
