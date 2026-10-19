# PsdMaker

完成イラストを、色・影・線を編集しやすいレイヤーに分けたPSDへ変換する、CodexとローカルPythonのツールです。作成した素材情報を別の線画の着色やトーン貼りにも使えます。

Codex Astraが画像を見て髪・肌・衣装・背景などを判断し、Pythonが位置合わせ、領域抽出、色・マスクの計算、PSD保存と読み戻しを行います。

## できること

| 目的 | 入力 | 必要な参照ファイル | 主な出力 |
|---|---|---|---|
| 完成イラストを編集可能なPSDに分解（第1機能・カラー） | 完成イラスト＋同じ構図の線画。線画がなければ画像生成を依頼可能 | なし | レイヤー分けしたPSD＋`coloring_reference.json` |
| 別の線画を参照色で着色（第2機能・カラー） | 着色したい線画 | 第1機能のPSD＋隣接する`coloring_reference.json`、またはPSDを参照できる作業フォルダ | 着色済みPSD。下塗りだけ、または影・ハイライト付き |
| カラーイラストを漫画トーン化（第1機能・トーン） | カラーイラスト。線画は任意 | なし | 線画・ベタ・ホワイト・パーツ別トーンのPSD＋`tone_reference.json`。原寸PNGも出力可能 |
| 別の線画へトーンを引き継ぐ（第2機能・トーン：**検証中**） | トーンを貼りたい線画 | トーン版のPSD＋隣接する`tone_reference.json`、またはPSDを参照できる作業フォルダ | 素材別のトーンを貼ったPSD・原寸PNG |

作業フォルダ（job）は、入力・領域・意味計画・再開用データをまとめたフォルダです。第2機能では新しい線画の領域を改めて確認し、素材を割り当てます。元のポーズやマスクをコピーする処理ではありません。第2機能が別ポーズの線画を自動生成することもありません。

カラーPSDは「衣装 → 袖 → ベース色・影・ハイライト」のように整理されます。同じ素材の色を揃えて出力しますが、PSD編集時に他のレイヤーの色まで自動で連動するわけではありません。

## 作例（カラー版）

完成イラストから作ったPSDの配色情報を使い、別ポーズの線画を着色した例です。影・ハイライトにはimagegenによる照明ガイドを使っています。

| 完成イラスト（第1機能の入力） | 別ポーズの線画（第2機能の入力） | 着色結果（第2機能の出力） |
|---|---|---|
| ![入力イラスト](examples/reference.jpg) | ![着色用線画](examples/coloring/lineart.jpg) | ![着色結果](examples/coloring/preview.jpg) |

- 分解結果：[編集可能PSD](examples/output/output.psd) ／ [配色情報JSON](examples/output/coloring_reference.json) ／ [プレビュー](examples/output/preview.jpg)
- 着色結果：[PSD](examples/coloring/output.psd) ／ [入力線画](examples/coloring/lineart.jpg)
- [作例ガイド：方式・再現条件・法線照明との比較・制約](examples/README.md#カラー版)

線画は作例用にimagegenで生成しました。通常の第2機能では利用者が線画を用意します。細線の途切れや照明の細部ずれが残っています。

## 作例（トーン版）

カラー原画をQwen-Image 2.1 Turboで再描画し、Manga Line Extractionで線を抽出した例です。掲載PSDはトーン設定を調整した版です。

| カラーイラスト（第1機能の入力） | トーン化結果（第1機能の出力） |
|---|---|
| ![トーン化するカラー原画](examples/manga/reference.jpg) | ![トーン設定を調整したPSDのプレビュー](examples/manga/preview.png) |

[原寸PNG](examples/manga/preview.png) ／ [編集可能PSD](examples/manga/output.psd) ／ [トーン情報JSON](examples/manga/tone_reference.json) ／ [線画PNG](examples/manga/lineart.png)

### imagegenのみの作例

同じ原画の縮小画像から、線画・再描画ガイド・背景透過画像をimagegenで生成した比較例です。調整版ではコートと背景のベタをトーンへ変え、コートのしわを見やすくしています。

| ツールの既定値 | Codexによる調整例 |
|---|---|
| ![imagegen・既定値版](examples/manga_imagegen_default/preview.png) | ![imagegen・調整版](examples/manga_imagegen_adjusted/preview.png) |

- 既定値版：[PNG](examples/manga_imagegen_default/preview.png) ／ [PSD](examples/manga_imagegen_default/output.psd) ／ [トーン情報JSON](examples/manga_imagegen_default/tone_reference.json)
- 調整版：[PNG](examples/manga_imagegen_adjusted/preview.png) ／ [PSD](examples/manga_imagegen_adjusted/output.psd) ／ [トーン情報JSON](examples/manga_imagegen_adjusted/tone_reference.json)
- [作例ガイド：生成素材・設定・検証結果・制約](examples/README.md#トーン版)

通常の制作では既定値を使い、Codexによる数値調整は推奨設定を依頼した場合だけ行います。Qwen作例とimagegen作例は出力サイズや設定が異なるため、同条件のモデル性能比較ではありません。線の粒状跡や生成時の形状差は残ります。

## 動作環境とセットアップ

- Windows、Git、Python 3.12以上。`python`コマンドが使え、プレビュー用のTkinterを含む環境が必要です。
- このフォルダを読み書きでき、画像を確認できるCodex環境。モデルは利用者のCodex画面で選択します。他のAIエージェントでの動作は未検証です。
- 初回の依存パッケージ取得に必要なインターネット接続。画像生成を使う工程には、その機能を利用できる環境も必要です。

PowerShellで取得・セットアップします。

```powershell
git clone https://github.com/potechitakusan/PSDMaker-A.git
cd PSDMaker-A
.\setup.bat
```

`setup.bat`は`python scripts/bootstrap.py`を実行し、プロジェクト内の`.venv`へ依存パッケージを入れます。以降のPython CLIは`.venv/Scripts/python.exe`を使います。ローカルCLIはOpenAI APIキーを要求しません。Codexや画像生成サービスの利用条件・料金は利用中のサービスに従います。

Qwenを使う場合は、別途Qwen-Image 2.1対応ノード・モデルを用意したComfyUIを起動してください。Manga Line Extractionには`comfyui_controlnet_aux`も必要です。これらは同梱せず、セットアップスクリプトも追加・更新しません。[必要なノードと実行方法](docs/MANGA_WORKFLOW.md#2b-qwen-image-21で生成するcomfyui)を参照してください。

**Qwenを選んでも、一連の作業が完全にローカルで完結するわけではありません。** Codexによる画像確認に加え、トーン版の標準工程では被写体と背景を分ける透過画像をimagegenで生成します。原画像が明らかな白背景の場合は、目視判断でこの透過生成を省略できます。再描画の`none`指定も、補助工程を含む外部AI利用を一括で無効にする指定ではありません。

外部へ画像を送りたくない場合は、画像を渡す前に、各工程のモデル・送信先と利用中の権限設定を確認してください。本ツールは完全ローカルモードを提供していません。送信・保存・学習利用等の条件はサービス／プラン／設定に依存します。[権利・外部送信の注意](#制約注意点)も確認してください。

## クイックスタート

Codexで取得した`PSDMaker-A`フォルダを開き、プロジェクト直下に`input`フォルダを作って入力画像を置きます。以下はCodexへ貼る依頼文です。ファイル名・`work/...`・`output/...`はサンプル名なので、自分の入力と保存先へ置き換えてください。用途ごとに別の作業フォルダと出力先を使います。

### 完成イラストをPSDに分解する

```text
AGENTS.mdとdocs/ARTIST_WORKFLOW.mdを読み、
input/reference.pngと、同じ構図の線画input/lineart.pngから編集可能なPSDを作成してください。
jobはwork/my_color、出力はoutput/my_color/output.psdにしてください。
描画レイヤー数と色統一許容値はお任せします。
位置合わせ、意味分類、編集設定、PSD生成、評価、事後レビューまで進めてください。
```

線画がない場合は、依頼文の入力指定を次のように置き換えます。画像生成機能を利用できる環境が必要です。

```text
入力はinput/reference.pngです。線画はありません。
画像生成機能で同じ構図の線画を別ファイルへ作り、位置ずれを確認してからPSDを作成してください。
```

描画レイヤー数は「100〜180枚」、色統一許容値は「30」のように指定できます。枚数にフォルダは含みません。色統一許容値は0〜100で、大きいほど同じ素材の色を揃えやすくします。未指定なら素材数と色のばらつきから決めます。

### 別の線画を参照色で着色する

```text
AGENTS.mdとdocs/COLORING_WORKFLOW.mdを読み、input/new_pose_lineart.pngを着色してください。
参照はoutput/my_color/output.psdと、その隣のcoloring_reference.jsonです。
jobはwork/my_coloring、出力はoutput/my_coloring/output.psdにしてください。
新線画の素材を確認して参照色で下塗りし、PSD生成と事後レビューまで進めてください。
```

影・ハイライトも必要なら、その希望と画像生成による照明ガイドの利用を依頼文に追加します。PSDと対応するJSONを同じフォルダに保管すれば、元のjobがなくても参照できます。ベース色の一致と、完成色の明るさ・色味は分けて確認します。

### カラーイラストを漫画トーン化する

```text
AGENTS.mdとdocs/MANGA_WORKFLOW.mdを読み、input/color.pngを漫画原稿風にトーン化してください。
再描画と線抽出はimagegen、トーン形式はdots（網点）でお願いします。
背景分離用の透過画像生成も含めてimagegenを使用できます。
jobはwork/my_manga、出力はoutput/my_manga/output.psdとoutput/my_manga/preview.pngにしてください。
トーン設定は既定値を使い、意味分類、位置合わせ、PSD生成、事後レビューまで進めてください。
```

再描画は`imagegen`／`qwen`／`none`、線抽出は`imagegen`／`qwen`（Manga Line Extraction）／`provided`（用意した線画）／`redraw`（再描画から抽出）、トーン形式は`dots`（網点）／`gray`（グレー塗り）から選べます。Qwenの再描画は既定1Mpxです。選択肢と補助工程の詳細は[トーン版の手順](docs/MANGA_WORKFLOW.md)を参照してください。

通常は既定値で出力します。自分で調整する場合は、位置合わせと意味分類の完了後に`start_manga_adjust.bat`を開きます。数値変更後は「反映」→「PNG出力」または「PSD出力」で保存します。PSD出力後にはCodexによる事後レビューも必要です。

### 別の線画へトーンを引き継ぐ（検証中）

```text
AGENTS.mdとdocs/TONE_TRANSFER_WORKFLOW.mdを読み、input/new_tone_lineart.pngへトーンを貼ってください。
参照はoutput/my_manga/output.psdと、その隣のtone_reference.jsonです。
jobはwork/my_tones、出力はoutput/my_tones/output.psdとoutput/my_tones/preview.pngにしてください。
参照の素材濃度と網設定を維持し、新線画の素材と役割を確認して、出力と事後レビューまで進めてください。
```

画面で進める場合は`start_manga_adjust.bat`の「別線画へトーン」から、参照・新線画・新規作業フォルダを選びます。領域に素材とベース・影・ハイライト・ベタの役割を割り当て、全領域を確認して「反映」し、出力します。閉じていない陰影を自動で描き足す機能はありません。[詳しい操作手順](docs/TONE_TRANSFER_WORKFLOW.md)を参照してください。

### 作業中の確認と成果物

`start_preview.bat`を開くと、Codexが登録した監視先のPSD・参照・差分・線画・背景などを別ウィンドウで確認できます。PSD作成前は待機し、保存後に表示を更新します。画面の起動は任意です。

PSDと参照JSONに加え、解析結果や比較画像・事後レビュー結果を保存します。`work/`は再開用データ、`output/`は成果物の保存先です。PSDを編集すると参照JSONのハッシュと一致しなくなるため、そのJSONを編集後のPSDとそのまま組み合わせることはできません。[配色JSON](docs/COLORING_REFERENCE.md)／[トーンJSON](docs/TONE_REFERENCE.md)の仕様を参照してください。

## 制約・注意点

- **第三者の制作物や、権利・外部AI利用の許諾が不明な画像、機密画像は入力しないでください。** 自分が権利を持つか、権利者から外部AIへの送信・解析・再構成・PSD出力に必要な許諾を得た画像だけを使ってください。公開画像、預かった原稿、二次創作、版権キャラクター、スクリーンショット、人物写真も、入力してよいとは限りません。
- **画像と作業指示はCodexおよび必要な画像生成機能の提供事業者へ送信されます。** 国外を含むサーバへの送信・保存・安全確認・人による確認・モデル改善や学習等の扱いは、サービス／プラン／設定／規約に依存します。作者はこれらを管理できず、送信後の完全削除も保証できません。
- **出力品質・用途適合性は保証しません。** パーツの誤認識・欠落・混入、線やマスクの位置ずれ、色・透過の不正確さ、PSD編集時の互換性問題が起こり得ます。公開・印刷・納品前には利用者自身で確認・修正してください。
- 数値評価は意味分離や見た目の保証ではありません。実PSDの色替え・単独表示・影や光の表示切替、トーンの原寸表示を確認します。原画との差とPSD読み戻しの誤差は別に評価します。
- 隠れたパーツの描き足し、Live2D用の完全パーツ展開、ベクター線画は対象外です。開いた線、細い模様、厚塗り、複雑な透過、特殊発光にも限界があります。二値化やJPG圧縮で細線が変わることがあります。
- 網点は共通の角度・位置で描きますが、拡大縮小や他の網との重なりでモアレが生じる場合があります。100%表示と実際の出力先で確認してください。
- 大きい画像や複雑な画像では処理時間・メモリ・トークン消費が増える場合があります。
- **付属作例のPhotoshop／CLIP STUDIO PAINTでの実アプリ検証は未実施です。** 読み戻し検証は、実アプリでの編集品質や未知の入力の品質を保証しません。

## 詳細ドキュメント

- [作例ガイド：入力・出力・再現条件・検証数値](examples/README.md)
- [Agent共通手順](docs/AGENT_WORKFLOW.md) ／ [Codexへの作業指示](AGENTS.md)
- [カラーPSD分解](docs/ARTIST_WORKFLOW.md) ／ [位置合わせ・領域抽出](docs/COMPACT_WORKFLOW.md)
- [別線画の着色](docs/COLORING_WORKFLOW.md) ／ [配色JSON](docs/COLORING_REFERENCE.md) ／ [任意の法線照明](docs/NORMAL_LIGHTING_WORKFLOW.md)
- [漫画トーン化](docs/MANGA_WORKFLOW.md) ／ [別線画へのトーン貼り](docs/TONE_TRANSFER_WORKFLOW.md) ／ [トーンJSON](docs/TONE_REFERENCE.md)
- [事後レビュー](docs/POST_REVIEW_CHECKLIST.md) ／ [仕様](docs/SPEC.md)

CLIを自分で使う場合は、次のヘルプと各手順のコマンド例を参照してください。CLIによる準備の後も、領域の意味分類と目視確認が必要です。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent --help
```

## ライセンス

[Unlicense](LICENSE)です。商用・非商用を問わず、利用・改変・再配布できます。付属の文書・作例も、提供者が権利を持つ範囲で同じ条件とします。依存ライブラリはそれぞれのライセンスに従います。

ただし、トーン版の両機能が使う共通処理の一部（`anime_layer_agent/manga_tone.py` ほか）は [ComfyUI-MangaScreen](https://github.com/ashukae/ComfyUI-MangaScreen)（Copyright (c) 2026 ashukae）のコードを改変して含みます。その部分はMIT Licenseに従います。再配布するときは [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) の著作権表示と許諾表示を一緒に含めてください。ComfyUIで使うQwen-Image 2.1やManga Line Extractionのモデルは同梱していません。各モデルのライセンスに従ってください。

## 注意および免責事項

本リポジトリは個人が公開するオープンソースソフトウェアです。次を了承したうえで利用してください。

- 入力画像および出力PSDの権利は元の権利者に留まり、作者は出力の著作権を主張しません。ただし、出力の利用可能な範囲は、利用者が入力時点で有していた権利を超えません。AI処理を経ても、第三者の著作物の利用が適法になるわけではありません。
- 法令、利用する外部AIサービスの規約、本READMEの注意に反する利用、権利者を偽る行為、サービスの制限回避、過度な自動実行、出力を第三者学習データとして不当に収集・利用する行為は行わないでください。第三者から権利侵害等の主張を受けた場合は、利用者が自己の責任と費用で対応してください。
- 本ソフトウェアは現状有姿で提供されます。適用法令で認められる最大限の範囲で、作者は、特定目的適合性、非侵害、無欠陥、継続提供、外部AIサービスの可用性、出力品質、データの機密性・完全削除を含む一切の保証をしません。入力・出力・公開・販売・外部送信・情報漏えい・学習利用・アカウント停止・第三者からの請求等により生じた損害について、作者は責任を負いません。
- フォーク、再配布、独自ホスティングを行う人は、その環境の運営者です。外部AIとの契約、料金、データ取扱い、入力確認、苦情対応は運営者自身の責任で行ってください。作者はフォーク先での利用に責任を負いません。

この注意および免責事項は予告なく更新されることがあります。最新版は本リポジトリを確認してください。
