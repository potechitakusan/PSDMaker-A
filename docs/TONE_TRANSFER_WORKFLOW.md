# 第2機能（トーン版）：別線画へのトーン貼り

第1機能（トーン版）で決めた素材別の濃度・ベタ・ホワイトと網設定を、利用者が指定した別線画へ引き継ぐ。新しい線画を変形せず、参照の領域番号・位置・マスクを移植しない。カラー版の第2機能は [COLORING_WORKFLOW.md](COLORING_WORKFLOW.md) を使う。

## 準備

start_preview.batの存在を確認し、開始時刻を記録する。通常の制作では利用者指定の線画が必須。実装試験だけは人工fixtureを別jobで使い、その制約を記録する。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-tone-transfer --lineart input/new_lineart.png --source-job work/source_manga --job work/new_tones --output output/new_tones/output.psd
```

参照jobの代わりに `--source-reference output/source/tone_reference.json` を指定できる。どちらか一方が必須。新jobは空の別フォルダに限定する。参照job・線画・参照PSDは変更しない。出力時の付属ファイルも守るため、参照PSDと同じフォルダへの出力は拒否する。

線画の二値化閾値 `--threshold` は1〜254（既定192）、隙間補助 `--gap-close` は0〜64px（既定3）、最小領域 `--min-area` は1〜1000px（既定12）。補助線は領域分割だけに使い、完成PSDの線へは描かない。線画は二値化後の画素で固定する。細線・アンチエイリアスの変化を `manga_alignment.png` で確認する。

## 付属作例を参照にする

[公開作例](../examples/README.md#トーン版) のPSDとJSONから、元の制作jobなしで準備できる。現在の第1機能PSDは利用者指定の調整版で、以下のコマンドはその設定を引き継ぐ。第2機能の付属ファイルは調整前の参照から作った検証用のため、現在の設定とは一致しない。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-tone-transfer --lineart examples/manga_transfer/lineart.png --source-reference examples/manga/tone_reference.json --job work/tone_example --output output/tone_example/output.psd
.\start_manga_adjust.bat --job work/tone_example
```

これは新規割当用の準備であり、完成作例の領域・役割はコピーしない。以下の手順で全領域を確認・割り当てて反映する。付属の完成作例では、二値化前の薄い線を塗り分け境界だけに使うPython補助を加えている。公開線画PNGは完成PSDの二値線なので、その薄線情報と補助割当を含まない。prepareだけで同じ分割・陰影になるわけではない。

## 参照情報

新しいbuild-manga/export-mangaはPSD隣に `tone_reference.json`（schema `tone_reference_v1`）を保存する。既存jobへの追加は `export-tone-reference --job work/source_manga`。以前のjobでも、その計画を現行描画処理へ通して濃度を解決する。元jobのPSD・計画・レビューは変更しない。

JSONにはPSDのファイル名・SHA-256・サイズ、tone_settings、素材のsemantic_id/display_name/group_path/tone_notes、数値のbase/shadow/highlight/beta_fillとbetaの可否を入れる。autoのベースは実描画時に選ばれた値、影・光は網点化前の対象画素の中央値。対象のない影・光は方針から補完する。元のグラデーションや陰影形状は含まない。網点の黒画素率を素材濃度として再推定しない。

PSDとJSONを同じフォルダに保管する。読み込み時にPSDハッシュ・サイズ・素材値を検証する。JSONの意味判断や画質の合格は保証しない。参照job経由ではPSDと意味計画、JSON経由ではPSDとJSONをハッシュ監視する。新jobの線画・配列・参照スナップショットも変更を検出する。

JSONの項目・濃度の制約・検証範囲は [TONE_REFERENCE.md](TONE_REFERENCE.md)。第2機能（トーン版）で構築したPSDとJSONからも、さらに別線画へ引き継げる。

## 領域の割り当てと画面調整

通常は参照の素材濃度・網設定をそのまま使う。Codexは新線画の素材・役割を判断するが、見栄えのための数値調整は利用者に推奨設定を求められた場合だけ行う。利用者の明示指定と、画面で利用者自身が行う調整は優先する。

`start_manga_adjust.bat` →「別線画へトーン」でも準備できる。既存の引継ぎjobは「ジョブを開く」で選ぶ。「参照画像を見る」で元PSDの完成プレビュー `source_appearance.png` を確認できる。

1. 参照素材と貼る役割（ベース・影・ハイライト・ベタ）を選ぶ。
2. 「領域の割当表示」の画像をクリック。ピンクは未割当、仮色は素材の区別。クリックは選択素材への明示的な再割当で、古い割当を取り除く。「割当を1つ戻す」で戻せる。右ドラッグでスクロールする。
3. 素材ごとのベース・影・ハイライト・ベタ下の濃度とベタの可否を確認する。通常は継承値を維持し、利用者は必要に応じて調整できる。濃度は0〜100%、ハイライト≤ベース≤影、ベタ下は100%未満。ベタを使わない場合はベタ指定領域と100%相当の部分をベタ下の濃度で描く。背景も明示的に割り当てる。端に接した領域を自動で背景にはしない。
4. 網の設定を選ぶ。「画像幅基準」はscreen_lines、「dpi・LPI基準」はdpi÷LPIで網間隔を決める。素材濃度はどちらでも上の指定値を使い、階調の再推定は行わない。後者はdots固定。前者はdots/grayを選べる。引継ぎ時は参照設定の数値と単位を保持するため、画像幅基準の同じ数値でも幅の異なる線画では網間隔が変わる。
5. 全領域の素材・役割を確認しチェックを入れて「反映」。未割当・重複・未知素材・不正濃度は拒否する。利用者の確認はuser_reviewedと記録し、Astraの目視確認と区別する。
6. 原寸プレビューを確認して「PNG出力」「PSD出力」。未反映・計算中・入力や計画が変わった場合は出力しない。100%表示で網を確認する。

反映は調整案を `tone_transfer_adjustments.json`、網設定を `manga_adjustments.json` へ保存する。調整案には基になる計画のハッシュを付け、再起動時も一致する案だけを読み込む。既存semantic_plan/PSDは反映だけでは変えない。PSD出力で適用案を意味計画へ記録し、旧計画をhistoryへ保存する。PNG出力は原寸・不透明RGBの描画結果を保存・読み戻し検証する。表示倍率は出力サイズに影響せず、dpi指定時はPNGにも解像度情報を付ける。PNG出力だけでPSDの事後レビューを完了したことにはしない。

プリセットは両形式共通の網設定を保存する。線画固有の領域割当と素材濃度はjobの調整案に保存する。新しい影や光を自動で生成する機能ではなく、線画で閉じられた領域に役割を指定する。閉じていない陰影を描き込むブラシや自動照明は対象外。

## CLIと意味計画

全 `manga_regions_*.png` と参照画像を実際に開き、新線画のRegion IDで割り当てる。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent assign-tone-region --job work/new_tones --region 2 --material hair --role base
```

領域番号と素材IDは作例。実際の一覧の値を指定する。CLIでの割当変更はpartial_reviewに戻し、古いGUI調整案を無効化する。Astraが全領域を目視確認したらsemantic_plan.classification_sourceをastra_reviewedにする。

各partはsemantic_id、display_name、group_path、source_material、regions、role_regions、transfer_toneを持つ。role_regionsはbase/shadow/highlight/betaごとのRegion ID配列で、regionsはその重複のない和。ひとつの素材に複数の閉領域をまとめ、役割で分ける。transfer_toneは参照JSONのtoneと同じ5項目を持つ。

画面で全領域を確認して「反映」した調整案と設定を使う場合:

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent export-manga-png --job work/new_tones --reuse-last --output output/new_tones/output.png
.\.venv\Scripts\python.exe -m anime_layer_agent export-manga --job work/new_tones --reuse-last --output output/new_tones/output.psd
```

CLIで意味計画を直接編集・確認した場合は次を使う。build-mangaはsemantic_planの値を使い、GUIの調整案は取り込まない。PNGもその計画で出すにはGUI調整案がない状態で実行する（assign-tone-regionは古い案を無効化する）。`--settings` で意味計画を指定すると、そのtone_settingsを使う。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent build-manga --job work/new_tones --output output/new_tones/output.psd
.\.venv\Scripts\python.exe -m anime_layer_agent export-manga-png --job work/new_tones --settings work/new_tones/semantic_plan.json --output output/new_tones/output.png
.\.venv\Scripts\python.exe -m anime_layer_agent post-review --job work/new_tones
```

`tone_transfer_job.json` と共通描画・レビュー用の `manga_job.json` を併存させる。再構築はbuild-manga。prepare-manga/register-mangaをこのjobへ使わない。

## 事後確認

PSDは「ホワイト」「線画」「ベタ・トーン/素材/パーツ」「紙」。ベタの下にも指定濃度のトーンを敷く（ベタ下の濃度0なら紙色）。黒ベース素材でベタを使う場合はホワイト下にもベタと網を敷くため、ホワイトを非表示にすると黒ベースへ戻る。線は独立レイヤーで固定し、線とベタは重複保存しない。

export-mangaはpost-reviewまで実行する。build-mangaの場合はpost-reviewを実行し、[MANGA_WORKFLOW.md](MANGA_WORKFLOW.md)の全比較画像とsource_appearance.pngを開く。入力線の保持、素材・役割の対応、背景、ベタ下、影/光OFF、網点1:1、PNGとPSDを両方出した場合の一致を確認し、finish-reviewで確定する。読み戻し数値と意味・見た目を分けて評価する。再構築後は全確認をやり直す。PNG単独出力も目視で確認する。
