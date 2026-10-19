# Agent共通実行手順

## 開始と再開

利用者の最新指示、AGENTS.md、SPEC.md、該当機能の公開手順をUTF-8で読む。PROGRESS.md、RESUME.md、active_job.jsonと該当docs/jobsの記録があれば確認し、入力・計画・成果物が一致する未完了フェーズから再開する。ローカル状態のない新しいcloneでも作業できる。

開始時刻を記録し、受信時刻が取得できなければ最初の作業時刻と明記する。フェーズと完了時刻、経過秒数・分秒を残す。画像解析より先にstart_preview.batの存在を確認し、利用者へ案内する。monitorで対象PSD/jobを登録し、画面の起動待ちでは作業を止めない。

Pythonは `.venv/Scripts/python.exe` を使う。環境の作成・依存不足の解消には `python scripts/bootstrap.py` を使う。依存追加・外部AI利用は [AGENTS.mdの権限方針](../AGENTS.md#権限と画像生成の利用) に従い、当該利用者の指示と環境の許可設定を確認する。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent monitor --psd output/my_job/output.psd --job work/my_job
```

## 工程を選ぶ

| 対象 | 手順 | 再構築 |
|---|---|---|
| 第1機能（カラー版）：新規の完成イラスト | ARTIST_WORKFLOW.md＋COMPACT_WORKFLOW.md | build-compact |
| 第2機能（カラー版）：利用者が指定した着色用線画 | COLORING_WORKFLOW.md | build-colored |
| compact_job.jsonがある既存job | 既存semantic_planを局所修正 | build-compact |
| coloring_job.jsonがある既存job | 領域・パレット・必要な照明を再確認 | build-colored |
| 第1機能（トーン版）：漫画化を依頼したカラーイラスト／manga_job.jsonだけがあるjob | MANGA_WORKFLOW.md | build-manga |
| 別線画へのトーン貼り／tone_transfer_job.jsonがあるjob（第2機能・トーン版） | TONE_TRANSFER_WORKFLOW.md。新線画の領域を分類 | build-manga / export-manga / export-manga-png |
| トーン版の第1・第2機能を画面調整する | start_manga_adjust.bat。第1機能は意味分類後、第2機能は領域割当から操作。「反映」で設定保存 | PNG出力 / PSD出力（CLIはexport-manga-png / export-manga） |
| job.json/layer_plan.jsonのschema 1 job | 旧CLI互換処理。必要なら各コマンドの--helpを確認 | build / evaluate / repair / apply-repair |

tone_transfer_job.jsonのあるjobはmanga_job.jsonも持つため、第2機能（トーン版）の手順を優先する。画面で保存した調整案を出力へ使う場合はexport-manga/export-manga-pngを使う。build-mangaはsemantic_planを直接使い、未出力のGUI調整案は取り込まない。

新規分解で線画がなければ上記の権限方針に従って画像生成機能で別ファイルへ作り、サイズ・局所ずれを補正前後の画像と数値で確認してから分類する。生成機能を利用できない場合は線画の提供が必要。第2機能の着色・トーン貼りでは指定線画を参照キャラクターのポーズへ変形しない。

現行方式の編集対象は `semantic_plan.json`。compactの構築時に出力されるschema 2の `layer_plan.json` は評価用で、直接編集しない。`evaluate`は構築済みcompactの再評価にも使えるが、意味計画を変更したら先に `build-compact` で再構築する。coloring・mangaは各buildの評価とpost-reviewを使う。

## 旧方式の既存ジョブを再開する

この節は `job.json` とschema 1の `layer_plan.json` を使う既存jobだけに適用する。新規制作は上の機能別手順を使う。`compact_job.json`、`coloring_job.json`、`manga_job.json`のあるjobへ旧方式のbuild・repair・apply-repairを実行しない。

overview/contact sheetを画像ツールで開き、Region IDと素材の役割を `layer_plan.json` に記入する。修正依頼の局所画像を確認し、パッチを作ってからapply-repairする。修正は最大3回。以下のjob名とパッチ名は置き換える。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent build --job work/legacy_job
.\.venv\Scripts\python.exe -m anime_layer_agent evaluate --job work/legacy_job
# 修正が必要な場合のみ
.\.venv\Scripts\python.exe -m anime_layer_agent repair --job work/legacy_job
# repairが出す局所画像を確認し、修正パッチを作成してから実行
.\.venv\Scripts\python.exe -m anime_layer_agent apply-repair --job work/legacy_job --patch work/legacy_job/repair_patch.json
```

`analyze` / `run`も旧方式の互換コマンドであり、現行の新規制作の入口には使わない。パッチ等の詳細は各コマンドの `--help` とjob内の修正依頼を参照する。

## 意味分類の責任

Pythonのoverview/contact sheetまたはgeometry_review/coloring_regionsの全ページを実際に画像ツールで開く。Region ID・素材の役割をsemantic_planへ記入する。機械推定を視覚確認済みと呼ばない。確認前はheuristic/unreviewed/partial_reviewを保持し、確認後だけastra_reviewedとする。

semantic_idとdisplay_nameを一意にし、カラー版では同素材と確認したものだけpalette_idを共有する。group_pathで編集階層を決める。第2機能への注意点はカラー版のcoloring_notes、トーン版のtone_notesへ書く。不明な意味はother/unknownとし、領域を二重割当しない。第2機能（トーン版）のGUIで利用者が行った確認はuser_reviewedと記録し、Astraの確認と区別する。画素・マスク・巨大な座標列・ブレンドはPythonに任せる。

背景単独画像、素材仮色、疑わしい小領域を目視し、局所選択や素材分離で修正する。色だけで背景の床影・家具などを前景と決めない。元画像の領域番号・座標を別画像へ流用しない。

## 構築と完了

各工程のbuildでPSDを保存し、数値評価を読む。原画との一致と意図した編集結果の読み戻しを分ける。着色では別ポーズの原画とSSIM比較しない。

POST_REVIEW_CHECKLIST.mdに従いpost-reviewで実PSDの全比較画像を作成し、作業したAstra自身が開いて所見・根拠を記入する。finish-reviewで確定し、数値だけで完了にしない。修正は最大3回を目安に局所へ絞り、再構築後は事後チェックをやり直す。未達・実アプリ未確認は明示する。

第1機能（カラー版）のPSDにはcoloring_reference.jsonを添えて渡す。既存jobはexport-coloring-referenceで追加できる。JSONは配色・意味の参照用で、視覚品質の合格証ではない。

トーン版の第1機能・第2機能のPSDにはtone_reference.jsonを添える。以前のjobはexport-tone-referenceで追加。画面の「別線画へトーン」から準備・新領域の割当・濃度調整・PNG/PSD出力ができる。Astraの確認はastra_reviewed、利用者の画面確認はuser_reviewedとして区別する。

## 永続記録

CLIはstatus.jsonとdocs/jobs/*-<pathhash>.mdを更新する。Astraの根拠と曖昧な点はdocs/jobs/*-decisions.mdへ記録する。終了・中断前にPROGRESS.mdとRESUME.mdへ入力、job、出力、変更計画、最後の成功操作、残課題を保存する。旧状態を現在の再開地点として重ね続けず、不要な履歴はローカルbackupへ退避する。

入力画像・通常の成果物・ジョブ記録・backupはGitへ追加しない。公開例として明示されたファイルだけ、画像・PSD・JSONの個人情報とメタデータを確認してexamplesの許可対象に加える。
