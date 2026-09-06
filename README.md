# PsdMaker

完成済みのアニメイラストと対応する線画から、色や影を編集しやすいレイヤー構造のPSDを作るための、Codex Agent＋ローカルPythonツールです。

単純な画像変換ではありません。Codexの視覚判断で「髪・肌・衣装・瞳・背景」などの意味を整理し、Pythonが位置合わせ、領域抽出、マスク生成、色計算、PSD保存、再構成評価を担当します。LLMに画素列や巨大なマスクを生成させず、人が確認できる領域一覧と短いJSON計画を介して両者を分離しています。

## できること

- 完成イラストと線画のサイズ差・局所ずれを補正
- 見えている領域を意味・素材ごとのパーツへ整理
- Base、Shadow、Highlight、Lineartを持つ階層PSDを生成
- 同素材のパーツに共通パレットを割り当て、まとめて色替えしやすくする
- 背景単独表示、色選択、連結成分レビューによる局所修正
- 保存したPSDを読み戻し、原画との差と意図した編集結果との差を別々に評価
- 色替え、線画、背景、影・光OFFなどの比較画像を使った事後レビュー

PSDの階層やレイヤー数は画像と設定によって変わります。新しい編集優先工程では、たとえば「キャラクター／衣装／袖」のような意味階層の中にBase、Shadow、Highlight、色・模様を隣接配置します。

## 作例

入力例:

![入力イラストの例](examples/reference.png)

生成例: [編集可能なPSDをダウンロード](examples/output/output.psd)

この作例は一つの入力に対する結果です。同等の品質、同じレイヤー数、同じ分離精度を別の画像で保証するものではありません。

## 必要なもの

- Windows環境（付属BATを利用する場合）
- Python 3.12以上（Tkinterを含む）
- 初回セットアップ時のインターネット接続
- このフォルダを読み書きできるCodex環境
- 完成イラストと、同じ構図の線画

線画がない場合は、利用中のCodex環境に画像生成機能があれば作成を依頼できます。ただし、生成線画は原画と完全一致しないため、位置合わせと目視確認が必要です。

## Codex／Astraの利用料金

ローカルPython CLI自体はOpenAI APIキーを要求しません。ただし、意味分類や画像の目視判断を含む一連のAgent工程にはCodexを使用します。

OpenAI公式情報では、CodexはFreeを含む各ChatGPTプランに含まれますが、プランごとに利用上限があります。GPT-6 Astraの利用可否は、プラン、使用するCodexクライアント、ワークスペース設定、提供状況によって異なります。このプロジェクトでは、利用画面で選択できる場合にGPT-6 Astraを推奨します。無料枠だけで処理を完了できるとは限らず、長時間の画像解析では有料プランや追加クレジットが必要になる場合があります。

CodexへChatGPTアカウントでサインインする場合は、そのChatGPTプランの利用枠が適用されます。APIキーで認証する場合はChatGPT契約とは別のAPI従量料金が適用されます。料金・対象プラン・モデル提供状況は変更されるため、実行前に公式ページを確認してください。

- [Codexの料金とプラン（OpenAI公式）](https://learn.chatgpt.com/docs/pricing)
- [Codexで利用できるモデル（OpenAI公式）](https://learn.chatgpt.com/docs/models)
- [GPT-6 Astraのモデルガイド（OpenAI公式）](https://developers.openai.com/api/docs/guides/latest-model)

## セットアップ

リポジトリ直下で `setup.bat` を実行します。

```powershell
.\setup.bat
```

必要なPythonパッケージはプロジェクト内の `.venv` にインストールされます。システム全体のPython環境は変更しません。

## はじめて使う場合

1. リポジトリ直下に `input` フォルダを作ります。
2. 完成画像を `input/reference.png`、線画を `input/lineart.png` として配置します。
3. `start_preview.bat` を起動します。PSDが作られるまでは待機画面になります。
4. このフォルダをCodexで開き、次のように依頼します。

```text
AGENTS.md、docs/ARTIST_WORKFLOW.md、docs/POST_REVIEW_CHECKLIST.mdを読み、
input/reference.pngとinput/lineart.pngからoutput/my_job/output.psdを作成してください。
描画レイヤー数と色統一許容値はお任せします。
位置合わせ、意味分類、編集設定、PSD生成、評価、事後レビューまで進めてください。
```

レイヤー数や色の統一度を指定したい場合は、最後から3行目を次のように変更します。

```text
描画レイヤー数は100〜180枚、色統一許容値は30にしてください。
```

描画レイヤー数はフォルダを含まない枚数です。色統一許容値は0〜100で、0は元の塗り色を優先し、値を上げるほど同素材の色を揃えやすくします。いずれも未指定または「お任せ」にできます。

## 処理の流れ

1. 入力と線画を検査し、線画側を原画へ位置合わせします。
2. Codexが領域一覧を確認し、意味・素材単位の `semantic_plan.json` を作ります。
3. 編集設定とパレットを決め、背景や小領域の混入を確認します。
4. PythonがPSDを生成し、原画差とPSD読み戻し誤差を測定します。
5. Codexが実PSDの色替え・線・背景・下塗りなどを事後確認します。

進行状況は `start_preview.bat` の別ウィンドウで確認できます。PSD、参照、差分、線画、背景、配色を切り替えられます。

## 主な出力

`output/my_job/` には通常、次のファイルが作成されます。

- `output.psd`: レイヤー分けされたPSD
- `reconstructed.png`: PSDを読み戻して再合成した画像
- `diff.png`: 参照画像との差分
- `analysis.json`: MAE、SSIM、色差、エッジ差などの評価
- `layer_plan.json`: PSD構成に使った意味計画
- 位置合わせ、背景、配色、事後レビュー用の比較画像とレポート

詳細なマスクや解析キャッシュは `work/my_job/` に保存されます。`input/`、`work/`、通常の `output/` はGitの公開対象外です。

## CLIを直接確認したい場合

通常はCodexが視覚確認を挟みながら実行します。主要コマンドの流れは次のとおりです。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent monitor --psd output/my_job/output.psd --job work/my_job
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-compact --reference input/reference.png --lineart input/lineart.png --job work/my_job

# Codexが領域画像を確認し、work/my_job/semantic_plan.jsonを作成した後に実行
.\.venv\Scripts\python.exe -m anime_layer_agent configure-editing --job work/my_job --layer-range auto --color-tolerance auto
.\.venv\Scripts\python.exe -m anime_layer_agent review-editing --job work/my_job
.\.venv\Scripts\python.exe -m anime_layer_agent build-compact --job work/my_job --output output/my_job/output.psd
.\.venv\Scripts\python.exe -m anime_layer_agent evaluate --job work/my_job
.\.venv\Scripts\python.exe -m anime_layer_agent post-review --job work/my_job

# 比較画像を実際に確認してpost_review_assessment.jsonを記入した後に実行
.\.venv\Scripts\python.exe -m anime_layer_agent finish-review --job work/my_job
```

詳細は次の文書を参照してください。

- [イラストレーター向け工程](docs/ARTIST_WORKFLOW.md)
- [コンパクト工程](docs/COMPACT_WORKFLOW.md)
- [Astra実行手順](docs/AGENT_WORKFLOW.md)
- [作業後セルフチェック](docs/POST_REVIEW_CHECKLIST.md)
- [仕様](docs/SPEC.md)

## 品質・精度についての重要な注意

このツールを使っても、精度の良いPSDや制作実務にそのまま使えるPSDが得られる保証はありません。結果は、原画と線画の一致度、線の閉じ方、塗り方、グラデーション、素材数、背景の複雑さ、Codexの意味判断などに左右されます。同じ入力でもモデルや設定、判断によって構成が変わることがあります。

MAEやSSIMなどの数値が合格しても、パーツの意味分離、境界、色替えの自然さ、レイヤー名、編集しやすさが正しいとは限りません。出力PSDと比較画像を必ず人が確認し、重要な用途ではPhotoshopまたはCLIP STUDIO PAINT上でも確認してください。

主な制限:

- 元画像で隠れている髪、腕、衣服などは自動で描き足しません。
- Live2D向けの完全なパーツ展開やベクター線画は生成しません。
- 厚塗り、複雑な透過、特殊発光、開いた線、細かな模様は正しく分離できない場合があります。
- AI生成線画には形状差や描き直しが含まれる場合があります。
- Photoshop／CLIP STUDIO PAINTでの完全な互換性を保証しません。
- 作例のPSDにも、細い境界や線画、極端な色替えで手作業の調整が必要になる場合があります。

## プライバシーと公開ファイル

利用者が置いた入力画像、解析途中のマスク、ジョブ記録、通常の生成PSDは `.gitignore` で除外されます。`examples/` の画像とPSDだけは公開作例として明示的に含めています。

Gitに無視されていることは、Codexや画像生成サービスへ送信されないことを意味しません。画像をCodexに確認させる場合や画像生成機能を使う場合は、利用しているサービスのデータ取り扱いと、画像を処理・公開する権利を各自で確認してください。

## 開発者向け検証

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts/verify_compact.py --job work/my_job
.\.venv\Scripts\python.exe scripts/verify_preview.py
```

CLI終了コードは、`0` が成功、`1` が入力または実行エラー、`2` が数値品質、編集結果の読み戻し、指定枚数、事後レビューのいずれかの未達です。`post-review` が画像を生成しただけでは、目視確認済みとは扱いません。
