# PsdMaker — Codex Astra 作業指示

このプロジェクトではCodex Astraが画像の意味判断を担当し、Python CLIを実行して編集可能なPSDを生成する。モデルの選択は利用者のCodex画面に従う。別のモデルへの変更やAPI契約を前提にしない。

## 各セッションの開始

1. `docs/SPEC.md` と該当機能の公開作業手順をUTF-8で読む。ローカル状態の `docs/PROGRESS.md`、`docs/RESUME.md`、入力メモが存在する場合は、あわせて読む。同じ会話で読んだ内容は保持し、変更・不明点・コンテキスト欠落がある箇所だけ再読込する。
2. `docs/active_job.json` と該当する `docs/jobs/*.md` があれば読み、未完了フェーズから再開する。入力、計画、成果物の一致を確認する。これらの状態ファイルと利用者の画像はGitへ追加しない。
3. **画像解析より先に `start_preview.bat` が存在することを確認する。なければ復元する。** 利用者へ起動するファイルを伝え、`monitor --psd ... --job ...` で監視先を登録する。利用者が.batを開けば別プロセスの画面を表示できる状態にする。画面の起動待ちで解析を止める必要はない。
4. `.venv/Scripts/python.exe` を使う。依存不足は `python scripts/bootstrap.py` で追加する。ユーザーはプロジェクト内の依存追加を許可済み。OSの権限やネットワーク承認が必要なら理由を説明する。

## トークン節約と品質維持

- 速度より節約を優先。通常は単独実行し、サブエージェントは利用者の明示依頼時のみ使う。
- 完了通知を優先。待機は原則60秒（環境の上限内）とし、頻繁な状態確認や重複実行を避ける。
- 読込・ログ・説明は必要箇所に絞る。変更や未解決事項がなければ再読込・再検証しない。
- モデル・推論設定と既定の品質確認を維持。全画像確認・再構築後のレビューは省略しない。

採否の根拠は`docs/TOKEN_POLICY.md`（方針見直し時のみ参照）。

## 任意の第2機能（カラー版）: 指定線画を着色する

- 利用者が着色対象の線画を指定した場合だけ、`docs/COLORING_WORKFLOW.md`の着色工程を実行する。既存の画像分解から自動的に着色や別ポーズ生成を開始しない。明示された開発試験の模擬線画は別jobで扱い、通常入力と区別して記録する。
- `prepare-coloring --lineart ... --source-job ... --job ...`で参照PSDのBase色と線画を準備。Pythonの領域一覧を目視し、semantic_planでパレットと意味を割当。座標塗りはfill-region、局所隙間補助はsplit-color-regionを使う。
- 参照jobがない場合は、PSDに隣接した `coloring_reference.json` を `--source-reference` へ渡せる（`--source-job`とは排他）。partsの意味名・階層・coloring_notesを手掛かりに、指定線画の領域を新規に確認する。元のbbox・領域番号・左右の位置を新ポーズへコピーしない。
- 下塗りはpaint-flats。任意のAI照明ガイドは全体画像から作り、prepare-lightingで位置合わせした実画像を確認。入力線画とパーツ形状を固定し、ガイドの明度を影・光に使う。
- 照明案は下塗りとsource_appearance.pngの両方を参照。事後確認ではtones比較で髪・肌・白布の明部と色味を確認し、Base一致だけで原画の印象を再現したとしない。
- coloring_job.jsonがあるjobはbuild-coloredで保存する。参照PSDと指定線画を上書きしない。post-reviewとfinish-reviewによる事後チェックを必須とし、評価はPython着色結果からのPSD読み戻しと意味・見た目の判断を分ける。

## 任意の第1機能（トーン版）: カラーイラストを漫画トーン化する

- 利用者が漫画化・モノクロ原稿化・トーン化を明示した場合だけ、`docs/MANGA_WORKFLOW.md`を実行する。カラー版の工程から自動起動しない。
- 開始時に、再描画モデル（imagegen / qwen / none）、線抽出（imagegen / qwen / provided / redraw）、トーン形式（dots / gray）を確認する。既に指定されていれば再質問しない。お任せなら既定値（imagegen・同じ・dots）を記録する。
- `prepare-manga`で元サイズと生成用縮小画像の倍率を記録する。imagegenは`generation_input.png`と`prompts.json`から別ファイルへ生成する。Qwenは利用者が起動したComfyUIへ`run-comfy-manga`で依頼する。ComfyUIのノード・モデルは変更しない。
- `register-manga`が倍率換算と局所位置合わせをする。`manga_alignment.png`を開き、生成画像のずれ・描き足し・欠落を確認して記録する。座標の判断は元画像のpxで行う。
- パーツはカラー原画を見て判断する。`manga_regions_*.png`の全領域を割り当て、意味名と階層を付ける。AIの再描画の明暗だけで素材を決めない。
- **通常はツール既定値で出力し、Codexによる仕上げ設定の調整を行わない。** tone_settingsはDEFAULT_SETTINGS、各partのtoneは既定（base:auto、shadow:auto、highlight:white、beta:true）を維持する。肌だからwhite、黒服だからblackなどの上書き、網・濃度・閾値の変更、前回値やプリセットの流用も行わない。利用者が明示した値は優先し、Codexの推奨設定の提案・適用は推奨を求められた場合だけ行う。
- 意味分類・位置合わせ・マスクの誤割当修正と事後レビューは継続する。既定値の見た目に制約があっても所見として記録し、無断で数値を変更しない。
- `build-manga`→`post-review`→全画像の目視→`finish-review`。線画単独・ベタ単独・ホワイト・パーツ別トーンと網点の1:1画像を必ず確認する。移植コードのMIT表示は`THIRD_PARTY_NOTICES.md`に維持する。

## 任意の第2機能（トーン版）: 別線画へトーンを貼る

- 利用者が指定した線画だけに `docs/TONE_TRANSFER_WORKFLOW.md` を適用する。カラー版とトーン版は第1・第2機能の別形式であり、機能番号を増やさない。
- `prepare-tone-transfer` に新線画と、参照jobまたはPSDに隣接した `tone_reference.json` を渡す。元ポーズの領域・座標・マスクをコピーしない。
- 新線画の全領域を目視し、素材とbase/shadow/highlight/betaを割り当てる。Astraは確認後にastra_reviewed、画面で利用者が確認した場合はuser_reviewedとして区別する。
- 参照から引き継ぐ素材濃度と網設定を維持し、Codexによる見栄えの数値調整は推奨設定を求められた場合だけ行う。新線画への素材・役割の割当は必要な意味判断として実施する。
- `start_manga_adjust.bat` で領域・素材濃度・網設定を調整できる。PNGはexport-manga-png、PSDはexport-manga/build-manga。元線画と参照PSDを上書きしない。PSDのpost-review→全画像確認→finish-reviewを省略しない。

## 第1機能（カラー版）: 画像からPSDを作る

- **新規制作は `docs/ARTIST_WORKFLOW.md` の編集優先工程を使う。** 最初に描画レイヤー数の範囲（フォルダは別集計）と色統一の許容値0〜100をまとめて受け取る。既指定は再質問せず、お任せ・未指定は画像の意味素材数と色のばらつきから算出して根拠を記録する。
- 意味計画を作ったら `configure-editing` を実行する。同素材と目視確認した左右パーツはpalette_idを共有し、group_pathで衣装→パーツ内にBase/Shadow/Highlight/色・模様を並べる。既定の線画はモノクロ、影・光はグレー。原画との差とPSD読み戻しの誤差を別々に報告する。
- 第2機能への引き継ぎに備え、各partに一意のsemantic_id、意味が分かるdisplay_name、palette_id、group_pathを付け、曖昧な素材・固有色・左右差は任意のcoloring_notesへ記録する。build-compactのartistプロファイルはPSDの実Baseからcoloring_reference.jsonを自動出力する。既存jobはexport-coloring-referenceで書き出せる。PSDとJSONを一組で渡し、事後レビューの結果・制約も説明する。JSON生成だけで意味確認済みとは扱わない。
- 背景単独画像を必ず確認する。混入は視覚確認した境界補助・select-color / assign-selectionで局所修正する。色だけを根拠に背景要素を前景と決めつけない。
- **作業後は `docs/POST_REVIEW_CHECKLIST.md` に従い作業したAstra自身が事後確認する。** post-reviewで実PSDの色替え・線・背景・下塗りを生成し、全比較画像を開いて所見を記録、finish-reviewで確定する。数値合格だけでは完了にしない。再構築したらチェックもやり直す。

- **線画がまだない場合は、画像生成AI機能で参照画像を線画化する。** 元絵の構図・形状を保持し、別ファイルへ保存して実画像を確認する。
- **分解・意味分類・PSD構築を始める前に、参照画像と線画のサイズ差と局所ずれをチェックする。ずれがあれば可能な限り補正し、補正前後の画像と数値を確認してから進める。** 元絵は変形せず、残るずれや再描画による差は記録する。
- 作業指示を受けたら開始時刻を記録し、各フェーズと完了時刻、経過秒数・何分何秒をjobの時間ログに残す。受信時刻が取得できない場合は最初の作業時刻を計測起点と明記する。

- **通常のイラストはコンパクト工程を優先する。** `prepare-compact` で線の局所位置合わせと元絵由来の線回収→意味別 `semantic_plan.json` → `build-compact`。詳細は `docs/COMPACT_WORKFLOW.md`。
- レイヤー数は利用者の範囲または編集工程の自動予算に従う（旧工程のみ50〜100項目が目安）。領域断片をレイヤーへ1対1で写さず、パーツと素材単位にまとめる。水増しや統合済み原画を最上段に被せて数値だけ合わせない。
- 線画は生成AI由来なら局所ずれを検査する。線位置は元画像を優先し、色は編集プロファイル（既定モノクロ、source指定時は元絵色）に従う。ずれた黒線をそのまま重ねない。画像生成による線画の使用はユーザー許可済みだが、新たなずれがないか必ず再確認する。
- `compact_job.json` のあるjobは `build-compact` で再構築する。従来の `build` / `apply-repair` はschema 1用で、コンパクトjobへ適用しない。コンパクトの局所修正はsemantic_planの該当ID割当/素材分離を修正して再構築する。

- `docs/AGENT_WORKFLOW.md` のコマンドと意味分類プロトコルに従う。
- Pythonが作ったoverview/contact sheetを実際に画像ツールで開き、Region IDを参照して `layer_plan.json` を編集する。機械推定をAstraの視覚判断済みと偽らない。
- LLMは意味、領域の役割、異常の判断だけを行う。画素、マスク、色、座標列、blend計算、PSD生成はPythonへ任せる。画像の再生成を分解の代用にしない。
- 不明な意味は `other` / `unknown` として記録する。semantic_idは自由な文字列。カラー領域を二重割当しない。
- `build` → `evaluate` → 必要なら `repair` の局所画像を確認し `apply-repair`。最大3回。全体再解析を修正のたびに繰り返さない。
- 成果物の実在と評価結果を検証し、未達の品質は未達と伝える。実画像未提供の場合は人工fixtureで実装を検証し、その制約を記録する。

## 永続記録

- 仕様変更は同じ作業中に `docs/SPEC.md` へ、進捗とテスト結果はローカル状態の `docs/PROGRESS.md` へ、次の具体的な操作は `docs/RESUME.md` へ記録する。
- ジョブのフェーズごとの状態はCLIが `docs/jobs/<job>.md` に保存する。意味判断の根拠と未確定事項も当該mdへ記録する（追記用 `notes.md` ではなく `docs/jobs/<job>-decisions.md` を使う）。
- 中断前・終了前に再開に必要な入力パス、job、変更した計画、最後の成功コマンド、残課題を残す。会話だけを進捗の保管場所にしない。
- 適切なテストを実行し、APIはインストールされた実装または公式資料で確認する。
