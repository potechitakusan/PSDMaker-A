# 第1機能（トーン版）：カラーイラストの漫画トーン化

利用者が「漫画化」「モノクロ原稿化」「トーン化」を明示した場合だけ実行する。カラー版の工程から自動起動しない。参照元のComfyUI-MangaScreen（MIT）のトーン化処理をパーツ単位に拡張し、漫画制作者が編集しやすいPSDを作る。移植部分の著作権表示は[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

役割分担:

- **Codex Astra**: カラー原画を見てパーツの意味・領域・階層を判断し、全確認画像を目視する。仕上げ数値は通常変更しない。
- **画像編集モデル**: 明暗と線を描き直す。Codex組込みimagegenか、ComfyUI上のQwen-Image 2.1を利用者が選ぶ。被写体と背景を分ける透過画像は、別途imagegenで生成する。指定原画像が明らかな白背景の場合は、Codexの判断で透過生成を省略できる。
- **Python**: 縮小・倍率換算・位置合わせ・トーン計算・PSD保存・読み戻しを担当する。

AIの再描画結果を完成原稿として貼らない。値（明暗）と線の手掛かりとして元サイズへ戻し、パーツ別に網点化する。

再描画・線抽出と背景分離は別の選択であり、Qwenを選んでも背景分離にはimagegenを使う場合がある。Codex自体による画像確認も含め、完全ローカルで完結する工程ではない。外部AI利用は [AGENTS.mdの権限方針](../AGENTS.md#権限と画像生成の利用) と利用者の明示制約に従う。

## 既定値で出力する方針

通常はツールの既定値でトーン化し、Codexによる仕上げ調整を挟まない。全体は `manga_tone.DEFAULT_SETTINGS`、パーツはbase:auto／shadow:auto／highlight:white／beta:trueを使う。肌・白布・黒服などの意味を理由に濃度やベタ可否を上書きしない。意味分類、位置合わせ、誤割当の修正、出力後の全画像確認は行う。

網点列数200、白の境界0.80、黒の境界0.30、薄トーン0.18、濃トーン0.42、gradation 1.0、線閾値0.30、白抜き0.4、小領域600、ベタ最小面積40、最小比率0.005が既定。既定形式はdots、方式はnode。CLIのdpiは未指定（None）で、GUI初期表示は600。画像幅に応じてCodexが網点列数を自動変更することもない。

利用者が指定した値・形式は優先する。Codexによる推奨設定の提案・適用は、利用者から推奨を求められた場合だけ行い、既定値との差と依頼を記録する。アジャスト画面は利用者自身が調整するために引き続き使える。既定値の結果が暗い・細部が消えるなどの所見は記録するが、事後レビューを理由に設定を勝手に変えない。新規の既定値制作に `--reuse-last` や他jobの調整案を使わない。

## 最初に確認すること

既に指定がある項目は再質問しない。未指定ならまとめて1回だけ確認し、お任せなら既定値を使って記録する。

| 項目 | 選択肢 | 既定 |
|---|---|---|
| 再描画モデル | `imagegen` / `qwen`（ComfyUI） / `none`（Manga Levelsのみ） | imagegen |
| 線抽出 | `imagegen` / `qwen`（Manga Line Extraction） / `provided`（利用者の線画） / `redraw`（再描画から抽出） | 再描画と同じ（noneの場合はredraw） |
| トーン形式 | `dots`（網点を画素で描く） / `gray`（グレー塗り。CLIP STUDIOのレイヤーのトーン化用） | dots |
| 線数 | `screen_lines`＝画像幅あたりの網点列数。網間隔5px未満は警告として記録し、自動変更しない | 200 |

## 1. 準備

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-manga --reference input/color.png --job work/<name> --redraw-engine imagegen --output output/<name>/output.psd
```

- 元画像は変更しない。`reference.png`（元サイズ）と、長辺`--max-side`（既定1536）以下へ縮小した`generation_input.png`を作る。
- `manga_job.json`の`generation_input.scale_to_original`に縮小倍率を記録する。生成画像の座標は、Pythonがこの倍率と生成画像のサイズから元画像の座標へ換算する。
- 第1機能（カラー版）のcompact jobで同じ画像のPSDを作成済みなら、`--source-job work/<first>`で確認済みのパーツマスクを流用できる。同じ画像であることを画素で照合する。
- 開始時刻はtiming.jsonへ記録される。start_preview.batとmonitorは他機能と同じ。

## 2a. imagegenで生成する（Codexが実行）

`generation_input.png`と`prompts.json`の`redraw`（必要なら`lineart`）を使う。プロンプトの`<image1>`は「入力画像」に置き換える。

- 構図・ポーズ・輪郭を変えない。枠・余白・トリミングを追加しない。
- 出力サイズが入力と違ってもよい。出力は`work/<name>/generated/redraw_imagegen.png`などの別ファイルへ保存する。
- 生成画像を実際に開き、顔・手・服の形が保たれているか確認する。描き足しや欠落は判断ログへ記録する。

## 2b. Qwen-Image 2.1で生成する（ComfyUI）

利用者がComfyUIを起動してから実行する。CLIはComfyUIへのノード・モデル追加や更新を行わない。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent run-comfy-manga --job work/<name> --task redraw
.\.venv\Scripts\python.exe -m anime_layer_agent run-comfy-manga --job work/<name> --task lineart
```

- `redraw`はQwen-Image 2.1 Turboの8ステップ編集。必要なノードは`TextEncodeQwenImage21`と`QwenImage21Cache`など。
- QwenImageによるAI編集（`redraw` / `qwen_lineart`）の既定サイズは1Mpx。縦横比を保って総画素数を調整する。`--megapixels`で0.25〜4Mpxを指定できる（例: `--megapixels 2`）。
- `lineart`はcomfyui_controlnet_auxのManga Line Extractionで、元サイズのreference.pngから線を抽出する。`qwen_lineart`はQwenに線画化を指示する。
- モデルは名前から自動選択する（turbo UNET、qwen3 VL系CLIP、Qwen-Image 2.1 VAE）。`--unet`・`--clip`・`--vae`で明示できる。
- 不足ノードは実行前に一覧で止まる。`--url`の既定は`http://127.0.0.1:8188`。
- 使用モデル、seed、プロンプト、ComfyUIのバージョン、出力ハッシュは`manga_job.json`の`generated`に記録される。

## 2c. 再描画なしで進める

`prepare-manga --redraw-engine none` を指定する。再描画の生成工程を省き、元画像のManga Levelsと線を使う。次節に従って背景透過画像を用意し、`register-manga --job work/<name> --foreground work/<name>/generated/foreground_imagegen.png` で登録する。明らかな白背景として透過生成を省略する場合は`--foreground`を付けない。線画がある場合は準備時に `--line-engine provided`、登録時に `--lineart input/lineart.png` を追加する。この場合も全領域の意味確認と事後レビューは必要。

## 3. 元サイズへの換算と位置合わせ

### 背景を透過した被写体画像を用意する

新規制作ではimagegenにカラー原画（`generation_input.png`）を渡し、背景を削除した透過PNGを別ファイル（例: `generated/foreground_imagegen.png`）へ作る。再描画モデルをQwen/noneにしていても、この背景分離の標準工程はimagegenを使う。利用者のモデル・外部送信に関する明示制約を優先する。`none`は陰影の再描画を省く指定である。

**例外: 利用者が指定した原画像を目視し、明らかな白背景とCodexが判断した場合は、追加確認なしで背景透過生成を省略できる。** 生成後の画像が白くなったことを根拠にせず、指定原画像で判断する。白い衣装・小物・髪のハイライトを背景と誤認しないよう確認する。省略理由と確認結果を`docs/jobs/<job>-decisions.md`へ記録し、新規jobでは`--foreground`を付けずに登録する。通常の線画由来の領域から背景を割り当て、背景/被写体単独を含む事後レビューは続ける。白背景か判断が曖昧な場合は標準の透過生成工程を使う。

- 構図、人物の位置と大きさ、顔、髪、衣装、小物、画面端を保持する。背景除去のみを指示し、中央への移動・トリミング・拡大・描き足しをしない。
- imagegenの`transparent_background:true`を使い、実alphaを持つPNGを保存する。白背景や描かれた市松模様は透過として扱わない。
- 髪先、マフラーなどの房、腕や持ち手の穴、画面端を確認する。元絵・線画・透過版のサイズ差と位置差を確認する。
- 透過版のRGBは完成原稿へ貼らない。Pythonがalphaを原画の格子へ位置合わせし、被写体と背景の領域を分ける。原画のRGB・陰影ガイド・線画の画素はこの背景分離では変更しない。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent register-manga --job work/<name> --redraw work/<name>/generated/redraw_imagegen.png --lineart work/<name>/generated/lineart_imagegen.png --foreground work/<name>/generated/foreground_imagegen.png
```

Qwenで生成した画像は`manga_job.json`の記録から自動で選ばれる。

- **倍率換算**: 生成画像が入力全体をどう収めたかを`stretch`/`contain`（余白付き）/`cover`（切り抜き）から線の一致で選ぶ（`--fit`で固定可）。`original_px_per_generated_px`は生成画像1pxに対応する元画像のpx数を表す。`cover`で見えない範囲は元画像のManga Levelsで補い、`invalid_fraction`へ記録する。
- **局所補正**: 滑らかな光学フロー（最大`--max-shift` px）で再描画のずれを補正する。線の距離（各距離を10pxで打ち切った双方向平均）が改善した場合だけ採用する。元画像は変形しない。
- `manga_alignment.png`を必ず開き、赤線と元画像の重なりを確認する。`needs_attention`がtrue（距離2px超、または範囲外0.2%超）なら、補正前後の数値と残る差を判断ログへ記録する。大きくずれた生成画像は作り直す。
- パーツ候補の領域は線画、再描画の線、source_jobの順で作る。カラー原画の上に`manga_regions_overview.png`（番号入り）と`manga_regions_*.png`（領域ごとの切り抜き）を出力する。座標はすべて元画像のpx。
- `--foreground`のalphaは同じ倍率・採用した局所補正で移し、0.5以上を被写体とする。被写体内は線画由来の領域を保持し、それ以外を背景領域にまとめる。`foreground_alignment.png`に生成透過版と元絵から取り出した被写体/背景、`foreground_mask.png`に実マスクを出す。背景除去自体を位置差と数えないよう、位置合わせの距離は被写体内部で測る。背景の穴、髪縁、房、画面端は距離点数と別に必ず目視する。
- 透過版を使う場合はパス・ハッシュ・サイズ・補正前後の距離を保存し、入力の変更は再登録を要求する。再登録は記録済みの透過画像を使う。CLI引数は任意で、新規作業では上記の明らかな白背景の例外を除いてこの透過生成工程を実施する。

## 4. パーツの意味分類（Astraの判断）

全領域画像とカラー原画を見て`semantic_plan.json`の`parts`を書く。

- 各領域は1回だけ割り当てる。未割当・重複は保存前に拒否される。
- semantic_idとdisplay_nameは一意にする。group_pathで「キャラクター→衣装」のような階層を作る。
- 意味は色だけで決めない。例えば赤いリボンと赤い服は別パーツにできる。
- 確認後に`classification_source`を`astra_reviewed`へ変更する。
- 背景は利用者から分割指示がない限り、空・雲・海などを同じ`background`の1パーツへまとめる。影・ハイライトは別レイヤーにし、新規計画では`separate_white_highlights:true`を付けて紙色の白も編集可能にする。数値・合成結果を変えず、白い部分をハイライトとして保存する。既存job・低水準APIのフラグ既定は変更しない。背景の形状は必ず原画と割り当てた領域で確認する。透過版を使う場合はそのマスクも確認し、AIの欠落をそのまま採用しない。

```json
{"semantic_id":"hair","display_name":"髪","group_path":["キャラクター","頭"],"regions":[2,3],
 "tone":{"base":"auto","shadow":"auto","highlight":"white","beta":true}}
```

`tone`の意味。通常は上の既定指定を維持する。次の上書きは利用者の明示指定、または求められた推奨設定に限る:

| キー | 値 | 効果 |
|---|---|---|
| base | auto / white / light / dark / black / 0〜1 | 既定autoはパーツ内で最も面積の多い明暗段階の濃度をそのまま使う |
| shadow | auto / none / light / dark / 0〜1 | ベースより暗い部分。autoは再描画の明暗とグラデーションを保つ |
| highlight | white / keep / none | ベースより明るい部分。whiteは紙色に抜く、keepは薄いトーンを残す |
| beta | true / false / force | 黒い塊をベタにするか。trueはノイズ除外あり、forceは小さな黒も残す（瞳・ボタンなど）、falseは濃いトーンで表す |

全体設定は`tone_settings`に書く（screen_lines、white_point、black_point、light_tone、dark_tone、gradation、pen_threshold、white_lines、min_region、tone_format、beta_min_area、beta_min_ratio）。参照元ノードと共通の項目は範囲も同じ。

ベタのノイズ除外（`beta: true`のパーツ）:

- 面積が`beta_min_area`（既定40px、幅1024px換算）未満の黒い粒は除く。
- 残った黒がパーツ面積の`beta_min_ratio`（既定0.5%）未満なら、再描画のノイズとみなしてそのパーツのベタを作らない。
- 除いた画素は、同じパーツの近くのトーンで埋める。
- baseをblackにしたパーツと`beta: force`は除外しない。除外した画素数はanalysis.jsonの`beta_specks_removed`と`beta_dropped_as_negligible`に出る。

## 5. アジャスト画面とPNG・PSD出力

通常は意味分類済みの計画を既定値のまま直接構築する。PNGにも同じ計画を明示する:

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent build-manga --job work/<name> --output output/<name>/output.psd
.\.venv\Scripts\python.exe -m anime_layer_agent export-manga-png --job work/<name> --settings work/<name>/semantic_plan.json --output output/<name>/preview.png
```

続いてpost-review、全画像確認、finish-reviewを行う。次の画面操作は任意の利用者調整用であり、Codexが通常工程で設定を最適化する手順ではない。

### 利用者がPython画面で調整する（任意）

AI生成（または再描画なし）・register-manga・全パーツの意味確認を終えたjobで、ルートの `start_manga_adjust.bat` を開く。引数なしでは現在の監視先の漫画jobを使い、画面の「ジョブを開く」から変更できる。明示する場合:

```powershell
.\start_manga_adjust.bat --job work/<name>
```

- **MangaScreen方式**: 参照元Manga Finishのscreen_lines、白黒の境界、薄/濃トーン、グラデーション、線の閾値、白抜きを調整する。既存のパーツ別tone指定を維持する。出力形式はdots（網点）/gray（グレー塗り）を選べる。
- **階調化方式**: トーン線数をLPIで指定する。dpi/LPIを網間隔pxに換算し、45°の共通網で描く。出力はdots（網点）固定。階調バーの三角をドラッグ、または数値で明るさの境界（0より大きく255未満）を調整する。既定の階調は暗い側から「ベタ（100%固定）」「暗い側の濃度」「中間1」「中間2」「ホワイト（0%固定）」（境界50/100/150/200、濃度100/50/30/15/0%）。ベタとホワイト以外の濃度0〜100%を変更できる。境界は追加・削除でき2〜8段階。暗い側から明るい側へ濃度を下げる。境界と等しい明るさは明るい側へ含める。
- 階調化方式は全パーツに共通の階調設定を適用し、MangaScreen方式のパーツ別base/shadow/highlight/beta指定を使用しない。マスク・階層は保持する。線周辺を除いてパーツ内で明暗を平均化する。
- **原稿の解像度（dpi）**: MangaScreen方式ではPSD/PNGに記録する解像度情報だけに使い、網点の計算には影響しない（網の細かさはscreen_linesで決まる）。階調化方式では網間隔＝dpi÷LPIの計算に使うため網点が変わる。
- 共通のノイズ調整はmin_region、beta_min_area、beta_min_ratio。線の閾値・白線はMangaScreen方式の画面で編集した値を両方式で共有する。参照元のコマ枠・紙質（Manga Panel）は未実装。
- 数値変更とモード変更だけではプレビューを更新しない。「反映」で元画像サイズの計算を別スレッドで行う。表示は「全体」「12.5%」「25%」「50%」「75%」「100%」。網点は100%で確認する。プレビュー・PNG・PSDは共通の描画関数を使う。
- 未反映の変更がある間と計算中は「PNG出力」「PSD出力」を無効にする。計算中に値を変えた場合も再反映が必要。入力・計画がプレビュー後に変わった場合は出力を拒否する。
- 反映に成功した値はjobの `manga_adjustments.json` とローカルの `docs/manga_last_settings.json` に保存する。反映だけでは意味計画・PSDを変更しない。両モードの数値を一緒に保存する。
- 「プリセットとして保存」で現在の数値（両方式）を名前を付けて `presets/manga/<名前>.json` へ保存する（保存日時を記録、Gitへは追加しない）。「プリセットを読み込む」で選んだプリセットを読み込み、名前と保存日時を表示する。プリセットはCLIの`--settings`にも渡せる。「ツール初期値にリセット」は表示中の方式を維持したまま、両方式の数値をツールの既定値（dpi 600）へ戻す。
- 「PSD出力」で保存先を選ぶ。適用済み設定をsemantic_plan.tone_settingsに記録し、前の計画をhistoryへ退避して構築する。post-review画像も自動生成する。視覚レビューを完了したことにはしない。
- 「PNG出力」は原寸・不透明RGBの合成画像を保存し、読み戻しを確認する。画面の表示倍率は出力サイズに影響しない。semantic_plan・既存PSDは変更せず、PSDの事後レビューも確定しない。
- dpiは画面では既定600、72〜2400。PSD/PNGへ解像度情報を保存する。旧jobのdpi未指定時のbuild-mangaは従来どおりdpiを追加しない。LPIは5〜200。MangaScreen方式のscreen_linesは40〜600。

### 画面なしで前回値を使う

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent export-manga --job work/<name> --reuse-last --output output/<name>/output.psd
.\.venv\Scripts\python.exe -m anime_layer_agent export-manga-png --job work/<name> --reuse-last --output output/<name>/output.png
```

jobの保存済み調整値を優先し、なければローカルの前回値を使う。どちらもなければ黙って既定値にせずエラーを出す。`--settings <調整JSON>`で設定ファイルも指定できる。GUIを起動せずPSD出力・読み戻し評価・post-review生成まで行う。finish-reviewによる目視所見の確定は別途必要。

PNG出力は画像保存と読み戻し確認までを行う。PSDを出力した場合は全事後レビューを別途行う。

既存の意味計画の設定で直接構築する場合は次を使う。画面で「反映」しただけの調整値は取り込まない:

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent build-manga --job work/<name> --output output/<name>/output.psd
```

PSDの構成（上から順）:

| フォルダ | レイヤー | 内容 |
|---|---|---|
| ホワイト | 白抜き、`<パーツ>_ハイライト` | ベタの中の主な線を白で抜いたもの。黒ベースのパーツ（黒髪など）のハイライトもここに入る |
| 線画 | 線画 | 細い線だけ（黒、透明背景） |
| ベタ・トーン/<階層>/<パーツ> | `_ベタ`, `_ハイライト`, `_影`, `_ベース` | パーツごとの黒い塊（ベタ、線は含めない）と、パーツ全体に一様なベース網、暗部の影網、明部を紙色に抜くハイライト。ベタの下にもベース・影の網を敷くため、ベタを削るとトーンが見える。黒のないパーツ（白だけ）とノイズだけのパーツにはベタを作らない |
| （最下段） | 紙 | 白い紙 |

- 網点は全レイヤー共通の45°・同一線数・同一位置のスクリーン。濃いトーンの網点は薄いトーンの網点を含み、ツールが生成したトーン同士の重なりを揃える。拡大縮小や入力線・生成ガイド由来の網、他の網との干渉まで防ぐものではないため、原寸で確認する。
- 濃度の数値は網点の割合と一致しない。参照元と同じ網点の形状で、18%は実際の黒面積がおよそ12%、42%はおよそ36%になる。
- ベタの下の網: 黒以外のベースのパーツはベースをパーツ全体へ、影をベタ部分にも敷く（影の濃度はshadowの数値、autoは濃いトーン、noneはベースのみ）。黒ベースのパーツ（黒髪など）はベタ部分に濃いトーンの`_ベース`を敷く。階調化方式では100%を除く最も濃い段階を敷く。
- 白いだけのパーツにはトーンレイヤーを作らない。透明のダミーレイヤーは作らない。
- 合成したPython目標（`manga_target.png`）とPSDの読み戻しを、MAE≤0.5、最大差≤3で検証する。参照元と同じパーツなしの1枚仕上げを`plain_finish.png`として比較用に保存する。
- 網点が5px未満になる線数は`warnings`に出る。

## 6. 事後レビュー（必須）

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent post-review --job work/<name>
# post_review/overview.png・screen_crop_1to1.png・manga_alignment.png・manga_parts_overview.pngを開いて記入
.\.venv\Scripts\python.exe -m anime_layer_agent finish-review --job work/<name>
```

overviewには次を並べる: カラー原画、元サイズへ戻した再描画、パーツなしの仕上げ、保存PSD、線画単独、ベタ単独、パーツ別着色ベタ、ホワイト単独、トーン単独、パーツ別着色トーン、影OFF、ハイライト/ホワイトOFF、ベースOFF。

`background`パーツがある場合は実PSDの背景単独・被写体単独も出す（全体共通の線画は含めず、素材レイヤーだけを表示）。背景透過版を使ったjobでは`foreground_alignment.png`と`foreground_mask.png`もレビュー根拠に含める。背景が1パーツであることと、その影・ハイライトが別レイヤーであることを確認する。

10項目（input_alignment, part_masks, tone_values, shadow_highlight, beta_white, linework, screen, hierarchy_budget, readback, delivery）へstatus・所見・根拠画像を記入する。特に確認すること:

- 肌・白布に網が入っていないか。黒髪・黒服がベタになっているか。
- 瞳・ボタンなど小さな黒が、ノイズとして除外されていないか。通常は所見に残し、`beta: force`への変更は明示指定・推奨依頼時だけ行う。
- 隣のパーツの黒がにじんでいないか。
- 網点を1:1で見たときの潰れやモアレ。
- AIの再描画で変わった表情・指・小物。

誤った意味・領域の割当は局所修正し、build-mangaで再構築する。toneやtone_settingsの変更は利用者の明示指定または求められた推奨設定に限る。既定値由来の見た目は所見として残す。再構築したら事後レビューもやり直す。

## 第2機能（トーン版）への引き継ぎ

[公開作例](../examples/README.md#トーン版) にはQwen-Image 2.1の再描画とManga Line Extractionから作った第1機能、別ポーズ線画へ素材濃度を継承した第2機能のPNG・PSD・JSON・線画を収録している。`examples/manga`は調整済みPSD、`examples/manga_transfer`は調整前の参照から作った検証用PSDで、参照設定は一致しない。元の制作jobがなくても公開PSDと対応するJSONから新しいトーン貼りを準備できるが、掲載中の転写結果の再現にはならない。

[imagegenのみの第1機能作例](../examples/README.md#imagegenのみの作例)は、線画・再描画・背景透過版をimagegenで作り、既定値版とCodex調整版を別PSD・別JSONにしている。Qwen作例にはimagegenによる背景透過工程を使っていない。作例ごとの制作条件と、新規制作の標準工程を区別する。

新しいPSDには `tone_reference.json` を自動出力する。旧jobは `export-tone-reference --job work/<name>` で書き出せる。PSDとJSONを一組で保管する。素材別のベース・影・光・ベタ下の濃度とベタ可否を解決し、意味名・階層・網設定を引き継ぐ。元の領域番号や陰影形状は移植しない。書式と検証範囲は [TONE_REFERENCE.md](TONE_REFERENCE.md)。

`start_manga_adjust.bat` の「別線画へトーン」で新線画へ貼り付けられる。詳細は [TONE_TRANSFER_WORKFLOW.md](TONE_TRANSFER_WORKFLOW.md)。両機能で「PNG出力」と `export-manga-png --job ... --reuse-last --output ...png` を使え、原寸のプレビューと同じ画像を保存する。PSDの事後レビューは別途必要。

## 制約

- AIの再描画は形を変えることがある。位置合わせは滑らかなずれだけを補正し、描き足し・欠落は直さない。
- パーツ境界は線画の閉領域に従う。開いた線や細い隙間では領域が混ざる。
- `gray`形式はペイントアプリ側のトーン化を前提とする。網点の線数・角度はアプリ側で設定する。
- Photoshop・CLIP STUDIO PAINTでの実アプリ検証は別途必要。入力画像の権利・外部AI送信の注意はREADMEに従う。
