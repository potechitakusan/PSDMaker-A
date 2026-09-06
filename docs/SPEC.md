# 仕様 v1

## 編集優先プロファイル（2nd concept対応）

新規制作のAgent手順は `docs/ARTIST_WORKFLOW.md`。従来compact計画との互換性を保ち、`configure-editing`でartistプロファイルへ移行する。利用者から描画枚数の範囲と色統一許容値0〜100を受け取り、お任せ・未指定は意味素材数と共有素材の色差から自動計算する。描画数・フォルダ数・合計を別記し、上限超過は保存前に拒否、下限未達は保存可能だが未達として扱う。

同じpalette_idは共通の下塗り色。group_pathの階層下で各パーツにBase/Shadow/Highlight/色補正をまとめる。影・光のRGBはneutral/cool/warmの一定照明色、透明度は画素別。色統一は原画の塗りからのCIE76距離を許容値/5以内に制限する。新規設定のdetail_mode=relativeは残る色差をMultiply/Screen色補正へ分離し、Baseの変更に応答させる。意図した固有色だけpart.detail_mode=pigmentでNormalへ残せる。detail_mode未指定の旧計画は旧方式を維持する。既定線画は回収線RGBの輝度を全チャンネルに適用したモノクロ。新規設定のline_cleanupは、補正ガイドに裏付けされない点や塗り跡を塗り側へ戻し、モノクロ化前の合成RGBを保つ。線画の変更は塗りの許容値と別で記録・評価する。

背景単独画像・疑わしい成分一覧を出力する。明るい背景を目視確認した場合だけbackground_cleanupを使い、近隣前景と背景色の比較で狭い境界を回収する。select-colorは原寸種座標、色差/RGB距離、4/8近傍/全体、色系統/HSV色相、source-partによる範囲制限を扱う。assign-selectionは入力一致を検証して再現条件を計画へ埋め込み、重複を拒否し、旧計画保存とpartial_reviewへの変更を行う。

評価は原画の品質基準を維持し、意図した編集結果editing_targetとのPSD読み戻しを独立測定する。後者はMAE≤0.5、最大チャンネル差≤3（0〜255）で検証する。編集用target/reportはハッシュを検証する。数値合格は意味分離や実アプリ確認の代用ではない。プレビューは線画・背景・配色も表示する。以下のレイヤー数・カラー線・役割別フォルダの記述は旧プロファイルの仕様。

## 事後レビューと局所修正

`docs/POST_REVIEW_CHECKLIST.md`を作業後に必須確認する。artistのbuild/evaluateは数値合格でもpost_review_requiredで止まり、`post-review`が実PSDから全パレットの強い色替え、素材仮色、線/背景単独、暗背景、影/光OFFを生成する。黒・グレーも彩度と明度を上げ、色替えが見えないまま検査を通さない。PSDファイル自体は変更しない。

Astraは10項目のstatus・所見・画像根拠を記入し、`finish-review`がPSD/計画/画像のハッシュ、全項目と数値評価の一致を検証する。未記入や古い根拠を拒否し、failはneeds_repair、limitationはcomplete_with_limitationsとして報告する。ハッシュ検証は視覚判断の正しさを保証せず、チェックリスト生成だけで確認済みとは扱わない。

`review-components --geometry ID`または`--material ID`は連結成分IDと画像一覧を生成する。semantic_planのcomponent_assignmentsにkind/region/components/from/toを記入すると、同色の白目と髪などを局所的に別素材へ移せる。未知ID・重複・元パーツ不一致を拒否する。新規compact_version=2は線画変形の画像外端を最近傍の領域で補い、画面端を一律に背景へ落とさない。

自動描画数の目安は前景素材数Nに対し2N+2〜5N+2（全てrelativeの場合）。pigment素材は上限を1ずつ減らす。実在する必要なレイヤーだけ保存し、フォルダ数は別に数える。

## コンパクト工程と作業時間ログ

- compactの意味計画には `recover_background_leaks`（既定true）がある。falseの場合は背景の色が暗いことだけを理由に前景へ再割当しない。前景と同色の独立した背景要素を保持する回帰テストがある。

- 線画未提供時は画像生成AI機能で線画を作り、原画像とは別に保存する。
- 分解開始前に参照画像と線画のサイズ差・局所ずれを検査する。ずれがあれば補正し、補正前後の画像と数値を確認する。元絵の位置を優先し、残る差は明示する。
- 指示後の最初の作業時刻（受信時刻が利用できる場合は受信時刻）から、成果物検証完了までの壁時計時間を記録する。jobのtiming.jsonへ開始・フェーズ・完了時刻、経過秒数と分秒を保存する。

## レイヤー構成

目標は通常50〜100レイヤー。領域ごとにレイヤーを作らず、意味と素材ごとにBase/Shadow/Highlightへまとめる。入力の生成線画を局所位置合わせした後、元絵の輪郭位置と色を回収して二重線を避ける。色配列とマスクはPythonで生成する。グラデーションを必要以上に量子化せず、パーツ内の画素別Multiply/Screen係数を解く。

実装済みの詳細は `docs/COMPACT_WORKFLOW.md`。以下のv1記述は従来CLIに関するもので、局所位置補正・コンパクト分解は新工程で扱う。

## 実行構成

Codex Astra + リポジトリのAGENTS.md + ローカルPython CLI。Astraは領域の意味と修正を判断し、Pythonは全画素処理を行う。APIキー、MCPサーバーは初版の必須条件にしない。

7フェーズ: 前処理 → 領域抽出 → Astraの意味分類 → レイヤー分解 → PSD保存 → 再構成評価 → 最大3回の局所修正。

CLI: analyze / build / evaluate / run。補助: monitor / summary / repair / apply-repair / demo。

入力は8bit相当のRGB/RGBAイラストと白背景または透明背景の線画。v1はセル塗り対象。軽微な平行移動を探索し、線と参照画像の暗部の一致が改善した場合だけ採用する。サイズ差は線画を参照サイズへリサイズして記録する。半透明の完成画像はv2対象として明示的に拒否する。

## データ

入力SHA-256と抽出設定・処理バージョンからjob IDを作る。`work/<job_id>/` に正規化画像、soft line alpha、ラベル配列、PNGマスク、領域統計、contact sheet、layer_plan.json、レイヤーRGBA、PSD、評価を保存する。同じジョブは前処理・抽出を再利用。異なる入力で既存jobを上書きしない。

Geometryは線の閉領域のConnected Componentsと近傍割当、ColorはGeometry内のLabクラスタリングと連結成分。領域ID、parent_id、bbox、centroid、pixel_area、mean/median RGB、mean Lab、dominant_colors、neighbor_ids、mask_pathを保存する。

機械的な初期計画は `classification_source: heuristic`。Astra確認後は `astra_reviewed`。`run` は初期計画でも最後まで動くが、意味分類済みとは表示しない。

PSD: Character内に下からBackground / Base / Shadows / Highlights / Lineart。BaseとLineartはNormal、影は数値ソルバーによるMultiply、光はNormal/Screenの比較。ブレンドは拡張可能な関数レジストリ。レイヤーPNGとPSDを同じデータから生成し、保存したPSDを再度開いて再構成する。生成した基本構造のPSDは、読み戻したPixelLayerのbbox内だけをNumPyで合成する。標準psd-tools合成との比較では8bit丸めによる最大1階調の差を許容する。

評価: RGB MAE(0–255)、SSIM、CIEDE2000、エッジ不一致率。全体、Geometry、semantic part別。既定品質はMAE≤4、SSIM≥0.95、平均ΔE≤4、edge mismatch≤0.03。worst regionと局所修正用cropを保存。合格でない場合は品質未達として記録する。

## 別プロセスの進捗画面

解析開始前から `start_preview.bat` を用意する。batは依存を整えpythonwでTkウィンドウを独立起動。既定は `docs/active_job.json` に従い、引数 `--psd` で任意のPSDを固定監視できる。未作成なら待機。

更新検出はmtime_ns・size・ファイルIDを監視（0.5秒間隔＋0.5秒の安定待ち）。安定したファイルをバックグラウンドスレッドで読む。生成PSDのSHA-256がlayers.jsonの保存時ハッシュと一致する場合、psd-toolsが保存時に更新したPSD内の合成画像を表示する。この検証により、多数のレイヤーを更新のたびに再合成する待ち時間を減らす。外部で編集されたPSDやハッシュ未確認のPSDはpsd-toolsでレイヤーを再合成する。

書き込み途中や一時ロックは最後の正常表示を保持して再試行。保存側は同じディレクトリに一時保存→os.replace。非常に短い間隔の連続保存は安定した最新状態へ集約される。画面はPSD、参照、差分を切替え、フェーズ・更新回数・レイヤー構造を表示する。

ジョブ状態は機械用JSONに加え `docs/jobs/*.md` を各フェーズで自動更新。設計・進捗・再開手順は常にdocs内のmdに置く。

## v1の境界と次の改善

- 初期計画は最大面積の色領域をBaseとする機械推定。髪・肌・衣装の認識はAstraの視覚確認を必要とする。
- 開いた線の領域漏れ、細かい色断片、元絵と異なる色/位置の線は誤差要因。グラデーションの一定色Shadow/Highlight近似は色むらを生じ得る。
- repairは局所的な役割割当を変更する。Geometryの分割し直し、曲線の非剛体位置合わせ、線色再推定は実装対象外。
- 1ジョブを複数の書き込みCLIから同時実行しない。プレビューは読み取り専用なので同時起動してよい。
- MCP wrapperはCLIと実画像の品質を安定させてから追加する。v1では必須にせず未実装。

## 参照資料

- [CodexのAGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md): プロジェクト作業指示の入口。
- [psd-tools layers API](https://psd-tools.readthedocs.io/en/latest/reference/psd_tools.api.layers.html): Group/PixelLayer生成。実装時はインストール済みAPIも検査する。
