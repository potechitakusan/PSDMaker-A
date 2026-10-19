# PsdMaker 仕様

Codex Astraが画像の意味・素材・異常を判断し、ローカルPython CLIが全画素処理とPSD生成を行う。モデルは利用者の画面で選択し、別モデルやAPI契約を必須にしない。機能は第1機能（完成イラストのPSD化）と第2機能（別線画への素材情報の引き継ぎ）に分け、それぞれカラー版・トーン版を用意する。第1機能（カラー版）の新規制作は編集優先工程を使う。

## 第1機能（カラー版）：完成イラストからPSD

手順は [ARTIST_WORKFLOW.md](ARTIST_WORKFLOW.md) と [COMPACT_WORKFLOW.md](COMPACT_WORKFLOW.md)。線画がなければ画像生成機能で別ファイルへ作成する。分解前にサイズ・局所ずれを調べ、元絵を変形せず線画側を位置合わせする。補正前後の画像・数値・残る描き直し差を確認する。

prepare-compactで線画ガイドを局所位置合わせし、元絵由来の線を回収する。Pythonの領域一覧をAstraが実際に開き、semantic_plan.jsonに意味・素材別パーツを割り当てる。未確認はheuristic/partial_reviewで、確認後だけastra_reviewedとする。領域は重複割当しない。

configure-editingはartistプロファイルを設定する。描画枚数の範囲と色統一許容値0〜100は、指定済みなら再質問せず、お任せ・未指定なら素材数と色のばらつきから決める。フォルダ数は別集計。上限超過は保存前に拒否し、下限未達は出力して未達と報告する。透明ダミーで枚数を増やさない。

同じpalette_idは共通の単色Base。group_pathの階層内にパーツ別Base/Shadow/Highlight/必要な色補正を置く。色統一による塗り変更はCIE76距離で許容値/5以内。既定の線はモノクロ、影・光は一定グレーと画素別透明度。cool/warmの照明も指定できる。新規detail_mode=relativeはBaseに応答するMultiply/Screen色補正、意図した固有色だけpigmentでNormalへ残せる。

新規configure-editingのbase_mode既定はreview_candidates。review-base-colors（review-editingでも生成）で最多色・L*40〜60%帯の中間色・60〜80%帯の通常明部色の3候補をpalette別に表示。線を除いたパーツごとの候補を等重みで共有化する。Astraが画像/素材の意味を確認しselect-base-colorで候補と理由を記録。全paletteの選択・reviewed・RGB・入力/マスク/共有構成ハッシュが一致するまでbuild-compactを拒否する。再設定では選択を保持。旧jobや明示base_mode=dominantは従来動作。選択後も色許容値・再分解・読み戻し・予算と全事後レビューを維持する。選択の証跡はediting_reportと任意coloring_reference.palettes.*.base_selectionへ保存し、実BaseからRGBを検証する。

line_cleanupは補正ガイドに裏付けされない点・塗り跡を塗り側へ戻し、モノクロ化前のRGB合成を保つ。線変更は塗りの許容値とは別に評価する。背景単独画像を必ず確認する。select-color/assign-selection、review-components/component_assignmentsは目視確認した範囲だけ局所修正し、計画をpartial_reviewへ戻す。色だけで背景要素を前景と決めない。

### 第2機能（カラー版）への引き継ぎ

artistのbuild-compactは `coloring_reference.json` をPSD隣とjobに自動保存する。既存jobはexport-coloring-referenceで追加できる。実PSDから単色Base RGB、素材IDと対応パーツ、PSD内Base階層・bboxを記録し、意味計画からdisplay_name/group_path/任意のcoloring_notesを引き継ぐ。画像サイズ・PSDと計画のSHA-256を含む。元jobの絶対パス・領域番号・マスクは含めない。compact jobから書き出す場合、各パレットに任意の`tones`を付ける。元絵の線を除いた下塗り（underlying、回収線の周辺1pxを除外）をBaseマスク内で測った明度3〜15/40〜60/60〜80/80〜95/95〜99.5%帯（shadow/midtone/light/bright/peak）のRGB・L*・C*。最多色をBaseに選んだ場合は陰寄りになり得るため、選択したBase色とは別に完成時の明暗幅を数値で引き継ぐ。

PSDとJSONだけで第2機能の参照として使える。読込時にPSDハッシュ・サイズ・実Base色とパーツ対応を検証する。曖昧なBase名、非単色Base、共有ID内の色不一致を拒否する。書式と制約は [COLORING_REFERENCE.md](COLORING_REFERENCE.md)。JSONの意味記述と事後レビューの正しさはハッシュでは保証しない。

## 第2機能（カラー版）：指定線画への着色

[COLORING_WORKFLOW.md](COLORING_WORKFLOW.md)に従う。利用者が指定した線画、または明示された開発試験の模擬線画だけを扱う。既存run/buildから自動起動しない。prepare-coloringはlineart/jobと、source-jobまたはsource-referenceのどちらか一方が必須。独立jobを使い、参照PSDと入力線画を上書きしない。

線画は白背景に合成した輝度を閾値1〜254（既定192）で二値化し、白を透明にした線を別保存する。PNG/JPG等を入力できる。隙間補助0〜64pxは塗り領域の境界だけに使い、PSDの線へ描き足さない。微小領域の閾値は1〜1000px（既定12）。

領域のbbox・面積・Python算出seed_xy・画像端接触を保存する。Astraが全領域を目視し、意味パーツとpalette_idを割り当てる。fill-regionは座標塗り、split-color-regionは局所隙間補助で対象外IDを保持する。未知色・領域、重複、範囲外、未割当を拒否する。画像端への接触だけで背景とは決めない。

paint-flatsは参照PSDの実Base RGBを厳密継承する。新しい線画のパーツは改めて分類し、元画像の領域ID・bboxをコピーしない。任意の全体AI照明案はprepare-lightingで位置合わせし、Astraが実画像と数値を確認する。線画は変形せず、ガイドの明度を固定色Multiply/Screenの透明度に使う。照明色はlighting.color_modelで選ぶ。新規prepare-lightingはパレットにtonesがあれば`source_tones`とし、素材ごとにShadow色=shadow帯/Base、Highlight色=(peak帯−Base)/(1−Base)を使う。不透明度100%で元絵の暗部・最明部に届き、灰色照明による白布の頭打ちや明部・影の脱色を防ぐ。tonesのない素材と旧jobは`neutral`（一定グレー、temperature指定可）。ガイドのRGB色相は使わない。build-coloredはanalysisのtone_reachに素材別の到達可能な明度と元絵bright帯を記録し、届かない素材をtone_reach_warningsに出す。lighting.tone_mappingは`absolute`（既定、ガイドの明度をそのまま使う）か`source_levels`。後者はパレットごとにガイド明度の中央値を元絵midtone、87.5%点をbrightへ直線で合わせ、倍率を0.5〜2に制限する。照明案の露出や旧方式で頭打ちになった明るさを補正し、ガイドのノイズは増幅しない。

prepare-coloringは実参照PSDのsource_appearance.pngと素材別の明度・色味を保存する。照明案には下塗りと参照完成色の両方を渡す。post-review（単独ではreview-coloring-tones）は両PSDの素材内側の明度40〜60/60〜80/80〜95%帯のRGB・L*・C*を比較し、差3以上を注意表示する。統計的比較は別ポーズの一致点数や自動合否ではない。finish-reviewはmotifs_lightingに完成色比較・色見本画像の根拠を必須とする。旧着色jobの再確定はpost-reviewを再実行する。Base一致だけで印象の一致とは扱わない。

build-coloredは各パーツ内のBase/Shadow/Highlightと最上段の透明線画を保存する。描画上限は既定3N+2（Nは前景パーツ数）、max_pixel_layersで指定可能。線画、参照PSD・計画または参照JSON、処理配列、照明案の変更をハッシュで検出する。

## 第1機能（トーン版）：カラーイラストの漫画トーン化

[MANGA_WORKFLOW.md](MANGA_WORKFLOW.md)に従う。利用者が明示した場合だけ実行し、独立jobを使う（compact/coloring jobと混在させない）。ComfyUI-MangaScreen（MIT、[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)）のトーン処理を移植する。

prepare-mangaは元画像を変更せず、reference.pngと長辺max_side（256〜4096、既定1536）以下のgeneration_input.pngを保存し、縮小倍率を記録する。再描画はimagegen（Codex）、qwen（ComfyUIのQwen-Image 2.1 Turbo、8ステップ）、none（Manga Levels）から選ぶ。線抽出はimagegen、qwen（Manga Line Extraction）、provided、redrawから選ぶ。run-comfy-mangaは標準ライブラリのHTTPで、起動済みComfyUIへアップロード・実行・取得を行う。QwenImageでAI編集するredraw/qwen_lineartの既定サイズは1Mpxで、CLI・実行関数・ワークフロー生成関数に共通。--megapixelsで0.25〜4Mpxを明示できる。Manga Line Extractionの解像度は原画幅基準。不足ノードは実行前に拒否する。モデル、seed、プロンプト、版、出力ハッシュを記録する。

register-mangaは生成画像を元画像の格子へ戻す。stretch/contain/coverのうち線距離（各距離を10pxで打ち切った双方向平均）が最小のものを選ぶ。次に平滑化した光学フローで最大max_shift px（0〜48、既定12）補正し、改善した場合だけ採用する。倍率、枠、補正前後の距離、2px以内の線の割合、範囲外の割合を記録する。距離2px超または範囲外0.2%超はneeds_attentionとする。範囲外はManga Levelsで補う。パーツ候補はgeometry_labelsで、線画、再描画の線、source_job（第1機能（カラー版）のcompact jobで確認済みの同一画像マスク）の順に作る。再登録で領域が変わったら計画を履歴へ退避し、割当を空にしてpartial_reviewへ戻す。

build-mangaはastra_reviewed・全領域を一度だけ割り当てた計画・一意の名前・入力ハッシュ一致を必須とする。

第1機能（トーン版）の新規制作は、通常imagegenで背景を透過した被写体PNGを別生成し、`register-manga --foreground`へ渡す。例外として、利用者が指定した原画像が明らかな白背景とCodexが目視判断した場合は、背景透過生成を省略できる。省略理由と白い被写体を背景と誤認していないことを判断ログへ記録し、新規jobでは`--foreground`を省いて通常の領域から背景を割り当てる。背景/被写体の分離確認と実PSDの事後レビューは継続する。

`--foreground`を使う場合は実alphaと被写体がある入力だけを受理し、原画格子へ倍率換算・局所補正したalpha≥0.5で領域を分割する。被写体内は元の領域IDを保持し、外側は新たな背景IDへまとめる。生成RGBは原画・陰影・線に混ぜない。被写体内部の線距離で補正採否を判定し、透過PNGのハッシュ変更は再登録を要求する。`foreground_alignment.png`・`foreground_mask.png`と実PSDの背景/被写体単独を事後レビューに含める。数値だけではAIの輪郭変更や穴の誤処理を合格にしない。CLI指定は任意で、旧jobと白背景の例外に対応する。画素値だけで白背景を自動判定する実装は追加しない。

通常制作はDEFAULT_SETTINGSとパーツ既定（base:auto、shadow:auto、highlight:white、beta:true）を維持し、Codexによる数値・トーン方針の仕上げ調整を省く。画像幅や素材意味を理由に網・濃度・閾値・ベタ可否を変更しない。意味分類・位置合わせ・誤割当修正・全画像レビューは継続し、既定値の見た目の制約を記録する。第2機能は参照由来の設定・素材濃度を維持する。利用者の明示指定を優先し、Codexの推奨設定の提案・適用は推奨を求められた場合だけ行う。新規の既定値制作には前回値や他jobの調整案を流用しない。GUIの利用者調整機能は維持する。

- **パーツ単位のトーン計算**: パーツの内部（境界帯max(2,3k)pxを除いた核）だけで、マスクした正規化畳み込み、バイラテラル平坦化、4段階化、小領域統合、グラデーションを計算する。境界帯には核の値を延長する。
- **ベースと影・光**: 最多の段階をベースとし、暗い段階を影、明るい段階をハイライトとする。tone.base/shadow/highlight/betaで上書きできる。
- **ベタと線**: ベタは線幅より広い黒い塊（opening）だけとし、細い線は線画に分ける。ベタはパーツごとのレイヤー（ベタ・トーン/階層/パーツ/<パーツ>_ベタ）にし、同じパーツのトーンの上に置く。ベタの下にもトーンを敷き、利用者がベタを削るとトーンが見えるようにする。黒以外のベースのパーツはベースをパーツ全体に、影をベタ部分にも敷く（影の濃度はshadowの数値、autoはdark_tone、noneはベースのみ）。黒ベースのパーツはベタ部分にdark_toneのベースを敷く。階調化方式では100%を除く最も濃い段階を敷く。tone.beta=trueの場合、beta_min_area·k²未満の粒を除く。残りがパーツ面積のbeta_min_ratio未満ならノイズとしてベタを作らず、除いた画素は同じパーツの最寄りのトーンで埋める。黒ベースとforceは除外しない。黒のないパーツにはベタレイヤーを作らない。
  保存時は線画と重なるベタ画素を除外し、同じ黒線が線画・ベタへ二重に保存されるのを防ぐ。合成画像は線画が同じ黒で補うため維持する。ホワイト用の内側判定は除外前のベタを使う。
- **網点**: 全レイヤー共通の45°・page-alignedのAM網点。参照元の3倍サンプリング多数決と画素単位で一致させ、濃い網点が薄い網点を含むようにする。tone_format=grayはグレー塗りで出力する。

第1機能（トーン版）の新規制作では、利用者から分割指示がない限り背景は`background`の1パーツにまとめ、影・ハイライトを独立レイヤーとして扱う。公開手順ではそのpartに`separate_white_highlights:true`を付ける。白ベースの紙色部分を不透明白のハイライトレイヤーとして同じパーツ内へ保存し、合成画素・トーン設定・引継ぎ濃度は変更しない。第2機能では明示的なhighlight領域の白だけを対象にする。既存jobと低水準APIのフラグ既定は無効のまま、空のレイヤーは作らない。

PSDは上から次の順にする。

1. ホワイト（白抜き・黒ベースのパーツのハイライト）
2. 線画
3. ベタ・トーン/階層/パーツ（ベタ・ハイライト・影・ベース）
4. 紙

読み戻しはPython目標に対しMAE≤0.5、最大差≤3。網点の間隔が5px未満の場合は警告する。post-review/finish-reviewはmanga_job.jsonがあればトーン版の第1・第2機能共通の10項目へ切り替わる。カラー版の処理・既定値・評価は変更しない。

## 第2機能（トーン版）：別線画へのトーン貼り

本機能は出力品質を検証中。ルートREADMEには機能説明と操作手順を残し、検証中であることを明示する。

[TONE_TRANSFER_WORKFLOW.md](TONE_TRANSFER_WORKFLOW.md)に従う。機能番号は「第1機能（カラー版／トーン版）」「第2機能（カラー版／トーン版）」に整理する。第1機能（トーン版）は原画のトーンPSD化、第2機能（トーン版）は参照の素材情報を新しい線画へ引き継ぐ工程。

build-manga/export-mangaはPSD隣にtone_reference.json（tone_reference_v1）を自動保存する。PSDファイル名・ハッシュ・サイズ、網設定と、素材ID・表示名・階層・任意tone_notes・数値のbase/shadow/highlight/beta_fill・betaの可否を保存する。autoは描画時の実Baseと網点化前の影/光中央値へ解決し、対象がない場合は方針から補完。旧jobはexport-tone-referenceで追加でき、元PSD・計画を変更しない。持ち運びはPSDとJSONを一組にし、読込時にハッシュ・サイズ・素材値を検証する。領域・bbox・マスク・陰影形状は含まない。書式・濃度の制約・検証範囲は [TONE_REFERENCE.md](TONE_REFERENCE.md)。

prepare-tone-transferは指定lineart、空の独立jobとsource-job/source-referenceのいずれかを必須にする。線を二値化（閾値1〜254、既定192）し、隙間補助0〜64px（既定3）・最小領域1〜1000px（既定12）で閉領域を抽出。補助線は完成線画へ加えない。元ポーズへ変形せず、各新領域に素材とbase/shadow/highlight/beta役割を割り当てる。背景も明示指定。重複・未割当・未知素材・不正濃度は拒否する。tone_transfer_job.jsonと共通描画用manga_job.jsonを併存させ、再構築はbuild-mangaを使う。prepare-manga/register-mangaは拒否する。

パーツはsource_material、transfer_tone、role_regionsを持ち、regionsは役割別割当の重複のない和。濃度は0〜1の有限数でhighlight≤base≤shadow、beta_fillは1未満、betaは真偽値を必須にする。beta_fill=0ならベタ下は紙色になる。線画は二値化後の入力画素で固定。素材濃度はどちらの網方式でも明示値を使用し、階調カーブや元画像から再推定しない。画像幅基準ではscreen_lines、dpi・LPI基準ではdpi/lpiで網間隔を決める。設定は元の数値・単位のまま引き継ぐため、前者の網間隔は新線画の幅に依存する。PSDは既存の紙・素材別Base/影/光/ベタ・固定線画・ホワイト構成を共用し、ベタの下にもトーンを敷く。

アジャスト画面の「別線画へトーン」で入力と参照を選択できる。領域表示で素材と役割を選んでクリックすると明示的に再割当し、1手戻しも可能。素材別の濃度・ベタ可否、網の形式・間隔・dpiを変更できる。確認チェックは利用者の場合user_reviewed、Astraの確認はastra_reviewedとして区別する。反映でtone_transfer_adjustments.jsonへ基になる計画のハッシュ付き適用案を保存し、PSD出力時に旧計画を履歴へ退避してsemantic_planへ反映。export-mangaは有効な適用案を取り込むが、build-mangaはsemantic_planを直接使う。網プリセットは素材割当・素材濃度を含まない。未反映・計算中・入力変更時はPNG/PSD出力を拒否する。

export-manga-pngと画面のPNG出力は共通render_mangaの原寸RGBをPNGに保存・読み戻し確認し、dpi指定時は解像度情報を付ける。プレビューの表示倍率では出力サイズを変えない。PNG出力はsemantic_plan・既存PSDを変更せず、PSDのレビュー状態を確定しない。PSD出力は読み戻しMAE≤0.5/最大差≤3とpost-reviewを使い、全画像を目視したfinish-reviewを必須にする。参照PSD・参照計画/JSON・入力線画・処理配列・参照スナップショットの変更を検出する。元画像と参照PSDを保護するため、参照PSDと同じフォルダへの出力も拒否する。

この機能の影・光は新線画で閉じられた領域への指定であり、自動照明、元の陰影形状の移植、開領域へ描くブラシは含まない。別ポーズの品質は意味と見た目で確認し、元絵との画素一致を合否にしない。

黒ベース素材のホワイト領域の下にもベタと網を敷き、ホワイト非表示で黒ベースへ戻せるようにする。通常の合成画像は維持する。第1機能の既存描画方式は変更しない。参照PSDの完成プレビューsource_appearance.pngを準備し、アジャスト画面と事後確認で参照できる。

## トーン版のアジャスト画面

### 第1機能（トーン版）の生成後調整

`start_manga_adjust.bat` / `anime_layer_agent.manga_editor`は位置合わせ・意味分類済み漫画jobの仕上げを調整する独立Tk画面。第2機能（トーン版）も同じ画面を使い、上記の領域割当・素材濃度・網設定に切り替わる。以下の明暗変換は第1機能（トーン版）に適用する。「MangaScreen方式」と「階調化方式」を切替できる。前者は既存tone_settingsとパーツ別toneを使い、出力形式dots/grayを選べる。dpiはPSD/PNGの解像度情報だけに使い、網間隔は画像幅基準のscreen_linesで決まる。後者はdpi（72〜2400）/lpi（5〜200）で網間隔を決定し、明るさ閾値（0より大きく255未満）・各区間の濃度0〜1（2〜8段階）を全パーツへ適用する。出力はdots固定。最も暗い区間はベタ（100%固定）、最も明るい区間はホワイト（0%固定）。既定は境界50/100/150/200、濃度100/50/30/15/0%（ベタ・暗い側・中間1・中間2・ホワイト）。閾値は昇順、濃度は暗い側から降順（同値可）。等号は明るい側へ含める。旧形式の`posterize_enabled`は読み込み時に無視する。マスク・階層は共有し、後者ではパーツ別toneの上書きを使わない。

### 両機能の設定保存と出力

「反映」でのみ原寸を非同期計算しプレビューと適用済み設定を保存する。表示だけを全体縮小/12.5%/25%/50%/75%/100%に切替可能。「プリセットとして保存」「プリセットを読み込む」は名前・保存日時付きの設定（schema manga_preset_v1）を非公開の`presets/manga/`へ保存・読込する。「ツール初期値にリセット」は表示中の方式を維持して両方式の数値をツール既定値（dpi 600）へ戻す。両モードの値は保持される。未反映時と処理中はPNG/PSD出力を無効にする。プレビュー・PNG・PSDはrender_mangaを共有し、入力/計画が変わったプレビューの出力は拒否。反映はsemantic_planや既存PSDを変更しない。保存先はjob/manga_adjustments.jsonと非公開docs/manga_last_settings.json。

PSD出力は適用値をsemantic_planへ記録してbuild-manga→読み戻し→post-review生成を行う。旧計画はhistoryへ保存し、目視確定は従来どおりfinish-review。export-manga --reuse-lastは画面なしで同じ工程を行い、jobの保存値、ローカル前回値の順で使用する。保存値がなければエラー。--settingsで調整JSON指定も可能。dpi指定時はPSDの解像度リソースへ書き、旧jobのdpi未指定は従来動作を維持する。コマ枠・紙質の調整は利用者指定により将来予定。

## 第2機能（カラー版）の任意の法線照明試作

第2機能（カラー版）の下塗り済みjobへ `prepare-normal-lighting --job ... --normals ... --light X Y Z` を追加。既存prepare-lighting方式は維持する。入力は固定線画と同じHxWx3の符号付きNPY法線（X右、Y上、Z手前）。可視化RGBは入力にしない。サイズ・有限値・非零ベクトルを検証し正規化する。画像・法線の自動位置合わせは行わず、推定時に同じ画素格子へ戻し、意味・形状のずれは目視確認する。照明選択・法線ハッシュ・光方向はsemantic_plan.lightingへ記録し、reviewedとnotesを必須にする。

build-coloredのnormals分岐はN·Lを0〜1へ制限し、0.65を中立点とする芸術的ランプからMultiply影・Screen光の透明度を作る。既存素材のsource_tones/neutral色を再利用し、各パーツマスクで切り抜く。線の近傍2pxは同素材の内側法線を参照する。Baseと最上段の線は固定。光は拡散的な明部の補助であり、物理的な鏡面反射、落ち影、AOの推定ではない。build_elapsed_secondsに構築・読み戻しまでの実時間を記録する。

法線の任意normal_curveは影/光のgain（0〜4）とgamma（.25〜4）で従来の明暗を調整する。未指定は従来通り。source_tones＋tone_mapping=source_rampは素材別N·Lの9/50/70/87.5/97.25%点を元絵のshadow/midtone/light/bright/peak RGBへ対応させる。節点間隔最低.08、全体幅.08未満/32画素未満/全5色帯なしは従来照明へfallbackしanalysis.normal_mappingへ記録。Baseとの差を画素別RGB/透明度のMultiply/Screen二層へ分解し、混合チャンネルの色変化も再現する。Base・線・マスク・予算は固定、AI画像のRGBは用いない。guide向けsource_levelsは法線で拒否する。元絵と色帯を合わせる処理であり、落ち影・艶・正しい法線・未知作例の品質は保証しない。色帯の適合と実PSD読み戻し・目視所見を分けて報告する。

法線光源は安定した番号1〜50から選ぶ。四隅各9点=36、上下左右の軸各3点=12、中央手前1、法線なし1。既定1番は左上手前(-.65,.65,.55)、各斜め群はXY±.2の周囲8点を加える。軸はXY距離.85/.55/.25、Z=.55。49番は(0,0,1)、50番はBaseと固定線のみ。prepare-normal-lightingの--preset/--lightは排他、方向省略は1番。select-normal-lightは方向/enabledを記録し照明再確認を必須にする。プレビュー画面に50選択の照明比較を追加しPSDと同じ計算式で非同期描画、PSD/計画は変更しない。保存への反映はCLI→再構築→全事後レビュー。非法線jobでは選択を無効にする。

公式Marigold推定はscripts/infer_marigold_normals.pyを独立venvで明示実行する。本体requirementsへTorchを追加せず、既存ComfyUIを変更しない。詳細と制約は[NORMAL_LIGHTING_WORKFLOW.md](NORMAL_LIGHTING_WORKFLOW.md)。試作の実画像は公開作例と同じポーズの独立開発jobとして扱う。

## 評価と事後レビュー

第1機能（カラー版）の原画基準はRGB MAE≤4、SSIM≥0.95、平均CIEDE2000≤4、edge mismatch≤0.03。意図した編集目標からの実PSD読み戻しは別にMAE≤0.5、最大チャンネル差≤3（0〜255）で検証する。第2機能（カラー版）はPythonの着色目標からの読み戻しを同じMAE/最大差で検証し、別ポーズの原画やAI画像との一致を品質点数にしない。

artistとcoloringは数値合格でもpost_review_requiredで止まる。post-reviewは実PSDの全パレットの強い色替え、素材仮色、線/背景単独、暗背景、影・光OFFを生成する。Astraが全比較画像を開き、10項目の所見・status・画像根拠を記入しfinish-reviewで確定する。未記入・古いPSD/計画/根拠・数値評価の不一致は拒否。failはneeds_repair、limitationはcomplete_with_limitations。再構築したら確認もやり直す。

トーン版の両機能はPythonのトーン描画目標からの読み戻しをMAE≤0.5・最大差≤3で検証し、元のカラー画像との一致を合否にしない。数値合格後もトーン版用の全10項目を目視確認し、finish-reviewで確定する。PNG単独出力の読み戻しは画像保存の検証であり、PSDレビューの代用にはしない。

数値・ハッシュ検証は視覚判断やPhotoshop/CLIP STUDIOの実アプリ検証の代用ではない。詳細は [POST_REVIEW_CHECKLIST.md](POST_REVIEW_CHECKLIST.md)。

## 保存・再開・プレビュー

トークン節約はAGENTS.mdの「トークン節約と品質維持」に従い、速度より重複処理・不要な読込と出力の削減を優先する。通常は単独実行、委任は利用者の明示依頼時のみ。品質基準・全画像確認・再構築後の事後レビューは維持する。採否とAPI設定の適用範囲は[TOKEN_POLICY.md](TOKEN_POLICY.md)に記録する。

8bit相当RGB/RGBAイラストと白背景または透明線画を対象とする。完成画像の複雑な半透明は対象外。NumPy/Pillow等が画素・マスク・ブレンドを処理し、psd-toolsでPSDを一時保存・再読込検証してからos.replaceする。入力を全画面レイヤーにして最上段へ重ね、分解誤差を隠す処理はしない。

work/jobに正規化入力、ハッシュ、配列、画像一覧、意味計画、レイヤーPNG、評価を保存する。異なる入力で既存jobを上書きしない。compact_job.jsonはbuild-compact、coloring_job.jsonはbuild-colored、manga_job.jsonはbuild-mangaで再構築する。tone_transfer_job.jsonのあるjobはmanga_job.jsonも持つため、第2機能（トーン版）の手順を優先する。保存済みの画面調整案を適用して出力する場合はexport-mangaを使う。旧schema 1用のanalyze/build/run/repair/apply-repairは互換性と基礎回帰試験用に維持するが、新規工程へ流用しない。

start_preview.batは画像解析前に存在を確認し、monitorで監視先を登録する。利用者が開けば別プロセスのTk画面を表示できる状態にする。監視はmtime_ns/size/file IDと安定待ちを使う。生成時ハッシュと一致するPSDは埋込み合成画像、変更されたPSDは再合成を表示。書込み途中や一時ロックでは最後の正常表示を保持し再試行する。

CLIはstatus.jsonとdocs/jobs/*.mdへ状態を保存する。開始・フェーズ・完了時刻と経過秒数・分秒を記録し、受信時刻がなければ最初の作業時刻を起点と明記する。判断はdocs/jobs/*-decisions.md、進捗はdocs/PROGRESS.md、次の操作はdocs/RESUME.mdへ記録する。

input/work/output/backup/.tmp、docsのジョブ・ローカル状態（manga_last_settings.jsonを含む）、presets/mangaは公開対象外。examplesは許可した作例のみ公開し、JPG/PNG/PSD/JSONのメタデータと意味記述も点検する。backupは実行・テストの依存にしない。

## 公開文書と作例

READMEは概要・用途別の入力と出力・代表作例・セットアップ・依頼例・制約・詳細資料・ライセンスの順で案内する。作例ごとの設定値・寸法・レイヤー数・検証数値と再現条件は [作例ガイド](../examples/README.md) にまとめる。公開例の制約や実アプリ未検証の記載は維持する。

カラー版の参照はexamples/outputのPSDと隣接JSON、対応入力はexamples/reference.jpg。別ポーズの主着色例はcoloring（imagegen/source_levels）、比較例はcoloring_normals（左上手前/source_ramp）。Base選択の任意証跡base_selectionは参照job経由でも参照JSON経由でも継承する。別ポーズ線画は作例用に生成したもので、通常の第2機能には線画の自動生成を含めない。新線画で領域を確認し、元のマスクや座標を移植しない。通常のREADMEと手順はこれらの作例へ案内し、旧作例へのリンクを置かない。

トーン版はexamples/manga（Qwenによる第1機能）とexamples/manga_transfer（第2機能）に、原寸preview.png・線画単独lineart.png・output.psd・tone_reference.jsonを格納する。第1機能の入力はexamples/manga/reference.jpg。第1機能は調整済みPSD、第2機能は調整前の参照から作った検証用PSDで、参照設定は一致しない。現在の第1機能のPSDとJSONは新しいトーン貼りに使えるが、掲載中の転写結果の再現を保証しない。両例とも背景は1パーツで、影と白ハイライトを独立させている。

examples/manga_imagegen_defaultとexamples/manga_imagegen_adjustedは、同じ原画の縮小画像とimagegenによる線画・再描画・背景透過版から作った比較例。既定値版とCodex調整版を別PSD・別JSONで保存する。共通の生成素材は既定値版フォルダへ収録する。Qwen作例はimagegenによる背景透過工程を使っていない作例であり、新規制作の標準工程とは区別する。作例の調整値を通常制作の既定値へ流用しない。

ルートREADMEではQwen作例の入力カラー画像とトーン化結果を並べ、その下にimagegenの既定値版・調整版を並べる。第2機能（トーン版）は検証中として機能と手順を案内し、作例画像・成果物への直接リンクは作例ガイドに置く。作例ガイドのファイル一覧は各成果物へリンクし、画像比較はルートREADMEへ案内する。

配布には実行コード・requirements・起動BATに加え、AGENTS.mdとREADMEから参照する公開作業手順・参照JSON仕様を含める。トーン版のMANGA_WORKFLOW.md・TONE_TRANSFER_WORKFLOW.md・TONE_REFERENCE.mdも必要。ローカル状態や生成キャッシュは含めない。配布の動作検証では、コミット済みファイルだけを分離した環境でimport・CLI・テスト・文書リンクを確認する。公開作例はPSD・画像・JSONの対応とメタデータを確認し、画像・PSD・JSONの変更時は読み戻しと全事後レビューを行う。文書のみの変更では、実装との整合性・文書リンク・コマンド例と成果物の不変を確認する。

## 対象外

隠れたパーツの描き足し、完全な隙間復元、Live2D用パーツ展開、ベクター線、特殊発光・厚塗りの完全分離、実ペイントアプリでの互換性保証は含まない。同じjobに複数の書込CLIを同時実行しない。プレビューは読取り専用なので併用できる。
