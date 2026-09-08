# PsdMaker 仕様

Codex Astraが画像の意味・素材・異常を判断し、ローカルPython CLIが全画素処理とPSD生成を行う。モデルは利用者の画面で選択し、別モデルやAPI契約を必須にしない。新規制作は編集優先工程を使う。

## 第1機能：完成イラストからPSD

手順は [ARTIST_WORKFLOW.md](ARTIST_WORKFLOW.md) と [COMPACT_WORKFLOW.md](COMPACT_WORKFLOW.md)。線画がなければ画像生成機能で別ファイルへ作成する。分解前にサイズ・局所ずれを調べ、元絵を変形せず線画側を位置合わせする。補正前後の画像・数値・残る描き直し差を確認する。

prepare-compactで線画ガイドを局所位置合わせし、元絵由来の線を回収する。Pythonの領域一覧をAstraが実際に開き、semantic_plan.jsonに意味・素材別パーツを割り当てる。未確認はheuristic/partial_reviewで、確認後だけastra_reviewedとする。領域は重複割当しない。

configure-editingはartistプロファイルを設定する。描画枚数の範囲と色統一許容値0〜100は、指定済みなら再質問せず、お任せ・未指定なら素材数と色のばらつきから決める。フォルダ数は別集計。上限超過は保存前に拒否し、下限未達は出力して未達と報告する。透明ダミーで枚数を増やさない。

同じpalette_idは共通の単色Base。group_pathの階層内にパーツ別Base/Shadow/Highlight/必要な色補正を置く。色統一による塗り変更はCIE76距離で許容値/5以内。既定の線はモノクロ、影・光は一定グレーと画素別透明度。cool/warmの照明も指定できる。新規detail_mode=relativeはBaseに応答するMultiply/Screen色補正、意図した固有色だけpigmentでNormalへ残せる。

line_cleanupは補正ガイドに裏付けされない点・塗り跡を塗り側へ戻し、モノクロ化前のRGB合成を保つ。線変更は塗りの許容値とは別に評価する。背景単独画像を必ず確認する。select-color/assign-selection、review-components/component_assignmentsは目視確認した範囲だけ局所修正し、計画をpartial_reviewへ戻す。色だけで背景要素を前景と決めない。

### 第2機能への引き継ぎ

artistのbuild-compactは `coloring_reference.json` をPSD隣とjobに自動保存する。既存jobはexport-coloring-referenceで追加できる。実PSDから単色Base RGB、素材IDと対応パーツ、PSD内Base階層・bboxを記録し、意味計画からdisplay_name/group_path/任意のcoloring_notesを引き継ぐ。画像サイズ・PSDと計画のSHA-256を含む。元jobの絶対パス・領域番号・マスクは含めない。

PSDとJSONだけで第2機能の参照として使える。読込時にPSDハッシュ・サイズ・実Base色とパーツ対応を検証する。曖昧なBase名、非単色Base、共有ID内の色不一致を拒否する。書式と制約は [COLORING_REFERENCE.md](COLORING_REFERENCE.md)。JSONの意味記述と事後レビューの正しさはハッシュでは保証しない。

## 第2機能：指定線画への着色

[COLORING_WORKFLOW.md](COLORING_WORKFLOW.md)に従う。利用者が指定した線画、または明示された開発試験の模擬線画だけを扱う。既存run/buildから自動起動しない。prepare-coloringはlineart/jobと、source-jobまたはsource-referenceのどちらか一方が必須。独立jobを使い、参照PSDと入力線画を上書きしない。

線画は白背景に合成した輝度を閾値1〜254（既定192）で二値化し、白を透明にした線を別保存する。PNG/JPG等を入力できる。隙間補助0〜64pxは塗り領域の境界だけに使い、PSDの線へ描き足さない。微小領域の閾値は1〜1000px（既定12）。

領域のbbox・面積・Python算出seed_xy・画像端接触を保存する。Astraが全領域を目視し、意味パーツとpalette_idを割り当てる。fill-regionは座標塗り、split-color-regionは局所隙間補助で対象外IDを保持する。未知色・領域、重複、範囲外、未割当を拒否する。画像端への接触だけで背景とは決めない。

paint-flatsは参照PSDの実Base RGBを厳密継承する。新しい線画のパーツは改めて分類し、元画像の領域ID・bboxをコピーしない。任意の全体AI照明案はprepare-lightingで位置合わせし、Astraが実画像と数値を確認する。線画は変形せず、ガイドの明度を固定色Multiply/Screenの透明度に使う。

prepare-coloringは実参照PSDのsource_appearance.pngと素材別の明度・色味を保存する。照明案には下塗りと参照完成色の両方を渡す。post-review（単独ではreview-coloring-tones）は両PSDの素材内側の明度40〜60/60〜80/80〜95%帯のRGB・L*・C*を比較し、差3以上を注意表示する。統計的比較は別ポーズの一致点数や自動合否ではない。finish-reviewはmotifs_lightingに完成色比較・色見本画像の根拠を必須とする。旧着色jobの再確定はpost-reviewを再実行する。Base一致だけで印象の一致とは扱わない。

build-coloredは各パーツ内のBase/Shadow/Highlightと最上段の透明線画を保存する。描画上限は既定3N+2（Nは前景パーツ数）、max_pixel_layersで指定可能。線画、参照PSD・計画または参照JSON、処理配列、照明案の変更をハッシュで検出する。

## 評価と事後レビュー

第1機能の原画基準はRGB MAE≤4、SSIM≥0.95、平均CIEDE2000≤4、edge mismatch≤0.03。意図した編集目標からの実PSD読み戻しは別にMAE≤0.5、最大チャンネル差≤3（0〜255）で検証する。第2機能はPythonの着色目標からの読み戻しを同じMAE/最大差で検証し、別ポーズの原画やAI画像との一致を品質点数にしない。

artistとcoloringは数値合格でもpost_review_requiredで止まる。post-reviewは実PSDの全パレットの強い色替え、素材仮色、線/背景単独、暗背景、影・光OFFを生成する。Astraが全比較画像を開き、10項目の所見・status・画像根拠を記入しfinish-reviewで確定する。未記入・古いPSD/計画/根拠・数値評価の不一致は拒否。failはneeds_repair、limitationはcomplete_with_limitations。再構築したら確認もやり直す。

数値・ハッシュ検証は視覚判断やPhotoshop/CLIP STUDIOの実アプリ検証の代用ではない。詳細は [POST_REVIEW_CHECKLIST.md](POST_REVIEW_CHECKLIST.md)。

## 保存・再開・プレビュー

トークン節約はAGENTS.mdの「トークン節約と品質維持」に従い、速度より重複処理・不要な読込と出力の削減を優先する。通常は単独実行、委任は利用者の明示依頼時のみ。品質基準・全画像確認・再構築後の事後レビューは維持する。採否とAPI設定の適用範囲は[TOKEN_POLICY.md](TOKEN_POLICY.md)に記録する。

8bit相当RGB/RGBAイラストと白背景または透明線画を対象とする。完成画像の複雑な半透明は対象外。NumPy/Pillow等が画素・マスク・ブレンドを処理し、psd-toolsでPSDを一時保存・再読込検証してからos.replaceする。入力を全画面レイヤーにして最上段へ重ね、分解誤差を隠す処理はしない。

work/jobに正規化入力、ハッシュ、配列、画像一覧、意味計画、レイヤーPNG、評価を保存する。異なる入力で既存jobを上書きしない。compact_job.jsonはbuild-compact、coloring_job.jsonはbuild-coloredで再構築する。旧schema 1用のanalyze/build/run/repair/apply-repairは互換性と基礎回帰試験用に維持するが、新規工程へ流用しない。

start_preview.batは画像解析前に存在を確認し、monitorで監視先を登録する。利用者が開けば別プロセスのTk画面を表示できる状態にする。監視はmtime_ns/size/file IDと安定待ちを使う。生成時ハッシュと一致するPSDは埋込み合成画像、変更されたPSDは再合成を表示。書込み途中や一時ロックでは最後の正常表示を保持し再試行する。

CLIはstatus.jsonとdocs/jobs/*.mdへ状態を保存する。開始・フェーズ・完了時刻と経過秒数・分秒を記録し、受信時刻がなければ最初の作業時刻を起点と明記する。判断はdocs/jobs/*-decisions.md、進捗はdocs/PROGRESS.md、次の操作はdocs/RESUME.mdへ記録する。

input/work/output/backup/.tmp、docsのジョブ・ローカル状態は公開対象外。examplesは許可した作例のみ公開し、JPG/PSD/JSONのメタデータと意味記述も点検する。backupは実行・テストの依存にしない。

## 対象外

隠れたパーツの描き足し、完全な隙間復元、Live2D用パーツ展開、ベクター線、特殊発光・厚塗りの完全分離、実ペイントアプリでの互換性保証は含まない。同じjobに複数の書込CLIを同時実行しない。プレビューは読取り専用なので併用できる。
